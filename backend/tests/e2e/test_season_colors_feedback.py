from uuid import uuid4

import pytest

from backend.tests.e2e.recommendation_flow import submit_recommendation


@pytest.mark.parametrize(
    "live_api", [{"provider": "local", "weather_stub": False, "starter_wardrobe": True}], indirect=True
)
def test_offline_weather_palette_and_feedback_forms(live_frontend):
    ui, backend, runtime = live_frontend
    password = "SeasonPalette123!"
    signup = ui.post(
        "/signup",
        data={
            "email": f"season-{uuid4().hex}@example.com",
            "password": password,
            "password_confirm": password,
        },
    )
    assert signup.status_code == 303
    headers = {"Authorization": "Bearer " + ui.cookies["fitjang_token"]}
    assert ui.get("/weather?lat=37&lon=127").status_code == 503
    page = ui.get("/recommend")
    assert page.status_code == 200 and "계절·월 평균으로 추천받기" in page.text
    result = submit_recommendation(ui, {"weather_mode": "seasonal", "season": "autumn", "month": "11"})
    assert result.status_code == 200, result.text[:500]
    assert "11월 전국 평균 대체값" in result.text
    recent = backend.get("/api/v1/recommendations", headers=headers).json()[0]
    gid = recent["outfits"][0]["items"]["top"]
    garment = backend.get(f"/api/v1/garments/{gid}", headers=headers).json()
    edit = ui.get(f"/garments/{gid}/edit")
    assert "서브 색상" in edit.text
    saved = ui.post(
        f"/garments/{gid}/edit",
        data={
            "category": garment["category"],
            "color": "navy",
            "secondary_colors": ["white", "red", "gray"],
            "pattern": "check",
        },
    )
    assert saved.status_code == 303, saved.text[:500]
    assert "서브:" in ui.get(saved.headers["location"]).text
    feedback = {
        "recommendation_id": recent["recommendation_id"],
        "outfit_id": recent["outfits"][0]["outfit_id"],
        "accepted": "0",
        "reason_codes": ["colors"],
        "reason": "차분한 색이 좋아요",
    }
    response = ui.post("/recommend/feedback", data=feedback, headers={"X-Requested-With": "fetch"})
    assert response.status_code == 200 and response.json()["ok"]
    refreshed = ui.get(f"/recommend/result/{recent['recommendation_id']}")
    assert 'value="0" aria-pressed="true"' in refreshed.text
    assert 'id="feedback-dialog"' in refreshed.text and "차분한 색이 좋아요" in refreshed.text
    events = backend.get(
        f"/api/v1/recommendations/{recent['recommendation_id']}/feedback", headers=headers
    ).json()
    assert events[0]["reason_codes"] == ["colors"]
