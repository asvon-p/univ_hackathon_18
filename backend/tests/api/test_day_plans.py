from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from backend.app.adapters.recommender import LocalRecommender, SafeRecommender
from backend.app.db.models import DayPlan, Recommendation
from backend.app.deps import get_recommender
from backend.tests.api.test_recommendations import recommend, use_engine


def plan_body():
    return {
        "date": "2026-11-12",
        "timezone": "Asia/Seoul",
        "events": [
            {
                "start_at": "2026-11-12T09:00:00+09:00",
                "end_at": "2026-11-12T10:00:00+09:00",
                "purpose": "class",
                "environment": "indoor",
            },
            {
                "start_at": "2026-11-12T18:00:00+09:00",
                "end_at": "2026-11-12T19:00:00+09:00",
                "purpose": "travel",
                "environment": "outdoor",
                "temperature_min_c": 0,
                "temperature_max_c": 3,
                "priority": "high",
            },
        ],
    }


def create_plan(client, account, body=None):
    response = client.post("/api/v1/day-plans", headers=account["headers"], json=body or plan_body())
    assert response.status_code == 201, response.text
    return response.json()


def test_owned_crud_versions_and_deleted_snapshot(
    api_client, auth_account, account_factory, closet, db_session
):
    plan = create_plan(api_client, auth_account)
    other = account_factory()
    path = f"/api/v1/day-plans/{plan['id']}"
    assert api_client.get("/api/v1/day-plans", headers=other["headers"]).json() == []
    for method, params in [
        ("get", {}),
        ("patch", {"json": {**plan_body(), "expected_version": 1}}),
        ("delete", {"params": {"expected_version": 1}}),
    ]:
        assert getattr(api_client, method)(path, headers=other["headers"], **params).status_code == 404
    result = recommend(
        api_client,
        auth_account,
        schedule_id=plan["id"],
        schedule_version=1,
        required_garment_ids=[closet["outerwear"]],
        outerwear_policy="removable",
    )
    assert result.status_code == 201, result.text
    assert result.json()["outfits"][0]["wearing_plan"][0]["outerwear_action"] == "wear"
    new = api_client.patch(path, headers=auth_account["headers"], json={**plan_body(), "expected_version": 1})
    assert new.status_code == 200 and new.json()["version"] == 2
    assert (
        api_client.patch(
            path, headers=auth_account["headers"], json={**plan_body(), "expected_version": 1}
        ).status_code
        == 409
    )
    assert recommend(api_client, auth_account, schedule_id=plan["id"], schedule_version=1).status_code == 409
    assert (
        api_client.delete(path, headers=auth_account["headers"], params={"expected_version": 1}).status_code
        == 409
    )
    assert (
        api_client.delete(path, headers=auth_account["headers"], params={"expected_version": 2}).status_code
        == 204
    )
    saved = api_client.get(
        "/api/v1/recommendations/" + result.json()["recommendation_id"], headers=auth_account["headers"]
    ).json()
    assert saved["context"]["day_plan"] is None and saved["context"]["schedule_id"] is None
    assert all(
        not o["wearing_plan"] and not o["limitations"] and o["score"] is None for o in saved["outfits"]
    )
    assert all(o["items"]["outerwear"] == closet["outerwear"] for o in saved["outfits"])
    assert db_session.scalar(select(func.count()).select_from(DayPlan)) == 0


