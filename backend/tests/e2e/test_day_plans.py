from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from PIL import Image

from backend.tests.api.test_day_plans import plan_body
from backend.tests.e2e.recommendation_flow import submit_recommendation


@pytest.mark.parametrize("live_api", [{"provider": "local"}], indirect=True)
@pytest.mark.parametrize("live_frontend", [{"mentor": True}], indirect=True)
def test_schedule_ui_and_concurrent_ownership(live_frontend):
    ui, backend, _ = live_frontend
    password = "DayFixture123!"
    photo = BytesIO()
    Image.new("RGB", (64, 64), "navy").save(photo, format="JPEG")
    accounts = []
    for _ in range(2):
        email = f"day-{uuid4().hex}@example.com"
        response = backend.post("/api/v1/auth/signup", json={"email": email, "password": password})
        assert response.status_code == 201, response.text
        headers = {"Authorization": "Bearer " + response.json()["access_token"]}
        ids = []
        for category in ("top", "bottom", "outerwear"):
            response = backend.post(
                "/api/v1/garments",
                headers=headers,
                files={"file": ("test.jpg", photo.getvalue(), "image/jpeg")},
            )
            assert response.status_code == 201
            gid = response.json()["garment_id"]
            assert (
                backend.patch(
                    f"/api/v1/garments/{gid}",
                    headers=headers,
                    json={"category": category, "style_candidates": ["formal"], "thermal_level": 3},
                ).status_code
                == 200
            )
            ids.append(gid)
        accounts.append((email, headers, ids))
    email, owner, ids = accounts[0]
    _, other, foreign = accounts[1]
    assert ui.post("/login", data={"email": email, "password": password}).status_code == 303
    assert ui.get("/day-plans").status_code == 200
    values = {
        "date": "2026-11-12",
        "timezone": "Asia/Seoul",
        "start": ["09:00", "18:00"],
        "end": ["10:00", "19:00"],
        "purpose": ["presentation", "travel"],
        "environment": ["indoor", "outdoor"],
        "intensity": ["low", "normal"],
        "priority": ["high", "normal"],
        "outdoor_minutes": ["", ""],
        "temperature_min_c": ["", "0"],
        "temperature_max_c": ["", "3"],
        "required_style": ["", ""],
    }
    saved = ui.post("/day-plans/save", data=values)
    assert saved.status_code == 303, saved.text
    params = {k: v[0] for k, v in parse_qs(urlparse(saved.headers["location"]).query).items()}
    plan_id = params["schedule_id"]
    form = ui.get(saved.headers["location"])
    assert form.status_code == 200
    assert f'value="{plan_id}" data-version="1" selected' in form.text, form.text[
        form.text.find('id="schedule-id"') : form.text.find('id="schedule-id"') + 700
    ]
    result = submit_recommendation(
        ui,
        {**params, "temp": 35, "required": ids[2], "outerwear_policy": "removable"},
    )
    assert result.status_code == 200, result.text[:1200]
    assert (
        "하루 착용 안내" in result.text and "아우터 벗고 소지" in result.text and "아우터 입기" in result.text
    )
    assert "09:00–10:00" in result.text and "18:00–19:00" in result.text
    assert "필수 포함" in result.text
    bad = ui.post("/day-plans/save", data={**values, "start": ["09:00", "09:30"]})
    assert bad.status_code == 422 and "겹칩니다" in bad.text
    assert ui.get("/day-plans?edit=" + plan_id).status_code == 200
    other_plan = backend.post("/api/v1/day-plans", headers=other, json=plan_body()).json()
    assert ui.get("/day-plans?edit=" + other_plan["id"]).status_code == 404

    def recommend_for(headers, pid, selected):
        return backend.post(
            "/api/v1/recommendations",
            headers=headers,
            json={
                "temperature_c": 35,
                "schedule_id": pid,
                "schedule_version": 1,
                "required_garment_ids": selected,
                "outerwear_policy": "removable",
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda args: recommend_for(*args), [(owner, plan_id, ids), (other, other_plan["id"], foreign)]
            )
        )
    for response, selected, denied in ((responses[0], ids, other), (responses[1], foreign, owner)):
        assert response.status_code == 201, response.text
        data = response.json()
        assert all(
            set(selected) <= set(o["items"].values()) and len(o["wearing_plan"]) == 2 for o in data["outfits"]
        )
        assert (
            backend.get("/api/v1/recommendations/" + data["recommendation_id"], headers=denied).status_code
            == 404
        )
    assert recommend_for(owner, other_plan["id"], ids).status_code == 404
    # Two stale editors race: only one expected-version update can commit.
    with ThreadPoolExecutor(max_workers=2) as pool:
        updates = list(
            pool.map(
                lambda _: backend.patch(
                    "/api/v1/day-plans/" + plan_id, headers=owner, json={**plan_body(), "expected_version": 1}
                ),
                range(2),
            )
        )
    assert sorted(r.status_code for r in updates) == [200, 409]
    assert ui.post("/day-plans/delete", data={"id": plan_id, "version": "2"}).status_code == 303
    assert backend.get("/api/v1/day-plans/" + plan_id, headers=owner).status_code == 404
    history = backend.get("/api/v1/recommendations", headers=owner).json()
    assert history and all(r["context"]["day_plan"] is None for r in history)
    assert backend.get("/api/v1/day-plans/" + other_plan["id"], headers=other).status_code == 200
    midnight = ui.post("/day-plans/save", data={**values, "start": ["22:00"], "end": ["00:00"]})
    assert midnight.status_code == 303, midnight.text
    midnight_id = parse_qs(urlparse(midnight.headers["location"]).query)["schedule_id"][0]
    midnight_data = backend.get("/api/v1/day-plans/" + midnight_id, headers=owner).json()
    assert midnight_data["events"][0]["end_at"] == "2026-11-13T00:00:00+09:00"
    edited = ui.get("/day-plans", params={"edit": midnight_id})
    assert 'name="end" type="time" value="00:00"' in edited.text
