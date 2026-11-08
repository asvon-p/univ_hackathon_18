from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.db.models import User
from backend.app.deps import get_current_user, get_db, get_storage
from backend.app.schemas.profile import ProfilePatch, ProfileView
from backend.app.services.profile import ProfileService
from backend.app.storage.base import StoragePort

router = APIRouter(prefix="/api/v1/profile", tags=["profile"])
frontend_router = APIRouter(prefix="/api/v1/users/me/profile", tags=["profile"])


@router.get("", response_model=ProfileView)
@frontend_router.get("", response_model=ProfileView)
def get_profile(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ProfileService(db).get(user)


@router.patch("", response_model=ProfileView)
@frontend_router.put("", response_model=ProfileView)
def patch_profile(
    body: ProfilePatch,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    storage: StoragePort = Depends(get_storage),
):
    return ProfileService(db, storage).patch(user, body)
