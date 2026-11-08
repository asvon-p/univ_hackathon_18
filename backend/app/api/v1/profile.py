from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.db.models import User
from backend.app.deps import get_current_user, get_db
from backend.app.schemas.profile import ProfilePatch, ProfileView
from backend.app.services.profile import ProfileService

router = APIRouter(prefix="/api/v1/profile", tags=["profile"])
frontend_router = APIRouter(prefix="/api/v1/users/me/profile", tags=["profile"])


@router.get("", response_model=ProfileView)
@frontend_router.get("", response_model=ProfileView)
def get_profile(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ProfileService(db).get(user)


@router.patch("", response_model=ProfileView)
@frontend_router.put("", response_model=ProfileView)
def patch_profile(body: ProfilePatch, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ProfileService(db).patch(user, body)
