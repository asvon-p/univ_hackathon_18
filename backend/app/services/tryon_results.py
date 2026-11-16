"""Owned durable results, with committed cleanup intent before file publication."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

from sqlalchemy import select

from backend.app.core.errors import AppError, ErrorCode
from backend.app.db.models import Garment, TryOnDeletionJob, TryOnResult, User, UserProfile
from backend.app.services import tryon_admission as admission
from backend.app.services.images import normalize_image, read_upload
from backend.app.services.storage_cleanup import TryOnCleanupService
from backend.app.services.user_lock import require_active_user
from shared.tryon_contract import layering_warnings
from shared.wardrobes import single_wardrobe


def retire_results(db, user_id):
    admission.release(db, user_id)
    for row in db.scalars(select(TryOnResult).where(TryOnResult.user_id == user_id)):
        if row.image_id:
            db.add(TryOnDeletionJob(image_id=row.image_id, user_id=user_id, available_at=datetime.now(UTC)))
        db.delete(row)


class TryOnResultService:
    def __init__(self, db, storage, session_key=None):
        self.db, self.storage = db, storage
        self.session_key = session_key

    def ephemeral(self, user_id):
        return self.db.get(User, user_id).photo_policy == "session"

    def memory(self, user_id):
        from backend.app.services.media_session import memory

        return memory(self.db, user_id, self.session_key)

    def owned(self, user_id, result_id):
        row = (
            self.memory(user_id).results.get(str(result_id))
            if self.ephemeral(user_id)
            else self.db.scalar(
                select(TryOnResult).where(TryOnResult.user_id == user_id, TryOnResult.id == result_id)
            )
        )
        if row is None:
            raise AppError(404, ErrorCode.NOT_FOUND, "착샷을 찾을 수 없습니다")
        return row

    def view(self, row):
        total = len(row.items)
        pending = admission.active(self.db, row.user_id)
        pending = pending is not None and pending.job_id == row.id
        return {
            "id": str(row.id),
            "items": row.items,
            "photo_version": row.photo_version,
            "render_version": row.render_version,
            "layering_status": row.layering_status,
            "warnings": layering_warnings(row.layering_status),
            "inputs_current": self.inputs_current(row),
            "status": "done" if row.image_id else ("queued" if pending else "interrupted"),
            "names": row.names,
            "total": total,
            "step": 1 if pending else total,
            "step_name": "접수 대기" if pending else "완료",
            "progress": 1 if row.image_id else 0,
            "seconds_left": 0,
            "skipped": [],
            "queue_position": None,
            "error": None if row.image_id or pending else "생성이 중단되었습니다. 다시 생성해 주세요.",
            "image": f"/tryon/{row.id}/image" if row.image_id else None,
            "created_at": row.created_at.isoformat(),
        }

    def inputs_current(self, row):
        """Old images remain in history, but edited/replaced clothes require a new render."""
        garments = list(
            self.db.scalars(
                select(Garment).where(
                    Garment.user_id == row.user_id, Garment.id.in_([UUID(gid) for gid in row.items.values()])
                )
            )
        )
        created = row.created_at.replace(tzinfo=UTC) if row.created_at.tzinfo is None else row.created_at
        return len(garments) == len(row.items) and all(
            (g.updated_at.replace(tzinfo=UTC) if g.updated_at.tzinfo is None else g.updated_at) <= created
            for g in garments
        )

    def create(self, user_id, items, *, wardrobe=None):
        require_active_user(self.db, user_id, exclusive=True)
        profile = self.db.get(UserProfile, user_id)
        transient = self.ephemeral(user_id)
        if transient and profile:
            entry = self.memory(user_id)
            profile = SimpleNamespace(
                body_photo_id=entry.photo_id,
                body_photo_updated_at=entry.updated,
                consent_body_info_at=profile.consent_body_info_at,
            )
        if not profile or not profile.body_photo_id or profile.consent_body_info_at is None:
            raise AppError(409, ErrorCode.BODY_PHOTO_NOT_FOUND, "전신 사진을 먼저 올려 주세요")
        names, garments = [], []
        for slot, gid in items.items():
            garment = self.db.scalar(select(Garment).where(Garment.id == gid, Garment.user_id == user_id))
            # category is the saved wearing role, independent of shape (e.g. outerwear + shirt).
            if garment is None or garment.attributes is None or garment.attributes.category != slot:
                raise AppError(404, ErrorCode.NOT_FOUND, "본인 옷장 의류를 선택해 주세요")
            names.append(garment.name or slot)
            garments.append(garment)
        # Validate every selected garment before reserving capacity or storing a result.
        # Ownership is checked first so a foreign ID cannot reveal its wardrobe.
        selected_wardrobe = single_wardrobe(garments)
        if selected_wardrobe is None:
            raise AppError(
                422,
                ErrorCode.TRYON_WARDROBE_MIXED,
                "내 옷장과 테스트용 옷장의 의류를 함께 입힐 수 없어요. 같은 옷장에서 선택해 주세요.",
            )
        if wardrobe is not None and wardrobe != selected_wardrobe:
            raise AppError(
                422,
                ErrorCode.TRYON_WARDROBE_MISMATCH,
                "선택한 옷장과 의류가 일치하지 않아요. 같은 옷장에서 다시 선택해 주세요.",
            )
        version = profile.body_photo_updated_at
        if version.tzinfo is None:
            version = version.replace(tzinfo=UTC)
        fields = dict(
            user_id=user_id,
            photo_id=profile.body_photo_id,
            photo_version=version.isoformat(),
            render_version="legacy",
            layering_status="not_requested",
            items={k: str(v) for k, v in items.items()},
            names=names,
            # PostgreSQL now() is the transaction start, which can precede a garment
            # edit in an enclosing transaction. Capture the actual admission time.
            created_at=datetime.now(UTC),
        )
        result_id = uuid4()
        admission.reserve(self.db, user_id, result_id, self.session_key)
        if transient:
            row = SimpleNamespace(**fields, id=result_id, image_id=None, image=None)
            self.db.commit()
            entry.results[str(row.id)] = row
            return self.view(row)
        row = TryOnResult(**fields, id=result_id)
        self.db.add(row)
        self.db.commit()
        return self.view(row)

    def finish(
        self,
        user_id,
        result_id,
        stream,
        max_bytes,
        *,
        render_version="legacy",
        layering_status="not_requested",
        stages=None,
    ):
        require_active_user(self.db, user_id, exclusive=True)
        existing = self.owned(user_id, result_id)
        if layering_status != "not_requested" and not {"top", "outerwear"} <= existing.items.keys():
            raise AppError(
                422, ErrorCode.VALIDATION_ERROR, "상의와 아우터가 있어야 보존 결과를 저장할 수 있습니다"
            )
        if existing.image_id:
            return self.view(existing)
        stages = stages or {}
        if not stages.keys() <= existing.items.keys():
            raise AppError(422, ErrorCode.VALIDATION_ERROR, "선택 의류에 없는 처리 단계입니다")
        admission.require(self.db, user_id, result_id, self.session_key)
        if self.ephemeral(user_id):
            row = self.owned(user_id, result_id)
            entry = self.memory(user_id)
            if entry.photo_id != row.photo_id:
                raise AppError(409, ErrorCode.BODY_PHOTO_NOT_FOUND, "사진이 변경되었습니다")
            data = normalize_image(read_upload(stream, max_bytes))
            admission.release(self.db, user_id, result_id)
            self.db.commit()
            row.image = data
            row.image_id = uuid4()
            row.render_version, row.layering_status = render_version, layering_status
            return self.view(row)
        image_id = uuid4()
        try:
            require_active_user(self.db, user_id, exclusive=True)
            self.owned(user_id, result_id)
            data = normalize_image(read_upload(stream, max_bytes))
            stage_data = {
                slot: normalize_image(read_upload(source, max_bytes)) for slot, source in stages.items()
            }
            self.db.add(
                TryOnDeletionJob(
                    image_id=image_id, user_id=user_id, available_at=datetime.now(UTC) + timedelta(minutes=10)
                )
            )
            self.db.commit()
            require_active_user(self.db, user_id, exclusive=True)
            row = self.owned(user_id, result_id)
            admission.require(self.db, user_id, result_id, self.session_key)
            profile = self.db.get(UserProfile, user_id)
            if (
                profile is None
                or profile.body_photo_id != row.photo_id
                or profile.consent_body_info_at is None
            ):
                raise AppError(409, ErrorCode.BODY_PHOTO_NOT_FOUND, "전신 사진이 삭제되거나 변경되었습니다")
            staging = self.db.scalar(
                select(TryOnDeletionJob).where(TryOnDeletionJob.image_id == image_id).with_for_update()
            )
            if staging is None:
                raise AppError(409, ErrorCode.NOT_FOUND, "저장 요청이 만료되었습니다")
            if row.image_id is None:
                self.storage.save(f"{user_id}/tryon/{image_id}/result.jpg", data)
                for slot, content in stage_data.items():
                    self.storage.save(f"{user_id}/tryon/{image_id}/{slot}.jpg", content)
                row.image_id = image_id
                row.render_version, row.layering_status = render_version, layering_status
            self.db.delete(staging)
            admission.release(self.db, user_id, result_id)
            self.db.commit()
            return self.view(row)
        except Exception:
            self.db.rollback()
            try:
                TryOnCleanupService(self.db, self.storage).process(image_id)
            except Exception:
                self.db.rollback()  # Durable cleanup worker retries later.
            raise

    def image(self, user_id, result_id):
        require_active_user(self.db, user_id)
        row = self.owned(user_id, result_id)
        if not row.image_id:
            raise AppError(404, ErrorCode.NOT_FOUND, "완성된 착샷이 없습니다")
        if self.ephemeral(user_id):
            return row.image
        with self.storage.open(f"{user_id}/tryon/{row.image_id}/result.jpg") as stream:
            return stream.read()

    def delete(self, user_id, result_id):
        require_active_user(self.db, user_id, exclusive=True)
        row = self.owned(user_id, result_id)
        admission.release(self.db, user_id, result_id)
        if self.ephemeral(user_id):
            self.db.commit()
            self.memory(user_id).results.pop(str(result_id), None)
            return
        image_id = row.image_id
        if image_id:
            self.db.add(TryOnDeletionJob(image_id=image_id, user_id=user_id, available_at=datetime.now(UTC)))
        self.db.delete(row)
        self.db.commit()
        if image_id:
            TryOnCleanupService(self.db, self.storage).process(image_id)

    def heartbeat(self, user_id, result_id):
        require_active_user(self.db, user_id, exclusive=True)
        row = self.owned(user_id, result_id)
        if self.ephemeral(user_id):
            valid_photo = self.memory(user_id).photo_id == row.photo_id
        else:
            profile = self.db.get(UserProfile, user_id)
            valid_photo = profile is not None and profile.body_photo_id == row.photo_id
        if not valid_photo:
            raise AppError(409, ErrorCode.BODY_PHOTO_NOT_FOUND, "전신 사진이 변경되었습니다")
        admission.require(self.db, user_id, result_id, self.session_key, renew=True)
        self.db.commit()

    def cancel(self, user_id, result_id):
        require_active_user(self.db, user_id, exclusive=True)
        self.owned(user_id, result_id)
        admission.release(self.db, user_id, result_id)
        self.db.commit()
