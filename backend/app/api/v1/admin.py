from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from backend.app.api.v1.garment_layers import layer_bytes
from backend.app.core.errors import AppError, ErrorCode
from backend.app.db.models import ApiUsage, Garment, TryOnResult, User
from backend.app.deps import get_current_user, get_db, get_storage
from backend.app.services.garment import GarmentService

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}


def require_admin(user=Depends(get_current_user)):
    if not user.is_admin:
        raise AppError(403, "ADMIN_REQUIRED", "관리자만 접근할 수 있습니다")
    return user


@router.get("/overview")
def overview(
    response: Response, admin=Depends(require_admin), db=Depends(get_db), offset: int = Query(0, ge=0)
):
    response.headers.update(HEADERS)
    totals = db.execute(
        select(
            func.count(ApiUsage.id),
            func.sum(ApiUsage.input_tokens),
            func.sum(ApiUsage.output_tokens),
            func.sum(ApiUsage.cached_tokens),
            func.sum(ApiUsage.estimated_usd),
        )
    ).one()
    unknown = db.scalar(select(func.count()).select_from(ApiUsage).where(ApiUsage.estimated_usd.is_(None)))
    rows = db.scalars(select(ApiUsage).order_by(ApiUsage.created_at.desc()).offset(offset).limit(30)).all()
    return {
        "calls": totals[0],
        "input_tokens": totals[1] or 0,
        "output_tokens": totals[2] or 0,
        "cached_tokens": totals[3] or 0,
        "estimated_usd": totals[4],
        "unknown_cost_calls": unknown,
        "events": [
            {
                "id": str(r.id),
                "model": r.model,
                "status": r.status,
                "input": r.input_tokens,
                "cached": r.cached_tokens,
                "output": r.output_tokens,
                "usd": r.estimated_usd,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }


@router.get("/artifacts")
def artifacts(
    response: Response,
    admin=Depends(require_admin),
    db=Depends(get_db),
    offset: int = Query(0, ge=0),
    owner: UUID | None = None,
):
    response.headers.update(HEADERS)
    garment_query = select(Garment)
    result_query = (
        select(TryOnResult)
        .join(User, User.id == TryOnResult.user_id)
        .where(User.photo_policy == "persistent", TryOnResult.image_id.is_not(None))
    )
    if owner:
        garment_query = garment_query.where(Garment.user_id == owner)
        result_query = result_query.where(TryOnResult.user_id == owner)
    garments = db.scalars(garment_query.order_by(Garment.created_at.desc()).offset(offset).limit(30)).all()
    results = db.scalars(result_query.order_by(TryOnResult.created_at.desc()).offset(offset).limit(30)).all()
    return {
        "garments": [
            {
                "id": str(r.id),
                "owner": str(r.user_id),
                "name": r.name or r.attributes.category,
                "opening": bool(r.opening_polygon),
            }
            for r in garments
        ],
        "results": [
            {
                "id": str(r.id),
                "owner": str(r.user_id),
                "names": r.names,
                "layering_status": r.layering_status,
                "created_at": r.created_at.isoformat(),
            }
            for r in results
        ],
    }


@router.get("/media/garments/{gid}/{kind}")
def garment_image(
    gid: UUID,
    kind: Literal["original", "cutout", "layer"],
    admin=Depends(require_admin),
    db=Depends(get_db),
    storage=Depends(get_storage),
):
    row = db.get(Garment, gid)
    if not row:
        raise AppError(404, ErrorCode.NOT_FOUND, "의류가 없습니다")
    if kind == "layer":
        return Response(layer_bytes(row, storage), media_type="image/png", headers=HEADERS)
    path, mime = GarmentService(db, storage).image(row.user_id, gid, kind)
    return FileResponse(path, media_type=mime, headers=HEADERS)


@router.get("/media/results/{rid}/{kind}")
def result_image(
    rid: UUID,
    kind: Literal["result", "top", "bottom", "outerwear"],
    admin=Depends(require_admin),
    db=Depends(get_db),
    storage=Depends(get_storage),
):
    row = db.get(TryOnResult, rid)
    if not row or not row.image_id or db.get(User, row.user_id).photo_policy != "persistent":
        raise AppError(404, ErrorCode.NOT_FOUND, "보관된 결과가 없습니다")
    key = f"{row.user_id}/tryon/{row.image_id}/{kind}.jpg"
    if not storage.exists(key):
        raise AppError(404, ErrorCode.NOT_FOUND, "해당 단계의 보관 이미지가 없습니다")
    return FileResponse(storage.local_path(key), media_type="image/jpeg", headers=HEADERS)
