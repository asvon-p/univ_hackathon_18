"""Private body photos: immutable versions, DB-first retirement, durable cleanup."""

import logging
from datetime import UTC, datetime, timedelta
from typing import BinaryIO
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import AppError, ErrorCode
from backend.app.db.models import BodyPhotoDeletionJob, User, UserProfile
from backend.app.repositories.profiles import ProfileRepository
from backend.app.schemas.body_photo import BodyPhotoResponse
from backend.app.services.images import normalize_image, read_upload
from backend.app.services.storage_cleanup import BodyPhotoCleanupService, TryOnCleanupService
from backend.app.services.user_lock import require_active_user
from backend.app.storage.base import InvalidStorageKey, StoragePort

log = logging.getLogger(__name__)


def retire_body_photo(db: Session, profile: UserProfile) -> UUID | None:
    from backend.app.services.tryon_admission import release

    release(db, profile.user_id)
    old_id = profile.body_photo_id
    if old_id is not None:
        db.add(BodyPhotoDeletionJob(photo_id=old_id, user_id=profile.user_id))
    profile.body_photo_id = None
    profile.body_photo_updated_at = None
    return old_id


def cleanup_photo(db: Session, storage: StoragePort, photo_id: UUID | None) -> None:
    if photo_id is not None:
        try:
            BodyPhotoCleanupService(db, storage).process(photo_id)
        except Exception:
            log.exception("Body photo cleanup deferred: %s", photo_id)


class BodyPhotoService:
    def __init__(self, db: Session, storage: StoragePort, session_key=None):
        self.db, self.storage = db, storage
        self.session_key = session_key

    def ephemeral(self, user_id):
        return self.db.get(User, user_id).photo_policy == "session"

    def _profile(self, user_id: UUID, *, write: bool) -> UserProfile:
        require_active_user(self.db, user_id, exclusive=write)
        profile = ProfileRepository(self.db).get(user_id, for_update=write)
        if profile is None:
            raise AppError(404, ErrorCode.NOT_FOUND, "프로필을 찾을 수 없습니다")
        return profile

    @staticmethod
    def _consent(profile: UserProfile):
        if profile.consent_body_info_at is None:
            raise AppError(
                422, ErrorCode.BODY_INFO_CONSENT_REQUIRED, "전신 사진 저장 전에 신체정보 수집에 동의해 주세요"
            )

    def upload(self, user_id: UUID, stream: BinaryIO, max_bytes: int) -> BodyPhotoResponse:
        if self.ephemeral(user_id):
            from backend.app.services.media_session import upload

            self._consent(self._profile(user_id, write=True))
            updated = upload(
                self.db, user_id, self.session_key, normalize_image(read_upload(stream, max_bytes))
            )
            return BodyPhotoResponse(body_photo_updated_at=updated)
        photo_id = uuid4()
        old_id = None
        try:
            profile = self._profile(user_id, write=True)
            self._consent(profile)
            data = normalize_image(read_upload(stream, max_bytes))
            # Commit cleanup intent before touching storage. A crash cannot leave an
            # untracked image. The delayed job is locked throughout publication.
            self.db.add(
                BodyPhotoDeletionJob(
                    photo_id=photo_id,
                    user_id=user_id,
                    available_at=datetime.now(UTC) + timedelta(minutes=10),
                )
            )
            self.db.commit()
            profile = self._profile(user_id, write=True)
            self._consent(profile)
            staging = self.db.scalar(
                select(BodyPhotoDeletionJob)
                .where(
                    BodyPhotoDeletionJob.photo_id == photo_id,
                )
                .with_for_update()
            )
            if staging is None:
                raise RuntimeError("Body photo staging expired")
            prefix = f"{user_id}/body-photos/{photo_id}"
            self.storage.save(f"{prefix}/original.jpg", data)
            previous_time = profile.body_photo_updated_at
            updated = datetime.now(UTC)
            if previous_time is not None:
                previous_time = (
                    previous_time.replace(tzinfo=UTC) if previous_time.tzinfo is None else previous_time
                )
                updated = max(updated, previous_time + timedelta(microseconds=1))
            old_id = retire_body_photo(self.db, profile)
            profile.body_photo_id, profile.body_photo_updated_at = photo_id, updated
            self.db.delete(staging)
            self.db.commit()
        except Exception:
            self.db.rollback()
            cleanup_photo(self.db, self.storage, photo_id)
            raise
        cleanup_photo(self.db, self.storage, old_id)
        return BodyPhotoResponse(body_photo_updated_at=updated)

    def image(self, user_id: UUID) -> bytes:
        if self.ephemeral(user_id):
            from backend.app.services.media_session import memory

            self._consent(self._profile(user_id, write=False))
            data = memory(self.db, user_id, self.session_key).body
            if data is None:
                raise AppError(404, ErrorCode.BODY_PHOTO_NOT_FOUND, "전신 사진이 없습니다")
            return data
        profile = self._profile(user_id, write=False)
        if profile.body_photo_id is None or profile.consent_body_info_at is None:
            raise AppError(404, ErrorCode.BODY_PHOTO_NOT_FOUND, "전신 사진이 없습니다")
        try:
            with self.storage.open(f"{user_id}/body-photos/{profile.body_photo_id}/original.jpg") as stream:
                # Read under the user lock so replacement/deletion cannot race FileResponse.
                return stream.read()
        except (OSError, InvalidStorageKey) as exc:
            raise AppError(404, ErrorCode.BODY_PHOTO_NOT_FOUND, "전신 사진이 없습니다") from exc

    def delete(self, user_id: UUID, *, include_tryons: bool = False) -> None:
        if self.ephemeral(user_id):
            from backend.app.services.media_session import discard

            self._profile(user_id, write=True)
            from backend.app.services.tryon_admission import release

            release(self.db, user_id, session_key=self.session_key)
            discard(self.session_key)
            return
        if include_tryons:
            self.purge(user_id)
            return
        try:
            profile = self._profile(user_id, write=True)
            photo_id = retire_body_photo(self.db, profile)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        cleanup_photo(self.db, self.storage, photo_id)

    def purge(self, user_id: UUID) -> None:
        """Retire all stored photo versions and derived results for this user."""
        from backend.app.services.tryon_results import retire_results

        cleaners = (
            BodyPhotoCleanupService(self.db, self.storage),
            TryOnCleanupService(self.db, self.storage),
        )
        try:
            profile = self._profile(user_id, write=True)
            retire_body_photo(self.db, profile)
            retire_results(self.db, user_id)
            self.db.flush()
            jobs = [
                (
                    cleaner,
                    list(
                        self.db.scalars(select(cleaner.id_column).where(cleaner.job_model.user_id == user_id))
                    ),
                )
                for cleaner in cleaners
            ]
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        for cleaner, ids in jobs:
            for image_id in ids:
                try:
                    cleaner.process(image_id)
                except Exception:
                    log.exception("Photo purge file cleanup deferred")
        if any(
            ids and self.db.scalar(select(cleaner.id_column).where(cleaner.id_column.in_(ids)).limit(1))
            for cleaner, ids in jobs
        ):
            raise AppError(
                503,
                "PHOTO_CLEANUP_PENDING",
                "사진 접근을 차단했어요. 일부 파일 정리는 서버에서 다시 시도합니다.",
            )
