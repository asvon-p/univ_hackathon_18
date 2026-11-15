import json
from uuid import UUID

import httpx
import pytest

from backend.app.adapters.openai_recommender import LocalFallback, OpenAIRecommender
from backend.app.adapters.recommender import SafeRecommender
from backend.app.core.config import Settings
from backend.app.db.models import Recommendation
from backend.app.deps import get_recommender
from backend.tests.adapters.test_openai_recommender import completed
from backend.tests.api.test_day_plans import create_plan
from backend.tests.api.test_recommendations import recommend
from backend.tests.api.test_style_references import confirm_reference, upload_reference


@pytest.mark.parametrize("failure", [None, "401", "429", "503", "timeout", "duplicate", "blank-reason"])
def test_gpt_ranking_or_local_fallback_persists_all_constraints(
    api_client, auth_account, account_factory, closet, add_garment, db_session, failure, caplog
):
    add_garment(auth_account, "top", styles=["casual"])
    add_garment(auth_account, "bottom", styles=["casual"])
    plan = create_plan(api_client, auth_account)
    reference = confirm_reference(api_client, auth_account, upload_reference(api_client, auth_account))
    captured, original = [], []

    class Candidates(LocalFallback):
        def recommend(self, context, garments):
            value = super().recommend(context, garments)
            original[:] = value.outfits
            return value

    def respond(request):
        body = json.loads(request.content)
        data = json.loads(body["input"])
        captured.append(data)
        assert str(request.url) == "https://api.openai.com/v1/responses"
        assert body["model"] == "gpt-6-luna" and body["store"] is False
        for private in [
            auth_account["id"],
            auth_account["email"],
            plan["id"],
            reference["id"],
            *closet.values(),
        ]:
            assert private not in body["input"]
        assert "2026-11-12" not in body["input"] and "image_url" not in body["input"]
        assert data["required_slots"] == ["outerwear"] and len(data["schedule"]) == 2
        assert data["reference_features"] and len(data["candidates"]) == 3
        if failure in {"401", "429", "503"}:
            return httpx.Response(int(failure), json={"error": "secret-upstream-body"})
        if failure == "timeout":
            raise httpx.ReadTimeout("secret-upstream-body", request=request)
        rows = [{"candidate_id": i, "reason": f"판단 이유 {i}"} for i in (2, 1, 0)]
        if failure == "duplicate":
            rows[-1]["candidate_id"] = 1
        if failure == "blank-reason":
            rows[0]["reason"] = "   "
        return httpx.Response(200, json=completed(rows))

    engine = OpenAIRecommender(
        Settings(_env_file=None, OPENAI_API_KEY="test-only-key"), transport=httpx.MockTransport(respond)
    )
    engine.candidates = Candidates()
    api_client.app.dependency_overrides[get_recommender] = lambda: SafeRecommender(engine, 3, LocalFallback())
    response = recommend(
        api_client,
        auth_account,
        temperature_c=7.5,
        preferred_styles=["casual", "minimal"],
        required_garment_ids=[closet["outerwear"]],
        schedule_id=plan["id"],
        schedule_version=1,
        reference_id=reference["id"],
        reference_version=reference["version"],
        outerwear_policy="removable",
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert captured and data["provider"] == ("fallback" if failure else "openai")
    expected = original if failure else list(reversed(original))
    assert [o["items"] for o in data["outfits"]] == [o.items.model_dump() for o in expected]
    assert [o["reason"] for o in data["outfits"]] == (
        [o.reason for o in expected] if failure else ["판단 이유 2", "판단 이유 1", "판단 이유 0"]
    )
    for outfit in data["outfits"]:
        assert outfit["items"]["outerwear"] == closet["outerwear"]
        assert len(outfit["wearing_plan"]) == 2 and outfit["reference_matches"]
    db_session.expire_all()
    row = db_session.get(Recommendation, UUID(data["recommendation_id"]))
    assert row.context["schedule_id"] == plan["id"] and row.context["reference_id"] == reference["id"]
    assert row.context["required_garment_ids"] == [closet["outerwear"]]
    assert [o.reason for o in row.outfits] == [o["reason"] for o in data["outfits"]]
    assert [str(o.top_id) for o in row.outfits] == [o.items.top for o in expected]
    assert api_client.get(f"/api/v1/recommendations/{row.id}", headers=auth_account["headers"]).json() == data
    other = account_factory()
    assert api_client.get(f"/api/v1/recommendations/{row.id}", headers=other["headers"]).status_code == 404
    assert "secret-upstream-body" not in caplog.text and "test-only-key" not in caplog.text


@pytest.mark.parametrize("resource", ["garment", "schedule", "reference"])
def test_foreign_constraints_rejected_before_paid_call(
    api_client, auth_account, account_factory, closet, add_garment, resource
):
    other = account_factory()
    if resource == "garment":
        body = {"required_garment_ids": [add_garment(other, "top")]}
    elif resource == "schedule":
        body = {"schedule_id": create_plan(api_client, other)["id"], "schedule_version": 1}
    else:
        ref = confirm_reference(api_client, other, upload_reference(api_client, other))
        body = {"reference_id": ref["id"], "reference_version": ref["version"]}
    calls = []

    def unexpected(request):
        calls.append(True)
        raise AssertionError("must not call OpenAI")

    engine = OpenAIRecommender(
        Settings(_env_file=None, OPENAI_API_KEY="test-key"), transport=httpx.MockTransport(unexpected)
    )
    api_client.app.dependency_overrides[get_recommender] = lambda: SafeRecommender(engine, 3, LocalFallback())
    assert recommend(api_client, auth_account, **body).status_code == 404
    assert not calls
