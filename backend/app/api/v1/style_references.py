from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.app.adapters.vision import SafeVision
from backend.app.core.config import Settings, get_settings
from backend.app.deps import get_current_user_id, get_db, get_storage, get_vision
from backend.app.services.style_references import StyleReferenceService
from backend.app.storage.base import StoragePort
from shared.style_references import ReferenceConfirmation, RegionAnalysis

router = APIRouter(prefix="/api/v1/style-references", tags=["style-references"])


def service(db: Session = Depends(get_db), storage: StoragePort = Depends(get_storage)):
    return StyleReferenceService(db, storage)


@router.post("", status_code=201)
def upload(
    file: UploadFile,
    user_id: UUID = Depends(get_current_user_id),
    svc: StyleReferenceService = Depends(service),
    settings: Settings = Depends(get_settings),
):
    return svc.upload(user_id, file.file, settings.max_upload_bytes)


@router.get("")
def list_references(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user_id: UUID = Depends(get_current_user_id),
    svc: StyleReferenceService = Depends(service),
):
    return svc.list(user_id, limit, offset)


@router.get("/{reference_id}")
def get(
    reference_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    svc: StyleReferenceService = Depends(service),
):
    return svc.view(svc.owned(user_id, reference_id))


@router.get("/{reference_id}/image")
def image(
    reference_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    svc: StyleReferenceService = Depends(service),
):
    return FileResponse(
        svc.image(user_id, reference_id),
        media_type="image/jpeg",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/{reference_id}/analyze")
def analyze(
    reference_id: UUID,
    body: RegionAnalysis,
    user_id: UUID = Depends(get_current_user_id),
    svc: StyleReferenceService = Depends(service),
    vision: SafeVision = Depends(get_vision),
    settings: Settings = Depends(get_settings),
):
    return svc.analyze(user_id, reference_id, body, vision, settings.max_upload_bytes)


@router.patch("/{reference_id}")
def confirm(
    reference_id: UUID,
    body: ReferenceConfirmation,
    user_id: UUID = Depends(get_current_user_id),
    svc: StyleReferenceService = Depends(service),
):
    return svc.confirm(user_id, reference_id, body)


@router.delete("/{reference_id}", status_code=204)
def delete(
    reference_id: UUID,
    expected_version: int = Query(ge=1),
    user_id: UUID = Depends(get_current_user_id),
    svc: StyleReferenceService = Depends(service),
):
    svc.delete(user_id, reference_id, expected_version)
    return Response(status_code=204)
