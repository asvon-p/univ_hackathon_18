"""DB 커밋 후 요청 프로세스가 정리를 수행하지 못한 상황을 실제 서버 worker가 복구합니다."""

import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.db.models import Garment, StorageDeletionJob
from scripts.demo_assets import CATALOG, garment_images


@pytest.mark.parametrize("live_api", ["mock"], indirect=True)
def test_live_worker_cleans_durable_job_without_another_delete_request(live_api):
    client, _, context = live_api
    email, password = f"cleanup-{uuid4().hex}@example.com", "e2e-only-password-123"
    signup = client.post(
        "/api/v1/auth/signup",
        json={
            "email": email,
            "password": password,
            "nickname": "cleanup",
            "invite_code": "e2e-invite-only",
        },
    )
    assert signup.status_code == 201
    login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    upload = client.post(
        "/api/v1/garments",
        headers=headers,
        files={
            "file": ("top.jpg", garment_images(CATALOG[0])[0], "image/jpeg"),
        },
    )
    assert upload.status_code == 201
    garment = upload.json()
    garment_id = UUID(garment["garment_id"])
    original = context["storage_dir"] / garment["image_url"].removeprefix("/media/")
    engine = create_engine(context["database_url"])
    try:
        with Session(engine) as db:
            row = db.get(Garment, garment_id)
            db.add(
                StorageDeletionJob(
                    garment_id=garment_id,
                    user_id=row.user_id,
                    available_at=datetime.now(UTC) + timedelta(seconds=1),
                )
            )
            db.delete(row)
            db.commit()  # 여기서 프로세스가 끝났다고 가정: 파일은 아직 그대로입니다.
        assert original.exists()
        assert client.get(garment["image_url"], headers=headers).status_code == 404
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with Session(engine) as db:
                pending = db.get(StorageDeletionJob, garment_id)
            if pending is None:
                break
            time.sleep(0.1)
        else:
            pytest.fail("Server worker did not finish the durable cleanup job")
        assert not original.exists()
        assert client.get(f"/api/v1/garments/{garment_id}", headers=headers).status_code == 404
    finally:
        engine.dispose()
