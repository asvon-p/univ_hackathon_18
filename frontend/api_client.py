"""팀원 D — 백엔드(C) API 연결 모듈.

프론트 화면(app.py)은 백엔드를 직접 부르지 않고 이 파일의 함수만 호출합니다.
C의 API 형식이 정해지거나 바뀌면 이 파일만 고치면 됩니다.

환경 변수
- BACKEND_API_URL   : 백엔드 주소 (기본 http://localhost:8000/api/v1)
- FRONTEND_USE_MOCK : "false"면 실제 백엔드 호출, 그 외에는 Mock (기본 Mock)
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

import httpx

BACKEND_API_URL = os.getenv("BACKEND_API_URL", "http://localhost:8000/api/v1").rstrip("/")
USE_MOCK = os.getenv("FRONTEND_USE_MOCK", "true").lower() != "false"
TIMEOUT = httpx.Timeout(10.0)

# Mock 모드 데모 계정 (화면 확인용, 실제 백엔드와 무관)
DEMO_EMAIL = "demo@fitjang.com"
DEMO_PASSWORD = "fitjang1234"


class ApiError(Exception):
    """백엔드 에러를 하나의 형태로 맞춥니다.

    - 팀 규칙: {"detail": "메시지", "code": "GARMENT_NOT_FOUND"}
    - FastAPI 기본 422: {"detail": [{"loc": [...], "msg": "...", "type": "..."}]}
    - status 0 = 서버에 연결하지 못함
    """

    def __init__(self, status: int, message: str, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code


@dataclass
class Token:
    access_token: str
    token_type: str
    expires_in: int  # 초


@dataclass
class Profile:
    """사용자 신체 정보 — 모두 선택 입력 (DECISIONS: 분리된 테이블에 저장)."""

    height_cm: float | None = None
    weight_kg: float | None = None
    age: int | None = None
    gender: str | None = None  # "female" | "male" | "other" | None (제안 값, C 확인 필요)

    def to_json(self) -> dict:
        return {"height_cm": self.height_cm, "weight_kg": self.weight_kg, "age": self.age, "gender": self.gender}

    @classmethod
    def from_json(cls, data: dict) -> "Profile":
        return cls(
            height_cm=data.get("height_cm"),
            weight_kg=data.get("weight_kg"),
            age=data.get("age"),
            gender=data.get("gender"),
        )


# ---------------------------------------------------------------------------
# 인증·프로필 — 아직 백엔드에 없는 API라 프론트가 제안하는 형식입니다 (C 확인 필요)
#
# POST /auth/login   JSON {"email", "password"}
#   200 {"access_token": "<JWT>", "token_type": "bearer", "expires_in": 86400}
#   401 {"detail": "...", "code": "INVALID_CREDENTIALS"}
#
# POST /auth/signup  JSON {"email", "password"}   ← 가입하면 바로 로그인되도록 토큰 반환
#   201 {"access_token", "token_type", "expires_in"}
#   400 {"detail": "...", "code": "EMAIL_ALREADY_EXISTS"}
#
# GET  /users/me/profile                (Authorization: Bearer)
#   200 {"height_cm": 170.0, "weight_kg": 60.5, "age": 24, "gender": "female"}  ← 값이 없으면 null
# PUT  /users/me/profile  JSON 위와 같은 형식
#   200 저장된 프로필
# ---------------------------------------------------------------------------
async def login(email: str, password: str) -> Token:
    if USE_MOCK:
        return await _mock_login(email, password)
    data = await _request("POST", "/auth/login", json={"email": email, "password": password})
    return _token(data)


async def signup(email: str, password: str) -> Token:
    if USE_MOCK:
        return await _mock_signup(email, password)
    data = await _request("POST", "/auth/signup", json={"email": email, "password": password})
    return _token(data)


async def get_profile(token: str) -> Profile:
    if USE_MOCK:
        return _mock_get_profile(token)
    return Profile.from_json(await _request("GET", "/users/me/profile", token=token))


async def save_profile(token: str, profile: Profile) -> Profile:
    if USE_MOCK:
        return await _mock_save_profile(token, profile)
    return Profile.from_json(await _request("PUT", "/users/me/profile", token=token, json=profile.to_json()))


def _token(data: dict) -> Token:
    return Token(
        access_token=data["access_token"],
        token_type=data.get("token_type", "bearer"),
        expires_in=int(data.get("expires_in", 60 * 60 * 24)),
    )


# ---------------------------------------------------------------------------
# Mock — 서버를 끄면 사라지는 메모리 저장소 (화면 확인용)
# ---------------------------------------------------------------------------
_MOCK_USERS: dict[str, str] = {DEMO_EMAIL: DEMO_PASSWORD}  # email → password
_MOCK_PROFILES: dict[str, Profile] = {DEMO_EMAIL: Profile(height_cm=165, age=24, gender="female")}
_DAY = 60 * 60 * 24


def _mock_token(email: str) -> Token:
    return Token(access_token=f"mock-token:{email}", token_type="bearer", expires_in=_DAY)


def _mock_email(token: str) -> str:
    email = token.removeprefix("mock-token:")
    if not token.startswith("mock-token:") or email not in _MOCK_USERS:
        raise ApiError(401, "로그인이 필요해요.", "UNAUTHORIZED")
    return email


async def _mock_login(email: str, password: str) -> Token:
    await asyncio.sleep(0.4)
    email = email.strip().lower()
    if _MOCK_USERS.get(email) != password:
        raise ApiError(401, "이메일 또는 비밀번호가 맞지 않아요.", "INVALID_CREDENTIALS")
    return _mock_token(email)


async def _mock_signup(email: str, password: str) -> Token:
    await asyncio.sleep(0.4)
    email = email.strip().lower()
    if email in _MOCK_USERS:
        raise ApiError(400, "이미 가입된 이메일이에요.", "EMAIL_ALREADY_EXISTS")
    _MOCK_USERS[email] = password
    _MOCK_PROFILES[email] = Profile()
    return _mock_token(email)


def _mock_get_profile(token: str) -> Profile:
    return _MOCK_PROFILES.get(_mock_email(token), Profile())


async def _mock_save_profile(token: str, profile: Profile) -> Profile:
    await asyncio.sleep(0.3)
    _MOCK_PROFILES[_mock_email(token)] = profile
    return profile


# ---------------------------------------------------------------------------
# 공통 요청
# ---------------------------------------------------------------------------
async def _request(method: str, path: str, *, token: str | None = None, **kwargs) -> dict:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(base_url=BACKEND_API_URL, timeout=TIMEOUT) as client:
            res = await client.request(method, path, headers=headers, **kwargs)
    except httpx.HTTPError:
        raise ApiError(0, "서버에 연결할 수 없어요. 잠시 후 다시 시도해 주세요.", "NETWORK_ERROR") from None

    if res.status_code == 204:
        return {}
    try:
        data = res.json()
    except ValueError:
        data = None
    if res.is_error:
        raise _to_api_error(res.status_code, data)
    return data if isinstance(data, dict) else {"items": data}


def _to_api_error(status: int, data: object) -> ApiError:
    fallback = _DEFAULT_MESSAGES.get(status, "알 수 없는 오류가 발생했어요. 잠시 후 다시 시도해 주세요.")
    if isinstance(data, dict):
        detail, code = data.get("detail"), data.get("code")
        if isinstance(detail, str) and detail:
            return ApiError(status, detail, code)
        if isinstance(detail, list):
            return ApiError(status, fallback, code or "VALIDATION_ERROR")
    return ApiError(status, fallback)


_DEFAULT_MESSAGES = {
    400: "요청이 올바르지 않아요.",
    401: "로그인이 필요해요.",
    403: "접근 권한이 없어요.",
    404: "찾을 수 없어요.",
    413: "파일이 너무 커요. 10MB 이하로 올려 주세요.",
    415: "jpg 또는 png 파일만 올릴 수 있어요.",
    422: "입력값을 다시 확인해 주세요.",
}
