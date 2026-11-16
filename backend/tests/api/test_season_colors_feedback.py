import json
from uuid import UUID, uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from backend.app.adapters.openai_recommender import LocalFallback, OpenAIRecommender
from backend.app.adapters.recommender import SafeRecommender
from backend.app.core.config import Settings
from backend.app.db.models import GarmentAttribute, Recommendation
from backend.app.deps import get_recommender, get_weather
from backend.tests.adapters.test_openai_recommender import completed
from backend.tests.api.test_day_plans import create_plan
from backend.tests.api.test_style_references import confirm_reference, upload_reference
from shared.seasonal_weather import MONTHLY_TEMPERATURES, season_for_month


@pytest.mark.parametrize("month", range(1, 13))
def test_monthly_weather_needs_no_network_and_is_persisted(
    api_client, auth_account, closet, db_session, month
):
    class Offline:
        def current(self, *args):
            raise AssertionError("Explicit season mode must not call the weather network")

    api_client.app.dependency_overrides[get_weather] = lambda: Offline()
    response = api_client.post(
        "/api/v1/recommendations",
        headers=auth_account["headers"],
        json={
            "season": season_for_month(month),
            "month": month,
            "temperature_c": 40,
            "lat": 37,
            "lon": 127,
            "required_garment_ids": [closet["top"]],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["context"]["temperature_c"] == MONTHLY_TEMPERATURES[month]
    assert body["weather"]["source"] == "seasonal" and body["weather"]["is_fallback"]
    assert all(o["items"]["top"] == closet["top"] for o in body["outfits"])
    row = db_session.get(Recommendation, UUID(body["recommendation_id"]))
    assert row.context["month"] == month


@pytest.mark.parametrize(
    "body",
    [
        {"season": "summer", "month": 1},
        {"month": 4},
        {"season": "invalid"},
        {"season": "spring", "month": True},
    ],
)
def test_invalid_season_input(api_client, auth_account, body):
    assert (
        api_client.post("/api/v1/recommendations", headers=auth_account["headers"], json=body).status_code
        == 422
    )


def test_secondary_colors_validate_preserve_and_isolate(
    api_client, auth_account, account_factory, closet, db_session
):
    path = f"/api/v1/garments/{closet['top']}"
    palette = {"color": "navy", "secondary_colors": ["white", "red", "gray"]}
    saved = api_client.patch(path, headers=auth_account["headers"], json=palette)
    assert saved.status_code == 200, saved.text
    assert saved.json()["secondary_colors"] == palette["secondary_colors"]
    for invalid in (["red"] * 2, ["multi"], ["unknown"], ["navy"], ["red", "white", "gray", "green"], None):
        assert (
            api_client.patch(
                path, headers=auth_account["headers"], json={"secondary_colors": invalid}
            ).status_code
            == 422
        )
    other = account_factory()
    assert api_client.patch(path, headers=other["headers"], json=palette).status_code == 404
    assert api_client.get(path, headers=other["headers"]).status_code == 404
    assert api_client.post(path + "/reanalyze", headers=auth_account["headers"]).status_code == 200
    db_session.expire_all()
    row = db_session.get(GarmentAttribute, UUID(closet["top"]))
    assert row.color == "navy" and row.secondary_colors == palette["secondary_colors"]


@pytest.mark.parametrize("mode", ["local", "schedule_reference", "openai", "fallback"])
def test_feedback_changes_next_ranking_and_preserves_constraints(
    api_client, auth_account, account_factory, closet, add_garment, db_session, mode
):
    add_garment(auth_account, "top")
    add_garment(auth_account, "bottom")
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
                    {"candidate_id": c["candidate_id"], "reason": "색상과 의견을 반영한 조합"}
                    for c in payload["candidates"]
                ]
            ),
        )

    engine = LocalFallback()
    if mode in ("openai", "fallback"):
        engine = OpenAIRecommender(
            Settings(_env_file=None, OPENAI_API_KEY="test-only"), transport=httpx.MockTransport(respond)
        )
    api_client.app.dependency_overrides[get_recommender] = lambda: SafeRecommender(engine, 3, LocalFallback())
    body = {"season": "autumn", "month": 11, "required_garment_ids": [closet["outerwear"]]}
    if mode == "schedule_reference":
        plan = create_plan(api_client, auth_account)
        ref = confirm_reference(api_client, auth_account, upload_reference(api_client, auth_account))
        body.update(
            schedule_id=plan["id"],
            schedule_version=plan["version"],
            reference_id=ref["id"],
            reference_version=ref["version"],
        )

    def generate():
        response = api_client.post(
            "/api/v1/recommendations",
            headers={**auth_account["headers"], "Idempotency-Key": str(uuid4())},
            json=body,
        )
        assert response.status_code == 201, response.text
        return response.json()

    first = generate()
    disliked = first["outfits"][0]
    path = f"/api/v1/recommendations/{first['recommendation_id']}/feedback"
    feedback = {
        "recommendation_id": first["recommendation_id"],
        "outfit_id": disliked["outfit_id"],
        "accepted": False,
        "reason": "다른 조합을 보여주세요",
        "reason_codes": ["combination"],
    }
    saved = api_client.post(path, headers=auth_account["headers"], json=feedback)
    assert saved.status_code == 201, saved.text
    other = account_factory()
    assert api_client.post(path, headers=other["headers"], json=feedback).status_code == 404
    assert api_client.get(path, headers=other["headers"]).status_code == 404
    second = generate()
    assert second["outfits"][0]["items"] != disliked["items"]
    assert all(o["items"]["outerwear"] == closet["outerwear"] for o in second["outfits"])
    assert second["context"]["recent_feedback"][0]["reason"] == feedback["reason"]
    if captured:
        assert captured[-1]["recent_feedback"][0]["reason_codes"] == ["combination"]
        assert auth_account["id"] not in json.dumps(captured[-1])
        assert closet["top"] not in json.dumps(captured[-1])
    # A later like supersedes the earlier dislike without deleting history.
    api_client.post(
        path,
        headers=auth_account["headers"],
        json={**feedback, "accepted": True, "reason": None, "reason_codes": []},
    )
    third = generate()
    assert len(third["context"]["recent_feedback"]) == 1
    assert third["context"]["recent_feedback"][0]["accepted"] is True
    db_session.expire_all()
    assert db_session.get(Recommendation, UUID(second["recommendation_id"])).context["recent_feedback"][0][
        "reason_codes"
    ] == ["combination"]


@pytest.mark.skipif(__import__("os").environ.get("TEST_POSTGRES") != "1", reason="PostgreSQL migration")
def test_new_columns_preserve_legacy_rows(db_session, auth_account, closet):
    connection = db_session.connection()
    config = Config("backend/alembic.ini")
    config.attributes["connection"] = connection
    command.downgrade(config, "0024")
    before = (
        connection.execute(text("SELECT to_jsonb(t) FROM garment_attributes t ORDER BY garment_id"))
        .scalars()
        .all()
    )
    command.upgrade(config, "head")
    after = (
        connection.execute(text("SELECT to_jsonb(t) FROM garment_attributes t ORDER BY garment_id"))
        .scalars()
        .all()
    )
    assert all(row.pop("secondary_colors") == [] for row in after)
    assert after == before
