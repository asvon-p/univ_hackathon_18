from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.core.errors import AppError, ErrorCode
from backend.app.deps import get_current_user_id, get_db, get_storage
from backend.app.services.garment import GarmentService
from backend.app.services.user_lock import require_active_user
from shared.garment_layers import refined_layer, restore_source_rgb, validate_opening
from shared.outerwear_opening import detect_opening

router = APIRouter(prefix="/api/v1/garments", tags=["garment-layers"])


class Opening(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["auto", "manual", "off"] = "manual"
    points: list[tuple[float, float]] = Field(default_factory=list, max_length=24)

    @model_validator(mode="after")
    def valid(self):
        validate_opening(self.points)
        if self.mode != "manual" and self.points:
            raise ValueError("자동/사용 안 함 모드에는 수동 좌표를 보내지 마세요")
        return self


@router.get("/{gid}/layer-config")
def config(
    gid: UUID,
    response: Response,
    user=Depends(get_current_user_id),
    db=Depends(get_db),
    storage=Depends(get_storage),
):
    row = GarmentService(db, storage)._owned(user, gid)
    response.headers["Cache-Control"] = "private, no-store"
    result = resolve_opening(
        row,
        load_cutout(row, storage)
        if row.attributes.category == "outerwear" and row.opening_mode == "auto"
        else None,
    )
    return {
        **result,
        "category": row.attributes.category,
        "mode": row.opening_mode,
        "manual_points": row.opening_polygon or [],
    }


@router.put("/{gid}/layer-config")
def save(
    gid: UUID,
    body: Opening,
    user=Depends(get_current_user_id),
    db=Depends(get_db),
    storage=Depends(get_storage),
):
    from datetime import UTC, datetime

    require_active_user(db, user, exclusive=True)
    row = GarmentService(db, storage)._owned(user, gid, lock=True)
    if body.points and row.attributes.category != "outerwear":
        raise AppError(422, ErrorCode.VALIDATION_ERROR, "앞여밈 영역은 아우터에만 지정할 수 있습니다")
    row.opening_mode = "off" if body.mode == "manual" and not body.points else body.mode
    if body.mode == "manual":
        row.opening_polygon = body.points
    row.updated_at = datetime.now(UTC)
    db.commit()
    return {"points": row.opening_polygon, "mode": row.opening_mode}


def load_cutout(row, storage):
    key = row.cutout_path if row.cutout_path and storage.exists(row.cutout_path) else row.original_path
    with storage.open(key) as stream, Image.open(stream) as im:
        image = im.convert("RGBA")
    # Existing cutout is preferred. If absent, remove background using the same local segmenter.
    if image.getchannel("A").getextrema() == (255, 255):
        from vision.pipeline import Pipeline, _get_cutout

        image = _get_cutout(image, Pipeline(segmentation="rembg"))
        if image is None:
            raise AppError(
                503, ErrorCode.VALIDATION_ERROR, "누끼 생성에 실패했습니다. 의류 재분석 후 다시 시도해 주세요"
            )
    if key != row.original_path and storage.exists(row.original_path):
        with storage.open(row.original_path) as stream, Image.open(stream) as original:
            image = restore_source_rgb(image, original)
    return image


def resolve_opening(row, image):
    if row.attributes.category != "outerwear" or row.opening_mode == "off":
        return {"points": [], "status": "off", "source": "off", "confidence": None, "reason": None}
    if row.opening_mode == "manual":
        return {
            "points": row.opening_polygon or [],
            "status": "manual",
            "source": "manual",
            "confidence": None,
            "reason": None,
        }
    return {**detect_opening(image), "source": "auto"}


def layer_bytes(row, storage):
    image = load_cutout(row, storage)
    opening = resolve_opening(row, image)
    return refined_layer(
        image,
        opening["points"],
        opening_mode=opening["source"],
        wearing_role=row.attributes.category,
        garment_type=row.attributes.subcategory,
    )


@router.get("/{gid}/layer-image")
def image(gid: UUID, user=Depends(get_current_user_id), db=Depends(get_db), storage=Depends(get_storage)):
    row = GarmentService(db, storage)._owned(user, gid)
    return Response(
        layer_bytes(row, storage), media_type="image/png", headers={"Cache-Control": "private, no-store"}
    )
