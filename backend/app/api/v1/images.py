"""Authenticated, non-persistent image normalization for browser crop previews."""

from uuid import UUID

from fastapi import APIRouter, Depends, Response, UploadFile

from backend.app.core.config import Settings, get_settings
from backend.app.deps import get_current_user_id
from backend.app.services.images import normalize_image, read_upload

router = APIRouter(prefix="/api/v1/images", tags=["images"])


@router.post("/normalize", response_class=Response)
def normalize_upload(
    file: UploadFile,
    user_id: UUID = Depends(get_current_user_id),
    settings: Settings = Depends(get_settings),
):
    """JPG/PNG/HEIC/HEIF → metadata-free JPEG, max 2048px; no DB or file writes."""
    data = normalize_image(read_upload(file.file, settings.max_upload_bytes))
    return Response(
        data,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )
