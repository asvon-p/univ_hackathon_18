from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from backend.app.api.v1.garments import get_garment_service
from backend.app.deps import get_current_user_id
from backend.app.services.garment import GarmentService

router = APIRouter(tags=["media"])


@router.get("/media/{path:path}")
def get_media(
    path: str,
    user_id: UUID = Depends(get_current_user_id),
    service: GarmentService = Depends(get_garment_service),
):
    filename, media_type = service.media(user_id, path)
    return FileResponse(
        filename,
        media_type=media_type,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )
