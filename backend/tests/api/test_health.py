def test_health_reports_adapters(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["adapters"] == {"vision": "mock", "recommender": "mock", "weather": "manual"}
    assert body["db"] in ("ok", "down")
    assert body["contract_version"]
