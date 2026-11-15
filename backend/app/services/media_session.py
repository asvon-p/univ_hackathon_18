"""Customer photos live only in process memory. DB stores session expiry, never images."""

import hashlib
from datetime import UTC, datetime, timedelta
from threading import RLock
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select

from backend.app.core.errors import AppError, ErrorCode
from backend.app.db.models import MediaSession, User
from backend.app.services.user_lock import require_active_user

IDLE_MINUTES = 30
LOCK = RLock()
MEDIA = {}


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def discard(key):
    with LOCK:
        MEDIA.pop(key, None)


def validate(db, user_id, key):
    session = db.get(MediaSession, key) if key else None
    if not session or session.user_id != user_id or utc(session.expires_at) <= datetime.now(UTC):
        discard(key)
        raise AppError(401, ErrorCode.AUTH_TOKEN_EXPIRED, "접속이 만료되었습니다. 다시 로그인해 주세요")
    return session


def memory(db, user_id, key):
    session = validate(db, user_id, key)
    with LOCK:
        entry = MEDIA.setdefault(
            key, SimpleNamespace(user_id=user_id, body=None, photo_id=None, updated=None, results={})
        )
        entry.expires = utc(session.expires_at)
        return entry


def upload(db, user_id, key, data):
    from backend.app.services.tryon_admission import release

    release(db, user_id, session_key=key)
    with LOCK:
        entry = memory(db, user_id, key)
        entry.body, entry.photo_id, entry.updated = data, uuid4(), datetime.now(UTC)
        entry.results.clear()
        return entry.updated


def discard_user(user_id):
    with LOCK:
        for key in [k for k, entry in MEDIA.items() if entry.user_id == user_id]:
            MEDIA.pop(key, None)


def expire_sessions(db):
    now = datetime.now(UTC)
    with LOCK:
        candidates = {key: value.user_id for key, value in MEDIA.items()}
    candidates.update(
        db.execute(
            select(MediaSession.token_hash, MediaSession.user_id).where(MediaSession.expires_at <= now)
        ).all()
    )
    db.rollback()
    for key, uid in candidates.items():
        # Serialize with activity/logout, including revocations from another backend.
        db.scalar(select(User).where(User.id == uid).with_for_update())
        db.expire_all()
        row = db.get(MediaSession, key)
        if row is None or utc(row.expires_at) <= now:
            if row is not None:
                db.delete(row)
            db.commit()
            discard(key)
        else:
            with LOCK:
                if key in MEDIA:
                    MEDIA[key].expires = utc(row.expires_at)
            db.rollback()


def activity(db, user_id, key):
    require_active_user(db, user_id, exclusive=True)
    session = validate(db, user_id, key)
    session.expires_at = datetime.now(UTC) + timedelta(minutes=IDLE_MINUTES)
    db.commit()
    with LOCK:
        if key in MEDIA:
            MEDIA[key].expires = utc(session.expires_at)


def logout(db, user_id, key):
    require_active_user(db, user_id, exclusive=True)
    from backend.app.services.tryon_admission import release

    release(db, user_id, session_key=key)
    row = db.get(MediaSession, key)
    if row and row.user_id == user_id:
        db.delete(row)
    db.commit()
    discard(key)
