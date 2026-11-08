import logging
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.orm import Session

from backend.app.db.models import AccountStorageDeletionJob, StorageDeletionJob
from backend.app.services.storage_cleanup import AccountStorageCleanupService
from backend.app.services.user_lock import require_active_user
from backend.app.storage.base import StoragePort

log = logging.getLogger(__name__)


class AccountService:
    def __init__(self, db: Session, storage: StoragePort):
        self.db = db
        self.storage = storage

    def delete(self, user_id: UUID) -> None:
        try:
            user = require_active_user(self.db, user_id, exclusive=True)
            self.db.add(AccountStorageDeletionJob(user_id=user_id))
            # 사용자 폴더 정리 작업이 기존 의류별 미완료 작업까지 담당합니다.
            self.db.execute(delete(StorageDeletionJob).where(StorageDeletionJob.user_id == user_id))
            # DB FK로 프로필/옷장/추천/요청 사용량을 지우고 BEFORE DELETE 트리거가
            # 피드백의 사용자/추천/코디 참조와 자유 입력 사유를 함께 익명화합니다.
            self.db.delete(user)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        try:
            AccountStorageCleanupService(self.db, self.storage).process(user_id)
        except Exception:
            log.exception("탈퇴 완료, 사용자 파일 정리는 재시도 대기: %s", user_id)
