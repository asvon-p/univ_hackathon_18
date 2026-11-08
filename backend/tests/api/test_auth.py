from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest
from sqlalchemy import func, select

from backend.app.core.config import get_settings
from backend.app.core.security import create_access_token, verify_password
from backend.app.db.models import User, UserProfile


def signup_body(**changes):
    return {"email": "Student@Example.COM", "password": "password-123", "nickname": " 대학생 ", **changes}


def test_signup_login_me_and_default_profile(api_client, db_session):
    response = api_client.post("/api/v1/auth/signup", json=signup_body())
    assert response.status_code == 201, response.text
    user = response.json()
    assert user["email"] == "student@example.com" and user["nickname"] == "대학생"
    assert user["created_at"].endswith("Z")
    assert "password" not in response.text and "password_hash" not in response.text
    stored = db_session.get(User, UUID(user["id"]))
    assert stored.password_hash.startswith("$2b$12$")
    assert verify_password("password-123", stored.password_hash)
    assert db_session.get(UserProfile, stored.id).height_cm is None
    login = api_client.post(
        "/api/v1/auth/login", json={"email": " STUDENT@example.com ", "password": "password-123"}
    )
    assert login.status_code == 200
    assert login.headers["cache-control"] == "no-store"
    body = login.json()
    assert body["token_type"] == "bearer" and body["expires_in"] == 86400
    claims = jwt.decode(body["access_token"], get_settings().JWT_SECRET, algorithms=["HS256"])
    assert claims["sub"] == user["id"] and claims["exp"] - claims["iat"] == 86400
    assert not ({"email", "password", "nickname", "height_cm"} & claims.keys())
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    assert api_client.get("/api/v1/auth/me", headers=headers).json() == {
        k: v for k, v in user.items() if k not in {"access_token", "token_type", "expires_in"}
    }
    profile = api_client.get("/api/v1/profile", headers=headers).json()
    assert profile["user_id"] == user["id"] and profile["preferred_styles"] == []
    assert profile["consent_body_info_at"] is None


def test_duplicate_email_and_invite_code(api_client, db_session):
    settings = get_settings().model_copy(update={"SIGNUP_INVITE_CODE": "대학-초대-2026"})
    api_client.app.dependency_overrides[get_settings] = lambda: settings
    for code in (None, "wrong"):
        response = api_client.post("/api/v1/auth/signup", json=signup_body(invite_code=code))
        assert response.status_code == 403 and response.json()["code"] == "INVITE_CODE_INVALID"
    assert db_session.scalar(select(func.count()).select_from(User)) == 0
    assert (
        api_client.post("/api/v1/auth/signup", json=signup_body(invite_code="대학-초대-2026")).status_code
        == 201
    )
    duplicate = api_client.post(
        "/api/v1/auth/signup", json=signup_body(email="student@example.com", invite_code="대학-초대-2026")
    )
    assert duplicate.status_code == 409 and duplicate.json()["code"] == "EMAIL_ALREADY_EXISTS"
    assert db_session.scalar(select(func.count()).select_from(User)) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"email": "not-email"},
        {"password": "short"},
        {"password": "가" * 25},
        {"password": "x" * 73},
        {"nickname": "  "},
        {"nickname": "x" * 51},
        {"is_active": True},
        {"id": str(uuid4())},
    ],
)
def test_signup_rejects_invalid_input_without_echoing_password(api_client, changes):
    response = api_client.post("/api/v1/auth/signup", json=signup_body(**changes))
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert signup_body(**changes)["password"] not in response.text


def test_unicode_password_at_bcrypt_boundary(api_client):
    password = "가" * 24
    assert api_client.post("/api/v1/auth/signup", json=signup_body(password=password)).status_code == 201
    assert (
        api_client.post(
            "/api/v1/auth/login", json={"email": "student@example.com", "password": password}
        ).status_code
        == 200
    )


def test_invalid_login_and_inactive_user(api_client, account_factory, db_session):
    account = account_factory()
    for email, password in (
        (account["email"], "wrong-password"),
        ("unknown@example.com", account["password"]),
    ):
        response = api_client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert response.status_code == 401 and response.json()["code"] == "AUTH_INVALID_CREDENTIALS"
        assert response.headers["www-authenticate"] == "Bearer"
    row = db_session.get(User, UUID(account["id"]))
    row.is_active = False
    db_session.commit()
    assert (
        api_client.post(
            "/api/v1/auth/login", json={"email": account["email"], "password": account["password"]}
        ).status_code
        == 401
    )
    assert api_client.get("/api/v1/auth/me", headers=account["headers"]).status_code == 401


