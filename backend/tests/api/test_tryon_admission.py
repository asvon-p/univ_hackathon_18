from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from backend.app.db.models import TryOnAdmission, TryOnQueueState, TryOnResult
from backend.app.services import tryon_admission as admission
from backend.tests.api.test_body_photo import PATH, picture
from backend.tests.api.test_tryon_results import RESULTS, finish, prepare


def test_one_active_outfit_and_capacity_errors(api_client, auth_account, account_factory, db_session):
    rid, gid = prepare(api_client, auth_account, db_session)
    body = {"items": {"top": gid}}
    h = auth_account["headers"]
    busy = api_client.post(RESULTS, headers=h, json=body)
    assert busy.status_code == 429 and busy.json()["code"] == "TRYON_USER_BUSY"
    assert busy.headers["retry-after"] == "5"
    assert db_session.scalar(select(func.count()).select_from(TryOnAdmission)) == 1
    assert api_client.get(f"{RESULTS}/{rid}", headers=h).json()["status"] == "queued"
    assert api_client.post(f"{RESULTS}/{rid}/heartbeat", headers=h).status_code == 204
    other = account_factory()
    for action in ("heartbeat", "cancel"):
        assert api_client.post(f"{RESULTS}/{rid}/{action}", headers=other["headers"]).status_code == 404
    assert api_client.post(f"{RESULTS}/{rid}/cancel", headers=h).status_code == 204
    assert db_session.scalar(select(func.count()).select_from(TryOnAdmission)) == 0
    retry = api_client.post(RESULTS, headers=h, json=body)
    assert retry.status_code == 201
    assert finish(api_client, h, retry.json()["id"]).status_code == 200
    assert db_session.scalar(select(func.count()).select_from(TryOnAdmission)) == 0


@pytest.mark.parametrize("policy", ["persistent", "session"])
def test_expiry_fences_late_result_and_allows_retry(api_client, auth_account, db_session, policy):
    rid, gid = prepare(api_client, auth_account, db_session, policy)
    lease = db_session.get(TryOnAdmission, UUID(rid))
    lease.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    h = auth_account["headers"]
    assert api_client.post(f"{RESULTS}/{rid}/heartbeat", headers=h).status_code == 409
    assert finish(api_client, h, rid).status_code == 409
    response = api_client.post(RESULTS, headers=h, json={"items": {"top": gid}})
    assert response.status_code == 201
    assert db_session.scalar(select(func.count()).select_from(TryOnAdmission)) == 1


def test_absolute_deadline_cannot_be_extended(api_client, auth_account, db_session):
    rid, _ = prepare(api_client, auth_account, db_session)
    lease = db_session.get(TryOnAdmission, UUID(rid))
    lease.deadline_at = datetime.now(UTC) + timedelta(seconds=30)
    db_session.commit()
    assert api_client.post(f"{RESULTS}/{rid}/heartbeat", headers=auth_account["headers"]).status_code == 204
    db_session.expire_all()
    lease = db_session.get(TryOnAdmission, UUID(rid))
    assert lease.expires_at == lease.deadline_at


def test_failed_admission_commit_does_not_leave_reservation(
    api_client, auth_account, db_session, monkeypatch
):
    rid, gid = prepare(api_client, auth_account, db_session)
    h = auth_account["headers"]
    assert api_client.post(f"{RESULTS}/{rid}/cancel", headers=h).status_code == 204
    before = db_session.scalar(select(func.count()).select_from(TryOnResult))
    with monkeypatch.context() as patch:
        patch.setattr(db_session, "commit", lambda: (_ for _ in ()).throw(SQLAlchemyError("failed")))
        with pytest.raises(SQLAlchemyError):
            api_client.post(RESULTS, headers=h, json={"items": {"top": gid}})
    assert db_session.scalar(select(func.count()).select_from(TryOnAdmission)) == 0
    assert db_session.scalar(select(func.count()).select_from(TryOnResult)) == before


@pytest.mark.parametrize("action", ["logout", "photo_change", "photo_delete", "withdraw", "account_delete"])
def test_customer_revocation_releases_capacity_without_storing_photos(
    api_client, auth_account, db_session, action
):
    rid, gid = prepare(api_client, auth_account, db_session, "session")
    h = auth_account["headers"]
    if action == "logout":
        response = api_client.post("/api/v1/auth/logout", headers=h)
    elif action == "photo_change":
        response = api_client.put(PATH, headers=h, files={"file": ("body.png", picture(), "image/png")})
    elif action == "withdraw":
        response = api_client.patch("/api/v1/profile", headers=h, json={"consent_body_info": False})
    else:
        response = api_client.delete(PATH if action == "photo_delete" else "/api/v1/auth/me", headers=h)
    assert response.status_code in (200, 204)
    # The test fixture doesn't auto-commit GET dependencies like production does.
    db_session.commit()
    assert db_session.scalar(select(func.count()).select_from(TryOnAdmission)) == 0
    assert db_session.get(TryOnResult, UUID(rid)) is None


def test_limit_is_shared_and_expired_slots_are_reclaimed(
    api_client, auth_account, account_factory, db_session
):
    rid, _ = prepare(api_client, auth_account, db_session)
    db_session.get(TryOnQueueState, 1).capacity = 1
    db_session.commit()
    other = account_factory()
    # Prepare another account without occupying a slot first.
    h = other["headers"]
    api_client.patch("/api/v1/profile", headers=h, json={"consent_body_info": True})
    api_client.put(PATH, headers=h, files={"file": ("body.png", picture(), "image/png")})
    g = api_client.post(
        "/api/v1/garments", headers=h, files={"file": ("top.png", picture(), "image/png")}
    ).json()
    body = {"items": {"top": g["garment_id"]}}
    full = api_client.post(RESULTS, headers=h, json=body)
    assert full.status_code == 429 and full.json()["code"] == "TRYON_QUEUE_FULL"
    assert full.headers["retry-after"] == "10"
    db_session.get(TryOnAdmission, UUID(rid)).expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    admission.cleanup(db_session)
    db_session.commit()
    assert api_client.post(RESULTS, headers=h, json=body).status_code == 201
