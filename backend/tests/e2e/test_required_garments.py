"""Real HTTP frontend and local recommendation with isolated PostgreSQL data."""

import re
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import create_engine, text

from backend.tests.e2e.recommendation_flow import submit_recommendation


@pytest.mark.parametrize("live_api", [{"provider": "local"}], indirect=True)
def test_required_garments_ui_persistence_and_concurrent_user_isolation(live_frontend):
    ui, backend, runtime = live_frontend
    password = "RequiredFixture123!"
    photo = BytesIO()
    Image.new("RGB", (64, 64), "navy").save(photo, format="JPEG")

    def account():
        email = f"required-{uuid4().hex}@example.com"
        response = backend.post("/api/v1/auth/signup", json={"email": email, "password": password})
        assert response.status_code == 201, response.text
        return email, {"Authorization": "Bearer " + response.json()["access_token"]}

    def upload(headers, category, name):
        response = backend.post(
            "/api/v1/garments",
            headers=headers,
            files={"file": ("fixture.jpg", photo.getvalue(), "image/jpeg")},
        )
        assert response.status_code == 201, response.text
        gid = response.json()["garment_id"]
        response = backend.patch(
            f"/api/v1/garments/{gid}",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={
                "category": category,
                "name": name,
                "thermal_level": 3,
                "style_candidates": ["minimal"],
            },
        )
        assert response.status_code == 200, response.text
        return gid

    email, owner = account()
    _, other = account()
    ids = {
        category: upload(owner, category, "필수 " + category) for category in ("top", "bottom", "outerwear")
    }
    upload(owner, "top", "대안 상의")
    foreign = {
        category: upload(other, category, "다른 사용자 " + category)
        for category in ("top", "bottom", "outerwear")
    }
    login = ui.post("/login", data={"email": email, "password": password})
    assert login.status_code == 303
    detail = ui.get(f"/garments/{ids['top']}")
    assert "이 옷으로 코디" in detail.text
    form = ui.get("/recommend", params={"required": ids["top"]})
    assert form.status_code == 200
    assert f'value="{ids["top"]}" selected' in form.text
    assert all(gid not in form.text for gid in foreign.values())
    selected = [ids["top"], ids["outerwear"]]
    values = [("temp", "18"), ("styles", "minimal"), *(("required", gid) for gid in selected)]
    page = submit_recommendation(ui, values)
    assert page.status_code == 200, page.text[:2000]
    assert "선택한 옷 2벌 필수 포함" in page.text
    assert page.text.count("· 필수 포함") >= 2
    back = unescape(re.search(r'href="([^"]+)">조건 바꾸기', page.text)[1])
    edit = ui.get(back)
    assert edit.status_code == 200
    assert all(f'value="{gid}" selected' in edit.text for gid in selected)
    bad = submit_recommendation(ui, [("temp", "bad"), ("required", ids["top"])])
    assert bad.status_code == 422 and f'value="{ids["top"]}" selected' in bad.text

    def request_for(headers, required):
        return backend.post(
            "/api/v1/recommendations",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={
                "temperature_c": 30,
                "required_garment_ids": required,
            },
        )

    # Hot weather eliminates thermal=3 local candidates, so both use the real fallback.
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(request_for, owner, selected),
            pool.submit(request_for, other, list(foreign.values())),
        ]
        results = [future.result() for future in futures]
    for response, required, auth, denied in (
        (results[0], selected, owner, other),
        (results[1], list(foreign.values()), other, owner),
    ):
        assert response.status_code == 201, response.text
        data = response.json()
        assert data["is_fallback"] and data["outfits"]
        assert all(set(required) <= set(o["items"].values()) for o in data["outfits"])
        path = f"/api/v1/recommendations/{data['recommendation_id']}"
        assert backend.get(path, headers=auth).json() == data
        assert backend.get(path, headers=denied).status_code == 404
    denied = request_for(owner, [foreign["top"]])
    assert denied.status_code == 404
    assert submit_recommendation(ui, {"temp": 18, "required": foreign["top"]}).status_code == 422

    changed = backend.patch(f"/api/v1/garments/{ids['top']}", headers=owner, json={"status": "laundry"})
    assert changed.status_code == 200
    rejected = request_for(owner, [ids["top"]])
    assert rejected.status_code == 422
    assert "REQUIRED_GARMENT_UNAVAILABLE" == rejected.json()["code"]
    old = backend.get("/api/v1/recommendations", headers=owner).json()
    assert len(old) == 2 and all(r["context"]["required_garment_ids"] == selected for r in old)
    engine = create_engine(runtime["database_url"])
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM users")) == 2
            assert connection.scalar(text("SELECT count(*) FROM garments")) == 7
            assert connection.scalar(text("SELECT count(*) FROM recommendations")) == 3
    finally:
        engine.dispose()