@pytest.mark.parametrize(
    "kind",
    [
        "expired",
        "signature",
        "algorithm",
        "no-exp",
        "no-iat",
        "invalid-sub",
        "type",
        "future",
        "unknown-user",
        "malformed",
    ],
)
def test_jwt_validation(api_client, account_factory, kind):
    account = account_factory()
    now = datetime.now(UTC)
    claims = {"sub": account["id"], "iat": now, "exp": now + timedelta(hours=1), "type": "access"}
    secret, algorithm = get_settings().JWT_SECRET, "HS256"
    if kind == "expired":
        claims["exp"] = now - timedelta(seconds=10)
    elif kind == "signature":
        secret = "another-secret-that-is-at-least-32-bytes-long"
    elif kind == "algorithm":
        algorithm = "HS384"
    elif kind in ("no-exp", "no-iat"):
        claims.pop(kind[3:])
    elif kind == "invalid-sub":
        claims["sub"] = "not-a-uuid"
    elif kind == "type":
        claims["type"] = "refresh"
    elif kind == "future":
        claims["iat"] = now + timedelta(hours=1)
    elif kind == "unknown-user":
        claims["sub"] = str(uuid4())
    token = "not.a.token" if kind == "malformed" else jwt.encode(claims, secret, algorithm=algorithm)
    response = api_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    expected = "AUTH_TOKEN_EXPIRED" if kind == "expired" else "AUTH_INVALID_TOKEN"
    assert response.json()["code"] == expected
    assert token not in response.text


def test_anonymous_requests_cannot_use_protected_routes(api_client):
    paths = [
        ("GET", "/api/v1/auth/me", {}),
        ("GET", "/api/v1/profile", {}),
        ("PATCH", "/api/v1/profile", {"json": {"nickname": "other"}}),
        ("GET", "/api/v1/garments", {}),
        ("POST", "/api/v1/garments", {"files": {"file": ("photo.jpg", b"x", "image/jpeg")}}),
        ("GET", f"/api/v1/garments/{uuid4()}", {}),
        ("PATCH", f"/api/v1/garments/{uuid4()}", {"json": {"status": "laundry"}}),
        ("DELETE", f"/api/v1/garments/{uuid4()}", {}),
        ("GET", f"/media/{uuid4()}/{uuid4()}/original.jpg", {}),
        ("POST", "/api/v1/recommendations", {"json": {"temperature_c": 15}}),
        ("GET", "/api/v1/recommendations", {}),
        ("GET", f"/api/v1/recommendations/{uuid4()}", {}),
        ("GET", f"/api/v1/recommendations/{uuid4()}/feedback", {}),
        (
            "POST",
            "/api/v1/recommendations/other/feedback",
            {"json": {"recommendation_id": "other", "outfit_id": "O1", "accepted": True}},
        ),
    ]
    for method, path, kwargs in paths:
        response = api_client.request(method, path, **kwargs)
        assert response.status_code == 401, (path, response.text)
        assert response.json()["code"] == "AUTH_REQUIRED"
    assert api_client.get("/api/v1/auth/me", headers={"Authorization": "Basic bogus"}).status_code == 401


def test_no_development_bypass_or_query_token(api_client, auth_account, db_session):
    assert (
        api_client.get(
            "/api/v1/auth/me?access_token=" + auth_account["headers"]["Authorization"][7:]
        ).status_code
        == 401
    )
    assert api_client.get("/api/v1/garments", headers={"X-User-Id": auth_account["id"]}).status_code == 401
    row = db_session.get(User, UUID(auth_account["id"]))
    db_session.delete(row)
    db_session.commit()
    assert api_client.get("/api/v1/auth/me", headers=auth_account["headers"]).status_code == 401


def test_server_requires_a_non_sample_secret():
    from backend.app.core.config import Settings

    for secret in ("", "short", "change-me-to-a-long-random-string"):
        settings = Settings(_env_file=None, JWT_SECRET=secret)
        with pytest.raises(RuntimeError, match="JWT_SECRET"):
            create_access_token(uuid4(), settings)
