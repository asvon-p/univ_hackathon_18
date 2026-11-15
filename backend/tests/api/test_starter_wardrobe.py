from collections import Counter

import pytest
from sqlalchemy import func, select

from backend.app.adapters.recommender import LocalRecommender, SafeRecommender
from backend.app.core.config import get_settings
from backend.app.db.models import AccountStorageDeletionJob, Garment, GarmentAttribute, User, UserProfile
from backend.app.deps import get_recommender
from backend.app.schemas.auth import SignupRequest
from backend.app.services.auth import AuthService
from backend.app.services.storage_cleanup import AccountStorageCleanupService
from backend.tests.api.test_recommendations import recommend


@pytest.fixture(autouse=True)
def enable_starters(api_client):
    settings = get_settings().model_copy(update={"STARTER_WARDROBE_ENABLED": True})
    api_client.app.dependency_overrides[get_settings] = lambda: settings
    return settings


def clothes(client, account):
    response = client.get("/api/v1/garments", headers=account["headers"])
    assert response.status_code == 200, response.text
    return response.json()


def test_signup_nine_private_ready_examples_and_local_recommendations(api_client, account_factory, storage):
    account = account_factory()
    garments = clothes(api_client, account)
    assert len(garments) == 9
    assert Counter(g["category"] for g in garments) == {"top": 3, "bottom": 3, "outerwear": 3}
    assert all(
        g["is_example"] and g["analysis_status"] == "done" and g["status"] == "available" for g in garments
    )
    assert all(g["wears_before_laundry"] == 1 and g["wear_count"] == 0 for g in garments)
    for g in garments:
        assert g["image_url"].startswith(f"/media/{account['id']}/{g['garment_id']}/")
        assert api_client.get(g["image_url"], headers=account["headers"]).status_code == 200
        thumbnail = api_client.get(
            f"/api/v1/garments/{g['garment_id']}/image?kind=thumbnail", headers=account["headers"]
        )
        assert thumbnail.status_code == 200 and thumbnail.headers["content-type"] == "image/webp"
    assert len([p for p in storage.root.rglob("*") if p.is_file()]) == 18
    local = SafeRecommender(LocalRecommender(), timeout_sec=10)
    api_client.app.dependency_overrides[get_recommender] = lambda: local
    response = recommend(api_client, account, temperature_c=10)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["provider"] == "local" and not body["is_fallback"] and body["outfits"]
    owned = {g["garment_id"] for g in garments}
    for outfit in body["outfits"]:
        assert outfit["items"]["top"] in owned and outfit["items"]["bottom"] in owned
        assert outfit["items"]["outerwear"] is None or outfit["items"]["outerwear"] in owned


def test_examples_edit_delete_upload_and_login_do_not_reseed(
    api_client, account_factory, add_garment, db_session
):
    account = account_factory()
    before = clothes(api_client, account)
    first = before[0]["garment_id"]
    edited = api_client.patch(
        f"/api/v1/garments/{first}", headers=account["headers"], json={"name": "연습용 수정"}
    )
    assert edited.status_code == 200 and edited.json()["is_example"]
    assert edited.json()["name"] == "연습용 수정"
    assert api_client.delete(f"/api/v1/garments/{first}", headers=account["headers"]).status_code == 204
    personal = add_garment(account, "top")
    db_session.expunge_all()
    login = api_client.post(
        "/api/v1/auth/login", json={"email": account["email"], "password": account["password"]}
    )
    account["headers"] = {"Authorization": "Bearer " + login.json()["access_token"]}
    after = clothes(api_client, account)
    assert len(after) == 9 and sum(g["is_example"] for g in after) == 8
    assert next(g for g in after if g["garment_id"] == personal)["is_example"] is False
    assert first not in {g["garment_id"] for g in after}
    repeat = api_client.post(
        "/api/v1/auth/signup",
        json={"email": account["email"], "password": account["password"], "nickname": "중복"},
    )
    assert repeat.status_code == 409 and clothes(api_client, account) == after


