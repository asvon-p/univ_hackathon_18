from fastapi.testclient import TestClient

from backend.app.adapters import registry
from backend.app.core.config import get_settings
from backend.app.main import create_app


def test_frontend_build_is_served_without_masking_api_routes(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>frontend-build-fixture</body></html>", encoding="utf-8")
    (dist / "assets/app.js").write_text("window.demoReady = true;", encoding="utf-8")
    monkeypatch.setenv("SERVE_FRONTEND", "true")
    monkeypatch.setenv("FRONTEND_DIST", str(dist))
    get_settings.cache_clear()
    registry.reset()
    try:
        with TestClient(create_app()) as client:
            response = client.get("/")
            assert response.status_code == 200 and "frontend-build-fixture" in response.text
            assert client.get("/assets/app.js").status_code == 200
            assert client.get("/health").headers["content-type"] == "application/json"
            assert client.get("/api/v1/garments").status_code == 401
            missing = client.get("/api/v1/missing")
            assert missing.status_code == 404 and missing.json()["code"] == "NOT_FOUND"
            assert client.get("/.env.demo").status_code == 404
            assert client.get("/docs").status_code == 200
    finally:
        get_settings.cache_clear()
        registry.reset()
