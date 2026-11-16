from uuid import uuid4

import pytest

from backend.tests.e2e.recommendation_flow import submit_recommendation


@pytest.mark.parametrize("live_api", [{"provider": "local"}], indirect=True)
def test_simultaneous_provision_is_once_per_account(live_api):
    from concurrent.futures import ThreadPoolExecutor
    from uuid import UUID

    from sqlalchemy.orm import Session

    from backend.app.db.engine import create_database_engine
    from backend.app.services.starter_wardrobe import provision_existing_user
    from backend.app.storage.local import LocalStorage

    api, _, runtime = live_api
    account = api.post(
        "/api/v1/auth/signup",
        json={"email": f"race-{uuid4().hex}@example.com", "password": "Wardrobe2026!", "nickname": "race"},
    ).json()
    engine = create_database_engine(runtime["database_url"])
    storage = LocalStorage(runtime["storage_dir"])

    def provision():
        with Session(engine) as db:
            return provision_existing_user(db, storage, UUID(account["id"]))

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(lambda _: provision(), range(2))) == [0, 9]
        rows = api.get(
            "/api/v1/garments", headers={"Authorization": "Bearer " + account["access_token"]}
        ).json()
        assert len(rows) == 9 and all(g["is_example"] for g in rows)
        assert len([p for p in runtime["storage_dir"].rglob("*") if p.is_file()]) == 18
    finally:
        engine.dispose()


@pytest.mark.parametrize("live_api", [{"provider": "local", "starter_wardrobe": True}], indirect=True)
@pytest.mark.parametrize("live_frontend", [{}, {"mentor": True}], indirect=True)
def test_test_wardrobe_navigation_and_recommendation(live_frontend):
    ui, api, runtime = live_frontend
    r = ui.post(
        "/signup",
        data={
            "email": f"partition-{uuid4().hex}@example.com",
            "password": "Wardrobe2026!",
            "password_confirm": "Wardrobe2026!",
        },
    )
    assert r.status_code == 303
    h = {"Authorization": "Bearer " + ui.cookies["fitjang_token"]}
    clothes = api.get("/api/v1/garments", headers=h).json()
    first = clothes[0]["garment_id"]
    page = ui.get("/wardrobe?wardrobe=example&category=top")
    assert page.status_code == 200 and page.text.count("data-garment-id=") == 3
    assert "<h1>테스트용 옷장</h1>" in page.text
    assert 'name="wardrobe" value="example"' in page.text
    partial = ui.get("/wardrobe?wardrobe=example&category=bottom&partial=true")
    assert partial.status_code == 200 and partial.json()["html"].count("data-garment-id=") == 3
    form = ui.get("/recommend?wardrobe=example&temp=10&styles=casual")
    assert form.status_code == 200 and 'value="example" selected' in form.text
    assert all(g["garment_id"] in form.text for g in clothes)
    assert "wardrobe" in ui.get("/day-plans", params={"back": "/recommend?wardrobe=example&temp=10"}).text
    result = submit_recommendation(ui, {"wardrobe": "example", "temp": "10", "required": [first]})
    assert result.status_code == 200 and "코디 1" in result.text and "테스트용 옷장" in result.text
    data = api.get("/api/v1/recommendations", headers=h).json()[0]
    assert data["context"]["wardrobe"] == "example"
    assert all(first in o["items"].values() for o in data["outfits"])
    assert "wardrobe=example" in result.text
    details = ui.get(f"/garments/{first}")
    assert "/wardrobe?wardrobe=example" in details.text
    deleted = ui.post(f"/garments/{first}/delete")
    assert deleted.status_code == 303 and "wardrobe=example" in deleted.headers["location"]
    assert "8벌" in ui.get(deleted.headers["location"]).text
