"""공통 에러. 모든 에러 응답은 {"detail": "...", "code": "..."} 형식입니다."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)


class ErrorCode:
    AUTH_INVALID_CREDENTIALS = "AUTH_INVALID_CREDENTIALS"
    AUTH_TOKEN_EXPIRED = "AUTH_TOKEN_EXPIRED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_INVALID_TOKEN = "AUTH_INVALID_TOKEN"
    BODY_INFO_CONSENT_REQUIRED = "BODY_INFO_CONSENT_REQUIRED"
    BODY_PHOTO_NOT_FOUND = "BODY_PHOTO_NOT_FOUND"
    EMAIL_ALREADY_EXISTS = "EMAIL_ALREADY_EXISTS"
    INVITE_CODE_INVALID = "INVITE_CODE_INVALID"
    GARMENT_NOT_FOUND = "GARMENT_NOT_FOUND"
    GARMENT_BUSY = "GARMENT_BUSY"
    GARMENT_IMAGE_NOT_FOUND = "GARMENT_IMAGE_NOT_FOUND"
    UPLOAD_TOO_LARGE = "UPLOAD_TOO_LARGE"
    UPLOAD_UNSUPPORTED_TYPE = "UPLOAD_UNSUPPORTED_TYPE"
    NOT_ENOUGH_GARMENTS = "NOT_ENOUGH_GARMENTS"
    RECOMMENDATION_NOT_FOUND = "RECOMMENDATION_NOT_FOUND"
    RECOMMENDATION_RATE_LIMITED = "RECOMMENDATION_RATE_LIMITED"
    WEATHER_UNAVAILABLE = "WEATHER_UNAVAILABLE"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AppError(Exception):
    """서비스 코드에서는 이 예외만 던집니다.

    예: raise AppError(404, ErrorCode.GARMENT_NOT_FOUND, "옷을 찾을 수 없습니다")
    """

    def __init__(self, status: int, code: str, detail: str, *, headers: dict[str, str] | None = None):
        super().__init__(detail)
        self.status, self.code, self.detail = status, code, detail
        self.headers = headers or {}


def _body(code: str, detail, **extra) -> dict:
    return {"detail": detail, "code": code, **extra}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        headers = dict(exc.headers)
        if exc.status == 401:
            headers["WWW-Authenticate"] = "Bearer"
        return JSONResponse(status_code=exc.status, content=_body(exc.code, exc.detail), headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg")} for e in exc.errors()]
        return JSONResponse(
            status_code=422,
            content=_body(ErrorCode.VALIDATION_ERROR, "요청 형식이 올바르지 않습니다", errors=errors),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = ErrorCode.NOT_FOUND if exc.status_code == 404 else f"HTTP_{exc.status_code}"
        return JSONResponse(status_code=exc.status_code, content=_body(code, exc.detail), headers=exc.headers)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500, content=_body(ErrorCode.INTERNAL_ERROR, "서버 내부 오류가 발생했습니다")
        )
