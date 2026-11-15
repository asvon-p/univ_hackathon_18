"""Cross-process admission. One outfit/user, eight outfits across both frontends."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, exists, func, or_, select, text

from backend.app.core.errors import AppError
from backend.app.db.models import MediaSession, TryOnAdmission, TryOnQueueState

LEASE_SECONDS = 120
MAX_SECONDS = 15 * 60


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def cleanup(db):
    now = datetime.now(UTC)
    revoked = ~exists(select(MediaSession.token_hash).where(
        MediaSession.token_hash == TryOnAdmission.session_key,
        MediaSession.user_id == TryOnAdmission.user_id,
        MediaSession.expires_at > now,
    ))
    db.execute(delete(TryOnAdmission).where(or_(
        TryOnAdmission.expires_at <= now,
        TryOnAdmission.deadline_at <= now,
        TryOnAdmission.session_key.is_not(None) & revoked,
    )), execution_options={"synchronize_session": False})


def active(db, user_id):
    now = datetime.now(UTC)
    return db.scalar(select(TryOnAdmission).where(
        TryOnAdmission.user_id == user_id,
        TryOnAdmission.expires_at > now,
        TryOnAdmission.deadline_at > now,
    ).execution_options(populate_existing=True))


def reserve(db, user_id, job_id, session_key=None):
    # Also supports isolated SQLite tests; production PostgreSQL serializes on this row.
    db.execute(text("INSERT INTO tryon_queue_state (id, capacity) VALUES (1, 8) ON CONFLICT (id) DO NOTHING"))
    state = db.scalar(select(TryOnQueueState).where(TryOnQueueState.id == 1).with_for_update())
    cleanup(db)
    if active(db, user_id):
        raise AppError(429, "TRYON_USER_BUSY", "이미 진행 중인 착샷이 있습니다. 완료 후 다시 요청해 주세요.",
                       headers={"Retry-After": "5"})
    if db.scalar(select(func.count()).select_from(TryOnAdmission)) >= state.capacity:
        raise AppError(429, "TRYON_QUEUE_FULL", "착샷 접수가 가득 찼습니다. 잠시 후 다시 시도해 주세요.",
                       headers={"Retry-After": "10"})
    now = datetime.now(UTC)
    db.add(TryOnAdmission(job_id=job_id, user_id=user_id, session_key=session_key,
                         expires_at=now + timedelta(seconds=LEASE_SECONDS),
                         deadline_at=now + timedelta(seconds=MAX_SECONDS)))
    db.flush()


def require(db, user_id, job_id, session_key=None, *, renew=False):
    row = db.scalar(select(TryOnAdmission).where(
        TryOnAdmission.job_id == job_id, TryOnAdmission.user_id == user_id
    ).with_for_update().execution_options(populate_existing=True))
    now = datetime.now(UTC)
    if (row is None or utc(row.expires_at) <= now or utc(row.deadline_at) <= now
            or (row.session_key and row.session_key != session_key)):
        raise AppError(409, "TRYON_EXPIRED", "착샷 접수가 종료되었습니다. 다시 요청해 주세요.")
    if renew:
        row.expires_at = min(now + timedelta(seconds=LEASE_SECONDS), utc(row.deadline_at))
    return row


def release(db, user_id, job_id=None, *, session_key=None):
    query = delete(TryOnAdmission).where(TryOnAdmission.user_id == user_id)
    if job_id is not None:
        query = query.where(TryOnAdmission.job_id == job_id)
    if session_key is not None:
        query = query.where(TryOnAdmission.session_key == session_key)
    db.execute(query, execution_options={"synchronize_session": False})
