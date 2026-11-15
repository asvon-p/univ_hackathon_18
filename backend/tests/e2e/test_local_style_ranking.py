"""실제 UI HTTP → JWT/API → local 엔진 → PostgreSQL 저장·조회, 전용 임시 스키마."""

import re
from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import create_engine, text

from backend.tests.e2e.recommendation_flow import submit_recommendation


@pytest.mark.parametrize("live_api", [{"provider": "local", "weather_stub": True}], indirect=True)
def test_style_ranking_profile_override_ui_cache_persistence_and_isolation(live_frontend):
    ui, backend, runtime = live_frontend
    password = "StyleRankingTest123!"
    email = f"style-{uuid4().hex}@example.com"

    def signup(address):
        response = backend.post(
            "/api/v1/auth/signup",
            json={"email": address, "password": password},
        )
        assert response.status_code == 201, response.text
        return response.json()

    owner, other = signup(email), signup(f"other-{uuid4().hex}@example.com")
    headers = {"Authorization": "Bearer " + owner["access_token"]}
    foreign = {"Authorization": "Bearer " + other["access_token"]}
    photo = BytesIO()
    Image.new("RGB", (64, 64), "white").save(photo, format="JPEG")

    def upload(category, style, auth):
        response = backend.post(
            "/api/v1/garments", headers=auth, files={"file": ("test.jpg", photo.getvalue(), "image/jpeg")}
        )
        assert response.status_code == 201 and response.json()["analysis_status"] == "done"
        gid = response.json()["garment_id"]
        response = backend.patch(
            f"/api/v1/garments/{gid}",
            headers=auth,
            json={
                "category": category,
                "style_candidates": [style],
                "color": "white" if category == "top" else "black",
                "thermal_level": None,
            },
        )
        assert response.status_code == 200, response.text
        return gid

    clothes = {
        (style, slot): upload(slot, style, headers)
        for style in ("street", "minimal")
        for slot in ("top", "bottom")
    }
    foreign_id = upload("top", "minimal", foreign)
    results = []

    def check_result(result, style, score=100):
        assert result["provider"] == "local" and result["is_fallback"] is False
        assert result["context"]["preferred_styles"] == [style]
        first = result["outfits"][0]
        assert first["items"]["top"] == clothes[style, "top"]
        assert first["items"]["bottom"] == clothes[style, "bottom"]
        assert first["score"] == score
        assert all("선호 스타일" in outfit["reason"] for outfit in result["outfits"])
        assert foreign_id not in result["garments"]
        assert [o["score"] for o in result["outfits"]] == sorted(
            [o["score"] for o in result["outfits"]], reverse=True
        )
        results.append(result)

    def recommend(**values):
        response = backend.post(
            "/api/v1/recommendations",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={"temperature_c": 15, "occasion": "daily", **values},
        )
        assert response.status_code == 201, response.text
        return response.json()

    def profile(style):
        response = backend.patch("/api/v1/profile", headers=headers, json={"preferred_styles": [style]})
        assert response.status_code == 200

    # 요청 선호, 프로필 기본 선호, 요청 선호로 덮어쓰는 모든 경로를 확인합니다.
    for style in ("minimal", "street"):
        check_result(recommend(preferred_styles=[style]), style)
    for style in ("minimal", "street"):
        profile(style)
        check_result(recommend(), style)
    check_result(recommend(preferred_styles=["minimal"]), "minimal")
    assert backend.get("/api/v1/profile", headers=headers).json()["preferred_styles"] == ["street"]

    # 화면의 선택 값이 실제 DB의 1위 조합까지 바꾸는지 확인합니다.
    assert ui.post("/login", data={"email": email, "password": password}).status_code == 303
    for style in ("minimal", "street"):
        page = submit_recommendation(ui, {"temp": 15, "occasion": "daily", "styles": style})
        assert page.status_code == 200 and "선호 스타일" in page.text
        rid = re.search(r'name="recommendation_id" value="([^"]+)"', page.text).group(1)
        check_result(backend.get(f"/api/v1/recommendations/{rid}", headers=headers).json(), style)

    # 프로필 변경 이후에도 과거 추천의 조건·순위·점수는 변하지 않습니다.
    relogin = backend.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert relogin.status_code == 200
    headers = {"Authorization": "Bearer " + relogin.json()["access_token"]}
    for result in results:
        path = f"/api/v1/recommendations/{result['recommendation_id']}"
        assert backend.get(path, headers=headers).json() == result
        assert backend.get(path, headers=foreign).status_code == 404
    assert backend.get("/api/v1/recommendations", headers=foreign).json() == []

    def history():
        response = backend.get("/api/v1/recommendations", headers=headers)
        assert response.status_code == 200
        return response.json()

    # 홈은 저장 결과만 조회합니다. 프로필을 바꿔도 새 추천은 명시적인 POST로 요청합니다.
    for style in ("street", "minimal"):
        profile(style)
        home = ui.get("/")
        assert home.status_code == 200, home.text
        records = history()
        assert len(records) == 7
        assert f"/garment-images/{clothes['street', 'top']}" in home.text
        assert "최근 추천 코디" in home.text
        assert ui.get("/").status_code == 200 and len(history()) == 7
    changed = backend.patch(
        f"/api/v1/garments/{clothes['minimal', 'top']}",
        headers=headers,
        json={"style_candidates": ["street"]},
    )
    assert changed.status_code == 200
    assert ui.get("/").status_code == 200
    assert len(history()) == 7

    engine = create_engine(runtime["database_url"])
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM users")) == 2
            assert connection.scalar(text("SELECT count(*) FROM garments")) == 5
            assert connection.scalar(text("SELECT count(*) FROM recommendations")) == 7
            for result in results:
                stored = (
                    connection.execute(
                        text(
                            "SELECT r.context, o.top_id, o.bottom_id, o.score "
                            "FROM recommendations r JOIN recommendation_outfits o "
                            "ON o.recommendation_id = r.id "
                            "WHERE r.id = :rid AND o.rank = 1"
                        ),
                        {"rid": result["recommendation_id"]},
                    )
                    .mappings()
                    .one()
                )
                assert stored["context"]["preferred_styles"] == result["context"]["preferred_styles"]
                assert str(stored["top_id"]) == result["outfits"][0]["items"]["top"]
                assert str(stored["bottom_id"]) == result["outfits"][0]["items"]["bottom"]
                assert stored["score"] == result["outfits"][0]["score"]
    finally:
        engine.dispose()
