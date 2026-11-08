from datetime import UTC, datetime
from uuid import UUID

import pytest

from backend.app.db.models import UserProfile


def test_profile_patch_persists_and_is_scoped_to_token(api_client, account_factory, db_session):
    owner, other = account_factory(), account_factory()
    values = {
        "nickname": " 캠퍼스 ",
        "preferred_styles": ["minimal", "casual", "minimal"],
        "disliked_colors": ["red"],
        "height_cm": 175,
        "weight_kg": 65,
        "birth_year": 2003,
        "gender": "other",
        "consent_body_info": True,
        "avatar_config": {"hair": "short", "scale": 1.0},
    }
    response = api_client.patch("/api/v1/profile", json=values, headers=owner["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["nickname"] == "캠퍼스" and body["preferred_styles"] == ["minimal", "casual"]
    assert body["consent_body_info_at"].endswith("Z")
    db_session.expunge_all()
    assert api_client.get("/api/v1/profile", headers=owner["headers"]).json() == body
    assert api_client.get("/api/v1/auth/me", headers=owner["headers"]).json()["nickname"] == "캠퍼스"
    other_profile = api_client.get(f"/api/v1/profile?user_id={owner['id']}", headers=other["headers"]).json()
    assert other_profile["user_id"] == other["id"] and other_profile["height_cm"] is None
    assert other_profile["preferred_styles"] == []
    rejected = api_client.patch(
        "/api/v1/profile", json={"user_id": owner["id"], "nickname": "hacked"}, headers=other["headers"]
    )
    assert rejected.status_code == 422
    assert api_client.get(f"/api/v1/profile/{owner['id']}", headers=other["headers"]).status_code == 404
    assert db_session.get(UserProfile, UUID(owner["id"])).height_cm == 175


def test_body_consent_required_and_revocation_clears_data(garment_client):
    for values in ({"height_cm": 180}, {"gender": "male"}, {"avatar_config": {"height": 180}}):
        response = garment_client.patch("/api/v1/profile", json=values)
        assert response.status_code == 422 and response.json()["code"] == "BODY_INFO_CONSENT_REQUIRED"
    first = garment_client.patch(
        "/api/v1/profile",
        json={
            "consent_body_info": True,
            "height_cm": 180,
            "weight_kg": 70,
            "avatar_config": {"hair": "short"},
        },
    )
    assert first.status_code == 200
    updated = garment_client.patch("/api/v1/profile", json={"weight_kg": 71, "preferred_styles": ["minimal"]})
    assert updated.status_code == 200 and updated.json()["height_cm"] == 180
    repeated = garment_client.patch("/api/v1/profile", json={"consent_body_info": True})
    assert repeated.json()["consent_body_info_at"] == first.json()["consent_body_info_at"]
    conflicting = garment_client.patch("/api/v1/profile", json={"consent_body_info": False, "height_cm": 170})
    assert conflicting.status_code == 422
    revoked = garment_client.patch("/api/v1/profile", json={"consent_body_info": False})
    assert revoked.status_code == 200
    for field in ("height_cm", "weight_kg", "birth_year", "gender", "avatar_config", "consent_body_info_at"):
        assert revoked.json()[field] is None
    assert revoked.json()["preferred_styles"] == ["minimal"]
    assert garment_client.patch("/api/v1/profile", json={"weight_kg": 71}).status_code == 422


@pytest.mark.parametrize(
    "values",
    [
        {"height_cm": 0},
        {"height_cm": 300},
        {"height_cm": True},
        {"weight_kg": 2},
        {"birth_year": datetime.now(UTC).year + 1},
        {"gender": "invalid"},
        {"preferred_styles": ["unknown"]},
        {"disliked_colors": ["unknown-color"]},
        {"preferred_styles": None},
        {"consent_body_info": None},
        {"consent_body_info": "yes"},
        {"consent_body_info_at": "2026-01-01T00:00:00Z"},
        {"nickname": "  "},
        {"avatar_config": {"oversize": "a" * 17000}},
        {"avatar_config": [1, 2, 3]},
    ],
)
def test_profile_rejects_invalid_values(garment_client, values):
    response = garment_client.patch("/api/v1/profile", json=values)
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"


def test_profile_nullable_fields_and_empty_preferences(garment_client):
    assert (
        garment_client.patch(
            "/api/v1/profile", json={"consent_body_info": True, "height_cm": 170}
        ).status_code
        == 200
    )
    response = garment_client.patch(
        "/api/v1/profile",
        json={"height_cm": None, "preferred_styles": [], "disliked_colors": [], "avatar_config": None},
    )
    assert response.status_code == 200 and response.json()["height_cm"] is None
    assert response.json()["consent_body_info_at"] is not None
