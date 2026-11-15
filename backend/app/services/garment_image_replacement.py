"""Administrative, atomic image replacement without reanalysis or destructive overwrites.

No public endpoint. Callers supply reviewed garment IDs and observed image versions.
Original files remain available for recovery; only new immutable files are staged.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select

from backend.app.core.errors import AppError, ErrorCode
from backend.app.db.models import Garment
from backend.app.services.images import normalize_image
from backend.app.services.thumbnails import create_thumbnail, thumbnail_key
from backend.app.services.tryon_admission import active, utc
from backend.app.services.user_lock import require_active_user


@dataclass(frozen=True)
class ImageReplacement:
    user_id: UUID
    garment_id: UUID
    expected_original: str
    expected_cutout: str | None
    expected_updated: datetime
    image: bytes


def replace_outerwear_images(db, storage, replacements):
    """Publish the whole batch in one DB commit; failures leave old references valid.

    A failed file write/commit can leave unreferenced new files, never missing old
    files. They remain under the garment's normal deletion/withdrawal cleanup scope.
    """
    if not replacements or len({r.garment_id for r in replacements}) != len(replacements):
        raise ValueError("Provide a nonempty batch with distinct garment IDs")
    prepared = [(r, normalize_image(r.image)) for r in replacements]
    manifest = []
    try:
        for user_id in sorted({r.user_id for r in replacements}, key=str):
            require_active_user(db, user_id, exclusive=True)
            if active(db, user_id):
                raise AppError(409, ErrorCode.GARMENT_BUSY, "착샷 완료 후 이미지를 교체해 주세요")
        locked = []
        for request, image in sorted(prepared, key=lambda pair: str(pair[0].garment_id)):
            row = db.scalar(
                select(Garment)
                .where(Garment.id == request.garment_id, Garment.user_id == request.user_id)
                .with_for_update(of=Garment)
                .execution_options(populate_existing=True)
            )
            if row is None or row.attributes.category != "outerwear":
                raise AppError(404, ErrorCode.GARMENT_NOT_FOUND, "교체할 아우터를 찾을 수 없습니다")
            if (
                row.original_path != request.expected_original
                or row.cutout_path != request.expected_cutout
                or utc(row.updated_at) != utc(request.expected_updated)
            ):
                raise AppError(409, ErrorCode.GARMENT_BUSY, "검토 후 의류가 변경되었습니다")
            locked.append((row, image))
        # Check every expected version before writing anything.
        for row, image in locked:
            original = f"{row.user_id}/{row.id}/original-open-{uuid4().hex}.jpg"
            assert not storage.exists(original)
            storage.save(original, image)
            thumbnail = thumbnail_key(row, original)
            storage.save(thumbnail, create_thumbnail(storage, original))
            manifest.append(
                {
                    "user_id": str(row.user_id),
                    "garment_id": str(row.id),
                    "old_original": row.original_path,
                    "old_cutout": row.cutout_path,
                    "old_updated": row.updated_at.isoformat(),
                    "new_original": original,
                    "new_cutout": None,
                    "new_thumbnail": thumbnail,
                }
            )
            row.original_path, row.cutout_path = original, None
            row.updated_at = datetime.now(UTC)
        db.commit()
        return manifest
    except Exception:
        db.rollback()
        raise