@pytest.mark.parametrize(
    "kind",
    [
        "overlap",
        "backwards",
        "naive",
        "offset",
        "next-day",
        "empty",
        "too-many",
        "temp-pair",
        "temp-order",
        "nan",
        "outside-time",
        "unknown-zone",
        "extra",
    ],
)
def test_schedule_validation(api_client, auth_account, kind):
    body = plan_body()
    event = body["events"][0]
    if kind == "overlap":
        body["events"].append(deepcopy(event))
    elif kind == "backwards":
        event["end_at"] = event["start_at"]
    elif kind == "naive":
        event["start_at"] = "2026-11-12T09:00:00"
    elif kind == "offset":
        event["start_at"] = "2026-11-12T00:00:00Z"
    elif kind == "next-day":
        event["end_at"] = "2026-11-13T01:00:00+09:00"
    elif kind == "empty":
        body["events"] = []
    elif kind == "too-many":
        body["events"] *= 6
    elif kind == "temp-pair":
        event["temperature_min_c"] = 10
    elif kind == "temp-order":
        event.update(temperature_min_c=30, temperature_max_c=10)
    elif kind == "nan":
        event.update(temperature_min_c="NaN", temperature_max_c=10)
    elif kind == "outside-time":
        event.update(environment="mixed", outdoor_minutes=100)
    elif kind == "unknown-zone":
        body["timezone"] = "../invalid"
    elif kind == "extra":
        event["user_id"] = str(uuid4())
    response = api_client.post("/api/v1/day-plans", headers=auth_account["headers"], json=body)
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("provider", ["local", "exception", "invalid", "timeout"])
def test_day_recommendation_persists_and_fallback_holds_required(api_client, auth_account, closet, provider):
    plan = create_plan(api_client, auth_account)
    if provider == "local":
        api_client.app.dependency_overrides[get_recommender] = lambda: SafeRecommender(LocalRecommender(), 5)
    else:

        def fail(context, garments):
            if provider == "timeout":
                import time

                time.sleep(0.08)
            if provider == "exception":
                raise RuntimeError("test unavailable")
            context.required_garment_ids.clear()
            context.day_plan.events.clear()
            return {"recommendation_id": "invalid", "outfits": []}

        use_engine(api_client, fail, timeout=0.01 if provider == "timeout" else 1)
    response = recommend(
        api_client,
        auth_account,
        temperature_c=35,
        required_garment_ids=[closet["top"], closet["outerwear"]],
        schedule_id=plan["id"],
        schedule_version=1,
        outerwear_policy="removable",
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["is_fallback"] == (provider != "local")
    for outfit in data["outfits"]:
        assert outfit["items"]["top"] == closet["top"] and outfit["items"]["outerwear"] == closet["outerwear"]
        assert len(outfit["wearing_plan"]) == 2
        assert outfit["wearing_plan"][0]["outerwear_action"] == "carry"
        assert outfit["wearing_plan"][1]["outerwear_action"] == "adjust"
        assert outfit["policy_version"] == "day-plan-v4-season-forecast"
    assert (
        api_client.get(
            "/api/v1/recommendations/" + data["recommendation_id"], headers=auth_account["headers"]
        ).json()
        == data
    )


@pytest.mark.parametrize("change", ["update", "delete"])
def test_schedule_change_during_recommendation_rejected(api_client, auth_account, closet, db_session, change):
    plan = create_plan(api_client, auth_account)

    def mutate(context, garments):
        row = db_session.get(DayPlan, UUID(plan["id"]))
        if change == "delete":
            db_session.delete(row)
        else:
            row.version += 1
        db_session.commit()
        return LocalRecommender().recommend(context, garments)

    use_engine(api_client, mutate)
    response = recommend(api_client, auth_account, schedule_id=plan["id"], schedule_version=1)
    assert response.status_code == 409 and response.json()["code"] == "DAY_PLAN_CHANGED"
    assert db_session.scalar(select(func.count()).select_from(Recommendation)) == 0


def test_foreign_plan_and_unconfirmed_payload_rejected(api_client, auth_account, account_factory, closet):
    other = account_factory()
    plan = create_plan(api_client, other)
    calls = []
    use_engine(api_client, lambda *_: calls.append(True))
    for gid in (plan["id"], str(uuid4())):
        assert recommend(api_client, auth_account, schedule_id=gid, schedule_version=1).status_code == 404
    assert not calls
    for fields in ({"schedule_id": plan["id"]}, {"schedule_version": 1}, {"day_plan": plan}):
        assert recommend(api_client, auth_account, **fields).status_code == 422


def test_shared_context_serialization_remains_compatible(api_client, auth_account, closet):
    from shared.contracts import UserContext

    body = UserContext(user_id="me", temperature_c=18, required_garment_ids=[closet["top"]]).model_dump(
        mode="json"
    )
    response = api_client.post("/api/v1/recommendations", headers=auth_account["headers"], json=body)
    assert response.status_code == 201, response.text
    assert response.json()["context"]["day_plan"] is None


def test_hard_style_is_not_relaxed_by_fallback(api_client, auth_account, closet):
    body = plan_body()
    body["events"][0]["required_style"] = "formal"
    plan = create_plan(api_client, auth_account, body)
    response = recommend(
        api_client,
        auth_account,
        required_garment_ids=[closet["top"]],
        schedule_id=plan["id"],
        schedule_version=1,
    )
    assert response.status_code == 422 and response.json()["code"] == "NO_FEASIBLE_OUTFIT"


def test_account_deletion_cascades_plans(api_client, auth_account, db_session):
    create_plan(api_client, auth_account)
    response = api_client.delete("/api/v1/auth/me", headers=auth_account["headers"])
    assert response.status_code == 204, response.text
    assert db_session.scalar(select(func.count()).select_from(DayPlan)) == 0


@pytest.mark.parametrize("failure", [False, True])
def test_gpt_judges_candidates_without_editing_schedule(api_client, auth_account, closet, failure):
    import json

    import httpx

    from backend.app.adapters.openai_recommender import LocalFallback, OpenAIRecommender
    from backend.app.core.config import Settings
    from backend.tests.adapters.test_openai_recommender import completed

    plan = create_plan(api_client, auth_account)

    def respond(request):
        payload = json.loads(request.content)
        context = json.loads(payload["input"])
        assert len(context["schedule"]) == 2
        assert context["schedule"][0]["environment"] == "indoor"
        assert context["outerwear_policy"] == "removable"
        assert plan["id"] not in payload["input"] and "2026-11-12" not in payload["input"]
        assert all(gid not in payload["input"] for gid in closet.values())
        assert "start_at" not in payload["input"] and "user_id" not in payload["input"]
        assert payload["store"] is False and "tools" not in payload
        if failure:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json=completed(
                [
                    {"candidate_id": c["candidate_id"], "reason": "후보의 하루 적합성 판단"}
                    for c in reversed(context["candidates"])
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
    response = recommend(
        api_client,
        auth_account,
        schedule_id=plan["id"],
        schedule_version=1,
        required_garment_ids=[closet["outerwear"]],
        outerwear_policy="removable",
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["is_fallback"] == failure and data["provider"] == ("fallback" if failure else "openai")
    assert all(
        o["items"]["outerwear"] == closet["outerwear"] and o["wearing_plan"][0]["outerwear_action"] == "wear"
        for o in data["outfits"]
    )
