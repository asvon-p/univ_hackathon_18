import re
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, text

from backend.tests.api.test_style_references import fixture_photo
from backend.tests.e2e.recommendation_flow import submit_recommendation
from shared.seasonal_weather import current_month, season_for_month


@pytest.mark.parametrize(
    "live_api",
    [{"provider": "local", "openai_stub": "success", "openai_delay": 0.6, "replica": True}],
    indirect=True,
)
@pytest.mark.parametrize("live_frontend", [{"mentor": False}, {"mentor": True}], indirect=True)
def test_shared_database_claim_concurrent_users_and_refresh_are_read_only(live_frontend):
    ui, api, runtime = live_frontend
    accounts = []
    for _ in range(2):
        email, password = f"dedup-{uuid4().hex}@example.com", "DedupFixture123!"
        signup = api.post("/api/v1/auth/signup", json={"email": email, "password": password})
        assert signup.status_code == 201
        headers = {"Authorization": "Bearer " + signup.json()["access_token"]}
        accounts.append((email, password, headers))
        for category, color in [
            ("top", "white"),
            ("top", "navy"),
            ("bottom", "black"),
            ("bottom", "beige"),
            ("outerwear", "blue"),
        ]:
            uploaded = api.post(
                "/api/v1/garments",
                headers=headers,
                files={"file": ("fixture.png", fixture_photo(), "image/png")},
            )
            assert uploaded.status_code == 201
            gid = uploaded.json()["garment_id"]
            changed = api.patch(
                "/api/v1/garments/" + gid,
                headers=headers,
                json={
                    "category": category,
                    "color": color,
                    "thermal_level": 3,
                    "style_candidates": ["minimal"],
                },
            )
            assert changed.status_code == 200
    key, barrier = str(uuid4()), Barrier(8)
    body = {
        "temperature_c": 7.5,
        "preferred_styles": ["minimal"],
        "wardrobe": "personal",
        "clothing_season": season_for_month(current_month()),
    }

    def concurrent(index):
        base = str(api.base_url) if index % 2 else runtime["replica_url"]
        headers = {**accounts[index // 4][2], "Idempotency-Key": key}
        with httpx.Client(base_url=base, trust_env=False, timeout=30) as client:
            barrier.wait(timeout=10)
            return client.post("/api/v1/recommendations", headers=headers, json=body)

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(concurrent, range(8)))
    assert all(r.status_code in (201, 409) for r in responses), [r.text for r in responses]
    assert any(r.status_code == 409 for r in responses)
    assert len(list(runtime["openai_calls"].glob("*.call"))) == 2
    records = []
    for _, _, headers in accounts:
        result = api.post("/api/v1/recommendations", headers={**headers, "Idempotency-Key": key}, json=body)
        assert result.status_code == 201 and result.json()["provider"] == "openai"
        records.append(result.json())
    assert records[0]["recommendation_id"] != records[1]["recommendation_id"]
    assert not set(records[0]["garments"]) & set(records[1]["garments"])
    email, password, headers = accounts[0]
    assert ui.post("/login", data={"email": email, "password": password}).status_code == 303
    saved_url = "/recommend/result/" + records[0]["recommendation_id"]
    for _ in range(3):
        page = ui.get(saved_url)
        assert page.status_code == 200 and "후보 판단" in page.text
        assert ui.get("/").status_code == 200  # Home must not generate either.
    replay = submit_recommendation(ui, {"temp": 7.5, "styles": "minimal"}, request_key=key)
    assert str(replay.url).endswith(saved_url) and replay.status_code == 200
    assert ui.get("/recommend/result", params={"temp": 7.5}).status_code == 303
    status = ui.get("/recommend/requests/" + key)
    assert status.status_code == 303 and status.headers["location"] == saved_url
    foreign = ui.get("/recommend/result/" + records[1]["recommendation_id"])
    assert foreign.status_code == 404
    assert len(list(runtime["openai_calls"].glob("*.call"))) == 2
    engine = create_engine(runtime["database_url"])
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM recommendations")) == 2
        assert connection.scalar(text("SELECT count(*) FROM api_usage")) == 2
        assert connection.scalar(text("SELECT sum(estimated_usd) FROM api_usage")) == pytest.approx(0.000264)
        assert (
            connection.scalar(text("SELECT count(*) FROM recommendation_requests WHERE status='completed'"))
            == 2
        )
        assert connection.scalar(text("SELECT count(*) FROM recommendation_outfits")) == 6
        assert (
            connection.scalar(
                text("SELECT sum(jsonb_array_length(request_times)) FROM recommendation_rate_limits")
            )
            == 2
        )
    engine.dispose()
    # A genuinely new click gets a new key and can intentionally create another result.
    new = submit_recommendation(ui, {"temp": 7.5, "styles": "minimal"})
    assert new.status_code == 200 and str(new.url) != str(page.url)
    assert re.search(r'name="recommendation_id"', new.text)
    assert len(list(runtime["openai_calls"].glob("*.call"))) == 3
