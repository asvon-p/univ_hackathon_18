from datetime import UTC, datetime, timedelta
from io import BytesIO
from uuid import UUID

import pytest

from backend.app.db.models import MediaSession, TryOnDeletionJob, TryOnResult, User, UserProfile
from backend.app.services import media_session
from backend.app.services.storage_cleanup import TryOnCleanupService
from backend.app.services.tryon_results import TryOnResultService
from backend.tests.api.test_body_photo import PATH, picture

RESULTS = "/api/v1/tryon-results"


def prepare(client, account, db, policy="persistent"):
    if policy == "session":
        db.get(User, UUID(account["id"])).photo_policy = policy
        db.commit()
        login = client.post(
            "/api/v1/auth/login", json={"email": account["email"], "password": account["password"]}
        )
        account["headers"] = {"Authorization": "Bearer " + login.json()["access_token"]}
    h = account["headers"]
    assert client.patch("/api/v1/profile", headers=h, json={"consent_body_info": True}).status_code == 200
    assert (
        client.put(PATH, headers=h, files={"file": ("body.png", picture(), "image/png")}).status_code == 200
    )
    g = client.post("/api/v1/garments", headers=h, files={"file": ("top.png", picture(), "image/png")}).json()
    gid = g["garment_id"]
    assert client.patch(f"/api/v1/garments/{gid}", headers=h, json={"category": "top"}).status_code == 200
    response = client.post(RESULTS, headers=h, json={"items": {"top": gid}})
    assert response.status_code == 201, response.text
    return response.json()["id"], gid


def finish(client, h, rid):
    return client.put(
        f"{RESULTS}/{rid}/image", headers=h, files={"file": ("result.png", picture(), "image/png")}
    )


@pytest.mark.parametrize("policy", ["persistent", "session"])
def test_edited_garment_invalidates_reuse_without_deleting_saved_result(
    api_client, auth_account, db_session, policy
):
    rid, gid = prepare(api_client, auth_account, db_session, policy)
    h = auth_account["headers"]
    assert finish(api_client, h, rid).json()["inputs_current"] is True
    before = api_client.get(f"{RESULTS}/{rid}/image", headers=h).content
    assert api_client.patch(f"/api/v1/garments/{gid}", headers=h, json={"name": "updated"}).status_code == 200
    assert api_client.get(f"{RESULTS}/{rid}", headers=h).json()["inputs_current"] is False
    assert api_client.get(f"{RESULTS}/{rid}/image", headers=h).content == before
    assert len(api_client.get(RESULTS, headers=h).json()) == 1


@pytest.mark.parametrize("policy", ["persistent", "session"])
@pytest.mark.parametrize("version", ["open-shirt-v4", "open-shirt-v4.1"])
def test_layering_metadata_warnings_and_ownership(
    api_client, auth_account, db_session, account_factory, policy, version
):
    rid, gid = prepare(api_client, auth_account, db_session, policy)
    h = auth_account["headers"]
    assert api_client.post(f"{RESULTS}/{rid}/cancel", headers=h).status_code == 204
    outer = api_client.post(
        "/api/v1/garments", headers=h, files={"file": ("outer.png", picture(), "image/png")}
    ).json()["garment_id"]
    assert (
        api_client.patch(f"/api/v1/garments/{outer}", headers=h, json={"category": "outerwear"}).status_code
        == 200
    )
    rid = api_client.post(RESULTS, headers=h, json={"items": {"top": gid, "outerwear": outer}}).json()["id"]
    data = {"render_version": version, "layering_status": "skipped"}
    other = account_factory()["headers"]
    assert (
        api_client.put(
            f"{RESULTS}/{rid}/image",
            headers=other,
            data=data,
            files={"file": ("result.png", picture(), "image/png")},
        ).status_code
        == 404
    )
    result = api_client.put(
        f"{RESULTS}/{rid}/image", headers=h, data=data, files={"file": ("result.png", picture(), "image/png")}
    )
    assert result.status_code == 200 and result.json()["warnings"]
    saved = api_client.get(f"{RESULTS}/{rid}", headers=h).json()
    assert saved["render_version"] == version and saved["layering_status"] == "skipped"
    row = db_session.get(TryOnResult, UUID(rid))
    if policy == "session":
        assert row is None
    else:
        assert row.render_version == version and row.layering_status == "skipped"


