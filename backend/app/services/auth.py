from hmac import compare_digest

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.core.errors import AppError, ErrorCode
from backend.app.core.security import create_access_token, dummy_password_hash, hash_password, verify_password
from backend.app.db.models import User, UserProfile
from backend.app.repositories.profiles import ProfileRepository
from backend.app.repositories.users import UserRepository
from backend.app.schemas.auth import LoginRequest, SignupRequest, SignupResponse, TokenResponse, UserView


class AuthService:
    def __init__(self, db: Session, settings: Settings):
        self.db = db
        self.settings = settings
        self.users = UserRepository(db)

    def signup(self, body: SignupRequest) -> SignupResponse:
        code = body.invite_code.get_secret_value() if body.invite_code is not None else ""
        if self.settings.SIGNUP_INVITE_CODE and not compare_digest(
            code.encode(), self.settings.SIGNUP_INVITE_CODE.encode()
        ):
            raise AppError(403, ErrorCode.INVITE_CODE_INVALID, "가입 초대 코드가 올바르지 않습니다")
        if self.users.by_email(str(body.email)) is not None:
            raise AppError(409, ErrorCode.EMAIL_ALREADY_EXISTS, "이미 가입된 이메일입니다")
        user = User(
            email=str(body.email),
            nickname=body.nickname,
            password_hash=hash_password(body.password.get_secret_value()),
        )
        try:
            self.users.add(user)
            ProfileRepository(self.db).add(
                UserProfile(user_id=user.id, preferred_styles=[], disliked_colors=[])
            )
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            if self.users.by_email(str(body.email)) is not None:
                raise AppError(409, ErrorCode.EMAIL_ALREADY_EXISTS, "이미 가입된 이메일입니다") from None
            raise
        return SignupResponse(
            **UserView.model_validate(user).model_dump(),
            access_token=create_access_token(user.id, self.settings),
            expires_in=self.settings.JWT_EXPIRE_MINUTES * 60,
        )

    def login(self, body: LoginRequest) -> TokenResponse:
        user = self.users.by_email(str(body.email))
        password_hash = user.password_hash if user is not None else dummy_password_hash()
        valid = verify_password(body.password.get_secret_value(), password_hash)
        if not valid or user is None or not user.is_active:
            raise AppError(
                401, ErrorCode.AUTH_INVALID_CREDENTIALS, "이메일 또는 비밀번호가 올바르지 않습니다"
            )
        return TokenResponse(
            access_token=create_access_token(user.id, self.settings),
            expires_in=self.settings.JWT_EXPIRE_MINUTES * 60,
        )
