from uuid import UUID

from fastapi import APIRouter, Depends, Response, UploadFile
from sqlalchemy.orm import Session

from backend.app.core.config import Settings, get_settings
from backend.app.deps import get_current_user_id, get_db, get_storage
from backend.app.schemas.body_photo import BodyPhotoResponse
from backend.app.services.body_photo import BodyPhotoService
from backend.app.storage.base import StoragePort

router = APIRouter(prefix="/api/v1/users/me/body-photo", tags=["body-photo"])
HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}


def service(db: Session = Depends(get_db), storage: StoragePort = Depends(get_storage)) -> BodyPhotoService:
    return BodyPhotoService(db, storage)


@router.put("", response_model=BodyPhotoResponse)
def upload_body_photo(
    file: UploadFile,
    response: Response,
    user_id: UUID = Depends(get_current_user_id),
    photos: BodyPhotoService = Depends(service),
    settings: Settings = Depends(get_settings),
):
    response.headers.update(HEADERS)
    return photos.upload(user_id, file.file, settings.max_upload_bytes)


@router.get("", response_class=Response)
def get_body_photo(user_id: UUID = Depends(get_current_user_id), photos: BodyPhotoService = Depends(service)):
    return Response(photos.image(user_id), media_type="image/jpeg", headers=HEADERS)


@router.delete("", status_code=204)
def delete_body_photo(
    user_id: UUID = Depends(get_current_user_id), photos: BodyPhotoService = Depends(service)
):
    photos.delete(user_id)
    return Response(status_code=204, headers=HEADERS)
