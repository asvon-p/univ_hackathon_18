from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from backend.app.core.config import Settings, get_settings
from backend.app.db.models import User
from backend.app.deps import get_current_user, get_current_user_id, get_db, get_storage
from backend.app.schemas.auth import (
    AccountDeletionRequest,
    LoginRequest,
    SignupRequest,
    SignupResponse,
    TokenResponse,
    UserView,
)
from backend.app.services.account import AccountService
from backend.app.services.auth import AuthService
from backend.app.storage.base import StoragePort

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def get_auth_service(
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> AuthService:
    return AuthService(db, settings)


@router.post("/signup", response_model=SignupResponse, status_code=201)
def signup(body: SignupRequest, response: Response, service: AuthService = Depends(get_auth_service)):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return service.signup(body)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, response: Response, service: AuthService = Depends(get_auth_service)):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return service.login(body)


@router.get("/me", response_model=UserView)
def me(user: User = Depends(get_current_user)):
    return UserView.model_validate(user)


def get_account_service(
    db: Session = Depends(get_db), storage: StoragePort = Depends(get_storage)
) -> AccountService:
    return AccountService(db, storage)


@router.delete("/me", status_code=204)
def delete_me(
    body: AccountDeletionRequest | None = None,
    user_id: UUID = Depends(get_current_user_id),
    service: AccountService = Depends(get_account_service),
):
    """본인 계정·연관 데이터 삭제와 피드백 익명화. 파일 정리는 실패 시 자동 재시도합니다."""
    service.delete(user_id)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
