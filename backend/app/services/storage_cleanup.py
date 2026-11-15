"""DB 삭제가 확정된 계정·의류의 파일을 정리합니다. 실패한 작업은 DB에 남겨 재시도합니다."""

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import (
    AccountStorageDeletionJob,
    BodyPhotoDeletionJob,
    Garment,
    ReferenceDeletionJob,
    StorageDeletionJob,
    StyleReference,
    TryOnDeletionJob,
    TryOnResult,
    User,
    UserProfile,
)
from backend.app.storage.base import StoragePort

log = logging.getLogger(__name__)


class StorageCleanupService:
    job_model = StorageDeletionJob
    id_field = "garment_id"

    def __init__(self, db: Session, storage: StoragePort):
        self.db = db
        self.storage = storage

    @property
    def id_column(self):
        return getattr(self.job_model, self.id_field)

    def _is_live(self, job) -> bool:
        return self.db.scalar(select(Garment.id).where(Garment.id == job.garment_id)) is not None

    def _prefix(self, job) -> str:
        return f"{job.user_id}/{job.garment_id}"

    def process(self, garment_id: UUID) -> bool:
        """처리 완료는 True, 보류/잠김/작업 없음은 False. 같은 작업은 행 잠금으로 직렬화합니다."""
        try:
            job = self.db.scalar(
                select(self.job_model)
                .where(self.id_column == garment_id)
                .with_for_update(skip_locked=True)
                .execution_options(populate_existing=True)
            )
            if job is None:
                self.db.rollback()
                return False
            # 살아 있는 계정·의류의 파일을 삭제하지 않습니다.
            if self._is_live(job):
                log.warning("살아 있는 대상의 파일 정리 작업을 취소합니다: %s", garment_id)
                self.db.delete(job)
                self.db.commit()
                return True
            try:
                # 경로는 DB의 사용자·의류 UUID로만 구성하며 임의 경로는 받지 않습니다.
                self.storage.delete_prefix(self._prefix(job))
            except OSError as exc:
                job.attempts += 1
                job.last_error = type(exc).__name__  # 민감한 경로/예외 내용은 DB에 저장하지 않습니다.
                delay = min(30 * (2 ** min(job.attempts - 1, 7)), 3600)
                job.available_at = datetime.now(UTC) + timedelta(seconds=delay)
                self.db.commit()
                log.warning("파일 정리 보류: target=%s, retry_in=%ss", garment_id, delay)
                return False
            self.db.delete(job)
            self.db.commit()
            return True
        except Exception:
            self.db.rollback()
            raise

    def drain(self, limit: int = 100) -> dict[str, int]:
        ids = list(
            self.db.scalars(
                select(self.id_column)
                .where(self.job_model.available_at <= datetime.now(UTC))
                .order_by(self.job_model.available_at, self.id_column)
                .limit(limit)
            )
        )
        self.db.rollback()  # 조회 트랜잭션을 끝내고 각 작업을 독립적으로 처리합니다.
        completed = sum(self.process(garment_id) for garment_id in ids)
        return {"selected": len(ids), "completed": completed, "deferred": len(ids) - completed}


class ReferenceCleanupService(StorageCleanupService):
    job_model = ReferenceDeletionJob
    id_field = "reference_id"

    def _is_live(self, job):
        return (
            self.db.scalar(
                select(StyleReference.id).where(
                    StyleReference.id == job.reference_id, StyleReference.image_path.is_not(None)
                )
            )
            is not None
        )

    def _prefix(self, job):
        return f"{job.user_id}/style-references/{job.reference_id}"

    def expire(self, limit=100):
        """A committed expiry revokes access first; file failure remains retryable after restart."""
        rows = self.db.scalars(
            select(StyleReference)
            .where(
                StyleReference.image_path.is_not(None),
                StyleReference.image_expires_at <= datetime.now(UTC),
            )
            .order_by(StyleReference.image_expires_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        for row in rows:
            detach_reference_image(self.db, row)
        self.db.commit()
        return len(rows)


def detach_reference_image(db, row):
    """Call while holding the reference row/user lock; commit before touching files."""
    if row.image_path is not None:
        if db.get(ReferenceDeletionJob, row.id) is None:
            db.add(ReferenceDeletionJob(reference_id=row.id, user_id=row.user_id))
        row.image_path = None
    row.image_expires_at = None
    row.regions = []


class AccountStorageCleanupService(StorageCleanupService):
    job_model = AccountStorageDeletionJob
    id_field = "user_id"

    def _is_live(self, job) -> bool:
        return self.db.scalar(select(User.id).where(User.id == job.user_id)) is not None

    def _prefix(self, job) -> str:
        return str(job.user_id)


class BodyPhotoCleanupService(StorageCleanupService):
    job_model = BodyPhotoDeletionJob
    id_field = "photo_id"

    def _is_live(self, job) -> bool:
        return (
            self.db.scalar(
                select(UserProfile.user_id).where(
                    UserProfile.user_id == job.user_id,
                    UserProfile.body_photo_id == job.photo_id,
                )
            )
            is not None
        )

    def _prefix(self, job) -> str:
        return f"{job.user_id}/body-photos/{job.photo_id}"


class TryOnCleanupService(StorageCleanupService):
    job_model = TryOnDeletionJob
    id_field = "image_id"

    def _is_live(self, job):
        return self.db.scalar(select(TryOnResult.id).where(TryOnResult.image_id == job.image_id)) is not None

    def _prefix(self, job):
        return f"{job.user_id}/tryon/{job.image_id}"


def process_pending_deletions() -> dict[str, int]:
    from backend.app.db.session import get_sessionmaker
    from backend.app.storage.local import get_storage

    with get_sessionmaker()() as db:
        from backend.app.services.media_session import expire_sessions

        expire_sessions(db)
        from backend.app.services.tryon_admission import cleanup as expire_admissions

        expire_admissions(db)
        db.commit()
        storage = get_storage()
        accounts = AccountStorageCleanupService(db, storage).drain()
        garments = StorageCleanupService(db, storage).drain()
        photos = BodyPhotoCleanupService(db, storage).drain()
        results = TryOnCleanupService(db, storage).drain()
        reference_cleanup = ReferenceCleanupService(db, storage)
        reference_cleanup.expire()
        references = reference_cleanup.drain()
        from backend.app.services.reference_temporary import expire_scratch

        expire_scratch()
        return {
            key: accounts[key] + garments[key] + photos[key] + results[key] + references[key]
            for key in accounts
        }
