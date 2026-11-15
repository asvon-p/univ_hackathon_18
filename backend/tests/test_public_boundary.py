import asyncio
import re
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from scripts import mentor_frontend as public


@pytest.mark.parametrize(
    "path",
    [
        "/.env",
        "/.git/config",
        "/.demo/mentor-demo/credentials.json",
        "/backend/app/main.py",
        "/frontend/",
        "/scripts/",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/api/v1/auth/me",
        "/storage/",
        "/editor",
        "/terminal",
        "/%252eenv",
        "/%252e%252e/backend/app/main.py",
        "/BACKEND/app/main.PY",
        "/C:%5cWindows",
        "/main.py.map",
        "/pyproject.toml",
        "/requirements.txt",
    ],
)
@pytest.mark.parametrize("method", ["GET", "PUT"])
def test_source_paths_are_rejected_before_routes_or_file_access(path, method):
    # No file is opened (including .env); even the downstream app must not run.
    request = Request(
        {
            "type": "http",
            "method": method,
            "scheme": "https",
            "path": path,
            "headers": [(b"host", b"127.0.0.1")],
            "query_string": b"",
        }
    )
    downstream = AsyncMock()
    response = asyncio.run(public.demo_headers(request, downstream))
    assert response.status_code == 404 and response.body == b"Not Found"
    downstream.assert_not_awaited()


def test_public_headers_nonce_and_host_protection():
    with TestClient(public.app, base_url="https://127.0.0.1") as client:
        first = client.get("/login")
        second = client.get("/signup")
        assert first.status_code == second.status_code == 200
        nonce = re.search(r"script-src 'nonce-([^']+)'", first.headers["content-security-policy"]).group(1)
        assert nonce not in second.headers["content-security-policy"]
        scripts = re.findall(r"<script\b([^>]*)>", first.text)
        assert scripts and all(f'nonce="{nonce}"' in attrs for attrs in scripts)
        assert first.headers["x-content-type-options"] == "nosniff"
        assert first.headers["x-frame-options"] == "DENY"
        assert "max-age=" in first.headers["strict-transport-security"]
        assert client.get("/login", headers={"Host": "attacker.example"}).status_code == 400
        assert client.put("/login").status_code == 405


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "https://attacker.example"},
        {"Origin": "http://127.0.0.1"},
        {"Origin": "null"},
        {"Origin": "https://127.0.0.1", "Sec-Fetch-Site": "cross-site"},
    ],
)
def test_foreign_or_missing_origin_cannot_submit_over_https(headers):
    with TestClient(public.app, base_url="https://127.0.0.1") as client:
        assert client.post("/signup", headers=headers).status_code == 403


def test_login_signup_limits_are_separate_and_expire(monkeypatch):
    limiter = public.AuthRequestLimit()
    assert all(limiter.allow("/signup", "campus", now=1) for _ in range(20))
    assert not limiter.allow("/signup", "campus", now=1)
    assert limiter.allow("/signup", "other", now=1)
    assert limiter.allow("/login", "campus", now=1)
    assert limiter.allow("/signup", "campus", now=62)
    monkeypatch.setattr(public.limiter, "allow", lambda *a: False)
    with TestClient(public.app, base_url="https://127.0.0.1") as client:
        response = client.post("/signup", headers={"Origin": "https://127.0.0.1"})
        assert response.status_code == 429 and response.headers["retry-after"] == "60"


def test_redirects_cannot_escape_to_another_host():
    from frontend.common import safe_next

    for destination in ("//evil.test", "/\\evil.test", "/%5cevil.test", "/%0a/evil.test"):
        assert safe_next(destination) == "/"
    assert safe_next("/wardrobe?q=navy") == "/wardrobe?q=navy"


def test_demo_backend_has_no_documentation_routes(monkeypatch):
    from backend.app.core.config import get_settings
    from backend.app.main import create_app

    monkeypatch.setattr(get_settings(), "APP_ENV", "demo")
    backend = create_app()
    assert backend.openapi_url is None
    assert backend.docs_url is None
    assert backend.redoc_url is None
