from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from backend.app.core.config import get_settings
from backend.app.core.security import create_access_token
from backend.app.db.models import (
    AccountStorageDeletionJob,
    FeedbackEvent,
    Garment,
    GarmentAttribute,
    Recommendation,
    RecommendationOutfit,
    RecommendationRateLimit,
    StorageDeletionJob,
    User,
    UserProfile,
)
from backend.app.services.storage_cleanup import AccountStorageCleanupService

PATH = "/api/v1/auth/me"


@pytest.fixture
def withdrawal(api_client, auth_account, account_factory, closet, add_garment, db_session, storage):
    other = account_factory()
    other_closet = {category: add_garment(other, category) for category in ("top", "bottom", "shoes")}
    assert (
        api_client.patch(
            "/api/v1/profile",
            headers=auth_account["headers"],
            json={"consent_body_info": True, "height_cm": 170, "avatar_config": {"body": "private"}},
        ).status_code
        == 200
    )
    recommendations = []
    events = []
    for account in (auth_account, other):
        response = api_client.post(
            "/api/v1/recommendations", headers=account["headers"], json={"temperature_c": 5}
        )
        assert response.status_code == 201
        rec = response.json()
        recommendations.append(rec)
        for accepted in [True, False] if account is auth_account else [True]:
            event = api_client.post(
                f"/api/v1/recommendations/{rec['recommendation_id']}/feedback",
                headers=account["headers"],
                json={
                    "recommendation_id": rec["recommendation_id"],
                    "outfit_id": rec["outfits"][0]["outfit_id"],
                    "accepted": accepted,
                    "reason": f"private reason for {account['email']}",
                },
            )
            assert event.status_code == 201
            events.append(event.json())
    user_id = UUID(auth_account["id"])
    # 등록된 cutout, 이전 버전, 행 없는 의류 폴더도 사용자 폴더 전체 정리에 포함됩니다.
    top = db_session.get(Garment, UUID(closet["top"]))
    top.cutout_path = f"{user_id}/{top.id}/cutout.png"
    for key in (top.cutout_path, f"{user_id}/{top.id}/cutout-old.png", f"{user_id}/{uuid4()}/orphan.tmp"):
        storage.save(key, b"owned image fixture")
    old_job = StorageDeletionJob(
        user_id=user_id, garment_id=uuid4(), available_at=datetime.now(UTC) + timedelta(hours=1)
    )
    db_session.add(old_job)
    db_session.commit()
    image_url = f"/media/{top.original_path}"
    return {
        "owner": auth_account,
        "other": other,
        "closet": closet,
        "other_closet": other_closet,
        "recommendations": recommendations,
        "events": events,
        "old_job": old_job.garment_id,
        "user_id": user_id,
        "image_url": image_url,
        "own_files": {p: p.read_bytes() for p in (storage.root / str(user_id)).rglob("*") if p.is_file()},
        "other_files": {p: p.read_bytes() for p in (storage.root / other["id"]).rglob("*") if p.is_file()},
    }


def assert_deleted(db, case):
    db.expunge_all()
    for model in (User, UserProfile, RecommendationRateLimit):
        assert db.get(model, case["user_id"]) is None
    for garment_id in case["closet"].values():
        assert db.get(Garment, UUID(garment_id)) is None
        assert db.get(GarmentAttribute, UUID(garment_id)) is None
    rec = case["recommendations"][0]
    assert db.get(Recommendation, UUID(rec["recommendation_id"])) is None
    for outfit in rec["outfits"]:
        assert db.get(RecommendationOutfit, UUID(outfit["outfit_id"])) is None
    assert db.get(StorageDeletionJob, case["old_job"]) is None
    for item in case["events"][:2]:
        event = db.get(FeedbackEvent, UUID(item["feedback_id"]))
        assert event.user_id is event.recommendation_id is event.outfit_id is event.reason is None
        assert event.accepted == item["accepted"] and event.created_at is not None
    other_event = db.get(FeedbackEvent, UUID(case["events"][2]["feedback_id"]))
    assert other_event.user_id == UUID(case["other"]["id"])
    assert other_event.reason == case["events"][2]["reason"]
    assert db.get(User, UUID(case["other"]["id"])) is not None
    assert db.get(Recommendation, UUID(case["recommendations"][1]["recommendation_id"])) is not None
    assert all(path.read_bytes() == data for path, data in case["other_files"].items())


def test_delete_account_cascades_anonymizes_and_revokes_all_tokens(
    api_client, withdrawal, db_session, storage
):
    case = withdrawal
    owner = case["owner"]
    second_token = create_access_token(
        case["user_id"], get_settings().model_copy(update={"JWT_EXPIRE_MINUTES": 1441})
    )
    response = api_client.delete(PATH, headers=owner["headers"])
    assert response.status_code == 204 and not response.content
    assert response.headers["Cache-Control"] == "no-store"
    assert_deleted(db_session, case)
    assert not (storage.root / owner["id"]).exists()
    assert db_session.get(AccountStorageDeletionJob, case["user_id"]) is None
    rec = case["recommendations"][0]
    garment_path = f"/api/v1/garments/{case['closet']['top']}"
    requests = [
        ("GET", PATH, None),
        ("GET", "/api/v1/profile", None),
        ("PATCH", "/api/v1/profile", {"nickname": "revive"}),
        ("GET", "/api/v1/garments", None),
        ("GET", case["image_url"], None),
        ("PATCH", garment_path, {"color": "black"}),
        ("DELETE", garment_path, None),
        ("POST", garment_path + "/reanalyze", None),
        ("GET", "/api/v1/recommendations", None),
        ("POST", "/api/v1/recommendations", {"temperature_c": 5}),
        ("GET", f"/api/v1/recommendations/{rec['recommendation_id']}/feedback", None),
        (
            "POST",
            f"/api/v1/recommendations/{rec['recommendation_id']}/feedback",
            {
                "recommendation_id": rec["recommendation_id"],
                "outfit_id": rec["outfits"][0]["outfit_id"],
                "accepted": True,
            },
        ),
        ("DELETE", PATH, None),
    ]
    for method, url, body in requests:
        denied = api_client.request(method, url, headers=owner["headers"], **({"json": body} if body else {}))
        assert denied.status_code == 401, (method, url, denied.text)
        assert denied.json()["code"] == "AUTH_INVALID_TOKEN"
    assert api_client.get(PATH, headers={"Authorization": "Bearer " + second_token}).status_code == 401
    assert (
        api_client.post(
            "/api/v1/auth/login", json={"email": owner["email"], "password": owner["password"]}
        ).status_code
        == 401
    )
    assert api_client.get(PATH, headers=case["other"]["headers"]).status_code == 200
    # 같은 이메일의 재가입도 새 UUID이며 이전 JWT를 되살리지 않습니다.
    joined = api_client.post(
        "/api/v1/auth/signup",
        json={"email": owner["email"], "password": owner["password"], "nickname": "new"},
    )
    assert joined.status_code == 201 and joined.json()["id"] != owner["id"]
    assert api_client.get(PATH, headers=owner["headers"]).status_code == 401


def test_failed_account_commit_keeps_all_data_and_never_deletes_files(
    api_client, withdrawal, db_session, storage, monkeypatch
):
    case = withdrawal
    called = []

    def fail():
        raise SQLAlchemyError("account commit failed")

    with monkeypatch.context() as patch:
        patch.setattr(db_session, "commit", fail)
        patch.setattr(storage, "delete_prefix", lambda key: called.append(key))
        with pytest.raises(SQLAlchemyError):
            api_client.delete(PATH, headers=case["owner"]["headers"])
    assert called == []
    db_session.expunge_all()
    assert db_session.get(User, case["user_id"]) is not None
    assert db_session.get(UserProfile, case["user_id"]).avatar_config == {"body": "private"}
    assert db_session.get(StorageDeletionJob, case["old_job"]) is not None
    assert db_session.get(AccountStorageDeletionJob, case["user_id"]) is None
    assert db_session.get(Recommendation, UUID(case["recommendations"][0]["recommendation_id"])) is not None
    for item in case["events"][:2]:
        assert db_session.get(FeedbackEvent, UUID(item["feedback_id"])).reason == item["reason"]
    assert all(path.read_bytes() == data for path, data in case["own_files"].items())
    assert api_client.get(PATH, headers=case["owner"]["headers"]).status_code == 200
    assert api_client.delete(PATH, headers=case["owner"]["headers"]).status_code == 204


