import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest

from backend.app.adapters.openai_recommender import LocalFallback, OpenAIRecommender
from backend.app.adapters.recommender import SafeRecommender
from backend.app.core.config import Settings
from backend.app.db.models import FeedbackEvent, Garment, Recommendation, RecommendationOutfit
from backend.app.deps import get_recommender
from backend.app.repositories.feedback import FeedbackRepository
from backend.tests.adapters.test_openai_recommender import completed


@pytest.mark.parametrize("mode", ["local", "openai", "fallback"])
@pytest.mark.parametrize("source,target", [("example", "personal"), ("personal", "example")])
def test_feedback_isolated_in_ranking_gpt_and_fallback(
    api_client, auth_account, account_factory, add_garment, db_session, mode, source, target
):
    # Same colors/styles in both closets: a color/style dislike would penalize the
    # other closet even though garment IDs differ, without the repository filter.
    fixed = {}
    for scope in ("personal", "example"):
        for category in ("top", "top", "bottom", "bottom", "outerwear"):
            gid = add_garment(auth_account, category)
            db_session.get(Garment, UUID(gid)).is_example = scope == "example"
            if category == "outerwear":
                fixed[scope] = gid
    db_session.commit()
    captured = []

    def respond(request):
        payload = json.loads(json.loads(request.content)["input"])
        captured.append(payload)
        if mode == "fallback":
            return httpx.Response(503)
        return httpx.Response(
            200,
            json=completed(
                [
                    {"candidate_id": c["candidate_id"], "reason": "색상과 취향을 반영한 조합"}
                    for c in payload["candidates"]
                ]
            ),
        )

    engine = (
        LocalFallback()
        if mode == "local"
        else OpenAIRecommender(
            Settings(_env_file=None, OPENAI_API_KEY="test-only"), transport=httpx.MockTransport(respond)
        )
    )
    api_client.app.dependency_overrides[get_recommender] = lambda: SafeRecommender(engine, 3, LocalFallback())

    def generate(scope):
        r = api_client.post(
            "/api/v1/recommendations",
            headers={**auth_account["headers"], "Idempotency-Key": str(uuid4())},
            json={"wardrobe": scope, "temperature_c": 10, "required_garment_ids": [fixed[scope]]},
        )
        assert r.status_code == 201, r.text
        result = r.json()
        assert all(o["items"]["outerwear"] == fixed[scope] for o in result["outfits"])
        if mode == "fallback":
            assert result["is_fallback"]
        return result

    baseline = generate(target)
    first = generate(source)
    path = f"/api/v1/recommendations/{first['recommendation_id']}/feedback"
    dislike = {
        "recommendation_id": first["recommendation_id"],
        "outfit_id": first["outfits"][0]["outfit_id"],
        "accepted": False,
        "reason": f"{source}-private-opinion",
        "reason_codes": ["colors", "style", "combination"],
    }
    assert api_client.post(path, headers=auth_account["headers"], json=dislike).status_code == 201
    like = {
        **dislike,
        "outfit_id": first["outfits"][1]["outfit_id"],
        "accepted": True,
        "reason": None,
        "reason_codes": [],
    }
    assert api_client.post(path, headers=auth_account["headers"], json=like).status_code == 201
    # Scope cannot be supplied by the client, or another user's recommendation used.
    assert (
        api_client.post(
            path, headers=auth_account["headers"], json={**dislike, "wardrobe": target}
        ).status_code
        == 422
    )
    other = account_factory()
    assert api_client.post(path, headers=other["headers"], json=dislike).status_code == 404
    assert api_client.get(path, headers=other["headers"]).status_code == 404

    unaffected = generate(target)
    assert unaffected["context"]["recent_feedback"] == []
    def signature(r):
        return [(o["items"], o["score"]) for o in r["outfits"]]

    assert signature(unaffected) == signature(baseline)
    if captured:
        assert captured[-1]["recent_feedback"] == []
        assert dislike["reason"] not in json.dumps(captured[-1])

    changed = generate(source)
    assert len(changed["context"]["recent_feedback"]) == 2
    assert changed["outfits"][0]["items"] != first["outfits"][0]["items"]
    assert {e["accepted"] for e in changed["context"]["recent_feedback"]} == {True, False}
    if captured:
        assert {e["accepted"] for e in captured[-1]["recent_feedback"]} == {True, False}
        assert dislike["reason"] in json.dumps(captured[-1])

    # Latest opinion wins inside its scope; append-only history and saved results survive.
    assert (
        api_client.post(
            path,
            headers=auth_account["headers"],
            json={**dislike, "accepted": True, "reason": None, "reason_codes": []},
        ).status_code
        == 201
    )
    latest = generate(source)
    assert len(latest["context"]["recent_feedback"]) == 2
    assert all(e["accepted"] for e in latest["context"]["recent_feedback"])
    assert len(api_client.get(path, headers=auth_account["headers"]).json()) == 3
    assert generate(target)["context"]["recent_feedback"] == []
    db_session.expire_all()
    assert db_session.get(Recommendation, UUID(first["recommendation_id"])).context["recent_feedback"] == []
    assert (
        len(db_session.get(Recommendation, UUID(changed["recommendation_id"])).context["recent_feedback"])
        == 2
    )


def test_scope_is_filtered_before_limit_and_legacy_history_is_preserved(
    db_session, auth_account, account_factory, closet
):
    uid = UUID(auth_account["id"])
    other = UUID(account_factory()["id"])
    now = datetime.now(UTC)

    def add(context, number, owner=uid):
        rec = Recommendation(user_id=owner, context=context, provider="local", latency_ms=0)
        db_session.add(rec)
        db_session.flush()
        outfit = RecommendationOutfit(
            recommendation_id=rec.id,
            rank=1,
            top_id=UUID(closet["top"]),
            bottom_id=UUID(closet["bottom"]),
            style_tags=[],
            reason="test",
        )
        db_session.add(outfit)
        db_session.flush()
        event = FeedbackEvent(
            user_id=owner,
            recommendation_id=rec.id,
            outfit_id=outfit.id,
            accepted=False,
            reason=str(number),
            created_at=now + timedelta(seconds=number),
        )
        db_session.add(event)
        db_session.flush()
        return event

    personal = add({"wardrobe": "personal"}, 0)
    legacy = [add({}, 1), add({"wardrobe": None}, 2), add({"wardrobe": "all"}, 3)]
    invalid = add({"wardrobe": "unknown"}, 4)
    foreign = add({"wardrobe": "personal"}, 5, other)
    examples = [add({"wardrobe": "example"}, i) for i in range(10, 35)]
    orphan = FeedbackEvent(
        user_id=uid, accepted=False, reason="orphan", created_at=now + timedelta(seconds=90)
    )
    db_session.add(orphan)
    db_session.flush()
    repo = FeedbackRepository(db_session)
    assert [e.id for e in repo.recent(uid, wardrobe="personal")] == [personal.id]
    assert [e.id for e in repo.recent(uid, wardrobe="example")] == [e.id for e in reversed(examples[-20:])]
    assert {e.id for e in repo.recent(uid)} == {e.id for e in legacy}
    assert repo.recent(other, wardrobe="example") == []
    assert [e.id for e in repo.recent(other, wardrobe="personal")] == [foreign.id]
    assert db_session.get(FeedbackEvent, invalid.id) is invalid
    assert db_session.get(FeedbackEvent, orphan.id) is orphan
    assert all(db_session.get(FeedbackEvent, e.id) is e for e in examples + legacy)
