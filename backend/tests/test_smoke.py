from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_recommendation_excludes_laundry():
    r = client.post("/api/v1/recommendations", json={"user_id": "U1", "temperature_c": 5})
    assert r.status_code == 200
    outfit = r.json()["outfits"][0]["items"]
    assert outfit["top"] != "G002"  # 세탁 중인 옷은 추천되면 안 됨
