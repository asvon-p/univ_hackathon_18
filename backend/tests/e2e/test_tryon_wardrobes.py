"""Local and mentor HTTP flows, real PostgreSQL, synthetic GPU boundary."""

import json
import time
from uuid import uuid4

import pytest

from backend.tests.api.test_body_photo import PATH, picture
from backend.tests.e2e.recommendation_flow import submit_recommendation


def setup_wardrobes(ui, api, email=None):
    password = "Wardrobes2026!"
    email = email or f"tryon-{uuid4().hex}@example.com"
    assert (
        ui.post(
            "/signup",
            data={
                "email": email,
                "password": password,
                "password_confirm": password,
            },
        ).status_code
        == 303
    )
    headers = {"Authorization": "Bearer " + ui.cookies["fitjang_token"]}
    clothes = api.get("/api/v1/garments", headers=headers).json()
    example = {
        slot: next(g["garment_id"] for g in clothes if g["category"] == slot)
        for slot in ("top", "bottom", "outerwear")
    }
    personal = {}
    for slot in example:
        photo = api.get(f"/api/v1/garments/{example[slot]}/image", headers=headers)
        added = api.post(
            "/api/v1/garments", headers=headers, files={"file": ("fixture.jpg", photo.content, "image/jpeg")}
        )
        assert added.status_code == 201
        personal[slot] = added.json()["garment_id"]
        assert (
            api.patch(
                f"/api/v1/garments/{personal[slot]}",
                headers=headers,
                json={"category": slot, "color": "navy", "style_candidates": ["casual"], "thermal_level": 3},
            ).status_code
            == 200
        )
    assert api.patch("/api/v1/profile", headers=headers, json={"consent_body_info": True}).status_code == 200
    assert (
        api.put(PATH, headers=headers, files={"file": ("synthetic.png", picture(), "image/png")}).status_code
        == 200
    )
    return headers, personal, example


@pytest.mark.parametrize("live_api", [{"provider": "local", "starter_wardrobe": True}], indirect=True)
@pytest.mark.parametrize(
    "live_frontend",
    [
        {"tryon": True, "tryon_stub": True},
        {"tryon": True, "tryon_stub": True, "mentor": True},
    ],
    indirect=True,
)
def test_tryon_request_scopes_and_screen_warnings(live_frontend):
    ui, api, runtime = live_frontend
    headers, personal, example = setup_wardrobes(ui, api)
    mixed = {"top": personal["top"], "bottom": example["bottom"]}
    # Omitted scope and forged scope both fail before any GPU call/DB reservation.
    for scope in (None, "personal", "example"):
        response = ui.post("/tryon", data={**mixed, **({"wardrobe": scope} if scope else {})})
        assert response.status_code == 422 and response.json()["code"] == "TRYON_WARDROBE_MIXED"
    assert not runtime["gpu_calls"].exists()
    assert api.get("/api/v1/tryon-results", headers=headers).json() == []
    for scope, items in (("personal", personal), ("example", example)):
        page = submit_recommendation(ui, {"wardrobe": scope, "temp": "20"})
        assert page.status_code == 200
        assert f'data-wardrobe="{scope}"' in page.text
        started = ui.post("/tryon", data={**items, "wardrobe": scope})
        assert started.status_code == 200, started.text
        rid = started.json()["id"]
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            state = ui.get(f"/tryon/{rid}").json()
            if state["status"] == "done":
                break
            assert state["status"] != "error", state
            time.sleep(0.1)
        assert state["status"] == "done"
        assert ui.get(f"/tryon/{rid}/image").status_code == 200
        assert api.get(f"/api/v1/tryon-results/{rid}", headers=headers).json()["items"] == items
        assert ui.post("/tryon", data={**items, "wardrobe": scope}).json()["id"] == rid
    calls = [json.loads(line) for line in runtime["gpu_calls"].read_text().splitlines()]
    assert len(calls) == 6  # Three garments each; repeated requests reuse results.
    legacy = api.post(
        "/api/v1/recommendations",
        headers=headers,
        json={
            "temperature_c": 20,
            "wardrobe": "all",
            "required_garment_ids": list(mixed.values()),
        },
    )
    assert legacy.status_code == 201, legacy.text
    legacy_page = ui.get(f"/recommend/result/{legacy.json()['recommendation_id']}")
    assert legacy_page.status_code == 200 and 'data-tryon-blocked="mixed"' in legacy_page.text
    assert "옷장 선택하고 다시 추천받기" in legacy_page.text
    assert 'data-tryon="' not in legacy_page.text
    assert len(api.get("/api/v1/tryon-results", headers=headers).json()) == 2
