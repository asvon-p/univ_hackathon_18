"""HTTPS proxy cookies and cross-origin protection on the real demo wrapper + PostgreSQL."""

import hashlib
import re
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image


@pytest.mark.parametrize("live_api", [{"provider": "local", "photo_policy": "session"}], indirect=True)
@pytest.mark.parametrize("live_frontend", [{"body_photo": True, "mentor": True}], indirect=True)
def test_mentor_https_cookie_and_cross_origin_protection(live_frontend):
    ui, _, runtime = live_frontend
    assert 'name="invite_code"' not in ui.get("/signup").text
    origin = str(ui.base_url).rstrip("/").replace("http://", "https://")
    response = ui.post(
        "/signup",
        headers={"X-Forwarded-Proto": "https", "Origin": origin},
        data={
            "email": f"mentor-{uuid4().hex}@example.com",
            "password": "MentorForms123!",
            "password_confirm": "MentorForms123!",
        },
    )
    assert response.status_code == 303
    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    token = ui.cookies["fitjang_token"]
    backend = live_frontend[1]
    assert (
        backend.get("/api/v1/profile", headers={"Authorization": "Bearer " + token}).json()["photo_policy"]
        == "session"
    )
    # The fixture transports HTTP locally; explicit cookie represents the proxy's HTTPS request.
    headers = {"X-Forwarded-Proto": "https", "Cookie": f"fitjang_token={token}"}
    assert 'name="consent_body_photo"' in ui.get("/profile/body-photo", headers=headers).text
    denied = ui.post("/profile/body-photo", headers={**headers, "Origin": "https://unrelated.example"})
    assert denied.status_code == 403
    assert denied.headers["permissions-policy"].startswith("camera=(),")
    own_form = ui.post("/profile/body-photo", headers={**headers, "Origin": origin})
    assert own_form.status_code == 422  # reaches the photo consent validation
    assert own_form.headers["permissions-policy"].startswith("camera=(self),")
    assert ui.get("/garments/new").headers["permissions-policy"].startswith("camera=(),")
    for path in ("/profile/body-photo", "/garments/new", "/recommend", "/tryon/history"):
        page = ui.get(path, headers=headers)
        assert page.status_code == 200
        camera = "self" if path in ("/profile/body-photo", "/garments/new") else ""
        assert page.headers["permissions-policy"] == (
            f"camera=({camera}), microphone=(), geolocation=(self), usb=()"
        )
        nonce = re.search(r"script-src 'nonce-([^']+)'", page.headers["content-security-policy"]).group(1)
        scripts = re.findall(r"<script\b([^>]*)>", page.text)
        assert scripts and all(f'nonce="{nonce}"' in attrs for attrs in scripts), path
        assert not re.search(r"\son(?:click|toggle)=", page.text)
    source_file = Path("frontend/app.py")
    before = hashlib.sha256(source_file.read_bytes()).hexdigest()
    upload_headers = {**headers, "Origin": origin}
    rejected = ui.post(
        "/garments/new",
        headers=upload_headers,
        files={"file": ("../../frontend/app.py", b"print('untrusted')", "image/jpeg")},
    )
    assert rejected.status_code == 415
    assert rejected.headers["permissions-policy"].startswith("camera=(self),")
    image = BytesIO()
    Image.new("RGB", (128, 128), "blue").save(image, format="JPEG")
    valid = ui.post(
        "/garments/new",
        headers=upload_headers,
        files={"file": ("../../frontend/app.py", image.getvalue(), "image/jpeg")},
    )
    assert valid.status_code == 303
    assert hashlib.sha256(source_file.read_bytes()).hexdigest() == before
    assert not (runtime["storage_dir"] / "frontend" / "app.py").exists()
    gid = valid.headers["location"].split("/")[2]
    assert "?size=thumbnail" in ui.get("/wardrobe", headers=headers).text
    small = ui.get(f"/garment-images/{gid}?size=thumbnail", headers=headers)
    assert small.status_code == 200 and small.headers["content-type"] == "image/webp"
    assert small.headers["cache-control"] == "private, no-store"
