"""연결: 로그인·회원가입·프로필 ↔ C의 feat/auth-profile  (상태: 미완성 · Mock으로 동작)

아직 백엔드에 없는 API라 프론트가 제안하는 형식입니다. C가 다르게 만들면 이 파일만 고칩니다.

POST /auth/login   JSON {"email", "password"}
  200 {"access_token": "<JWT>", "token_type": "bearer", "expires_in": 86400}
  401 {"detail": "...", "code": "INVALID_CREDENTIALS"}

POST /auth/signup  JSON {"email", "password"}   ← 가입하면 바로 로그인되도록 토큰 반환
  201 {"access_token", "token_type", "expires_in"}
  400 {"detail": "...", "code": "EMAIL_ALREADY_EXISTS"}

GET  /users/me/profile                (Authorization: Bearer)
  200 {"height_cm": 170.0, "weight_kg": 60.5, "age": 24, "gender": "female"}  ← 값이 없으면 null
PUT  /users/me/profile  JSON 위와 같은 형식 (body_photo_updated_at은 보내지 않음)
  200 저장된 프로필
  ※ GET 응답에 "body_photo_updated_at": "2026-11-07T12:00:00Z" | null 도 넣어 주세요 (전신 사진 유무·캐시용)

전신 사진 (FASHN VTON v1.5 착샷의 사람 사진) — 프론트가 항상 세로 2:3, 1080x1620 JPEG로 만들어 보냄
PUT    /users/me/body-photo   multipart "file" (jpg·png, 10MB 이하)
  200 {"body_photo_updated_at": "..."}   400/413/415 {"detail", "code"}
  (선택) 머리~발끝이 다 안 보이면 422 {"detail": "머리부터 발끝까지 나오게 찍어 주세요.", "code": "BODY_NOT_FULL"}
GET    /users/me/body-photo   → 이미지 파일 / 404 BODY_PHOTO_NOT_FOUND
DELETE /users/me/body-photo   → 204

TODO(C 확인): gender 값("female"/"male"/"other"/null)은 contracts.py에 아직 없음
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from . import USE_MOCK, ApiError, request, request_bytes

# Mock 모드 데모 계정 (화면 확인용, 실제 백엔드와 무관)
DEMO_EMAIL = "demo@fitjang.com"
DEMO_PASSWORD = "fitjang1234"


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
    gender: str | None = None
    body_photo_updated_at: str | None = None  # 읽기 전용: 전신 사진이 있으면 올린 시각

    def to_json(self) -> dict:
        return {
            "height_cm": self.height_cm,
            "weight_kg": self.weight_kg,
            "age": self.age,
            "gender": self.gender,
        }

    @classmethod
    def from_json(cls, data: dict) -> Profile:
        return cls(
            height_cm=data.get("height_cm"),
            weight_kg=data.get("weight_kg"),
            age=data.get("age"),
            gender=data.get("gender"),
            body_photo_updated_at=data.get("body_photo_updated_at"),
        )


async def login(email: str, password: str) -> Token:
    if USE_MOCK:
        return await _mock_login(email, password)
    return _token(await request("POST", "/auth/login", json={"email": email, "password": password}))


async def signup(email: str, password: str, invite_code: str = "") -> Token:
    if USE_MOCK:
        return await _mock_signup(email, password)
    return _token(
        await request(
            "POST", "/auth/signup", json={"email": email, "password": password, "invite_code": invite_code}
        )
    )


async def get_profile(token: str) -> Profile:
    if USE_MOCK:
        email = mock_email(token)
        photo = _MOCK_BODY_PHOTOS.get(email)
        return replace(
            _MOCK_PROFILES.get(email, Profile()),
            body_photo_updated_at=photo[2] if photo else None,
        )
    return Profile.from_json(await request("GET", "/users/me/profile", token=token))


async def save_profile(token: str, profile: Profile, *, consent_body_info: bool | None = None) -> Profile:
    if USE_MOCK:
        await asyncio.sleep(0.3)
        _MOCK_PROFILES[mock_email(token)] = profile
        return profile
    payload = profile.to_json()
    if consent_body_info is not None:
        payload["consent_body_info"] = consent_body_info
    return Profile.from_json(await request("PUT", "/users/me/profile", token=token, json=payload))


# ---- 전신 사진 (착샷용) ------------------------------------------------------
async def upload_body_photo(token: str, filename: str, content: bytes, content_type: str) -> str | None:
    """전신 사진 저장 → 올린 시각."""
    if USE_MOCK:
        await asyncio.sleep(0.4)
        now = datetime.now(UTC).isoformat(timespec="seconds")
        _MOCK_BODY_PHOTOS[mock_email(token)] = (content, content_type, now)
        return now
    files = {"file": (filename, content, content_type)}
    data = await request("PUT", "/users/me/body-photo", token=token, files=files)
    return data.get("body_photo_updated_at")


async def fetch_body_photo(token: str) -> tuple[bytes, str]:
    """전신 사진 (내용, content-type). 없으면 ApiError(404)."""
    if USE_MOCK:
        photo = _MOCK_BODY_PHOTOS.get(mock_email(token))
        if not photo:
            raise ApiError(404, "전신 사진이 없어요.", "BODY_PHOTO_NOT_FOUND")
        return photo[0], photo[1]
    return await request_bytes("GET", "/users/me/body-photo", token=token)


async def delete_body_photo(token: str) -> None:
    if USE_MOCK:
        _MOCK_BODY_PHOTOS.pop(mock_email(token), None)
        return
    await request("DELETE", "/users/me/body-photo", token=token)


def _token(data: dict) -> Token:
    return Token(
        access_token=data["access_token"],
        token_type=data.get("token_type", "bearer"),
        expires_in=int(data.get("expires_in", 60 * 60 * 24)),
    )


# ---- Mock: 서버를 끄면 사라지는 메모리 저장소 ---------------------------------
_MOCK_USERS: dict[str, str] = {DEMO_EMAIL: DEMO_PASSWORD}  # email → password
_MOCK_PROFILES: dict[str, Profile] = {DEMO_EMAIL: Profile(height_cm=165, age=24, gender="female")}
_MOCK_BODY_PHOTOS: dict[str, tuple[bytes, str, str]] = {}  # email → (사진, 형식, 올린 시각)
_DAY = 60 * 60 * 24


def mock_email(token: str) -> str:
    """Mock 토큰 → 이메일. 다른 연결 파일(옷장 Mock 등)에서도 사용."""
    email = token.removeprefix("mock-token:")
    if not token.startswith("mock-token:") or email not in _MOCK_USERS:
        raise ApiError(401, "로그인이 필요해요.", "UNAUTHORIZED")
    return email


async def _mock_login(email: str, password: str) -> Token:
    await asyncio.sleep(0.4)
    email = email.strip().lower()
    if _MOCK_USERS.get(email) != password:
        raise ApiError(401, "이메일 또는 비밀번호가 맞지 않아요.", "INVALID_CREDENTIALS")
    return Token(access_token=f"mock-token:{email}", token_type="bearer", expires_in=_DAY)


async def _mock_signup(email: str, password: str) -> Token:
    await asyncio.sleep(0.4)
    email = email.strip().lower()
    if email in _MOCK_USERS:
        raise ApiError(400, "이미 가입된 이메일이에요.", "EMAIL_ALREADY_EXISTS")
    _MOCK_USERS[email] = password
    _MOCK_PROFILES[email] = Profile()
    return Token(access_token=f"mock-token:{email}", token_type="bearer", expires_in=_DAY)
