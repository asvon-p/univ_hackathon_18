import time
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from backend.app.adapters import registry
from backend.app.adapters.base import RecommendOutcome
from backend.app.adapters.recommender import SafeRecommender
from backend.app.core.config import get_settings
from backend.app.db.models import Garment, Recommendation, RecommendationOutfit, RecommendationRateLimit
from backend.app.deps import get_recommender
from shared.contracts import Outfit, OutfitItems, RecommendationResult


def recommend(client, account, **values):
    return client.post(
        "/api/v1/recommendations", headers=account["headers"], json={"temperature_c": 5, **values}
    )


def engine_result(items):
    return RecommendationResult(
        recommendation_id="engine-id",
        outfits=[
            Outfit(outfit_id="engine-outfit", items=OutfitItems(**items), reason="모델 추천 이유", score=0.82)
        ],
    )


class Engine:
    name = "local"

    def __init__(self, callback):
        self.callback = callback

    def recommend(self, context, garments):
        return self.callback(context, garments)


def use_engine(client, callback, *, timeout=1):
    adapter = SafeRecommender(Engine(callback), timeout_sec=timeout)
    client.app.dependency_overrides[get_recommender] = lambda: adapter


def test_upload_recommend_save_read_and_login_again(api_client, auth_account, closet, db_session):
    response = recommend(api_client, auth_account)
    assert response.status_code == 201, response.text
    body = response.json()
    recommendation_id = UUID(body["recommendation_id"])
    assert body["provider"] == "mock" and body["is_fallback"] is False
    assert body["latency_ms"] >= 0 and body["created_at"].endswith("Z")
    assert body["outfits"][0]["items"] == {**closet, "shoes": None}
    UUID(body["outfits"][0]["outfit_id"])
    assert set(body["garments"]) == set(closet.values()) - {closet["shoes"]}
    for garment in body["garments"].values():
        assert api_client.get(garment["image_url"], headers=auth_account["headers"]).status_code == 200
    row = db_session.get(Recommendation, recommendation_id)
    assert row.user_id == UUID(auth_account["id"])
    assert row.context["user_id"] == auth_account["id"]
    assert row.outfits[0].rank == 1
    db_session.expunge_all()
    login = api_client.post(
        "/api/v1/auth/login", json={"email": auth_account["email"], "password": auth_account["password"]}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert api_client.get(f"/api/v1/recommendations/{recommendation_id}", headers=headers).json() == body
    assert api_client.get("/api/v1/recommendations", headers=headers).json() == [body]


def test_engine_receives_only_eligible_owned_garments_and_profile_defaults(
    api_client,
    auth_account,
    account_factory,
    closet,
    add_garment,
):
    other = account_factory()
    hidden = {
        add_garment(other, "top"),
        add_garment(auth_account, "top", status="laundry"),
        add_garment(auth_account, "bottom", status="inactive"),
        add_garment(auth_account, "shoes", analysis_status="failed"),
        add_garment(auth_account, "top", analysis_status="pending"),
    }
    excluded = add_garment(auth_account, "top")
    hidden.add(excluded)
    api_client.patch(
        "/api/v1/profile", headers=auth_account["headers"], json={"preferred_styles": ["minimal", "casual"]}
    )
    seen = []

    def callback(context, garments):
        seen.append((context.model_dump(), {g.garment_id for g in garments}))
        return engine_result(closet)

    use_engine(api_client, callback)
    response = recommend(api_client, auth_account, occasion="interview", excluded_items=[excluded])
    assert response.status_code == 201
    body = response.json()
    context, received = seen[0]
    assert context["user_id"] == auth_account["id"] and context["occasion"] == "interview"
    assert context["preferred_styles"] == ["minimal", "casual"]
    assert received == set(closet.values()) - {closet["shoes"]} and not received & hidden
    assert body["context"] == context
    assert body["provider"] == "local" and body["outfits"][0]["score"] == 0.82
    assert body["outfits"][0]["reason"] == "모델 추천 이유"
    assert body["recommendation_id"] != "engine-id" and body["outfits"][0]["outfit_id"] != "engine-outfit"
    explicit = recommend(api_client, auth_account, preferred_styles=["street"])
    assert explicit.status_code == 201 and seen[-1][0]["preferred_styles"] == ["street"]
    api_client.patch(
        "/api/v1/profile", headers=auth_account["headers"], json={"preferred_styles": ["sporty"]}
    )
    assert (
        api_client.get(
            f"/api/v1/recommendations/{body['recommendation_id']}", headers=auth_account["headers"]
        ).json()
        == body
    )


@pytest.mark.parametrize("unusable", ["laundry", "inactive", "failed", "pending", "excluded"])
def test_missing_required_slot_returns_422_without_calling_engine(
    api_client,
    auth_account,
    closet,
    unusable,
    db_session,
):
    top = db_session.get(Garment, UUID(closet["top"]))
    excluded = []
    if unusable in ("laundry", "inactive"):
        top.status = unusable
    elif unusable in ("failed", "pending"):
        top.analysis_status = unusable
    else:
        excluded = [closet["top"]]
    db_session.commit()
    called = []
    use_engine(api_client, lambda context, garments: called.append(True))
    response = recommend(api_client, auth_account, excluded_items=excluded)
    assert response.status_code == 422 and response.json()["code"] == "NOT_ENOUGH_GARMENTS"
    assert called == []
    assert db_session.scalar(select(func.count()).select_from(Recommendation)) == 0


def test_another_users_wardrobe_cannot_fill_empty_wardrobe(api_client, account_factory, add_garment):
    owner, other = account_factory(), account_factory()
    for category in ("top", "bottom", "shoes"):
        add_garment(owner, category)
    response = recommend(api_client, other)
    assert response.status_code == 422 and response.json()["code"] == "NOT_ENOUGH_GARMENTS"


@pytest.mark.parametrize(
    "mode", ["exception", "timeout", "empty", "unknown", "wrong-slot", "malformed", "nan"]
)
def test_engine_failure_or_invalid_result_is_replaced_and_persisted(
    api_client,
    auth_account,
    closet,
    db_session,
    mode,
):
    def callback(context, garments):
        if mode == "exception":
            raise RuntimeError("LLM unavailable")
        if mode == "timeout":
            time.sleep(0.08)
        raw = engine_result(closet).model_dump()
        if mode == "empty":
            raw["outfits"] = []
        elif mode == "unknown":
            raw["outfits"][0]["items"]["top"] = str(uuid4())
        elif mode == "wrong-slot":
            raw["outfits"][0]["items"]["top"] = closet["shoes"]
        elif mode == "malformed":
            raw["outfits"][0]["items"]["top"] = None
        elif mode == "nan":
            raw["outfits"][0]["score"] = float("nan")
        return raw

    use_engine(api_client, callback, timeout=0.01 if mode == "timeout" else 1)
    response = recommend(api_client, auth_account)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["provider"] == "fallback" and body["is_fallback"] is True
    assert body["outfits"][0]["items"] == {**closet, "shoes": None}
    assert db_session.get(Recommendation, UUID(body["recommendation_id"])).provider == "fallback"
    assert len(db_session.get(RecommendationRateLimit, UUID(auth_account["id"])).request_times) == 1
    assert (
        api_client.get(
            f"/api/v1/recommendations/{body['recommendation_id']}", headers=auth_account["headers"]
        ).json()
        == body
    )


def test_drop_foreign_duplicate_and_excluded_outfits_then_limit_to_three(
    api_client,
    auth_account,
    account_factory,
    closet,
    add_garment,
    db_session,
):
    other = account_factory()
    foreign = add_garment(other, "top")
    excluded = add_garment(auth_account, "top")
    tops = [closet["top"]] + [add_garment(auth_account, "top") for _ in range(3)]
    variants = [tops[0], tops[0], foreign, excluded, *tops[1:]]
    raw = {
        "recommendation_id": "reused-engine-id",
        "outfits": [
            {"outfit_id": "reused-engine-outfit", "items": {**closet, "top": top}, "score": 0.82}
            for top in variants
        ],
    }
    use_engine(api_client, lambda context, garments: raw)
    response = recommend(api_client, auth_account, excluded_items=[excluded])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["provider"] == "local" and body["is_fallback"] is False
    assert [o["items"]["top"] for o in body["outfits"]] == tops[:3]
    assert len({o["outfit_id"] for o in body["outfits"]}) == 3
    assert foreign not in body["garments"] and excluded not in body["garments"]
    record = db_session.get(Recommendation, UUID(body["recommendation_id"]))
    assert [o.rank for o in record.outfits] == [1, 2, 3]
    second = recommend(api_client, auth_account, excluded_items=[excluded]).json()
    assert second["recommendation_id"] != body["recommendation_id"]
    assert not {o["outfit_id"] for o in body["outfits"]} & {o["outfit_id"] for o in second["outfits"]}


@pytest.mark.parametrize("spare_top", [True, False])
def test_state_changed_during_inference_is_rechecked_before_saving(
    api_client,
    auth_account,
    closet,
    add_garment,
    db_session,
    spare_top,
):
    alternative = add_garment(auth_account, "top") if spare_top else None

    class ChangingAdapter:
        def recommend(self, context, garments):
            db_session.get(Garment, UUID(closet["top"])).status = "laundry"
            db_session.commit()
            return RecommendOutcome(engine_result(closet), "local", False, 0)

    api_client.app.dependency_overrides[get_recommender] = lambda: ChangingAdapter()
    response = recommend(api_client, auth_account)
    if spare_top:
        assert response.status_code == 201
        assert response.json()["provider"] == "fallback"
        assert response.json()["outfits"][0]["items"]["top"] == alternative
        assert closet["top"] not in response.json()["garments"]
    else:
        assert response.status_code == 422
        assert db_session.scalar(select(func.count()).select_from(Recommendation)) == 0


def test_ownership_on_detail_history_and_feedback(api_client, auth_account, account_factory, closet):
    own = recommend(api_client, auth_account).json()
    other = account_factory()
    path = f"/api/v1/recommendations/{own['recommendation_id']}"
    denied = api_client.get(path, headers=other["headers"])
    missing = api_client.get(f"/api/v1/recommendations/{uuid4()}", headers=other["headers"])
    assert denied.status_code == 404 and denied.json() == missing.json()
    assert api_client.get("/api/v1/recommendations", headers=other["headers"]).json() == []
    assert (
        api_client.get(
            f"/api/v1/recommendations?user_id={auth_account['id']}", headers=other["headers"]
        ).json()
        == []
    )
    assert api_client.get(path).status_code == 401
    assert api_client.get("/api/v1/recommendations").status_code == 401
    assert recommend(api_client, other, user_id=auth_account["id"]).status_code == 422
    feedback = {
        "recommendation_id": own["recommendation_id"],
        "outfit_id": own["outfits"][0]["outfit_id"],
        "accepted": True,
    }
    assert api_client.post(path + "/feedback", json=feedback, headers=other["headers"]).status_code == 404
    valid = api_client.post(path + "/feedback", json=feedback, headers=auth_account["headers"])
    assert valid.status_code == 201 and valid.json()["accepted"] is True
    feedback["outfit_id"] = str(uuid4())
    assert (
        api_client.post(path + "/feedback", json=feedback, headers=auth_account["headers"]).status_code == 404
    )


def test_history_pagination_and_historical_status(api_client, auth_account, closet):
    for _ in range(3):
        assert recommend(api_client, auth_account).status_code == 201
    history = api_client.get("/api/v1/recommendations", headers=auth_account["headers"]).json()
    assert len(history) == 3
    assert (
        api_client.get("/api/v1/recommendations?limit=1", headers=auth_account["headers"]).json()
        == history[:1]
    )
    assert (
        api_client.get("/api/v1/recommendations?limit=2&offset=1", headers=auth_account["headers"]).json()
        == history[1:]
    )
    api_client.patch(
        f"/api/v1/garments/{closet['top']}", headers=auth_account["headers"], json={"status": "laundry"}
    )
    detail = api_client.get(
        f"/api/v1/recommendations/{history[0]['recommendation_id']}", headers=auth_account["headers"]
    ).json()
    assert detail["outfits"] == history[0]["outfits"]
    assert detail["garments"][closet["top"]]["status"] == "laundry"


def test_garment_deletion_removes_only_affected_outfits(
    api_client, auth_account, closet, add_garment, db_session
):
    extra_top = add_garment(auth_account, "top")
    body = recommend(api_client, auth_account).json()
    assert len(body["outfits"]) == 2
    assert (
        api_client.delete(f"/api/v1/garments/{closet['top']}", headers=auth_account["headers"]).status_code
        == 204
    )
    remaining = api_client.get(
        f"/api/v1/recommendations/{body['recommendation_id']}", headers=auth_account["headers"]
    ).json()
    assert len(remaining["outfits"]) == 1 and remaining["outfits"][0]["items"]["top"] == extra_top
    assert closet["top"] not in remaining["garments"]
    assert (
        api_client.delete(f"/api/v1/garments/{closet['shoes']}", headers=auth_account["headers"]).status_code
        == 204
    )
    unaffected = api_client.get(
        f"/api/v1/recommendations/{body['recommendation_id']}", headers=auth_account["headers"]
    ).json()
    assert unaffected == remaining  # 새 추천은 신발 삭제에 영향받지 않습니다.
    assert (
        api_client.delete(f"/api/v1/garments/{closet['bottom']}", headers=auth_account["headers"]).status_code
        == 204
    )
    empty = api_client.get(
        f"/api/v1/recommendations/{body['recommendation_id']}", headers=auth_account["headers"]
    ).json()
    assert empty["outfits"] == [] and empty["garments"] == {}
    assert db_session.scalar(select(func.count()).select_from(RecommendationOutfit)) == 0


def test_database_failure_rolls_back_all_recommendation_rows(
    api_client, auth_account, closet, db_session, monkeypatch
):
    commit = db_session.commit
    calls = 0

    def fail():
        nonlocal calls
        calls += 1
        if calls == 2:  # 첫 커밋은 사용량 기록, 두 번째가 실제 추천 저장입니다.
            raise SQLAlchemyError("recommendation commit failed")
        commit()

    monkeypatch.setattr(db_session, "commit", fail)
    with pytest.raises(SQLAlchemyError):
        recommend(api_client, auth_account)
    assert db_session.scalar(select(func.count()).select_from(Recommendation)) == 0
    assert db_session.scalar(select(func.count()).select_from(RecommendationOutfit)) == 0
    assert len(db_session.get(RecommendationRateLimit, UUID(auth_account["id"])).request_times) == 1


def test_local_provider_connects_actual_recommendation_package(api_client, auth_account, closet, monkeypatch):
    monkeypatch.setenv("RECOMMENDER_PROVIDER", "local")
    get_settings.cache_clear()
    registry.reset()
    try:
        body = recommend(api_client, auth_account).json()
        assert body["provider"] == "local" and body["is_fallback"] is False
        assert body["outfits"][0]["items"] == {**closet, "shoes": None, "outerwear": None}
        assert "색상·상황 점수" in body["outfits"][0]["reason"]
        assert body["outfits"][0]["score"] is not None
    finally:
        monkeypatch.setenv("RECOMMENDER_PROVIDER", "mock")
        get_settings.cache_clear()
        registry.reset()


def test_invalid_requests_and_empty_wardrobe(api_client, auth_account):
    for values in (
        {"temperature_c": "hot"},
        {"excluded_items": ["not-a-uuid"]},
        {"preferred_styles": [""]},
    ):
        response = recommend(api_client, auth_account, **values)
        assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"
    assert recommend(api_client, auth_account).json()["code"] == "NOT_ENOUGH_GARMENTS"
    assert (
        api_client.get("/api/v1/recommendations/invalid", headers=auth_account["headers"]).status_code == 422
    )
    for query in ("limit=0", "limit=101", "offset=-1"):
        assert (
            api_client.get(f"/api/v1/recommendations?{query}", headers=auth_account["headers"]).status_code
            == 422
        )
    response = api_client.get("/api/v1/nope")
    assert response.status_code == 404 and response.json()["code"] == "NOT_FOUND"
