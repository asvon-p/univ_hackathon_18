from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, text

from backend.tests.api.test_body_photo import PATH, picture
from backend.tests.e2e.test_reanalysis import register, upload


def account(client):
    uid, headers = register(client)
    assert (
        client.patch("/api/v1/profile", headers=headers, json={"consent_body_info": True}).status_code == 200
    )
    assert (
        client.put(PATH, headers=headers, files={"file": ("body.png", picture(), "image/png")}).status_code
        == 200
    )
    item = upload(client, headers)
    return headers, {"items": {"top": item["garment_id"]}}


@pytest.mark.parametrize("live_api", ["mock"], indirect=True)
@pytest.mark.parametrize("capacity", [2, 8])
def test_concurrent_user_and_global_limits_postgresql(live_api, capacity):
    client, _, runtime = live_api
    h, body = account(client)
    path = "/api/v1/tryon-results"
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(lambda _: client.post(path, headers=h, json=body), range(6)))
    assert sorted(r.status_code for r in responses) == [201, 429, 429, 429, 429, 429]
    rid = next(r.json()["id"] for r in responses if r.status_code == 201)
    assert client.post(f"{path}/{rid}/cancel", headers=h).status_code == 204
    accounts = [(h, body), *(account(client) for _ in range(capacity))]
    engine = create_engine(runtime["database_url"])
    try:
        with engine.begin() as db:
            db.execute(
                text("UPDATE tryon_queue_state SET capacity=:capacity WHERE id=1"), {"capacity": capacity}
            )
        with ThreadPoolExecutor(max_workers=capacity + 1) as pool:
            responses = list(pool.map(lambda a: client.post(path, headers=a[0], json=a[1]), accounts))
        assert sorted(r.status_code for r in responses) == [201] * capacity + [429]
        with engine.connect() as db:
            assert db.scalar(text("SELECT count(*) FROM tryon_admissions")) == capacity
            assert db.scalar(text("SELECT count(DISTINCT user_id) FROM tryon_admissions")) == capacity
        # A separate DB connection sees the same occupied slots.
        denied = next(i for i, r in enumerate(responses) if r.status_code == 429)
        assert responses[denied].json()["code"] == "TRYON_QUEUE_FULL"
        for a, result in zip(accounts, responses, strict=True):
            if result.status_code == 201:
                assert client.post(f"{path}/{result.json()['id']}/cancel", headers=a[0]).status_code == 204
        assert client.post(path, headers=accounts[denied][0], json=accounts[denied][1]).status_code == 201
    finally:
        engine.dispose()
