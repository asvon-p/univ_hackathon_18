from uuid import UUID

import pytest

from backend.app.db.models import FeedbackEvent, Garment
from backend.tests.api.test_recommendations import recommend


def test_legacy_text_feedback_changes_new_recommendation_without_rewriting_history(
    api_client, auth_account, account_factory, closet, add_garment, db_session
):
    for role, gid in closet.items():
        db_session.get(Garment, UUID(gid)).attributes.thermal_level = 5 if role == "outerwear" else 4
    db_session.commit()
    first = recommend(api_client, auth_account, temperature_c=-3, wardrobe="personal").json()
    other = account_factory()
    path = f"/api/v1/recommendations/{first['recommendation_id']}/feedback"
    opinion = {
        "recommendation_id": first["recommendation_id"],
        "outfit_id": first["outfits"][0]["outfit_id"],
        "accepted": False,
        "reason": "너무 추워요",
    }
    assert api_client.post(path, headers=other["headers"], json=opinion).status_code == 404
    event = api_client.post(path, headers=auth_account["headers"], json=opinion)
    assert event.status_code == 201 and event.json()["reason_codes"] == ["too_cold"]
    row = db_session.get(FeedbackEvent, UUID(event.json()["feedback_id"]))
    row.reason_codes = []  # Previously stored text-only feedback must work without a migration.
    db_session.commit()
    warmer = add_garment(auth_account, "top")
    db_session.get(Garment, UUID(warmer)).attributes.thermal_level = 5
    db_session.commit()
    second = recommend(
        api_client,
        auth_account,
        temperature_c=-3,
        wardrobe="personal",
        required_garment_ids=[closet["bottom"]],
    )
    assert second.status_code == 201, second.text
    assert all(o["items"]["top"] == warmer for o in second.json()["outfits"])
    assert second.json()["context"]["recent_feedback"][0]["reason_codes"] == ["too_cold"]
    assert row.reason_codes == []
    assert (
        api_client.get(
            f"/api/v1/recommendations/{first['recommendation_id']}", headers=auth_account["headers"]
        ).json()
        == first
    )
    # Test wardrobe opinions never enter personal recommendations, and vice versa.
    for role in ("top", "bottom", "outerwear"):
        gid = add_garment(auth_account, role)
        db_session.get(Garment, UUID(gid)).is_example = True
        db_session.get(Garment, UUID(gid)).attributes.thermal_level = 5 if role == "outerwear" else 4
    db_session.commit()
    example = recommend(api_client, auth_account, temperature_c=-3, wardrobe="example")
    assert example.status_code == 201
    assert example.json()["context"]["recent_feedback"] == []
    assert all(g["thermal_level"] == 4 for g in example.json()["garments"].values() if g["category"] == "top")


def test_cold_required_and_unknown_warmth_fail_with_actionable_message(
    api_client, auth_account, closet, db_session
):
    top = db_session.get(Garment, UUID(closet["top"]))
    for level in (1, None):
        top.attributes.thermal_level = level
        db_session.commit()
        response = recommend(api_client, auth_account, temperature_c=-3, required_garment_ids=[closet["top"]])
        assert response.status_code == 422
        assert "상의 두꺼움 이상" in response.text and "아우터 매우 두꺼움 이상" in response.text
        assert "NO_FEASIBLE_OUTFIT" in response.text


@pytest.mark.parametrize(
    "temperature,levels",
    [
        (-0.1, (4, 4, 5)),
        (0, (3, 3, 4)),
        (5, (3, 3, 4)),
        (5.1, (3, 3, 3)),
        (15, (3, 3, 3)),
        (15.1, (2, 2, 2)),
        (22, (2, 2, 2)),
        (22.1, (1, 1, 1)),
    ],
)
def test_temperature_boundary_minima_are_enforced_before_database_save(
    api_client, auth_account, closet, db_session, temperature, levels
):
    for role, level in zip(("top", "bottom", "outerwear"), levels, strict=True):
        db_session.get(Garment, UUID(closet[role])).attributes.thermal_level = level
    db_session.commit()
    result = recommend(api_client, auth_account, temperature_c=temperature)
    assert result.status_code == 201, result.text
    if temperature <= 15:
        assert all(o["items"]["outerwear"] == closet["outerwear"] for o in result.json()["outfits"])
    if levels[0] > 1:
        db_session.get(Garment, UUID(closet["top"])).attributes.thermal_level -= 1
        db_session.commit()
        assert recommend(api_client, auth_account, temperature_c=temperature).status_code == 422
