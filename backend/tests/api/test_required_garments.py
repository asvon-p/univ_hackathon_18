from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from backend.app.adapters.recommender import LocalRecommender, SafeRecommender
from backend.app.db.models import Garment, Recommendation
from backend.app.deps import get_recommender
from backend.tests.api.test_recommendations import engine_result, recommend, use_engine


@pytest.mark.parametrize("slots", [["top"], ["bottom"], ["outerwear"], ["top", "bottom", "outerwear"]])
@pytest.mark.parametrize("provider", ["mock", "local"])
def test_required_slots_are_preserved_and_persisted(
    api_client, auth_account, closet, add_garment, db_session, slots, provider
):
    for category in ("top", "bottom", "outerwear"):
        add_garment(auth_account, category, styles=["formal"])
    required = [closet[slot] for slot in slots]
    if provider == "local":
        api_client.app.dependency_overrides[get_recommender] = lambda: SafeRecommender(LocalRecommender(), 2)
    response = recommend(api_client, auth_account, temperature_c=15, required_garment_ids=required)
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["outfits"]
    assert data["provider"] == provider
    for outfit in data["outfits"]:
        assert set(required) <= set(outfit["items"].values())
    assert data["context"]["required_garment_ids"] == required
    row = db_session.get(Recommendation, UUID(data["recommendation_id"]))
    assert row.context["required_garment_ids"] == required
    db_session.expire_all()
    read = api_client.get(f"/api/v1/recommendations/{row.id}", headers=auth_account["headers"])
    assert read.status_code == 200 and read.json() == data


@pytest.mark.parametrize("failure", ["exception", "timeout", "omitted", "mutated-context"])
def test_fallback_never_replaces_required_outer(api_client, auth_account, closet, add_garment, failure):
    import time

    add_garment(auth_account, "outerwear")

    def callback(context, garments):
        if failure == "timeout":
            time.sleep(0.08)
        if failure == "exception":
            raise RuntimeError("provider unavailable")
        if failure == "mutated-context":
            context.required_garment_ids.clear()
        return engine_result({"top": closet["top"], "bottom": closet["bottom"]})

    use_engine(api_client, callback, timeout=0.01 if failure == "timeout" else 1)
    response = recommend(
        api_client, auth_account, temperature_c=30, required_garment_ids=[closet["outerwear"]]
    )
    assert response.status_code == 201, response.text
    assert response.json()["is_fallback"]
    assert all(o["items"]["outerwear"] == closet["outerwear"] for o in response.json()["outfits"])


@pytest.mark.parametrize("kind", ["foreign", "missing"])
def test_required_id_ownership_is_checked_before_engine(
    api_client, auth_account, account_factory, closet, add_garment, db_session, kind
):
    gid = add_garment(account_factory(), "top") if kind == "foreign" else str(uuid4())
    calls = []
    use_engine(api_client, lambda *_: calls.append(True))
    response = recommend(api_client, auth_account, required_garment_ids=[gid])
    assert response.status_code == 404 and response.json()["code"] == "GARMENT_NOT_FOUND"
    assert not calls
    assert db_session.scalar(select(func.count()).select_from(Recommendation)) == 0


@pytest.mark.parametrize(
    "kind",
    ["laundry", "inactive", "pending", "failed", "shoes", "same-slot", "excluded", "duplicate", "too-many"],
)
def test_invalid_required_selection_is_rejected(
    api_client, auth_account, closet, add_garment, db_session, kind
):
    fields = {}
    if kind in ("laundry", "inactive"):
        ids = [add_garment(auth_account, "top", status=kind)]
    elif kind in ("pending", "failed"):
        ids = [add_garment(auth_account, "top", analysis_status=kind)]
    elif kind == "shoes":
        ids = [closet["shoes"]]
    elif kind == "same-slot":
        ids = [closet["top"], add_garment(auth_account, "top")]
    elif kind == "duplicate":
        ids = [closet["top"]] * 2
    elif kind == "too-many":
        ids = list(closet.values())
    else:
        ids = [closet["top"]]
        fields["excluded_items"] = ids
    calls = []
    use_engine(api_client, lambda *_: calls.append(True))
    response = recommend(api_client, auth_account, required_garment_ids=ids, **fields)
    assert response.status_code == 422, response.text
    assert not calls
    assert db_session.scalar(select(func.count()).select_from(Recommendation)) == 0