@pytest.mark.parametrize("partial", [False, True])
def test_file_failure_is_durable_and_old_jwt_stays_blocked(
    api_client, withdrawal, db_session, storage, monkeypatch, partial
):
    case = withdrawal

    def fail(prefix):
        assert prefix == str(case["user_id"])
        if partial:
            next(iter(case["own_files"])).unlink()
        raise PermissionError("busy")

    with monkeypatch.context() as patch:
        patch.setattr(storage, "delete_prefix", fail)
        assert api_client.delete(PATH, headers=case["owner"]["headers"]).status_code == 204
    assert_deleted(db_session, case)
    job = db_session.get(AccountStorageDeletionJob, case["user_id"])
    assert job.attempts == 1 and job.last_error == "PermissionError"
    assert api_client.get(case["image_url"], headers=case["owner"]["headers"]).status_code == 401
    assert api_client.get(case["image_url"], headers=case["other"]["headers"]).status_code == 404
    assert AccountStorageCleanupService(db_session, storage).drain()["selected"] == 0
    job.available_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    assert AccountStorageCleanupService(db_session, storage).drain()["completed"] == 1
    assert not (storage.root / str(case["user_id"])).exists()
    assert AccountStorageCleanupService(db_session, storage).drain()["selected"] == 0


@pytest.mark.parametrize("mode", ["cleanup-commit", "lost-account-commit-reply"])
def test_commit_acknowledgment_failure_is_recovered_from_job(
    api_client, withdrawal, db_session, storage, monkeypatch, mode
):
    case = withdrawal
    commit = db_session.commit
    calls = 0

    def fail():
        nonlocal calls
        calls += 1
        if mode == "cleanup-commit" and calls == 2:
            raise SQLAlchemyError("cleanup acknowledgment failed")
        commit()
        if mode == "lost-account-commit-reply" and calls == 1:
            raise SQLAlchemyError("account committed but acknowledgment lost")

    with monkeypatch.context() as patch:
        patch.setattr(db_session, "commit", fail)
        if mode == "cleanup-commit":
            assert api_client.delete(PATH, headers=case["owner"]["headers"]).status_code == 204
        else:
            with pytest.raises(SQLAlchemyError):
                api_client.delete(PATH, headers=case["owner"]["headers"])
    assert_deleted(db_session, case)
    assert db_session.get(AccountStorageDeletionJob, case["user_id"]) is not None
    assert AccountStorageCleanupService(db_session, storage).drain()["completed"] == 1
    assert not (storage.root / str(case["user_id"])).exists()


def test_empty_account_and_unauthorized_or_target_injection_requests(
    api_client, auth_account, account_factory, db_session
):
    other = account_factory()
    assert api_client.delete(PATH).status_code == 401
    assert api_client.delete(PATH, headers={"Authorization": "Bearer invalid"}).status_code == 401
    for body in ({"user_id": other["id"]}, {"email": other["email"]}, {"is_demo": True}):
        assert (
            api_client.request("DELETE", PATH, headers=auth_account["headers"], json=body).status_code == 422
        )
    assert (
        api_client.delete(f"/api/v1/auth/{other['id']}", headers=auth_account["headers"]).status_code == 404
    )
    assert api_client.delete(PATH, headers=auth_account["headers"]).status_code == 204
    assert api_client.get(PATH, headers=other["headers"]).status_code == 200
    assert db_session.scalar(select(func.count()).select_from(AccountStorageDeletionJob)) == 0


def test_account_cleanup_never_removes_live_user_files(db_session, storage, auth_account):
    user_id = UUID(auth_account["id"])
    key = f"{user_id}/{uuid4()}/original.jpg"
    storage.save(key, b"live")
    db_session.add(AccountStorageDeletionJob(user_id=user_id))
    db_session.commit()
    assert AccountStorageCleanupService(db_session, storage).drain()["completed"] == 1
    assert storage.exists(key) and db_session.get(User, user_id) is not None
