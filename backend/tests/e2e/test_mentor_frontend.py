"""HTTPS proxy cookies and cross-origin protection on the real demo wrapper + PostgreSQL."""

from uuid import uuid4

import pytest


@pytest.mark.parametrize("live_api", [{"provider": "local", "invite_code": ""}], indirect=True)
@pytest.mark.parametrize("live_frontend", [{"body_photo": True, "mentor": True}], indirect=True)
def test_mentor_https_cookie_and_cross_origin_protection(live_frontend):
    ui, _, _ = live_frontend
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
    # The fixture transports HTTP locally; explicit cookie represents the proxy's HTTPS request.
    headers = {"X-Forwarded-Proto": "https", "Cookie": f"fitjang_token={token}"}
    assert 'name="consent_body_photo"' in ui.get("/profile/body-photo", headers=headers).text
    denied = ui.post("/profile/body-photo", headers={**headers, "Origin": "https://unrelated.example"})
    assert denied.status_code == 403
    own_form = ui.post("/profile/body-photo", headers={**headers, "Origin": origin})
    assert own_form.status_code == 422  # reaches the photo consent validation
