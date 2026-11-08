from io import BytesIO
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from backend.app.db.models import Garment, StorageDeletionJob, User, UserProfile
from backend.app.services.account import AccountService
from backend.app.services.garment import GarmentService
from backend.app.services.storage_cleanup import AccountStorageCleanupService, StorageCleanupService
from scripts.demo_assets import CATALOG
from scripts.seed_demo import load_credentials, seed_demo

EMAIL = "seed@example.com"
PASSWORD = "seed-test-password-123"


def test_seed_is_repeatable_preserves_edits_and_serves_images(api_client, db_session, storage):
    first = seed_demo(db_session, storage, EMAIL, PASSWORD)
    assert first["created_garments"] == 12 and first["created_files"] == 24
    user_id = UUID(first["user_id"])
    top_id = uuid5(user_id, "white-tee")
    db_session.get(Garment, top_id).status = "laundry"
    db_session.get(UserProfile, user_id).preferred_styles = ["street"]
    db_session.commit()
    second = seed_demo(db_session, storage, EMAIL, PASSWORD)
    assert second["created_garments"] == second["created_files"] == 0
    assert db_session.scalar(select(func.count()).select_from(User)) == 1
    assert db_session.scalar(select(func.count()).select_from(Garment)) == 12
    assert db_session.get(Garment, top_id).status == "laundry"
    assert db_session.get(UserProfile, user_id).preferred_styles == ["street"]
    login = api_client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert login.status_code == 200
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    garments = api_client.get("/api/v1/garments", headers=headers).json()
    assert {g["category"] for g in garments} == {"top", "bottom", "shoes", "outerwear"}
    for garment in garments:
        assert garment["analysis_status"] == "done" and garment["model_version"] == "demo-seed-v1"
        assert api_client.get(garment["image_url"], headers=headers).status_code == 200
        cutout = api_client.get(garment["cutout_url"], headers=headers)
        assert cutout.status_code == 200
        with Image.open(BytesIO(cutout.content)) as image:
            assert image.mode == "RGBA" and image.getchannel("A").getextrema() == (0, 255)
    response = api_client.post("/api/v1/recommendations", headers=headers, json={"temperature_c": 5})
    assert response.status_code == 201
    assert all(g["status"] == "available" for g in response.json()["garments"].values())


def test_seed_repairs_only_missing_image_files(db_session, storage):
    result = seed_demo(db_session, storage, EMAIL, PASSWORD)
    garment_id = uuid5(UUID(result["user_id"]), CATALOG[0].key)
    garment = db_session.get(Garment, garment_id)
    original = storage.open(garment.original_path).read()
    from pathlib import Path

    Path(storage.local_path(garment.cutout_path)).unlink()
    repaired = seed_demo(db_session, storage, EMAIL, PASSWORD)
    assert repaired["created_garments"] == 0 and repaired["created_files"] == 1
    assert storage.open(garment.original_path).read() == original
    assert storage.exists(garment.cutout_path)


def test_seed_refuses_existing_non_demo_account(db_session, storage):
    ordinary_id = uuid4()
    db_session.add(User(id=ordinary_id, email=EMAIL, password_hash="unchanged", nickname="existing"))
    db_session.commit()
    with pytest.raises(RuntimeError, match="일반 계정"):
        seed_demo(db_session, storage, EMAIL, PASSWORD)
    assert db_session.get(User, ordinary_id).password_hash == "unchanged"
    assert db_session.scalar(select(func.count()).select_from(Garment)) == 0
    assert list(storage.root.rglob("*.jpg")) == []


def test_seed_does_not_reset_password_or_reactivate_user(db_session, storage):
    first = seed_demo(db_session, storage, EMAIL, PASSWORD)
    with pytest.raises(RuntimeError, match="상태/비밀번호"):
        seed_demo(db_session, storage, EMAIL, "a-different-password")
    user = db_session.get(User, UUID(first["user_id"]))
    user.is_active = False
    db_session.commit()
    with pytest.raises(RuntimeError, match="상태/비밀번호"):
        seed_demo(db_session, storage, EMAIL, PASSWORD)
    assert db_session.get(User, user.id).is_active is False


def test_seed_commit_failure_removes_only_files_it_created(db_session, storage, monkeypatch):
    user_id = uuid5(NAMESPACE_URL, f"fitjang:demo:v1:{EMAIL}")
    monkeypatch.setattr("scripts.seed_demo.uuid4", lambda: user_id)
    key = f"{user_id}/{uuid5(user_id, CATALOG[0].key)}/original.jpg"
    storage.save(key, b"pre-existing file must survive")

    def fail():
        raise SQLAlchemyError("commit failed")

    monkeypatch.setattr(db_session, "commit", fail)
    with pytest.raises(SQLAlchemyError):
        seed_demo(db_session, storage, EMAIL, PASSWORD)
    assert db_session.scalar(select(func.count()).select_from(User)) == 0
    assert db_session.scalar(select(func.count()).select_from(Garment)) == 0
    assert storage.open(key).read() == b"pre-existing file must survive"
    assert len(list(storage.root.rglob("*.jpg"))) == 1 and list(storage.root.rglob("*.png")) == []


def test_generated_credentials_are_stable_and_never_printed(tmp_path, capsys):
    path = tmp_path / ".env.demo"
    email, password = load_credentials(path)
    assert len(password) >= 24 and email == "demo@example.com"
    assert load_credentials(path) == (email, password)
    assert capsys.readouterr().out == ""


def test_seed_recreation_cancels_pending_cleanup_before_reusing_uuid(db_session, storage, monkeypatch):
    seeded = seed_demo(db_session, storage, EMAIL, PASSWORD)
    user_id = UUID(seeded["user_id"])
    garment_id = uuid5(user_id, CATALOG[0].key)

    def fail(prefix):
        raise PermissionError("busy")

    with monkeypatch.context() as patch:
        patch.setattr(storage, "delete_prefix", fail)
        GarmentService(db_session, storage).delete(user_id, garment_id)
    assert db_session.get(StorageDeletionJob, garment_id) is not None
    assert seed_demo(db_session, storage, EMAIL, PASSWORD)["created_garments"] == 1
    assert db_session.get(StorageDeletionJob, garment_id) is None
    assert StorageCleanupService(db_session, storage).drain()["selected"] == 0
    garment = db_session.get(Garment, garment_id)
    assert storage.exists(garment.original_path) and storage.exists(garment.cutout_path)


def test_deleted_demo_account_is_recreated_with_new_identity_and_old_token_stays_invalid(
    api_client, db_session, storage, monkeypatch
):
    old = seed_demo(db_session, storage, EMAIL, PASSWORD)
    login = api_client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}

    def fail(prefix):
        raise PermissionError("old account files busy")

    with monkeypatch.context() as patch:
        patch.setattr(storage, "delete_prefix", fail)
        AccountService(db_session, storage).delete(UUID(old["user_id"]))
    new = seed_demo(db_session, storage, EMAIL, PASSWORD)
    assert new["user_id"] != old["user_id"] and new["created_garments"] == 12
    assert api_client.get("/api/v1/auth/me", headers=headers).status_code == 401
    assert AccountStorageCleanupService(db_session, storage).process(UUID(old["user_id"]))
    assert not (storage.root / old["user_id"]).exists()
    assert len(list((storage.root / new["user_id"]).rglob("*.png"))) == 12
