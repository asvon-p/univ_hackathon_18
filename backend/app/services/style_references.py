import logging
import threading
from datetime import UTC, datetime, timedelta
from io import BytesIO
from uuid import uuid4

from PIL import Image
from sqlalchemy import func, select

from backend.app.adapters._timeout import run_with_timeout
from backend.app.core.errors import AppError, ErrorCode
from backend.app.db.models import Recommendation, ReferenceAnalysisUsage, ReferenceDeletionJob, StyleReference
from backend.app.services.images import normalize_image, read_upload
from backend.app.services.storage_cleanup import ReferenceCleanupService, detach_reference_image
from backend.app.services.user_lock import require_active_user
from shared.contracts import ReferenceFeature, StyleReferenceSnapshot

log = logging.getLogger(__name__)
_analysis_slots = threading.BoundedSemaphore(2)
IMAGE_TTL = timedelta(minutes=30)


def image_available(row):
    expiry = row.image_expires_at
    if expiry is not None and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    return bool(row.image_path and expiry and expiry > datetime.now(UTC))


def extract_crop(vision, data, user_id, reference_id, max_bytes):
    if not _analysis_slots.acquire(blocking=False):
        raise AppError(
            429,
            ErrorCode.REFERENCE_LIMITED,
            "사진 분석 접수 중입니다. 잠시 후 다시 시도해 주세요",
            headers={"Retry-After": "5"},
        )

    def work():
        try:
            # Hold admission until the actual worker finishes, even if HTTP times out.
            extractor = getattr(vision, "_reference_extract", vision._isolated_extract)
            return extractor(data, str(user_id), str(reference_id), max_bytes)[0]
        finally:
            _analysis_slots.release()

    try:
        return run_with_timeout(
            work,
            min(vision.timeout_sec, 20),
        )
    except Exception:
        return None


class StyleReferenceService:
    def __init__(self, db, storage=None):
        self.db, self.storage = db, storage

    def owned(self, user_id, reference_id, *, version=None, lock=False, ready=False):
        query = (
            select(StyleReference)
            .where(StyleReference.user_id == user_id, StyleReference.id == reference_id)
            .execution_options(populate_existing=True)
        )
        row = self.db.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise AppError(404, ErrorCode.REFERENCE_NOT_FOUND, "참고 사진을 찾을 수 없습니다")
        if version is not None and row.version != version:
            raise AppError(
                409, ErrorCode.REFERENCE_CHANGED, "참고 스타일이 변경되었습니다. 다시 선택해 주세요"
            )
        if ready and row.status != "ready":
            raise AppError(
                422, ErrorCode.REFERENCE_NOT_CONFIRMED, "사진의 의류 특징을 확인한 뒤 추천해 주세요"
            )
        return row

    @staticmethod
    def view(row):
        return {
            "id": str(row.id),
            "version": row.version,
            "status": row.status,
            "features": row.features,
            "regions": [],
            "notes": row.notes,
            "focus": row.focus,
            "image_url": f"/api/v1/style-references/{row.id}/image" if image_available(row) else None,
            "image_available": image_available(row),
            "image_expires_at": row.image_expires_at,
            "retention_policy": "keywords_only",
        }

    @staticmethod
    def snapshot(row):
        return StyleReferenceSnapshot(
            id=str(row.id), version=row.version, features=row.features, focus=row.focus
        )

    def list(self, user_id, limit=50, offset=0):
        return [
            self.view(r)
            for r in self.db.scalars(
                select(StyleReference)
                .where(StyleReference.user_id == user_id)
                .order_by(StyleReference.created_at.desc(), StyleReference.id)
                .limit(limit)
                .offset(offset)
            )
        ]

    def upload(self, user_id, stream, max_bytes):
        data = normalize_image(read_upload(stream, max_bytes))
        rid = uuid4()
        key = f"{user_id}/style-references/{rid}/original.jpg"
        require_active_user(self.db, user_id, exclusive=True)
        count = self.db.scalar(
            select(func.count()).select_from(StyleReference).where(StyleReference.user_id == user_id)
        )
        pending = self.db.scalar(
            select(func.count())
            .select_from(ReferenceDeletionJob)
            .where(ReferenceDeletionJob.user_id == user_id)
        )
        if count + pending >= 50:
            raise AppError(
                429,
                ErrorCode.REFERENCE_LIMITED,
                "참고 사진은 최대 50개까지 보관할 수 있습니다. 사용하지 않는 사진을 삭제해 주세요",
            )
        self.db.add(
            ReferenceDeletionJob(
                reference_id=rid, user_id=user_id, available_at=datetime.now(UTC) + timedelta(minutes=10)
            )
        )
        self.db.commit()  # Durable cleanup intent before any file write.
        try:
            self.storage.save(key, data)
            require_active_user(self.db, user_id)
            row = StyleReference(
                id=rid,
                user_id=user_id,
                image_path=key,
                image_expires_at=datetime.now(UTC) + IMAGE_TTL,
                features=[],
                regions=[],
                notes=[],
                version=1,
                status="draft",
                focus="balanced",
            )
            self.db.add(row)
            self.db.delete(self.db.get(ReferenceDeletionJob, rid))
            self.db.commit()
            return self.view(row)
        except Exception:
            self.db.rollback()
            try:
                ReferenceCleanupService(self.db, self.storage).process(rid)
            except Exception:
                log.warning("참고 사진 업로드 실패 후 정리 작업 보류")
            raise

    def image(self, user_id, rid):
        row = self.owned(user_id, rid)
        if not image_available(row) or not self.storage.exists(row.image_path):
            raise AppError(404, ErrorCode.REFERENCE_NOT_FOUND, "참고 사진을 찾을 수 없습니다")
        return self.storage.local_path(row.image_path)

    def analyze(self, user_id, rid, body, vision, max_bytes):
        require_active_user(self.db, user_id, exclusive=True)
        row = self.owned(user_id, rid, version=body.expected_version, lock=True)
        if not image_available(row):
            raise AppError(
                409,
                ErrorCode.REFERENCE_CHANGED,
                "일회성 원본이 만료되었거나 이미 분석되었습니다. "
                "키워드를 직접 수정하거나 새 사진을 올려 주세요",
            )
        key = row.image_path
        now = datetime.now(UTC).timestamp()
        usage = self.db.get(ReferenceAnalysisUsage, user_id)
        recent = [t for t in (usage.requests if usage else []) if t > now - 60]
        if len(recent) >= 10:
            raise AppError(
                429,
                ErrorCode.REFERENCE_LIMITED,
                "사진 영역 분석은 사용자별 분당 10회까지 가능합니다",
                headers={"Retry-After": str(max(1, int(61 - (now - recent[0]))))},
            )
        if usage is None:
            usage = ReferenceAnalysisUsage(user_id=user_id)
            self.db.add(usage)
        usage.requests = recent + [now]
        crops = []
        try:
            with self.storage.open(key) as stream, Image.open(stream) as image:
                for r in body.selected:
                    box = (
                        round(r.x * image.width),
                        round(r.y * image.height),
                        round((r.x + r.width) * image.width),
                        round((r.y + r.height) * image.height),
                    )
                    if box[2] - box[0] < 32 or box[3] - box[1] < 32:
                        raise AppError(
                            422,
                            ErrorCode.VALIDATION_ERROR,
                            "각 의류 영역의 가로·세로는 최소 32픽셀이어야 합니다",
                        )
                    crop = image.crop(box).convert("RGB")
                    out = BytesIO()
                    crop.save(out, format="JPEG", quality=90)
                    crops.append((r, crop, out.getvalue()))
        except OSError:
            raise AppError(404, ErrorCode.REFERENCE_NOT_FOUND, "참고 사진을 찾을 수 없습니다") from None
        # Consume the one-off image before model work. A crash/failure cannot retain the original.
        detach_reference_image(self.db, row)
        row.version += 1
        claimed_version = row.version
        self.db.commit()
        self._cleanup_image(rid)
        features = []
        for region, crop, data in crops:
            try:
                result = extract_crop(vision, data, user_id, rid, max_bytes)
            except AppError as exc:
                if exc.status != 429:
                    raise
                result = None  # Consumed originals are not retained when model capacity is unavailable.
            method, attrs = "manual", {}
            if result and result.success and result.attributes:
                attrs = result.attributes.model_dump(
                    include={"color", "pattern", "subcategory", "style_candidates"}
                )
                method = "vision" if "clip" in (result.model_version or "") else "baseline"
                if result.attributes.category != region.category:
                    attrs["subcategory"] = None
            if method != "vision":
                from vision.colors import dominant_color

                attrs = {"color": dominant_color(crop, None), "pattern": "unknown", "style_candidates": []}
                method = "baseline"
            features.append(
                ReferenceFeature(category=region.category, extraction=method, **attrs).model_dump(mode="json")
            )
        require_active_user(self.db, user_id)
        row = self.owned(user_id, rid, version=claimed_version, lock=True)
        row.features = features
        row.notes = ["원본 보관을 종료했습니다. 추출한 의류 키워드를 확인하거나 수정해 주세요."]
        if any(f["extraction"] != "vision" for f in features):
            row.notes.append(
                "AI 속성 분석을 사용할 수 없어 픽셀 색상만 추정했습니다. 나머지는 직접 입력해 주세요."
            )
        row.status = "draft"
        self.db.commit()
        return self.view(row)

    def _cleanup_image(self, rid):
        try:
            ReferenceCleanupService(self.db, self.storage).process(rid)
        except Exception:
            log.warning("참고 원본 접근 차단 완료, 파일 정리는 재시도 대기")

    def confirm(self, user_id, rid, body):
        require_active_user(self.db, user_id)
        row = self.owned(user_id, rid, version=body.expected_version, lock=True)
        previous = {f["category"]: f for f in row.features}
        fields = set(type(body.features[0]).model_fields)
        features = []
        for item in body.features:
            values = item.model_dump(mode="json")
            old = previous.get(item.category, {})
            unchanged = values == {k: v for k, v in old.items() if k in fields}
            features.append(
                {**values, "extraction": old.get("extraction", "manual") if unchanged else "manual"}
            )
        row.features, row.focus, row.status = features, body.focus, "ready"
        detach_reference_image(self.db, row)
        row.version += 1
        self.db.commit()
        self._cleanup_image(rid)
        return self.view(row)

    def delete(self, user_id, rid, version):
        require_active_user(self.db, user_id)
        row = self.owned(user_id, rid, version=version, lock=True)
        records = self.db.scalars(
            select(Recommendation)
            .where(
                Recommendation.user_id == user_id,
                Recommendation.context["reference_id"].as_string() == str(rid),
            )
            .with_for_update()
        ).all()
        for record in records:
            record.context = {
                **record.context,
                "reference_id": None,
                "reference_version": None,
                "style_reference": None,
            }
            for outfit in record.outfits:
                # Keep schedule steps; remove reference-derived text and combined scores.
                outfit.details = {k: v for k, v in (outfit.details or {}).items() if k == "wearing_plan"}
                outfit.reason = "참고 사진이 삭제되어 재현 설명과 점수를 정리했습니다"
                outfit.score = None
        if self.db.get(ReferenceDeletionJob, rid) is None:
            self.db.add(ReferenceDeletionJob(reference_id=rid, user_id=user_id))
        self.db.delete(row)
        self.db.commit()
        try:
            ReferenceCleanupService(self.db, self.storage).process(rid)
        except Exception:
            log.warning("참고 사진 삭제 확정, 파일 정리는 재시도 대기")
