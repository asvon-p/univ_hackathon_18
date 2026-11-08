import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from io import BytesIO
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.app.core.errors import AppError
from backend.app.db.models import BodyPhotoDeletionJob, UserProfile
from backend.app.services.body_photo import BodyPhotoService
from backend.app.storage.local import LocalStorage
from backend.tests.api.test_body_photo import PATH, picture
from backend.tests.e2e.test_reanalysis import register


@pytest.mark.parametrize("live_api", ["local"], indirect=True)
def test_real_http_body_photo_and_withdrawal(live_api):
    client, _, runtime = live_api
    uid, headers = register(client)
    _, other = register(client)
    assert (
        client.patch("/api/v1/profile", headers=headers, json={"consent_body_info": True}).status_code == 200
    )
    uploaded = client.put(PATH, headers=headers, files={"file": ("body.png", picture(), "image/png")})
    assert uploaded.status_code == 200
    assert client.get(PATH, headers=other).status_code == 404
    assert client.get(PATH, headers=headers).status_code == 200
    engine = create_engine(runtime["database_url"])
    try:
        with Session(engine) as db:
            stored = db.get(UserProfile, uid)
            assert stored.body_photo_id is not None and stored.body_photo_updated_at is not None
        assert client.delete("/api/v1/auth/me", headers=headers).status_code == 204
        assert client.get(PATH, headers=headers).status_code == 401
        assert not (runtime["storage_dir"] / str(uid)).exists()
    finally:
        engine.dispose()


@pytest.mark.parametrize("live_api", ["mock"], indirect=True)
def test_withdrawal_between_staging_and_publication_cannot_recreate_files(live_api):
    client, _, runtime = live_api
    uid, headers = register(client)
    assert (
        client.patch("/api/v1/profile", headers=headers, json={"consent_body_info": True}).status_code == 200
    )
    staged, resume = Event(), Event()
    engine = create_engine(runtime["database_url"])

    def upload():
        with Session(engine, expire_on_commit=False) as db:
            commit = db.commit
            count = 0

            def pause_after_staging():
                nonlocal count
                commit()
                count += 1
                if count == 1:
                    staged.set()
                    assert resume.wait(10)

            db.commit = pause_after_staging
            return BodyPhotoService(db, LocalStorage(runtime["storage_dir"])).upload(
                uid, BytesIO(picture()), 10 * 1024 * 1024
            )

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(upload)
            try:
                assert staged.wait(10)
                assert client.delete("/api/v1/auth/me", headers=headers).status_code == 204
            finally:
                resume.set()
            with pytest.raises(AppError) as denied:
                future.result(timeout=10)
            assert denied.value.status == 401
        assert not (runtime["storage_dir"] / str(uid)).exists()
        with Session(engine) as db:
            assert db.scalar(select(BodyPhotoDeletionJob).where(BodyPhotoDeletionJob.user_id == uid)) is None
    finally:
        engine.dispose()


@pytest.mark.parametrize("live_api", ["mock"], indirect=True)
def test_background_worker_recovers_abandoned_upload(live_api):
    client, _, runtime = live_api
    uid, headers = register(client)
    photo_id = uuid4()
    storage = LocalStorage(runtime["storage_dir"])
    key = f"{uid}/body-photos/{photo_id}/original.jpg"
    storage.save(key, picture())
    engine = create_engine(runtime["database_url"])
    try:
        with Session(engine) as db:
            db.add(BodyPhotoDeletionJob(photo_id=photo_id, user_id=uid, available_at=datetime.now(UTC)))
            db.commit()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with Session(engine) as db:
                if db.get(BodyPhotoDeletionJob, photo_id) is None:
                    break
            time.sleep(0.1)
        else:
            pytest.fail("Body photo cleanup worker did not recover abandoned upload")
        assert not storage.exists(key)
        assert client.get(PATH, headers=headers).status_code == 404
    finally:
        engine.dispose()