def test_persistent_result_survives_memory_reset_and_photo_removal(
    api_client, auth_account, db_session, storage, account_factory
):
    rid, gid = prepare(api_client, auth_account, db_session)
    h = auth_account["headers"]
    assert finish(api_client, h, rid).status_code == 200
    media_session.MEDIA.clear()
    # Body photo is independent of the owner's explicitly retained completed result.
    assert api_client.delete(PATH, headers=h).status_code == 204
    result = api_client.get(f"{RESULTS}/{rid}/image", headers=h)
    assert result.status_code == 200 and result.headers["cache-control"] == "private, no-store"
    assert len(api_client.get(RESULTS, headers=h).json()) == 1
    other = account_factory()["headers"]
    for method, suffix in [("GET", ""), ("GET", "/image"), ("DELETE", "")]:
        assert api_client.request(method, f"{RESULTS}/{rid}{suffix}", headers=other).status_code == 404
    assert api_client.get(f"{RESULTS}/{rid}/image").status_code == 401
    assert api_client.delete("/api/v1/auth/me", headers=h).status_code == 204
    assert not (storage.root / auth_account["id"]).exists()
    assert db_session.get(TryOnResult, UUID(rid)) is None


@pytest.mark.parametrize("fail_once", [False, True])
def test_full_photo_purge_removes_history_and_files(
    api_client, auth_account, db_session, storage, monkeypatch, fail_once
):
    rid, gid = prepare(api_client, auth_account, db_session)
    h = auth_account["headers"]
    assert finish(api_client, h, rid).status_code == 200
    owner_dir = storage.root / auth_account["id"]
    media = [path for subdir in ("body-photos", "tryon") for path in (owner_dir / subdir).rglob("*.jpg")]
    assert len(media) == 2
    if fail_once:
        delete_prefix = storage.delete_prefix

        def fail_body_photo(prefix):
            if "/body-photos/" in prefix:
                raise OSError("simulated disk error")
            delete_prefix(prefix)

        monkeypatch.setattr(storage, "delete_prefix", fail_body_photo)
    deletion = api_client.delete(PATH, headers=h, params={"include_tryons": True})
    assert deletion.status_code == (503 if fail_once else 204)
    assert api_client.get(PATH, headers=h).status_code == 404
    assert api_client.get(f"{RESULTS}/{rid}/image", headers=h).status_code == 404
    assert api_client.get(RESULTS, headers=h).json() == []
    assert api_client.get(f"/api/v1/garments/{gid}", headers=h).status_code == 200
    if fail_once:
        assert any(path.exists() for path in media)
        monkeypatch.setattr(storage, "delete_prefix", delete_prefix)
        assert api_client.delete(PATH, headers=h, params={"include_tryons": True}).status_code == 204
    assert not any(path.exists() for path in media)
    assert db_session.get(TryOnResult, UUID(rid)) is None


def test_customer_images_never_reach_database_or_disk_and_logout_retires_token(
    api_client, auth_account, db_session, storage
):
    rid, gid = prepare(api_client, auth_account, db_session, "session")
    h = auth_account["headers"]
    assert finish(api_client, h, rid).status_code == 200
    assert db_session.get(TryOnResult, UUID(rid)) is None
    assert db_session.get(UserProfile, UUID(auth_account["id"])).body_photo_id is None
    assert not (storage.root / auth_account["id"] / "body-photos").exists()
    assert not (storage.root / auth_account["id"] / "tryon").exists()
    assert api_client.get(f"{RESULTS}/{rid}/image", headers=h).status_code == 200
    assert api_client.post("/api/v1/auth/logout", headers=h).status_code == 204
    assert api_client.get(f"{RESULTS}/{rid}/image", headers=h).status_code == 401
    assert finish(api_client, h, rid).status_code == 401
    token = api_client.post(
        "/api/v1/auth/login", json={k: auth_account[k] for k in ("email", "password")}
    ).json()["access_token"]
    fresh = {"Authorization": "Bearer " + token}
    assert api_client.get(PATH, headers=fresh).status_code == 404
    assert api_client.get(RESULTS, headers=fresh).json() == []
    assert api_client.get(f"/api/v1/garments/{gid}", headers=fresh).status_code == 200


def test_customer_idle_expiry_and_restart_erase_memory_not_clothes(
    api_client, auth_account, db_session, storage
):
    rid, gid = prepare(api_client, auth_account, db_session, "session")
    h = auth_account["headers"]
    assert finish(api_client, h, rid).status_code == 200
    key = media_session.token_hash(h["Authorization"].split()[1])
    assert api_client.post("/api/v1/auth/activity", headers=h).status_code == 204
    row = db_session.get(MediaSession, key)
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    media_session.expire_sessions(db_session)
    assert key not in media_session.MEDIA
    assert db_session.get(MediaSession, key) is None
    assert api_client.get(PATH, headers=h).status_code == 401


