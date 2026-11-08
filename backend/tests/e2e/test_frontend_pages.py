"""Real rendered pages -> D connectors -> backend JWT -> actual PostgreSQL."""

import re
from io import BytesIO
from uuid import uuid4

import httpx
import pytest
from PIL import Image
from sqlalchemy import create_engine, text

from scripts.demo_assets import CATALOG, garment_images


@pytest.mark.parametrize(
    "live_api",
    [
        {"provider": "local", "invite_code": "ui-invite", "weather_stub": True},
        {"provider": "local", "invite_code": "ui-invite", "weather_stub": False},
    ],
    indirect=True,
)
def test_integrated_frontend_forms_weather_wardrobe_recommendation_feedback(live_frontend, request):
    ui, backend, runtime = live_frontend
    automatic = request.node.callspec.params["live_api"]["weather_stub"]
    assert ui.get("/wardrobe").status_code == 303
    email, password = f"pages-{uuid4().hex}@example.com", "FrontendPages123!"
    signup = ui.post("/signup", data={"email": email, "password": password, "password_confirm": password})
    assert signup.status_code == 403
    signup = ui.post(
        "/signup",
        data={"email": email, "password": password, "password_confirm": password, "invite_code": "ui-invite"},
    )
    assert signup.status_code == 303 and "fitjang_token" in ui.cookies
    profile = ui.get(signup.headers["location"])
    assert profile.status_code == 200 and 'name="consent_body_info"' in profile.text
    assert 'href="/profile/body-photo' not in profile.text
    values = {"height_cm": "170.5", "weight_kg": "60.5", "age": "24", "welcome": "1"}
    denied = ui.post("/profile", data=values)
    assert denied.status_code == 422 and "수집 동의" in denied.text
    saved = ui.post("/profile", data={**values, "consent_body_info": "true"})
    assert saved.status_code == 303 and saved.headers["location"].startswith("/wardrobe")
    headers = {"Authorization": "Bearer " + ui.cookies["fitjang_token"]}
    assert backend.get("/api/v1/profile", headers=headers).json()["height_cm"] == 170.5
    home = ui.get("/")
    assert home.status_code == 200
    weather = ui.get("/weather?lat=37.5665&lon=126.978")
    if automatic:
        assert weather.status_code == 200 and weather.json()["temperature_c"] == 6.5
        assert "6.5°C" in home.text and "Open-Meteo.com" in home.text
    else:
        assert weather.status_code == 503 and weather.json()["code"] == "WEATHER_UNAVAILABLE"
        assert "기온을 불러오지 못했어요" in home.text
    ids = []
    for category in ("top", "bottom", "outerwear"):
        item = next(g for g in CATALOG if g.category == category)
        image = Image.open(BytesIO(garment_images(item)[0])).resize((1080, 1080))
        output = BytesIO()
        image.save(output, format="JPEG")
        uploaded = ui.post("/garments/new", files={"file": ("garment.jpg", output.getvalue(), "image/jpeg")})
        assert uploaded.status_code == 303, uploaded.text
        confirm_path = uploaded.headers["location"]
        garment_id = confirm_path.split("/")[2]
        ids.append(garment_id)
        page = ui.get(confirm_path)
        assert page.status_code == 200 and f"/garment-images/{garment_id}" in page.text
        confirmed = ui.post(
            confirm_path,
            data={
                "category": category,
                "color": "navy",
                "pattern": "solid",
                "name": category + " test",
                "location": "dorm",
            },
        )
        assert confirmed.status_code == 303, confirmed.text
        assert ui.get(f"/garment-images/{garment_id}").headers["content-type"].startswith("image/")
    assert ui.get("/wardrobe?q=navy").status_code == 200
    form = ui.get("/recommend")
    assert form.status_code == 200 and 'id="weather-status"' in form.text
    temp = 6.5 if automatic else 0
    result = ui.get("/recommend/result", params={"temp": temp, "style_text": "꾸안꾸"})
    assert result.status_code == 200 and "코디 1" in result.text, result.text
    rid = re.search(r'name="recommendation_id" value="([^"]+)"', result.text).group(1)
    oid = re.search(r'name="outfit_id" value="([^"]+)"', result.text).group(1)
    for accepted in ("1", "0"):
        feedback = ui.post(
            "/recommend/feedback",
            data={"recommendation_id": rid, "outfit_id": oid, "accepted": accepted},
            headers={"X-Requested-With": "fetch"},
        )
        assert feedback.status_code == 200 and feedback.json()["ok"]
    record = backend.get(f"/api/v1/recommendations/{rid}", headers=headers).json()
    assert record["context"]["temperature_c"] == temp
    assert record["outfits"][0]["items"]["shoes"] is None
    assert len(backend.get(f"/api/v1/recommendations/{rid}/feedback", headers=headers).json()) == 2
    engine = create_engine(runtime["database_url"])
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM users")) == 1
        assert conn.scalar(text("SELECT count(*) FROM garments")) == 3
        assert conn.scalar(text("SELECT count(*) FROM feedback_events")) == 2
    engine.dispose()
    with httpx.Client(base_url=ui.base_url, trust_env=False) as stranger:
        assert stranger.get(f"/garment-images/{ids[0]}").status_code in (303, 401)
        assert stranger.get("/weather?lat=37&lon=127").status_code == 401
    assert ui.post("/logout").status_code == 303
    assert ui.get("/wardrobe").status_code == 303
    login = ui.post("/login", data={"email": email, "password": password})
    assert login.status_code == 303
    assert "top test" in ui.get("/wardrobe").text
