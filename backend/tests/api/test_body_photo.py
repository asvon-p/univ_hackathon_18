from datetime import UTC, datetime
from io import BytesIO
from uuid import UUID, uuid4

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from backend.app.core.config import get_settings
from backend.app.db.models import AccountStorageDeletionJob, BodyPhotoDeletionJob, UserProfile
from backend.app.services.storage_cleanup import AccountStorageCleanupService, BodyPhotoCleanupService

PATH = "/api/v1/users/me/body-photo"


def picture(color="navy", fmt="PNG"):
    out = BytesIO()
    image = Image.new("RGB", (60, 90), color)
    exif = Image.Exif()
    exif[270] = "private metadata"
    image.save(out, format=fmt, exif=exif)
    return out.getvalue()


@pytest.fixture
def owner(api_client, auth_account):
    assert (
        api_client.patch(
            "/api/v1/profile", headers=auth_account["headers"], json={"consent_body_info": True}
        ).status_code
        == 200
    )
    return auth_account


def put(client, account, data=None):
    return client.put(
        PATH,
        headers=account["headers"],
        files={"file": ("untrusted-name.png", picture() if data is None else data, "image/png")},
    )


def profile(db, owner):
    db.expire_all()
    return db.get(UserProfile, UUID(owner["id"]))


def key(owner, photo_id):
    return f"{owner['id']}/body-photos/{photo_id}/original.jpg"


def test_upload_replace_delete_and_profile_contract(api_client, owner, db_session, storage):
    first = put(api_client, owner)
    assert first.status_code == 200
    stamp = first.json()["body_photo_updated_at"]
    assert stamp.endswith("Z")
    old_id = profile(db_session, owner).body_photo_id
    data = api_client.get(PATH, headers=owner["headers"])
    assert data.status_code == 200 and data.headers["content-type"] == "image/jpeg"
    assert data.headers["cache-control"] == "private, no-store"
    assert data.headers["x-content-type-options"] == "nosniff"
    with Image.open(BytesIO(data.content)) as img:
        assert img.size == (60, 90) and not img.getexif()
    for route in ("/api/v1/profile", "/api/v1/users/me/profile"):
        assert api_client.get(route, headers=owner["headers"]).json()["body_photo_updated_at"] == stamp
    second = put(api_client, owner, picture("red", "JPEG"))
    assert second.status_code == 200
    assert second.json()["body_photo_updated_at"] > stamp
    new_id = profile(db_session, owner).body_photo_id
    assert new_id != old_id and not storage.exists(key(owner, old_id))
    assert storage.exists(key(owner, new_id))
    assert api_client.delete(PATH, headers=owner["headers"]).status_code == 204
    assert api_client.delete(PATH, headers=owner["headers"]).status_code == 204
    assert api_client.get(PATH, headers=owner["headers"]).json()["code"] == "BODY_PHOTO_NOT_FOUND"
    assert profile(db_session, owner).body_photo_updated_at is None
    assert not storage.exists(key(owner, new_id))


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
def test_requires_authentication(api_client, method):
    kwargs = {"files": {"file": ("photo.png", picture(), "image/png")}} if method == "PUT" else {}
    assert api_client.request(method, PATH, **kwargs).status_code == 401


def test_isolation_and_no_raw_media_route(api_client, owner, account_factory, db_session, storage):
    other = account_factory()
    assert put(api_client, owner).status_code == 200
    old_id = profile(db_session, owner).body_photo_id
    assert api_client.get(PATH, headers=other["headers"]).status_code == 404
    assert api_client.get(PATH + "?user_id=" + owner["id"], headers=other["headers"]).status_code == 404
    assert api_client.delete(PATH, headers=other["headers"]).status_code == 204
    for account in (owner, other):
        assert api_client.get("/media/" + key(owner, old_id), headers=account["headers"]).status_code == 404
        assert (
            api_client.delete(
                f"/api/v1/users/{owner['id']}/body-photo", headers=account["headers"]
            ).status_code
            == 404
        )
    assert storage.exists(key(owner, old_id))
    assert api_client.get(PATH, headers=owner["headers"]).status_code == 200


def test_consent_required_and_revocation_removes_photo(api_client, auth_account, db_session, storage):
    assert put(api_client, auth_account).json()["code"] == "BODY_INFO_CONSENT_REQUIRED"
    assert not list(storage.root.rglob("*.jpg"))
    assert (
        api_client.patch(
            "/api/v1/profile", headers=auth_account["headers"], json={"consent_body_info": True}
        ).status_code
        == 200
    )
    assert put(api_client, auth_account).status_code == 200
    old_id = profile(db_session, auth_account).body_photo_id
    result = api_client.put(
        "/api/v1/users/me/profile", headers=auth_account["headers"], json={"consent_body_info": False}
    )
    assert result.status_code == 200 and result.json()["body_photo_updated_at"] is None
    assert not storage.exists(key(auth_account, old_id))
    assert api_client.get(PATH, headers=auth_account["headers"]).status_code == 404


@pytest.mark.parametrize("bad", [b"", b"not an image", picture(fmt="GIF")])
def test_invalid_images_preserve_existing_photo(api_client, owner, db_session, storage, bad):
    assert put(api_client, owner).status_code == 200
    old_id = profile(db_session, owner).body_photo_id
    assert put(api_client, owner, bad).status_code == 415
    assert profile(db_session, owner).body_photo_id == old_id
    assert storage.exists(key(owner, old_id))


def test_upload_limit(api_client, owner, monkeypatch):
    monkeypatch.setattr(get_settings(), "MAX_UPLOAD_MB", 1)
    assert put(api_client, owner, b"x" * (1024 * 1024 + 1)).status_code == 413