@pytest.mark.parametrize("failure", ["file", "commit"])
def test_failed_result_publication_does_not_leave_live_image(
    api_client, auth_account, db_session, storage, monkeypatch, failure
):
    rid, _ = prepare(api_client, auth_account, db_session)
    svc = TryOnResultService(db_session, storage)
    original = db_session.commit
    calls = 0

    def commit():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("commit failed")
        original()

    if failure == "commit":
        monkeypatch.setattr(db_session, "commit", commit)
    else:
        save = storage.save

        def fail_stage(key, data):
            if key.endswith("/top.jpg"):
                raise OSError("stage disk failed")
            return save(key, data)

        monkeypatch.setattr(storage, "save", fail_stage)
    with pytest.raises((RuntimeError, OSError)):
        svc.finish(
            UUID(auth_account["id"]),
            UUID(rid),
            BytesIO(picture()),
            1024 * 1024,
            stages={"top": BytesIO(picture())},
        )
    assert db_session.get(TryOnResult, UUID(rid)).image_id is None
    assert not list((storage.root / auth_account["id"] / "tryon").rglob("*.jpg"))


def test_failed_file_deletion_retries_after_db_commit(
    api_client, auth_account, db_session, storage, monkeypatch
):
    rid, _ = prepare(api_client, auth_account, db_session)
    h = auth_account["headers"]
    assert finish(api_client, h, rid).status_code == 200
    image_id = db_session.get(TryOnResult, UUID(rid)).image_id
    with monkeypatch.context() as patch:
        patch.setattr(storage, "delete_prefix", lambda *args: (_ for _ in ()).throw(OSError("locked")))
        assert api_client.delete(f"{RESULTS}/{rid}", headers=h).status_code == 204
    assert api_client.get(f"{RESULTS}/{rid}/image", headers=h).status_code == 404
    assert db_session.get(TryOnDeletionJob, image_id) is not None
    assert TryOnCleanupService(db_session, storage).process(image_id)
    assert not (storage.root / auth_account["id"] / "tryon" / str(image_id)).exists()


def test_customer_sessions_are_distinct_and_worker_observes_remote_logout(
    api_client, auth_account, db_session, storage
):
    rid, gid = prepare(api_client, auth_account, db_session, "session")
    h = auth_account["headers"]
    assert finish(api_client, h, rid).status_code == 200
    key = media_session.token_hash(h["Authorization"].split()[1])
    token = api_client.post(
        "/api/v1/auth/login", json={k: auth_account[k] for k in ("email", "password")}
    ).json()["access_token"]
    fresh = {"Authorization": "Bearer " + token}
    assert media_session.token_hash(token) != key
    assert api_client.get(PATH, headers=fresh).status_code == 404
    assert api_client.get(f"{RESULTS}/{rid}/image", headers=fresh).status_code == 404
    assert api_client.get(f"/api/v1/garments/{gid}", headers=fresh).status_code == 200
    # A different backend process revoked the session in PostgreSQL.
    db_session.delete(db_session.get(MediaSession, key))
    db_session.commit()
    media_session.expire_sessions(db_session)
    assert key not in media_session.MEDIA


def test_customer_restart_drops_images_but_preserves_valid_login_and_clothes(
    api_client, auth_account, db_session, storage
):
    rid, gid = prepare(api_client, auth_account, db_session, "session")
    h = auth_account["headers"]
    assert finish(api_client, h, rid).status_code == 200
    media_session.MEDIA.clear()
    assert api_client.get(PATH, headers=h).status_code == 404
    assert api_client.get(f"{RESULTS}/{rid}", headers=h).status_code == 404
    assert api_client.get(f"/api/v1/garments/{gid}", headers=h).status_code == 200


def test_consent_withdrawal_retires_persistent_results(api_client, auth_account, db_session, storage):
    rid, _ = prepare(api_client, auth_account, db_session)
    h = auth_account["headers"]
    assert finish(api_client, h, rid).status_code == 200
    response = api_client.patch("/api/v1/profile", headers=h, json={"consent_body_info": False})
    assert response.status_code == 200
    assert api_client.get(f"{RESULTS}/{rid}/image", headers=h).status_code == 404
    job = db_session.query(TryOnDeletionJob).one()
    assert TryOnCleanupService(db_session, storage).drain()["completed"] == 1, (
        job.available_at,
        datetime.now(UTC),
    )
    assert not list((storage.root / auth_account["id"] / "tryon").rglob("*.jpg"))