@pytest.mark.parametrize("change", ["laundry", "category", "delete"])
def test_required_change_during_inference_returns_conflict_without_save(
    api_client, auth_account, closet, add_garment, db_session, change
):
    add_garment(auth_account, "top")

    def callback(*_):
        garment = db_session.get(Garment, UUID(closet["top"]))
        if change == "delete":
            db_session.delete(garment)
        elif change == "category":
            garment.attributes.category = "outerwear"
        else:
            garment.status = "laundry"
        db_session.commit()
        return engine_result({"top": closet["top"], "bottom": closet["bottom"]})

    use_engine(api_client, callback)
    response = recommend(api_client, auth_account, required_garment_ids=[closet["top"]])
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "REQUIRED_GARMENT_CHANGED"
    assert db_session.scalar(select(func.count()).select_from(Recommendation)) == 0


def test_service_final_validation_also_enforces_required_items(api_client, auth_account, closet, add_garment):
    from backend.app.adapters.base import RecommendOutcome

    other_top = add_garment(auth_account, "top")

    class BypassSafeAdapter:
        def recommend(self, *_):
            return RecommendOutcome(
                result=engine_result({"top": other_top, "bottom": closet["bottom"]}),
                provider="local",
                is_fallback=False,
                latency_ms=0,
                dropped=[],
            )

    api_client.app.dependency_overrides[get_recommender] = BypassSafeAdapter
    response = recommend(api_client, auth_account, required_garment_ids=[closet["top"]])
    assert response.status_code == 201 and response.json()["is_fallback"]
    assert all(o["items"]["top"] == closet["top"] for o in response.json()["outfits"])


def test_legacy_stored_context_without_required_ids_still_reads(api_client, auth_account, closet, db_session):
    created = recommend(api_client, auth_account).json()
    row = db_session.get(Recommendation, UUID(created["recommendation_id"]))
    row.context = {k: v for k, v in row.context.items() if k != "required_garment_ids"}
    db_session.commit()
    response = api_client.get(f"/api/v1/recommendations/{row.id}", headers=auth_account["headers"])
    assert response.status_code == 200
    assert response.json()["context"]["required_garment_ids"] == []


@pytest.mark.parametrize("failure", [False, True])
def test_judgment_adapter_and_fallback_save_only_fixed_owned_candidates(
    api_client, auth_account, closet, db_session, failure
):
    import json

    import httpx

    from backend.app.adapters.openai_recommender import LocalFallback, OpenAIRecommender
    from backend.app.core.config import Settings
    from backend.tests.adapters.test_openai_recommender import completed

    def respond(request):
        data = json.loads(request.content)
        context = json.loads(data["input"])
        assert context["required_slots"] == ["outerwear", "top"]
        assert auth_account["id"] not in data["input"]
        assert all(gid not in data["input"] for gid in closet.values())
        if failure:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json=completed(
                [
                    {"candidate_id": c["candidate_id"], "reason": "고정 후보 평가"}
                    for c in context["candidates"]
                ]
            ),
        )

    adapter = SafeRecommender(
        OpenAIRecommender(
            Settings(_env_file=None, OPENAI_API_KEY="fixture-only-key"),
            transport=httpx.MockTransport(respond),
        ),
        2,
        LocalFallback(),
    )
    api_client.app.dependency_overrides[get_recommender] = lambda: adapter
    required = [closet["top"], closet["outerwear"]]
    response = recommend(api_client, auth_account, temperature_c=30, required_garment_ids=required)
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["is_fallback"] == failure
    assert data["provider"] == ("fallback" if failure else "openai")
    assert all(set(required) <= set(o["items"].values()) for o in data["outfits"])
    row = db_session.get(Recommendation, UUID(data["recommendation_id"]))
    assert row.context["required_garment_ids"] == required
