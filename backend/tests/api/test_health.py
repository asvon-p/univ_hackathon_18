def test_health_reports_adapters(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["adapters"] == {"vision": "mock", "recommender": "mock", "weather": "manual"}
    assert body["db"] in ("ok", "down")
    assert body["contract_version"]


def test_health_exposes_optional_vision_failure(api_client, monkeypatch):
    from types import SimpleNamespace

    from backend.app.api.v1 import health

    monkeypatch.setattr(health, "check_db", lambda: (True, None))
    monkeypatch.setattr(
        health, "get_vision", lambda: SimpleNamespace(name="local", warmup_error="ImportError")
    )
    result = api_client.get("/health").json()
    assert result["status"] == "degraded" and result["db"] == "ok"
    assert result["vision_error"] == "ImportError"
