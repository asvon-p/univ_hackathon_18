import logging
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import BinaryIO
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from backend.app.adapters.base import VisionOutcome
from backend.app.adapters.vision import SafeVision
from backend.app.core.errors import AppError, ErrorCode
from backend.app.db.models import Garment, GarmentAttribute, StorageDeletionJob
from backend.app.mappers import garment_view
from backend.app.repositories.garments import GarmentRepository
from backend.app.schemas.garment import GarmentPatch, GarmentView
from backend.app.services.images import normalize_image, read_upload
from backend.app.services.storage_cleanup import StorageCleanupService
from backend.app.services.user_lock import require_active_user
from backend.app.storage.base import InvalidStorageKey, StoragePort
from shared.contracts import Category, GarmentStatus

log = logging.getLogger(__name__)


class GarmentService:
    def __init__(self, db: Session, storage: StoragePort):
        self.db = db
        self.storage = storage
        self.repository = GarmentRepository(db)

    def _owned(self, user_id: UUID, garment_id: UUID, *, lock: bool = False) -> Garment:
        garment = self.repository.get(user_id, garment_id, lock=lock)
        if garment is None:
            raise AppError(404, ErrorCode.GARMENT_NOT_FOUND, "옷을 찾을 수 없습니다")
        return garment

    def list(
        self, user_id: UUID, category: Category | None = None, status: GarmentStatus | None = None
    ) -> list[GarmentView]:
        return [garment_view(g) for g in self.repository.list(user_id, category, status)]

    def get(self, user_id: UUID, garment_id: UUID) -> GarmentView:
        return garment_view(self._owned(user_id, garment_id))

    def upload(self, user_id: UUID, stream: BinaryIO, vision: SafeVision, max_bytes: int) -> GarmentView:
        data = normalize_image(read_upload(stream, max_bytes))
        garment_id = uuid4()
        prefix = f"{user_id}/{garment_id}"
        key = f"{prefix}/original.jpg"
        garment = Garment(
            id=garment_id,
            user_id=user_id,
            original_path=key,
            attributes=GarmentAttribute(style_candidates=[]),
        )
        try:
            require_active_user(self.db, user_id)
            self.storage.save(key, data)
            self.repository.add(garment)
            # 분석 시작 전 업로드를 확정합니다. 분석 실패와 사진 등록을 분리합니다.
            self.db.commit()
        except Exception:
            self.db.rollback()
            try:
                self.storage.delete_prefix(prefix)
            except OSError:
                log.exception("업로드 실패 후 파일 정리 실패: %s", prefix)
            raise

        # 최초 등록 커밋 후 탈퇴가 완료돼도 모델의 늦은 출력이 사용자 폴더를 재생성하지 않습니다.
        outcome, cutout = vision.extract_isolated(data, str(user_id), str(garment_id), max_bytes)
        try:
            require_active_user(self.db, user_id)
            garment = self._owned(user_id, garment_id, lock=True)
            if self._apply_analysis(garment, outcome) and cutout:
                garment.cutout_path = self._publish_cutout(cutout, prefix) or garment.cutout_path
            self.db.commit()
            return garment_view(garment)
        except Exception:
            self.db.rollback()
            raise

    def _apply_analysis(self, garment: Garment, outcome: VisionOutcome) -> bool:
        result = outcome.result
        attributes = garment.attributes
        attributes.model_version = result.model_version[:50] if result.model_version else None
        attributes.raw_output = {
            "result": result.model_dump(mode="json"),
            "provider": outcome.provider,
            "latency_ms": outcome.latency_ms,
        }
        attributes.analyzed_at = datetime.now(UTC)
        error = result.error
        if result.success and result.attributes is not None:
            try:
                values = GarmentPatch.model_validate(
                    result.attributes.model_dump(exclude={"garment_id", "cutout_path", "status"})
                )
                protected = set(attributes.user_edited_fields)
                for field, value in values.model_dump(exclude_unset=True).items():
                    if field not in protected:
                        setattr(attributes, field, value)
                attributes.source = "user" if protected else "model"
                garment.analysis_status = "done"
                attributes.error = None
                garment.updated_at = datetime.now(UTC)
                return True
            except ValidationError:
                error = "비전 분석 결과의 속성 값이 올바르지 않습니다"
        garment.analysis_status = "failed"
        attributes.error = error or "의류 분석에 실패했습니다"
        garment.updated_at = datetime.now(UTC)
        return False

    def reanalyze(self, user_id: UUID, garment_id: UUID, vision: SafeVision, max_bytes: int) -> GarmentView:
        try:
            require_active_user(self.db, user_id)
            # 동일 의류의 중복 재분석은 거부하고 PATCH/DELETE는 완료 후 최신 행으로 처리합니다.
            garment = self.repository.get(user_id, garment_id, lock=True, nowait=True)
            if garment is None:
                raise AppError(404, ErrorCode.GARMENT_NOT_FOUND, "옷을 찾을 수 없습니다")
            try:
                with self.storage.open(garment.original_path) as stream:
                    image = read_upload(stream, max_bytes)
            except (OSError, InvalidStorageKey) as exc:
                raise AppError(
                    404, ErrorCode.GARMENT_IMAGE_NOT_FOUND, "원본 이미지를 찾을 수 없습니다"
                ) from exc
            outcome, cutout = vision.extract_isolated(image, str(user_id), str(garment_id), max_bytes)
            if self._apply_analysis(garment, outcome) and cutout:
                garment.cutout_path = (
                    self._publish_cutout(cutout, f"{user_id}/{garment_id}") or garment.cutout_path
                )
            self.db.commit()
            return garment_view(garment)
        except OperationalError as exc:
            self.db.rollback()
            if getattr(exc.orig, "sqlstate", None) == "55P03":
                raise AppError(
                    409, ErrorCode.GARMENT_BUSY, "의류 처리 중입니다. 잠시 후 다시 시도해 주세요"
                ) from exc
            raise
        except Exception:
            self.db.rollback()
            raise

    def _publish_cutout(self, data: bytes, prefix: str) -> str | None:
        try:
            normalized = normalize_image(data, cutout=True)
        except AppError:
            log.warning("cutout이 올바른 PNG가 아닙니다: %s", prefix)
            return None
        # 기존/미등록 버전은 의류 삭제·탈퇴의 폴더 정리까지 보존합니다.
        key = f"{prefix}/cutout-{uuid4().hex}.png"
        self.storage.save(key, normalized)
        return key

    def patch(self, user_id: UUID, garment_id: UUID, patch: GarmentPatch) -> GarmentView:
        require_active_user(self.db, user_id)
        garment = self._owned(user_id, garment_id, lock=True)
        values = patch.model_dump(exclude_unset=True)
        for field in ("status", "name", "location"):
            if field in values:
                setattr(garment, field, values.pop(field))
        if values:
            for field, value in values.items():
                setattr(garment.attributes, field, value)
            garment.attributes.source = "user"
            garment.attributes.user_edited_fields = sorted(
                set(garment.attributes.user_edited_fields) | values.keys()
            )
            if garment.attributes.category is not None:
                garment.analysis_status = "done"
                garment.attributes.error = None
        if patch.model_fields_set:
            garment.updated_at = datetime.now(UTC)
        self.db.commit()
        return garment_view(garment)

    def delete(self, user_id: UUID, garment_id: UUID) -> None:
        try:
            require_active_user(self.db, user_id)
            # 중복 삭제를 직렬화하고, 삭제와 정리 작업을 함께 확정합니다.
            if self.repository.get(user_id, garment_id, lock=True) is None:
                raise AppError(404, ErrorCode.GARMENT_NOT_FOUND, "옷을 찾을 수 없습니다")
            self.db.add(StorageDeletionJob(garment_id=garment_id, user_id=user_id))
            self.repository.delete(user_id, garment_id)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        # 이 지점 전에는 파일을 건드리지 않습니다. 파일/정리 완료 커밋이 실패해도
        # 의류 삭제는 이미 확정됐으며 DB의 작업을 서버 worker가 다시 처리합니다.
        try:
            StorageCleanupService(self.db, self.storage).process(garment_id)
        except Exception:
            log.exception("의류 삭제 완료, 파일 정리는 재시도 대기: %s", garment_id)

    def image(self, user_id: UUID, garment_id: UUID, kind: str) -> tuple[str, str]:
        garment = self._owned(user_id, garment_id)
        # 분석 실패/누끼 미생성 시에도 원본을 표시합니다.
        key = garment.cutout_path if kind == "cutout" and garment.cutout_path else garment.original_path
        return self.media(user_id, key)

    def media(self, user_id: UUID, key: str) -> tuple[str, str]:
        parts = PurePosixPath(key).parts
        try:
            if len(parts) != 3 or str(user_id) != parts[0]:
                raise ValueError
            garment_id = UUID(parts[1])
        except ValueError as exc:
            raise AppError(404, ErrorCode.NOT_FOUND, "이미지를 찾을 수 없습니다") from exc
        garment = self._owned(user_id, garment_id)
        if key not in (garment.original_path, garment.cutout_path):
            raise AppError(404, ErrorCode.NOT_FOUND, "이미지를 찾을 수 없습니다")
        try:
            if not self.storage.exists(key):
                raise FileNotFoundError
            path = self.storage.local_path(key)
        except (InvalidStorageKey, OSError) as exc:
            raise AppError(404, ErrorCode.NOT_FOUND, "이미지를 찾을 수 없습니다") from exc
        return path, "image/png" if key == garment.cutout_path else "image/jpeg"
