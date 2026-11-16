from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from backend.app.core.config import get_settings
from backend.app.db.models import TryOnResult
from backend.app.deps import get_current_user, get_current_user_id, get_db, get_storage
from backend.app.services.tryon_results import TryOnResultService

router = APIRouter(prefix="/api/v1/tryon-results", tags=["tryon-results"])
HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}


class CreateResult(BaseModel):
    items: dict[Literal["top", "bottom", "outerwear"], UUID] = Field(min_length=1, max_length=3)
    # Older clients may omit this; the service still derives and checks the DB scope.
    wardrobe: Literal["personal", "example"] | None = None


def service(db=Depends(get_db), storage=Depends(get_storage), user=Depends(get_current_user)):
    return TryOnResultService(db, storage, getattr(user, "_photo_session_key", None))


@router.post("", status_code=201)
def create(body: CreateResult, user=Depends(get_current_user_id), svc=Depends(service)):
    return svc.create(user, body.items, wardrobe=body.wardrobe)


@router.get("")
def listing(response: Response, user=Depends(get_current_user_id), svc=Depends(service)):
    response.headers.update(HEADERS)
    if svc.ephemeral(user):
        return [svc.view(row) for row in svc.memory(user).results.values() if row.image_id]
    rows = svc.db.scalars(
        select(TryOnResult)
        .where(TryOnResult.user_id == user, TryOnResult.image_id.is_not(None))
        .order_by(TryOnResult.created_at.desc())
        .limit(100)
    )
    return [svc.view(row) for row in rows]


@router.get("/{result_id}")
def detail(result_id: UUID, response: Response, user=Depends(get_current_user_id), svc=Depends(service)):
    response.headers.update(HEADERS)
    return svc.view(svc.owned(user, result_id))


@router.put("/{result_id}/image")
def finish(
    result_id: UUID,
    file: UploadFile,
    stage_top: UploadFile | None = None,
    stage_bottom: UploadFile | None = None,
    stage_outerwear: UploadFile | None = None,
    render_version: Literal[
        "legacy",
        "inner-preserve-v1",
        "opening-layer-v2",
        "auto-opening-v3",
        "auto-opening-v3.1",
        "auto-opening-v3.2",
        "open-shirt-v4",
        "open-shirt-v4.1",
        "occlusion-length-v5",
    ] = Form("legacy"),
    layering_status: Literal["not_requested", "applied", "skipped", "approximate"] = Form("not_requested"),
    user=Depends(get_current_user_id),
    svc=Depends(service),
    settings=Depends(get_settings),
):
    return svc.finish(
        user,
        result_id,
        file.file,
        settings.max_upload_bytes,
        render_version=render_version,
        layering_status=layering_status,
        stages={
            key: upload.file
            for key, upload in [("top", stage_top), ("bottom", stage_bottom), ("outerwear", stage_outerwear)]
            if upload is not None
        },
    )


@router.post("/{result_id}/heartbeat", status_code=204)
def heartbeat(result_id: UUID, user=Depends(get_current_user_id), svc=Depends(service)):
    svc.heartbeat(user, result_id)
    return Response(status_code=204, headers=HEADERS)


@router.post("/{result_id}/cancel", status_code=204)
def cancel(result_id: UUID, user=Depends(get_current_user_id), svc=Depends(service)):
    svc.cancel(user, result_id)
    return Response(status_code=204, headers=HEADERS)


@router.get("/{result_id}/image")
def image(result_id: UUID, user=Depends(get_current_user_id), svc=Depends(service)):
    return Response(svc.image(user, result_id), media_type="image/jpeg", headers=HEADERS)


@router.delete("/{result_id}", status_code=204)
def delete(result_id: UUID, user=Depends(get_current_user_id), svc=Depends(service)):
    svc.delete(user, result_id)
    return Response(status_code=204, headers=HEADERS)
