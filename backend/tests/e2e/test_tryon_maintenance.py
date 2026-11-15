"""Live UI + PostgreSQL remain usable through GPU maintenance transitions."""

from uuid import uuid4

import pytest

from scripts.demo_assets import CATALOG, garment_images
from shared.tryon_maintenance import write_state


@pytest.mark.parametrize("live_api", [{"provider": "local"}], indirect=True)
@pytest.mark.parametrize("live_frontend", [{"tryon": True}, {"tryon": True, "mentor": True}], indirect=True)
def test_training_pause_preserves_session_wardrobe_and_backend(live_frontend):
    ui, backend, runtime = live_frontend
    email, password = f"switch-{uuid4().hex}@example.com", "SwitchTest123!"
    assert (
        ui.post(
            "/signup", data={"email": email, "password": password, "password_confirm": password}
        ).status_code
        == 303
    )
    token = ui.cookies["fitjang_token"]
    headers = {"Authorization": "Bearer " + token}
    before = backend.get("/api/v1/profile", headers=headers).json()
    garment = backend.post(
        "/api/v1/garments",
        headers=headers,
        files={"file": ("top.jpg", garment_images(CATALOG[0])[0], "image/jpeg")},
    )
    assert garment.status_code == 201
    wardrobe_before = backend.get("/api/v1/garments", headers=headers).json()
    path = runtime["tryon_maintenance"]
    for phase in ("draining", "paused", "starting", "error"):
        write_state(path, phase)
        status = ui.get("/tryon/service-status")
        assert status.status_code == 200 and status.json()["phase"] == phase
        assert "no-store" in status.headers["cache-control"]
        assert status.json()["message"] in ui.get("/profile").text
        rejected = ui.post("/tryon", data={"top": "anything"})
        assert rejected.status_code == 503 and rejected.json()["code"] == "TRYON_PAUSED"
        assert backend.get("/health").json()["db"] == "ok"
        assert backend.get("/api/v1/profile", headers=headers).json() == before
        assert backend.get("/api/v1/garments", headers=headers).json() == wardrobe_before
        assert ui.cookies["fitjang_token"] == token
    write_state(path, "ready")
    assert ui.get("/tryon/service-status").json()["available"]
    assert ui.post("/tryon", data={"top": "anything"}).status_code == 409  # Needs body photo now.
    assert ui.get("/profile").status_code == 200
