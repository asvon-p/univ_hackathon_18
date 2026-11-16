from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.app.adapters.vision import SafeVision
from backend.app.core.config import Settings, get_settings
from backend.app.deps import get_current_user_id, get_db, get_storage, get_vision
from backend.app.schemas.garment import GarmentPage, GarmentPatch, GarmentView
from backend.app.services.garment import GarmentService
from backend.app.storage.base import StoragePort
from shared.contracts import Category, GarmentStatus
from shared.wardrobes import WardrobeScope

router = APIRouter(prefix="/api/v1/garments", tags=["garments"])


def get_garment_service(
    db: Session = Depends(get_db), storage: StoragePort = Depends(get_storage)
) -> GarmentService:
    return GarmentService(db, storage)


@router.post("", response_model=GarmentView, status_code=201)
def upload_garment(
    file: UploadFile,
    wearing_role: Annotated[Category | None, Form()] = None,
    garment_type: Annotated[str | None, Form(max_length=50)] = None,
    user_id: UUID = Depends(get_current_user_id),
    service: GarmentService = Depends(get_garment_service),
    vision: SafeVision = Depends(get_vision),
    settings: Settings = Depends(get_settings),
):
    """사진을 저장합니다. 분석 실패도 201이며 analysis_status=failed로 반환합니다."""
    return service.upload(
        user_id,
        file.file,
        vision,
        settings.max_upload_bytes,
        wearing_role=wearing_role,
        garment_type=garment_type,
    )


@router.get("", response_model=list[GarmentView])
def list_garments(
    wardrobe: WardrobeScope = "all",
    category: Category | None = None,
    status: GarmentStatus | None = None,
    user_id: UUID = Depends(get_current_user_id),
    service: GarmentService = Depends(get_garment_service),
):
    return service.list(user_id, category, status, wardrobe)


@router.get("/page", response_model=GarmentPage)
def paginated_garments(
    response: Response,
    wardrobe: WardrobeScope = "all",
    category: Category | None = None,
    status: GarmentStatus | None = None,
    q: str = Query(default="", max_length=50),
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=24, ge=1, le=60),
    user_id: UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    from backend.app.services.garment_page import garment_page

    response.headers["Cache-Control"] = "private, no-store"
    return garment_page(
        db, user_id, category=category, status=status, q=q, cursor=cursor, limit=limit, wardrobe=wardrobe
    )


@router.get("/{garment_id}", response_model=GarmentView)
def get_garment(
    garment_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    service: GarmentService = Depends(get_garment_service),
):
    return service.get(user_id, garment_id)


@router.patch("/{garment_id}", response_model=GarmentView)
def patch_garment(
    garment_id: UUID,
    body: GarmentPatch,
    user_id: UUID = Depends(get_current_user_id),
    service: GarmentService = Depends(get_garment_service),
):
    return service.patch(user_id, garment_id, body)


@router.post("/{garment_id}/wear", response_model=GarmentView)
def record_wear(
    garment_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    service: GarmentService = Depends(get_garment_service),
):
    return service.record_wear(user_id, garment_id)


@router.post("/{garment_id}/reanalyze", response_model=GarmentView)
def reanalyze_garment(
    garment_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    service: GarmentService = Depends(get_garment_service),
    vision: SafeVision = Depends(get_vision),
    settings: Settings = Depends(get_settings),
):
    """원본을 재분석하며 수동 수정 필드를 보존합니다. 분석 실패도 200, 상태와 error로 확인합니다."""
    return service.reanalyze(user_id, garment_id, vision, settings.max_upload_bytes)


@router.delete("/{garment_id}", status_code=204)
def delete_garment(
    garment_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    service: GarmentService = Depends(get_garment_service),
):
    """의류 삭제 확정 후 204. 파일 정리가 실패해도 접근은 차단되며 서버가 재시도합니다."""
    service.delete(user_id, garment_id)
    return Response(status_code=204)


@router.get("/{garment_id}/image", response_class=FileResponse)
def get_garment_image(
    garment_id: UUID,
    kind: Literal["original", "cutout", "thumbnail"] = "cutout",
    user_id: UUID = Depends(get_current_user_id),
    service: GarmentService = Depends(get_garment_service),
):
    filename, media_type = service.image(user_id, garment_id, kind)
    return FileResponse(
        filename,
        media_type=media_type,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )
