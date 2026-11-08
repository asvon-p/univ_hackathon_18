from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from backend.app.db.models import (
    FeedbackEvent,
    Garment,
    GarmentAttribute,
    RecommendationOutfit,
    StorageDeletionJob,
)
from backend.app.services.storage_cleanup import StorageCleanupService
from scripts.demo_assets import CATALOG, garment_images


@pytest.fixture
def deletion_case(api_client, auth_account, closet, storage, db_session):
    garment_id = UUID(closet["top"])
    row = db_session.get(Garment, garment_id)
    cutout = f"{row.user_id}/{row.id}/cutout.png"
    storage.save(cutout, garment_images(CATALOG[0])[1])
    row.cutout_path = cutout
    db_session.commit()
    headers = auth_account["headers"]
    recommendation = api_client.post(
        "/api/v1/recommendations", headers=headers, json={"temperature_c": 5}
    ).json()
    outfit_id = UUID(recommendation["outfits"][0]["outfit_id"])
    feedback = api_client.post(
        f"/api/v1/recommendations/{recommendation['recommendation_id']}/feedback",
        headers=headers,
        json={
            "recommendation_id": recommendation["recommendation_id"],
            "outfit_id": str(outfit_id),
            "accepted": True,
        },
    ).json()
    files = {}
    for key in (row.original_path, cutout):
        with storage.open(key) as stream:
            files[key] = stream.read()
    return {
        "id": garment_id,
        "path": f"/api/v1/garments/{garment_id}",
        "headers": headers,
        "files": files,
        "outfit_id": outfit_id,
        "feedback_id": UUID(feedback["feedback_id"]),
    }


def assert_deleted(db, client, case):
    db.expire_all()
    assert db.get(Garment, case["id"]) is None
    assert db.get(GarmentAttribute, case["id"]) is None
    assert db.get(RecommendationOutfit, case["outfit_id"]) is None
    assert db.get(FeedbackEvent, case["feedback_id"]).outfit_id is None
    assert client.get(case["path"], headers=case["headers"]).status_code == 404
    for key in case["files"]:
        assert client.get(f"/media/{key}", headers=case["headers"]).status_code == 404


def test_failed_delete_commit_preserves_original_cutout_and_related_records(
    api_client, db_session, storage, deletion_case, monkeypatch
):
    case = deletion_case
    deleted_files = []
    original_delete = storage.delete_prefix

    def observe(prefix):
        deleted_files.append(prefix)
        original_delete(prefix)

    with monkeypatch.context() as patch:
        patch.setattr(storage, "delete_prefix", observe)
        patch.setattr(db_session, "commit", lambda: (_ for _ in ()).throw(SQLAlchemyError("commit failed")))
        with pytest.raises(SQLAlchemyError):
            api_client.delete(case["path"], headers=case["headers"])
    db_session.expunge_all()
    assert deleted_files == []
    assert db_session.get(Garment, case["id"]) is not None
    assert db_session.get(GarmentAttribute, case["id"]) is not None
    assert db_session.get(RecommendationOutfit, case["outfit_id"]) is not None
    assert db_session.get(FeedbackEvent, case["feedback_id"]).outfit_id == case["outfit_id"]
    assert db_session.scalar(select(func.count()).select_from(StorageDeletionJob)) == 0
    for key, before in case["files"].items():
        assert api_client.get(f"/media/{key}", headers=case["headers"]).content == before
    # 장애 해소 후 같은 요청을 재시도하면 한 번만 삭제됩니다.
    assert api_client.delete(case["path"], headers=case["headers"]).status_code == 204
    assert_deleted(db_session, api_client, case)
    assert all(not storage.exists(key) for key in case["files"])
    assert db_session.get(StorageDeletionJob, case["id"]) is None


@pytest.mark.parametrize("partial", [False, True])
def test_file_failure_remains_durable_and_later_cleanup_is_idempotent(
    api_client, db_session, storage, deletion_case, monkeypatch, partial
):
    case = deletion_case

    def fail(prefix):
        if partial:
            Path(storage.local_path(next(iter(case["files"])))).unlink(missing_ok=True)
        raise PermissionError("file busy")

    with monkeypatch.context() as patch:
        patch.setattr(storage, "delete_prefix", fail)
        assert api_client.delete(case["path"], headers=case["headers"]).status_code == 204
    assert_deleted(db_session, api_client, case)
    job = db_session.get(StorageDeletionJob, case["id"])
    assert job.attempts == 1 and job.last_error == "PermissionError"
    assert sum(storage.exists(key) for key in case["files"]) == (1 if partial else 2)
    # 재시도 지연을 지키며, 새 서비스 인스턴스도 DB 작업으로 정리할 수 있습니다.
    assert StorageCleanupService(db_session, storage).drain()["selected"] == 0
    job.available_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    assert StorageCleanupService(db_session, storage).drain() == {
        "selected": 1,
        "completed": 1,
        "deferred": 0,
    }
    assert all(not storage.exists(key) for key in case["files"])
    assert db_session.get(StorageDeletionJob, case["id"]) is None
    assert StorageCleanupService(db_session, storage).drain()["selected"] == 0
    assert api_client.delete(case["path"], headers=case["headers"]).status_code == 404


def test_cleanup_commit_failure_keeps_job_for_retry_without_restoring_deleted_garment(
    api_client, db_session, storage, deletion_case, monkeypatch
):
    case = deletion_case
    commit = db_session.commit
    calls = 0

    def fail_cleanup_commit():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise SQLAlchemyError("cleanup acknowledgment failed")
        commit()

    with monkeypatch.context() as patch:
        patch.setattr(db_session, "commit", fail_cleanup_commit)
        assert api_client.delete(case["path"], headers=case["headers"]).status_code == 204
    assert_deleted(db_session, api_client, case)
    assert all(not storage.exists(key) for key in case["files"])
    assert db_session.get(StorageDeletionJob, case["id"]) is not None
    assert StorageCleanupService(db_session, storage).drain()["completed"] == 1
    assert db_session.get(StorageDeletionJob, case["id"]) is None


def test_unknown_commit_outcome_is_recovered_from_persisted_job(
    api_client, db_session, storage, deletion_case, monkeypatch
):
    case = deletion_case
    commit = db_session.commit

    def lost_reply():
        commit()
        raise SQLAlchemyError("commit succeeded but acknowledgment lost")

    with monkeypatch.context() as patch:
        patch.setattr(db_session, "commit", lost_reply)
        with pytest.raises(SQLAlchemyError):
            api_client.delete(case["path"], headers=case["headers"])
    assert_deleted(db_session, api_client, case)
    assert all(storage.exists(key) for key in case["files"])
    assert db_session.get(StorageDeletionJob, case["id"]) is not None
    assert StorageCleanupService(db_session, storage).drain()["completed"] == 1
    assert all(not storage.exists(key) for key in case["files"])


def test_cleanup_never_deletes_files_for_a_live_garment(db_session, storage, deletion_case):
    case = deletion_case
    user_id = db_session.get(Garment, case["id"]).user_id
    db_session.add(StorageDeletionJob(garment_id=case["id"], user_id=user_id))
    db_session.commit()
    assert StorageCleanupService(db_session, storage).drain()["completed"] == 1
    assert db_session.get(Garment, case["id"]) is not None
    assert all(storage.exists(key) for key in case["files"])
