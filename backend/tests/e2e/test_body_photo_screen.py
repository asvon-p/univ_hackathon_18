"""Real frontend photo form, JWT and PostgreSQL; no generated personal images in tests."""

from io import BytesIO
from uuid import uuid4

import httpx
import pytest
from PIL import Image


@pytest.mark.parametrize("live_api", [{"provider": "local", "invite_code": ""}], indirect=True)
@pytest.mark.parametrize(
    "live_frontend", [{"body_photo": True}, {"body_photo": True, "mentor": True}], indirect=True
)
def test_body_photo_screen_consent_upload_isolation_delete(live_frontend):
    ui, backend, _ = live_frontend
    email = f"photo-screen-{uuid4().hex}@example.com"
    assert (
        ui.post(
            "/signup",
            data={"email": email, "password": "PhotoScreen123!", "password_confirm": "PhotoScreen123!"},
        ).status_code
        == 303
    )
    headers = {"Authorization": "Bearer " + ui.cookies["fitjang_token"]}
    assert 'href="/profile/body-photo' in ui.get("/profile").text
    assert 'name="consent_body_photo"' in ui.get("/profile/body-photo").text
    assert (
        backend.patch(
            "/api/v1/profile", headers=headers, json={"consent_body_info": True, "height_cm": 175.0}
        ).status_code
        == 200
    )
    buffer = BytesIO()
    Image.new("RGB", (600, 900), "#888888").save(buffer, "PNG")
    files = {"file": ("body.png", buffer.getvalue(), "image/png")}
    assert ui.post("/profile/body-photo", files=files).status_code == 422
    assert backend.get("/api/v1/users/me/body-photo", headers=headers).status_code == 404
    saved = ui.post("/profile/body-photo", files=files, data={"consent_body_photo": "1"})
    assert saved.status_code == 303
    assert backend.get("/api/v1/profile", headers=headers).json()["height_cm"] == 175.0
    photo = ui.get("/body-photo")
    assert photo.status_code == 200 and photo.headers["content-type"] == "image/jpeg"
    assert photo.headers["cache-control"] == "private, no-store"
    with httpx.Client(base_url=ui.base_url, trust_env=False) as other:
        assert other.get("/body-photo").status_code == 401
        assert other.post("/login", data={"email": email, "password": "PhotoScreen123!"}).status_code == 303
        assert other.get("/body-photo").status_code == 200
    assert ui.post("/profile/body-photo/delete").status_code == 303
    assert ui.get("/body-photo").status_code == 404
