from uuid import UUID

import pytest
from sqlalchemy import select

from backend.app.db.models import Garment, StorageDeletionJob, User
from backend.app.services.starter_wardrobe import provision_existing_user


def test_provision_once_preserves_personal_garments_even_after_example_deletion(
    api_client, auth_account, add_garment, db_session, storage
):
    uid = UUID(auth_account["id"])
    personal = add_garment(auth_account, "top")
    url = f"/api/v1/garments/{personal}"
    before = api_client.get(url, headers=auth_account["headers"]).json()
    assert provision_existing_user(db_session, storage, uid) == 9
    assert provision_existing_user(db_session, storage, uid) == 0
    assert api_client.get(url, headers=auth_account["headers"]).json() == before
    examples = list(db_session.scalars(select(Garment).where(Garment.user_id == uid, Garment.is_example)))
    assert len(examples) == 9
    for garment in examples:
        assert (
            api_client.delete(f"/api/v1/garments/{garment.id}", headers=auth_account["headers"]).status_code
            == 204
        )
    assert provision_existing_user(db_session, storage, uid) == 0
    assert api_client.get(url, headers=auth_account["headers"]).json() == before


@pytest.mark.parametrize("failure", ["file", "commit", "lost_ack"])
def test_provision_failure_never_deletes_existing_images(
    auth_account, add_garment, db_session, storage, monkeypatch, failure
):
    uid = UUID(auth_account["id"])
    personal = add_garment(auth_account, "top")
    original_files = {
        p.relative_to(storage.root): p.read_bytes() for p in storage.root.rglob("*") if p.is_file()
    }
    target, name = (storage, "save") if failure == "file" else (db_session, "commit")
    original = getattr(target, name)
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == (3 if failure == "file" else 1):
            if failure == "lost_ack":
                original(*args, **kwargs)
            raise OSError("injected")
        return original(*args, **kwargs)

    monkeypatch.setattr(target, name, fail_once)
    with pytest.raises(OSError, match="injected"):
        provision_existing_user(db_session, storage, uid)
    assert all((storage.root / p).read_bytes() == data for p, data in original_files.items())
    assert db_session.get(Garment, UUID(personal)) is not None
    examples = list(db_session.scalars(select(Garment).where(Garment.user_id == uid, Garment.is_example)))
    assert len(examples) == (9 if failure == "lost_ack" else 0)
    assert db_session.get(User, uid).starter_wardrobe_provisioned == (failure == "lost_ack")
    assert list(db_session.scalars(select(StorageDeletionJob))) == []
    if failure != "lost_ack":
        assert {p.relative_to(storage.root) for p in storage.root.rglob("*") if p.is_file()} == set(
            original_files
        )


@pytest.mark.parametrize("failure", [False, True])
def test_scoped_pagination_recommendation_and_ownership(
    api_client, auth_account, account_factory, add_garment, db_session, storage, failure
):
    if failure:
        from backend.app.adapters.recommender import SafeRecommender
        from backend.app.deps import get_recommender

        class Broken:
            name = "broken"

            def recommend(self, *args):
                raise RuntimeError("injected provider failure")

        fallback = SafeRecommender(Broken(), timeout_sec=2)
        api_client.app.dependency_overrides[get_recommender] = lambda: fallback
    owner, other = auth_account, account_factory()
    personal = {add_garment(owner, c) for c in ("top", "bottom", "outerwear")}
    provision_existing_user(db_session, storage, UUID(owner["id"]))
    provision_existing_user(db_session, storage, UUID(other["id"]))
    h = owner["headers"]

    def page(scope, **params):
        return api_client.get("/api/v1/garments/page", headers=h, params={"wardrobe": scope, **params})

    first = page("example", limit=4).json()
    assert first["total"] == first["matched_count"] == 9 and first["has_more"]
    assert all(g["is_example"] for g in first["items"])
    assert page("personal").json()["total"] == 3
    assert page("personal", cursor=first["next_cursor"]).status_code == 422
    all_examples = api_client.get("/api/v1/garments", headers=h, params={"wardrobe": "example"}).json()
    example_ids = {g["garment_id"] for g in all_examples}
    gid = all_examples[0]["garment_id"]
    for suffix in ("", "/image?kind=thumbnail"):
        assert api_client.get(f"/api/v1/garments/{gid}{suffix}", headers=other["headers"]).status_code == 404
    for scope, allowed in (("personal", personal), ("example", example_ids)):
        response = api_client.post(
            "/api/v1/recommendations",
            headers=h,
            json={"wardrobe": scope, "temperature_c": 10, "required_garment_ids": [next(iter(allowed))]},
        )
        assert response.status_code == 201, response.text
        data = response.json()
        assert data["context"]["wardrobe"] == scope and data["outfits"]
        if failure:
            assert data["is_fallback"]
        for outfit in data["outfits"]:
            assert {v for v in outfit["items"].values() if v} <= allowed
        saved = api_client.get(f"/api/v1/recommendations/{data['recommendation_id']}", headers=h).json()
        assert saved["context"]["wardrobe"] == scope
    bad = api_client.post(
        "/api/v1/recommendations",
        headers=h,
        json={"wardrobe": "personal", "temperature_c": 10, "required_garment_ids": [gid]},
    )
    assert bad.status_code == 422
    assert api_client.get("/api/v1/garments", headers=h, params={"wardrobe": "invalid"}).status_code == 422