@pytest.mark.parametrize("operation,fail_on", [("upload", 1), ("upload", 2), ("delete", 1), ("revoke", 1)])
def test_commit_failure_preserves_old_photo(
    api_client, owner, db_session, storage, monkeypatch, operation, fail_on
):
    assert put(api_client, owner).status_code == 200
    old_id = profile(db_session, owner).body_photo_id
    before = storage.open(key(owner, old_id)).read()
    commit = db_session.commit
    calls = 0

    def fail_once():
        nonlocal calls
        calls += 1
        if calls == fail_on:
            raise SQLAlchemyError("simulated commit failure")
        commit()

    monkeypatch.setattr(db_session, "commit", fail_once)
    with pytest.raises(SQLAlchemyError):
        if operation == "upload":
            put(api_client, owner, picture("red"))
        elif operation == "delete":
            api_client.delete(PATH, headers=owner["headers"])
        else:
            api_client.patch("/api/v1/profile", headers=owner["headers"], json={"consent_body_info": False})
    assert profile(db_session, owner).body_photo_id == old_id
    assert api_client.get(PATH, headers=owner["headers"]).content == before
    assert len(list(storage.root.rglob("*.jpg"))) == 1


@pytest.mark.parametrize("operation", ["replace", "delete", "revoke"])
def test_cleanup_failure_is_retried_without_deleting_current_photo(
    api_client, owner, db_session, storage, monkeypatch, operation
):
    assert put(api_client, owner).status_code == 200
    old_id = profile(db_session, owner).body_photo_id
    delete = storage.delete_prefix
    monkeypatch.setattr(storage, "delete_prefix", lambda _: (_ for _ in ()).throw(OSError("busy")))
    if operation == "replace":
        assert put(api_client, owner, picture("red")).status_code == 200
    elif operation == "delete":
        assert api_client.delete(PATH, headers=owner["headers"]).status_code == 204
    else:
        assert (
            api_client.patch(
                "/api/v1/profile", headers=owner["headers"], json={"consent_body_info": False}
            ).status_code
            == 200
        )
    current_id = profile(db_session, owner).body_photo_id
    job = db_session.get(BodyPhotoDeletionJob, old_id)
    assert job is not None and job.attempts == 1
    assert storage.exists(key(owner, old_id))
    assert api_client.get(PATH, headers=owner["headers"]).status_code == (
        200 if operation == "replace" else 404
    )
    monkeypatch.setattr(storage, "delete_prefix", delete)
    assert BodyPhotoCleanupService(db_session, storage).process(old_id)
    assert not storage.exists(key(owner, old_id))
    if current_id:
        assert storage.exists(key(owner, current_id))


def test_partial_file_write_and_cleanup_failure_are_durable(
    api_client, owner, db_session, storage, monkeypatch
):
    save, delete = storage.save, storage.delete_prefix

    def fail_save(k, data):
        save(k, data)
        raise OSError("write failed after creating file")

    monkeypatch.setattr(storage, "save", fail_save)
    monkeypatch.setattr(storage, "delete_prefix", lambda _: (_ for _ in ()).throw(OSError("busy")))
    with pytest.raises(OSError):
        put(api_client, owner)
    job = db_session.scalar(select(BodyPhotoDeletionJob))
    assert job is not None and job.attempts == 1
    assert profile(db_session, owner).body_photo_id is None
    assert api_client.get(PATH, headers=owner["headers"]).status_code == 404
    monkeypatch.setattr(storage, "delete_prefix", delete)
    assert BodyPhotoCleanupService(db_session, storage).process(job.photo_id)
    assert not list(storage.root.rglob("*.jpg"))


def test_withdrawal_cleans_all_versions_and_revokes_access(
    api_client, owner, account_factory, db_session, storage, monkeypatch
):
    other = account_factory()
    assert put(api_client, owner).status_code == 200
    old_id = profile(db_session, owner).body_photo_id
    orphan = uuid4()
    storage.save(key(owner, orphan), picture())
    db_session.add(BodyPhotoDeletionJob(photo_id=orphan, user_id=UUID(owner["id"])))
    db_session.commit()
    delete = storage.delete_prefix
    monkeypatch.setattr(storage, "delete_prefix", lambda _: (_ for _ in ()).throw(OSError("busy")))
    assert api_client.delete("/api/v1/auth/me", headers=owner["headers"]).status_code == 204
    assert api_client.get(PATH, headers=owner["headers"]).status_code == 401
    assert put(api_client, owner).status_code == 401
    assert api_client.get(PATH, headers=other["headers"]).status_code == 404
    assert db_session.get(BodyPhotoDeletionJob, orphan) is None
    assert db_session.get(AccountStorageDeletionJob, UUID(owner["id"])) is not None
    assert storage.exists(key(owner, old_id))
    monkeypatch.setattr(storage, "delete_prefix", delete)
    assert AccountStorageCleanupService(db_session, storage).process(UUID(owner["id"]))
    assert not (storage.root / owner["id"]).exists()


def test_cleanup_never_removes_a_live_photo(api_client, owner, db_session, storage):
    assert put(api_client, owner).status_code == 200
    pid = profile(db_session, owner).body_photo_id
    db_session.add(
        BodyPhotoDeletionJob(photo_id=pid, user_id=UUID(owner["id"]), available_at=datetime.now(UTC))
    )
    db_session.commit()
    assert BodyPhotoCleanupService(db_session, storage).process(pid)
    assert storage.exists(key(owner, pid))
