import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from threading import Event
from uuid import UUID

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from backend.app.adapters.vision import SafeVision
from backend.app.core.errors import AppError
from backend.app.db.models import (
    AccountStorageDeletionJob,
    FeedbackEvent,
    Garment,
    Recommendation,
    User,
    UserProfile,
)
from backend.app.services.garment import GarmentService
from backend.app.storage.local import LocalStorage
from backend.tests.api.test_reanalysis import NewVision
from backend.tests.e2e.test_reanalysis import register, upload
from scripts.demo_assets import CATALOG, garment_images


def wait_for_cleanup(engine, user_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with Session(engine) as db:
            pending = db.get(AccountStorageDeletionJob, user_id)
        if pending is None:
            return
        time.sleep(0.1)
    pytest.fail("Account cleanup worker did not finish")


@pytest.mark.parametrize("live_api", ["mock", "local"], indirect=True)
def test_live_account_deletion_removes_data_files_and_anonymizes_feedback(live_api):
    client, _, context = live_api
    user_id, headers = register(client)
    _, other_headers = register(client)
    for category in ("top", "bottom", "shoes"):
        garment = upload(client, headers)
        assert (
            client.patch(
                f"/api/v1/garments/{garment['garment_id']}", headers=headers, json={"category": category}
            ).status_code
            == 200
        )
    rec = client.post("/api/v1/recommendations", headers=headers, json={"temperature_c": 5})
    assert rec.status_code == 201
    body = rec.json()
    feedback = client.post(
        f"/api/v1/recommendations/{body['recommendation_id']}/feedback",
        headers=headers,
        json={
            "recommendation_id": body["recommendation_id"],
            "outfit_id": body["outfits"][0]["outfit_id"],
            "accepted": False,
            "reason": "personal data to erase",
        },
    )
    assert feedback.status_code == 201
    engine = create_engine(context["database_url"])
    try:
        assert client.delete("/api/v1/auth/me", headers=headers).status_code == 204
        assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
        assert client.get(garment["image_url"], headers=headers).status_code == 401
        assert client.get("/api/v1/auth/me", headers=other_headers).status_code == 200
        wait_for_cleanup(engine, user_id)
        assert not (context["storage_dir"] / str(user_id)).exists()
        with Session(engine) as db:
            assert db.get(User, user_id) is None and db.get(UserProfile, user_id) is None
            assert db.scalar(select(func.count()).select_from(Garment)) == 0
            assert db.scalar(select(func.count()).select_from(Recommendation)) == 0
            event = db.get(FeedbackEvent, UUID(feedback.json()["feedback_id"]))
            assert event.user_id is event.recommendation_id is event.outfit_id is event.reason is None
            assert event.accepted is False
    finally:
        engine.dispose()


@pytest.mark.parametrize("live_api", ["mock"], indirect=True)
def test_server_worker_completes_account_cleanup_after_request_process_stops(live_api):
    client, _, context = live_api
    user_id, headers = register(client)
    garment = upload(client, headers)
    engine = create_engine(context["database_url"])
    try:
        with Session(engine) as db:
            db.add(
                AccountStorageDeletionJob(
                    user_id=user_id, available_at=datetime.now(UTC) + timedelta(seconds=1)
                )
            )
            db.delete(db.get(User, user_id))
            db.commit()  # 즉시 파일 정리를 수행하기 전에 프로세스가 종료된 상황
        assert (context["storage_dir"] / garment["image_url"].removeprefix("/media/")).exists()
        assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
        wait_for_cleanup(engine, user_id)
        assert not (context["storage_dir"] / str(user_id)).exists()
    finally:
        engine.dispose()


@pytest.mark.parametrize("live_api", ["mock"], indirect=True)
@pytest.mark.parametrize("operation", ["upload", "reanalyze"])
def test_inflight_vision_cannot_recreate_files_after_withdrawal(live_api, operation):
    client, _, context = live_api
    user_id, headers = register(client)
    garment = upload(client, headers) if operation == "reanalyze" else None
    engine = create_engine(context["database_url"])
    started, finish, delete_started = Event(), Event(), Event()
    workspace = []

    class Slow:
        name = "slow-withdrawal"

        def extract(self, image_path, garment_id):
            workspace.append(Path(image_path).parents[2])
            started.set()
            assert finish.wait(10)
            return NewVision().extract(image_path, garment_id)

    def analyze():
        with Session(engine, expire_on_commit=False) as db:
            service = GarmentService(db, LocalStorage(context["storage_dir"]))
            adapter = SafeVision(Slow(), 12)
            if operation == "upload":
                return service.upload(
                    user_id, BytesIO(garment_images(CATALOG[0])[0]), adapter, 10 * 1024 * 1024
                )
            return service.reanalyze(user_id, UUID(garment["garment_id"]), adapter, 10 * 1024 * 1024)

    def withdraw():
        delete_started.set()
        return client.delete("/api/v1/auth/me", headers=headers)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            analysis = executor.submit(analyze)
            try:
                assert started.wait(5)
                deletion = executor.submit(withdraw)
                assert delete_started.wait(2)
                if operation == "upload":
                    assert deletion.result(timeout=5).status_code == 204
                    wait_for_cleanup(engine, user_id)
                    assert not (context["storage_dir"] / str(user_id)).exists()
                else:
                    # 재분석은 게시 완료까지 사용자 잠금을 유지해 탈퇴가 기다립니다.
                    with pytest.raises(TimeoutError):
                        deletion.result(timeout=0.2)
            finally:
                finish.set()
            if operation == "upload":
                with pytest.raises(AppError) as caught:
                    analysis.result(timeout=5)
                assert caught.value.status == 401
            else:
                assert analysis.result(timeout=5).analysis_status == "done"
                assert deletion.result(timeout=5).status_code == 204
        wait_for_cleanup(engine, user_id)
        assert not (context["storage_dir"] / str(user_id)).exists()
        assert not workspace[0].exists()
        assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(Garment)) == 0
    finally:
        finish.set()
        engine.dispose()