def test_accounts_do_not_share_ids_images_or_mutations(api_client, account_factory, storage):
    owner, other = account_factory(), account_factory()
    one, two = clothes(api_client, owner), clothes(api_client, other)
    assert not ({g["garment_id"] for g in one} & {g["garment_id"] for g in two})
    target = one[0]
    path = f"/api/v1/garments/{target['garment_id']}"
    for method, url, kwargs in [
        ("GET", path, {}),
        ("PATCH", path, {"json": {"name": "침범"}}),
        ("DELETE", path, {}),
        ("GET", path + "/image?kind=thumbnail", {}),
        ("GET", target["image_url"], {}),
    ]:
        assert api_client.request(method, url, headers=other["headers"], **kwargs).status_code == 404
    assert api_client.delete("/api/v1/auth/me", headers=owner["headers"]).status_code == 204
    assert not (storage.root / owner["id"]).exists()
    assert clothes(api_client, other) == two
    assert len([p for p in (storage.root / other["id"]).rglob("*") if p.is_file()]) == 18


def signup_service(db, storage, settings):
    body = SignupRequest(email="failure@example.com", password="SignupTest2026!", nickname="가입 검증")
    return AuthService(db, settings, storage), body


@pytest.mark.parametrize("failure", ["file", "commit"])
def test_signup_failure_rolls_back_account_attributes_and_files(
    db_session, storage, enable_starters, monkeypatch, failure
):
    service, body = signup_service(db_session, storage, enable_starters)
    original = storage.save if failure == "file" else db_session.commit
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == (5 if failure == "file" else 1):
            raise OSError("injected failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(
        storage if failure == "file" else db_session, "save" if failure == "file" else "commit", fail_once
    )
    with pytest.raises(OSError, match="injected"):
        service.signup(body)
    for model in (User, UserProfile, Garment, GarmentAttribute, AccountStorageDeletionJob):
        assert db_session.scalar(select(func.count()).select_from(model)) == 0
    assert not any(p.is_file() for p in storage.root.rglob("*"))


def test_failed_cleanup_has_durable_retry_job(db_session, storage, enable_starters, monkeypatch):
    service, body = signup_service(db_session, storage, enable_starters)
    commit, remove = db_session.commit, storage.delete_prefix
    calls = 0

    def fail_first_commit():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("commit failed")
        return commit()

    def fail_remove(*args):
        raise OSError("file locked")

    monkeypatch.setattr(db_session, "commit", fail_first_commit)
    monkeypatch.setattr(storage, "delete_prefix", fail_remove)
    with pytest.raises(OSError, match="commit failed"):
        service.signup(body)
    job = db_session.scalars(select(AccountStorageDeletionJob)).one()
    assert job.attempts == 1 and db_session.scalar(select(func.count()).select_from(User)) == 0
    monkeypatch.setattr(storage, "delete_prefix", remove)
    assert AccountStorageCleanupService(db_session, storage).process(job.user_id)
    assert not any(p.is_file() for p in storage.root.rglob("*"))


def test_lost_commit_acknowledgement_never_removes_committed_images(
    db_session, storage, enable_starters, monkeypatch
):
    service, body = signup_service(db_session, storage, enable_starters)
    original = db_session.commit

    def committed_then_error():
        original()
        raise OSError("commit acknowledgement lost")

    monkeypatch.setattr(db_session, "commit", committed_then_error)
    with pytest.raises(OSError, match="acknowledgement lost"):
        service.signup(body)
    monkeypatch.setattr(db_session, "commit", original)
    db_session.expunge_all()
    user = db_session.scalars(select(User)).one()
    assert user.email == "failure@example.com"
    assert db_session.scalar(select(func.count()).select_from(Garment)) == 9
    assert len([p for p in (storage.root / str(user.id)).rglob("*") if p.is_file()]) == 18
    assert db_session.scalar(select(func.count()).select_from(AccountStorageDeletionJob)) == 0


def test_feature_disabled_preserves_empty_account_behavior(api_client, account_factory, enable_starters):
    api_client.app.dependency_overrides[get_settings] = lambda: enable_starters.model_copy(
        update={"STARTER_WARDROBE_ENABLED": False}
    )
    assert clothes(api_client, account_factory()) == []
