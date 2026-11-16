from uuid import uuid4

import pytest

from backend.tests.e2e.recommendation_flow import submit_recommendation


@pytest.mark.parametrize("live_api", [{"provider": "local", "starter_wardrobe": True}], indirect=True)
@pytest.mark.parametrize("live_frontend", [{}, {"mentor": True}], indirect=True)
def test_signup_immediate_recommendations_with_examples(live_frontend):
    ui, backend, runtime = live_frontend
    assert "각 3벌의 예시 옷" in ui.get("/signup").text
    signup = ui.post(
        "/signup",
        data={
            "email": f"starter-{uuid4().hex}@example.com",
            "password": "Starter2026!",
            "password_confirm": "Starter2026!",
        },
    )
    assert signup.status_code == 303
    assert "예시 옷장으로 바로 시작하기" in ui.get(signup.headers["location"]).text
    personal = ui.get("/wardrobe?wardrobe=personal")
    assert personal.status_code == 200 and 'data-garment-id=' not in personal.text
    page = ui.get("/wardrobe?wardrobe=example")
    assert page.status_code == 200 and "9벌" in page.text
    assert page.text.count("data-example-badge") == 9 and page.text.count("data-garment-id=") == 9
    h = {"Authorization": "Bearer " + ui.cookies["fitjang_token"]}
    before = backend.get("/api/v1/garments", headers=h).json()
    result = submit_recommendation(ui, {"temp": "10"})
    assert result.status_code == 200 and "코디 1" in result.text
    stored = backend.get("/api/v1/recommendations", headers=h).json()
    assert len(stored) == 1 and stored[0]["outfits"]
    assert stored[0]["context"]["wardrobe"] == "example"
    assert backend.get("/api/v1/garments", headers=h).json() == before
    assert all(backend.get(g["image_url"], headers=h).status_code == 200 for g in before)
