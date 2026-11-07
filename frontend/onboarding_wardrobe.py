"""feat/onboarding-wardrobe-ui (D) — 첫 화면·로그인·회원가입·프로필·옷장·옷 등록.

다른 팀원과 연결되는 부분은 connect/ 폴더에만 있습니다:
- connect/auth_profile.py     ↔ C feat/auth-profile
- connect/garment_storage.py  ↔ C feat/garment-storage (+ B vision·segmentation)
"""

from __future__ import annotations

import re
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .common import (
    CATEGORY_ORDER,
    LABELS,
    TOKEN_COOKIE,
    current_user,
    go_login,
    http_status,
    ko,
    logged_in,
    logged_out,
    register_templates,
    render,
    safe_next,
)
from .connect import ApiError
from .connect import auth_profile as auth
from .connect import garment_storage as closet

router = APIRouter()

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
GENDERS = [("", "선택 안 함"), ("female", "여성"), ("male", "남성"), ("other", "기타")]
# 종류별 세부 종류 선택지 (contracts.py에 SUBCATEGORIES가 생기면 그걸로 교체)
SUBCATEGORIES = {
    "top": [
        "t_shirt",
        "shirt",
        "blouse",
        "sweater",
        "sweatshirt",
        "hoodie",
        "cardigan",
        "vest",
    ],
    "bottom": ["jeans", "slacks", "pants", "jogger", "shorts", "skirt"],
    "outerwear": ["coat", "jacket", "padding", "jumper", "blazer"],
    "shoes": ["sneakers", "loafers", "boots", "sandals", "heels", "dress_shoes"],
}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # DECISIONS: jpg·png, 최대 10MB
MAX_STYLES = 3


def check_email(email: str) -> str | None:
    if not email:
        return "이메일을 입력해 주세요."
    if not EMAIL_RE.match(email):
        return "이메일 형식이 올바르지 않아요."
    return None


# =============================================================================
# 로그인·회원가입·프로필
# =============================================================================
# ---- 로그인 -----------------------------------------------------------------
@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str | None = None):
    if current_user(request) is not None:
        return RedirectResponse(safe_next(next), status_code=303)
    return render(request, "login.html", next=safe_next(next))


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    next: str = Form("/"),
):
    email = email.strip()
    errors = {
        k: v
        for k, v in {
            "email": check_email(email),
            "password": None if password else "비밀번호를 입력해 주세요.",
        }.items()
        if v
    }
    if errors:
        return render(
            request,
            "login.html",
            status=422,
            email=email,
            errors=errors,
            next=safe_next(next),
        )

    try:
        token = await auth.login(email, password)
    except ApiError as e:
        # 비밀번호 칸은 비우고 서버 메시지만 보여줌
        return render(
            request,
            "login.html",
            status=http_status(e),
            email=email,
            server_error=e.message,
            next=safe_next(next),
        )
    return logged_in(safe_next(next), token, email)


@router.post("/logout")
async def logout():
    return logged_out()


# ---- 회원가입 ---------------------------------------------------------------
@router.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    if current_user(request) is not None:
        return RedirectResponse("/", status_code=303)
    return render(request, "signup.html")


@router.post("/signup", response_class=HTMLResponse)
async def signup_submit(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    password_confirm: str = Form(""),
):
    email = email.strip()
    errors: dict[str, str] = {}
    if msg := check_email(email):
        errors["email"] = msg
    if not password:
        errors["password"] = "비밀번호를 입력해 주세요."
    elif (
        len(password) < 8
        or not re.search(r"[A-Za-z]", password)
        or not re.search(r"\d", password)
    ):
        errors["password"] = "영문과 숫자를 섞어 8자 이상 입력해 주세요."
    if not password_confirm:
        errors["password_confirm"] = "비밀번호를 한 번 더 입력해 주세요."
    elif password and password != password_confirm:
        errors["password_confirm"] = "비밀번호가 서로 달라요."
    if errors:
        return render(request, "signup.html", status=422, email=email, errors=errors)

    try:
        token = await auth.signup(email, password)
    except ApiError as e:
        if e.code == "EMAIL_ALREADY_EXISTS":
            return render(
                request,
                "signup.html",
                status=http_status(e),
                email=email,
                errors={"email": e.message},
            )
        return render(
            request,
            "signup.html",
            status=http_status(e),
            email=email,
            server_error=e.message,
        )
    # 가입하면 바로 로그인 → 프로필 입력(건너뛰기 가능)
    return logged_in("/profile?welcome=1", token, email)


# ---- 프로필 (키·몸무게·나이·성별, 모두 선택) ----------------------------------
@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, welcome: int = 0, done: str = ""):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return go_login(request)
    try:
        profile = await auth.get_profile(token)
    except ApiError as e:
        if e.status == 401:
            return logged_out()
        return render(
            request,
            "profile.html",
            active="profile",
            bare=bool(welcome),
            status=http_status(e),
            welcome=bool(welcome),
            form=ProfileForm.empty(),
            server_error=e.message,
        )
    return render(
        request,
        "profile.html",
        active="profile",
        bare=bool(welcome),
        welcome=bool(welcome),
        form=ProfileForm.from_profile(profile),
        body_photo=profile.body_photo_updated_at,
        notice=DONE_MESSAGES.get(done),
    )


@router.post("/profile", response_class=HTMLResponse)
async def profile_submit(
    request: Request,
    height_cm: str = Form(""),
    weight_kg: str = Form(""),
    age: str = Form(""),
    gender: str = Form(""),
    welcome: int = Form(0),
):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return go_login(request)
    form = ProfileForm(
        height_cm=height_cm.strip(),
        weight_kg=weight_kg.strip(),
        age=age.strip(),
        gender=gender,
    )
    profile, errors = form.validate()
    if errors:
        return render(
            request,
            "profile.html",
            active="profile",
            bare=bool(welcome),
            status=422,
            welcome=bool(welcome),
            form=form,
            errors=errors,
            body_photo=await body_photo_version(token),
        )
    try:
        await auth.save_profile(token, profile)
    except ApiError as e:
        if e.status == 401:
            return logged_out()
        return render(
            request,
            "profile.html",
            active="profile",
            bare=bool(welcome),
            status=http_status(e),
            welcome=bool(welcome),
            form=form,
            server_error=e.message,
            body_photo=await body_photo_version(token),
        )
    if welcome:  # 가입 직후: 1/2 신체 정보 → 2/2 전신 사진
        return RedirectResponse("/profile/body-photo?welcome=1", status_code=303)
    return RedirectResponse("/wardrobe?done=saved", status_code=303)


class ProfileForm:
    """화면 입력값(문자열) ↔ auth.Profile 변환과 검사."""

    def __init__(
        self, height_cm: str = "", weight_kg: str = "", age: str = "", gender: str = ""
    ):
        self.height_cm, self.weight_kg, self.age, self.gender = (
            height_cm,
            weight_kg,
            age,
            gender,
        )

    @classmethod
    def empty(cls) -> ProfileForm:
        return cls()

    @classmethod
    def from_profile(cls, p: auth.Profile) -> ProfileForm:
        def num(v: float | None) -> str:
            if v is None:
                return ""
            return str(int(v)) if float(v).is_integer() else str(v)

        return cls(num(p.height_cm), num(p.weight_kg), num(p.age), p.gender or "")

    def validate(self) -> tuple[auth.Profile, dict[str, str]]:
        errors: dict[str, str] = {}

        def number(
            name: str, raw: str, lo: float, hi: float, unit: str, integer: bool = False
        ):
            if not raw:
                return None
            try:
                value = int(raw) if integer else round(float(raw), 1)
            except ValueError:
                errors[name] = (
                    "정수로 입력해 주세요." if integer else "숫자로 입력해 주세요."
                )
                return None
            if not lo <= value <= hi:
                errors[name] = f"{lo:g}~{hi:g}{unit} 사이로 입력해 주세요."
                return None
            return value

        profile = auth.Profile(
            height_cm=number("height_cm", self.height_cm, 100, 230, "cm"),
            weight_kg=number("weight_kg", self.weight_kg, 20, 200, "kg"),
            age=number("age", self.age, 10, 100, "세", integer=True),
            gender=self.gender or None,
        )
        if self.gender not in {g for g, _ in GENDERS}:
            errors["gender"] = "성별을 다시 선택해 주세요."
        return profile, errors


# ---- 전신 사진 (FASHN VTON v1.5 착샷의 사람 사진) ------------------------------
# 머리끝~발끝이 다 나온 세로 2:3 사진. 착샷 결과(576x864)와 같은 비율이고,
# 결과 크기에 맞추려고 줄이지는 않음 (FASHN 권장) → 1080x1620 JPEG
BODY_W, BODY_H = 1080, 1620


async def body_photo_version(token: str) -> str | None:
    """전신 사진을 올린 시각 (없거나 못 읽으면 None)."""
    try:
        return (await auth.get_profile(token)).body_photo_updated_at
    except ApiError:
        return None


@router.get("/profile/body-photo", response_class=HTMLResponse)
async def body_photo_page(request: Request, welcome: int = 0):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return go_login(request)
    return render(
        request,
        "body_photo.html",
        active="profile",
        bare=bool(welcome),
        welcome=bool(welcome),
        has_photo=bool(await body_photo_version(token)),
    )


@router.post("/profile/body-photo", response_class=HTMLResponse)
async def body_photo_upload(
    request: Request,
    file: Annotated[UploadFile | None, File()] = None,
    welcome: Annotated[int, Form()] = 0,
):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return go_login(request)

    def fail(status: int, message: str) -> HTMLResponse:
        return render(
            request,
            "body_photo.html",
            status=status,
            active="profile",
            bare=bool(welcome),
            welcome=bool(welcome),
            file_error=message,
        )

    try:
        content, content_type, size = await read_photo(file)
    except PhotoError as e:
        return fail(e.status, e.message)
    if size and abs(size[0] * BODY_H - size[1] * BODY_W) > size[0] * BODY_H * 0.02:
        return fail(400, "사진을 세로 2:3 비율로 잘라서 올려 주세요.")

    try:
        await auth.upload_body_photo(token, file.filename, content, content_type)
    except ApiError as e:
        if e.status == 401:
            return logged_out()
        return fail(http_status(e), e.message)
    if welcome:
        return RedirectResponse("/wardrobe?done=body_saved", status_code=303)
    return RedirectResponse("/profile?done=body_saved", status_code=303)


@router.post("/profile/body-photo/delete")
async def body_photo_delete(request: Request):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return go_login(request)
    try:
        await auth.delete_body_photo(token)
    except ApiError as e:
        if e.status == 401:
            return logged_out()
        if e.status != 404:  # 이미 없으면 지운 것과 같음
            return render(
                request,
                "placeholder.html",
                status=http_status(e),
                active="profile",
                title="전신 사진을 지우지 못했어요",
                message=e.message,
                back="/profile",
            )
    return RedirectResponse("/profile?done=body_deleted", status_code=303)


@router.get("/body-photo")
async def body_photo_image(request: Request):
    """내 전신 사진 대신 받아 주기 (토큰을 붙여 백엔드에서 받아 옴). 주소의 ?v=올린 시각으로 캐시 구분."""
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return Response(status_code=401)
    try:
        content, content_type = await auth.fetch_body_photo(token)
    except ApiError as e:
        return Response(status_code=e.status if e.status >= 400 else 502)
    return Response(
        content,
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=300"},
    )


# =============================================================================
# 옷장·옷 등록
# =============================================================================
# ---- 옷장 -------------------------------------------------------------------
@router.get("/wardrobe", response_class=HTMLResponse)
async def wardrobe(
    request: Request,
    category: str = "",
    status: str = "",
    q: str = "",
    done: str = "",
):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return go_login(request)
    category = category if category in LABELS["category"] else ""
    status = status if status in LABELS["status"] else ""
    try:
        garments = await closet.list_garments(token)
    except ApiError as e:
        if e.status == 401:
            return logged_out()
        return render(
            request,
            "wardrobe.html",
            status=http_status(e),
            active="wardrobe",
            garments=[],
            total=0,
            counts={},
            category=category,
            status_filter=status,
            server_error=e.message,
        )

    q = " ".join(q.split())[:50]
    if q:
        garments_for_view = [g for g in garments if search_match(g, q)]
    else:
        garments_for_view = garments
    in_status = [g for g in garments_for_view if not status or g.status == status]
    counts = {c: sum(1 for g in in_status if g.category == c) for c in CATEGORY_ORDER}
    shown = [g for g in in_status if not category or g.category == category]
    return render(
        request,
        "wardrobe.html",
        active="wardrobe",
        garments=shown,
        total=len(garments),
        counts=counts,
        all_count=len(in_status),
        category=category,
        status_filter=status,
        q=q,
        notice=DONE_MESSAGES.get(done),
    )


def wardrobe_url(category: str = "", status: str = "", q: str = "") -> str:
    params = {
        k: v for k, v in (("category", category), ("status", status), ("q", q)) if v
    }
    return "/wardrobe" + (f"?{urlencode(params)}" if params else "")


# 검색할 때 쓰는 일상 색 이름 (화면 표시는 LABELS의 "블랙" 등)
COLOR_WORDS = {
    "black": "검정 검은 검은색",
    "white": "흰 흰색 하양 하얀",
    "gray": "회색 그레이",
    "ivory": "아이보리 크림",
    "beige": "베이지",
    "brown": "갈색 브라운",
    "camel": "카멜 낙타색",
    "khaki": "카키",
    "navy": "남색 네이비 곤색",
    "blue": "파랑 파란 파란색 청색",
    "sky_blue": "하늘 하늘색",
    "denim": "데님 청",
    "green": "초록 초록색 녹색",
    "olive": "올리브",
    "red": "빨강 빨간 빨간색",
    "burgundy": "버건디 와인",
    "pink": "분홍 분홍색",
    "purple": "보라 보라색",
    "yellow": "노랑 노란 노란색",
    "orange": "주황 주황색",
    "multi": "여러색 알록달록",
}


def search_match(g, query: str) -> bool:
    """검색: 이름·위치·세부 종류(니트)·종류·색·패턴·스타일·상태를 한글·영어로.
    여러 단어면 모두 들어 있어야 함 (예: "네이비 니트")."""
    words = [
        getattr(g, "name", None) or "",
        getattr(g, "location", None) or "",
        g.category,
        ko(g.category, "category"),
        g.subcategory or "",
        ko(g.subcategory, "subcategory") if g.subcategory else "",
        g.color or "",
        ko(g.color, "color") if g.color else "",
        COLOR_WORDS.get(g.color or "", ""),
        g.pattern,
        ko(g.pattern, "pattern"),
        ko(g.status, "status"),
        *g.style_candidates,
        *(ko(st, "style") for st in g.style_candidates),
    ]
    haystack = " ".join(words).lower().replace("_", " ").replace("-", " ")
    compact = haystack.replace(" ", "")
    return all(t in haystack or t in compact for t in query.lower().split())


# ---- 옷 등록: 1) 사진 올리기 → 2) 자동으로 채운 정보 확인 -------------------
@router.get("/garments/new", response_class=HTMLResponse)
async def garment_new(request: Request):
    if current_user(request) is None:
        return go_login(request)
    return render(request, "garment_new.html", active="wardrobe")


@router.post("/garments/new", response_class=HTMLResponse)
async def garment_upload(
    request: Request, file: Annotated[UploadFile | None, File()] = None
):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return go_login(request)

    def fail(status: int, message: str) -> HTMLResponse:
        return render(
            request,
            "garment_new.html",
            status=status,
            active="wardrobe",
            file_error=message,
        )

    try:
        content, content_type, size = await read_photo(file)
    except PhotoError as e:
        return fail(e.status, e.message)
    if size and abs(size[0] - size[1]) > 1:
        return fail(400, "사진을 1:1 정사각형으로 잘라서 올려 주세요.")

    try:
        garment = await closet.create_garment(
            token, file.filename, content, content_type
        )
    except ApiError as e:
        if e.status == 401:
            return logged_out()
        return fail(http_status(e), e.message)
    return RedirectResponse(f"/garments/{garment.garment_id}/confirm", status_code=303)


class PhotoError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


async def read_photo(
    file: UploadFile | None,
) -> tuple[bytes, str, tuple[int, int] | None]:
    """올린 사진 공통 검사 (jpg·png, 10MB) → (내용, content-type, (가로, 세로) 또는 None)."""
    if file is None or not file.filename:
        raise PhotoError(400, "사진을 골라 주세요.")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise PhotoError(413, "사진이 너무 커요. 10MB 이하로 올려 주세요.")
    content_type = image_type(content)
    if content_type is None:
        raise PhotoError(415, "jpg 또는 png 사진만 올릴 수 있어요.")
    return content, content_type, image_size(content)


def image_type(content: bytes) -> str | None:
    """확장자 대신 파일 앞부분으로 jpg·png 여부 확인."""
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    return None


# JPEG에서 가로·세로가 들어 있는 SOF 마커
_JPEG_SOF = {
    0xC0,
    0xC1,
    0xC2,
    0xC3,
    0xC5,
    0xC6,
    0xC7,
    0xC9,
    0xCA,
    0xCB,
    0xCD,
    0xCE,
    0xCF,
}


def image_size(content: bytes) -> tuple[int, int] | None:
    """(가로, 세로). 라이브러리 없이 jpg·png 헤더만 읽음. 못 읽으면 None."""
    if content.startswith(b"\x89PNG") and len(content) >= 24:
        return int.from_bytes(content[16:20], "big"), int.from_bytes(
            content[20:24], "big"
        )
    if content.startswith(b"\xff\xd8"):
        i = 2
        while i + 9 < len(content):
            if content[i] != 0xFF:
                i += 1
                continue
            marker = content[i + 1]
            if marker == 0xFF or marker == 0x01 or 0xD0 <= marker <= 0xD8:
                i += 1 if marker == 0xFF else 2
                continue
            if marker in _JPEG_SOF:
                h = int.from_bytes(content[i + 5 : i + 7], "big")
                w = int.from_bytes(content[i + 7 : i + 9], "big")
                return w, h
            i += 2 + int.from_bytes(content[i + 2 : i + 4], "big")
    return None


# ---- 2) 정보 확인 (등록 직후, mode="new") · 정보 수정 (상세 화면에서, mode="edit") ----
def garment_error(
    request: Request, e: ApiError, title: str = "옷을 찾을 수 없어요"
) -> HTMLResponse:
    return render(
        request,
        "placeholder.html",
        status=http_status(e),
        active="wardrobe",
        title=title,
        message=e.message,
        back="/wardrobe",
    )


async def my_locations(token: str) -> list[str]:
    """내 옷들에 이미 쓴 위치 (입력 칸 추천용)."""
    try:
        garments = await closet.list_garments(token)
    except ApiError:
        return []
    return sorted({g.location for g in garments if getattr(g, "location", None)})


async def garment_form_page(request: Request, garment_id: str, mode: str):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return go_login(request)
    try:
        garment = await closet.get_garment(token, garment_id)
    except ApiError as e:
        return logged_out() if e.status == 401 else garment_error(request, e)
    return render(
        request,
        "garment_confirm.html",
        active="wardrobe",
        mode=mode,
        g=garment,
        form=GarmentForm.from_garment(garment),
        locations=await my_locations(token),
    )


async def garment_form_submit(request: Request, garment_id: str, mode: str):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return go_login(request)
    data = await request.form()
    form = GarmentForm(
        str(data.get("category", "")),
        str(data.get("subcategory", "")),
        str(data.get("color", "unknown")),
        str(data.get("pattern", "unknown")),
        [str(v) for v in data.getlist("styles")],
        str(data.get("thermal_level", "")),
        str(data.get("name", "")),
        str(data.get("location", "")),
    )
    fields, errors = form.validate()
    try:
        if errors:
            garment = await closet.get_garment(token, garment_id)
            return render(
                request,
                "garment_confirm.html",
                status=422,
                active="wardrobe",
                mode=mode,
                g=garment,
                form=form,
                errors=errors,
                locations=await my_locations(token),
            )
        await closet.update_garment(token, garment_id, fields)
    except ApiError as e:
        return (
            logged_out()
            if e.status == 401
            else garment_error(request, e, "저장하지 못했어요")
        )
    if mode == "new":
        return RedirectResponse("/wardrobe?done=added", status_code=303)
    return RedirectResponse(f"/garments/{garment_id}?done=updated", status_code=303)


@router.get("/garments/{garment_id}/confirm", response_class=HTMLResponse)
async def garment_confirm(request: Request, garment_id: str):
    return await garment_form_page(request, garment_id, "new")


@router.post("/garments/{garment_id}/confirm", response_class=HTMLResponse)
async def garment_confirm_submit(request: Request, garment_id: str):
    return await garment_form_submit(request, garment_id, "new")


@router.get("/garments/{garment_id}/edit", response_class=HTMLResponse)
async def garment_edit(request: Request, garment_id: str):
    return await garment_form_page(request, garment_id, "edit")


@router.post("/garments/{garment_id}/edit", response_class=HTMLResponse)
async def garment_edit_submit(request: Request, garment_id: str):
    return await garment_form_submit(request, garment_id, "edit")


@router.post("/garments/{garment_id}/cancel")
async def garment_cancel(request: Request, garment_id: str):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return go_login(request)
    try:
        await closet.delete_garment(token, garment_id)
    except ApiError as e:
        if e.status == 401:
            return logged_out()
        # 이미 없는 옷이면 그냥 옷장으로
    return RedirectResponse("/wardrobe", status_code=303)


# ---- 옷 상세 · 상태 변경 · 삭제 ------------------------------------------------
DONE_MESSAGES = {
    "added": "옷장에 저장했어요.",
    "deleted": "옷을 삭제했어요.",
    "saved": "프로필을 저장했어요.",
    "updated": "정보를 저장했어요.",
    "status": "상태를 바꿨어요.",
    "body_saved": "전신 사진을 저장했어요.",
    "body_deleted": "전신 사진을 지웠어요.",
}


@router.get("/garments/{garment_id}", response_class=HTMLResponse)
async def garment_detail(request: Request, garment_id: str, done: str = ""):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return go_login(request)
    try:
        garment = await closet.get_garment(token, garment_id)
    except ApiError as e:
        return logged_out() if e.status == 401 else garment_error(request, e)
    return render(
        request,
        "garment_detail.html",
        active="wardrobe",
        g=garment,
        notice=DONE_MESSAGES.get(done),
    )


@router.post("/garments/{garment_id}/status")
async def garment_status(request: Request, garment_id: str, status: str = Form("")):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return go_login(request)
    if status not in LABELS["status"]:
        return RedirectResponse(f"/garments/{garment_id}", status_code=303)
    try:
        await closet.update_garment(token, garment_id, {"status": status})
    except ApiError as e:
        return (
            logged_out()
            if e.status == 401
            else garment_error(request, e, "상태를 바꾸지 못했어요")
        )
    return RedirectResponse(f"/garments/{garment_id}?done=status", status_code=303)


@router.post("/garments/{garment_id}/delete")
async def garment_delete(request: Request, garment_id: str):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return go_login(request)
    try:
        await closet.delete_garment(token, garment_id)
    except ApiError as e:
        return (
            logged_out()
            if e.status == 401
            else garment_error(request, e, "삭제하지 못했어요")
        )
    return RedirectResponse("/wardrobe?done=deleted", status_code=303)


@router.get("/garment-images/{garment_id}")
async def garment_image(request: Request, garment_id: str):
    """옷 사진 대신 받아 주기: 토큰을 붙여 백엔드에서 받아 옴 (connect/garment_storage.fetch_image)."""
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return Response(status_code=401)
    try:
        content, content_type = await closet.fetch_image(token, garment_id)
    except ApiError as e:
        return Response(status_code=e.status if e.status >= 400 else 502)
    return Response(
        content,
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=300"},
    )


MAX_TEXT = 30  # 이름·위치 최대 글자 수


class GarmentForm:
    """확인·수정 화면 입력값 검사 → PATCH /garments/{id} 로 보낼 값."""

    def __init__(
        self,
        category="",
        subcategory="",
        color="unknown",
        pattern="unknown",
        styles=(),
        thermal_level="",
        name="",
        location="",
    ):
        self.category, self.subcategory = category, subcategory
        self.color, self.pattern = color or "unknown", pattern or "unknown"
        self.styles, self.thermal_level = list(styles), str(thermal_level or "")
        self.name = " ".join((name or "").split())  # 앞뒤·중복 공백 정리
        self.location = " ".join((location or "").split())

    @classmethod
    def from_garment(cls, g) -> GarmentForm:
        return cls(
            g.category,
            (g.subcategory or "").replace("-", "_"),
            g.color or "unknown",
            g.pattern,
            g.style_candidates,
            g.thermal_level or "",
            getattr(g, "name", None) or "",
            getattr(g, "location", None) or "",
        )

    def validate(self) -> tuple[dict, dict[str, str]]:
        errors: dict[str, str] = {}
        if len(self.name) > MAX_TEXT:
            errors["name"] = f"이름은 {MAX_TEXT}자까지 쓸 수 있어요."
        if len(self.location) > MAX_TEXT:
            errors["location"] = f"위치는 {MAX_TEXT}자까지 쓸 수 있어요."
        if self.category not in LABELS["category"]:
            errors["category"] = "종류를 골라 주세요."
        elif self.subcategory and self.subcategory not in SUBCATEGORIES[self.category]:
            errors["subcategory"] = "세부 종류를 다시 골라 주세요."
        if self.color not in LABELS["color"]:
            errors["color"] = "색상을 다시 골라 주세요."
        if self.pattern not in LABELS["pattern"]:
            errors["pattern"] = "패턴을 다시 골라 주세요."
        if any(s not in LABELS["style"] for s in self.styles):
            errors["styles"] = "스타일을 다시 골라 주세요."
        elif len(self.styles) > MAX_STYLES:
            errors["styles"] = f"스타일은 {MAX_STYLES}개까지 고를 수 있어요."
        thermal = None
        if self.thermal_level:
            if self.thermal_level.isdigit() and 1 <= int(self.thermal_level) <= 5:
                thermal = int(self.thermal_level)
            else:
                errors["thermal_level"] = "두께를 다시 골라 주세요."
        fields = {
            "name": self.name or None,
            "location": self.location or None,
            "category": self.category,
            "subcategory": self.subcategory or None,
            "color": self.color,
            "pattern": self.pattern,
            "style_candidates": self.styles,
            "thermal_level": thermal,
        }
        return fields, errors


# =============================================================================
# 템플릿 (이 브랜치 화면)
# =============================================================================
AUTH_BASE_HTML = """{% extends "base.html" %}
{% from "macros.html" import brand %}
{% block head %}
<style>
  .auth { min-height: 100%; }
  .intro { display: none; }
  .form-side { min-height: 100%; padding: 40px 16px; display: flex; flex-direction: column; }
  .form-wrap { width: 100%; max-width: 384px; margin: 0 auto; }
  .mobile-brand { margin-bottom: 40px; }
  .mobile-brand p { margin: 12px 0 0; }
  .form-wrap form { margin-top: 32px; }
  .switch { margin-top: 24px; text-align: center; font-size: .875rem; }
  .switch a { font-weight: 600; text-decoration: none; color: var(--ink); }
  .switch a:hover { text-decoration: underline; text-underline-offset: 4px; }
  .mock { margin-top: 40px; padding: 16px; border: 1px dashed var(--line); border-radius: var(--radius);
          background: #f5f5f5; font-size: .875rem; }
  .mock strong { color: var(--ink-soft); }
  .mock p { margin: 4px 0 8px; }

  @media (min-width: 640px) { .form-side { padding: 40px 32px; } }
  @media (min-width: 1024px) {
    .auth { display: grid; grid-template-columns: 1.1fr 1fr; }
    .form-side { justify-content: center; }
    .mobile-brand { display: none; }
    .intro { position: relative; overflow: hidden; display: flex; flex-direction: column;
             justify-content: space-between; padding: 48px; background: var(--ink); color: #fff; }
    .intro h2 { margin: 0; font-size: 2.25rem; line-height: 1.25; }
    .intro ul { margin: 32px 0 0; padding: 0; list-style: none; color: #d4d4d4; }
    .intro li { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
    .intro li::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: var(--muted); }
    .intro .copy { position: relative; z-index: 1; max-width: 28rem; }
    .intro small { position: relative; z-index: 1; color: #737373; }
    .hanger { position: absolute; right: 48px; bottom: 112px; width: 20rem; color: #404040; pointer-events: none; }
  }
  @media (min-width: 1280px) { .hanger { width: 24rem; } }
</style>
{% endblock %}
{% block body %}
<div class="auth">
  <aside class="intro">
    {{ brand(inverted=True) }}
    <div class="copy">
    </div>
    <small>© 2026 Fitjang</small>
    <svg class="hanger" aria-hidden="true" viewBox="0 0 400 240" fill="none" stroke="currentColor"
         stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
      <path d="M176 44c0-13 11-24 24-24s24 11 24 24c0 12-9 18-20 24l-4 4"/>
      <path d="M200 72 30 200c-8 6-4 16 6 16h328c10 0 14-10 6-16L200 72Z"/>
      <path d="M70 216h260"/>
    </svg>
  </aside>
  <main class="form-side">
    <div class="form-wrap">
      <div class="mobile-brand">
        {{ brand() }}
      </div>
      {% block form %}{% endblock %}
    </div>
  </main>
</div>
{% endblock %}
"""

LOGIN_HTML = """{% extends "auth_base.html" %}
{% from "macros.html" import field %}
{% block title %}로그인{% endblock %}
{% block form %}
<h1>로그인</h1>
<form class="stack" method="post" action="/login" novalidate data-loading="로그인 중…">
  <input type="hidden" name="next" value="{{ next }}">
  {{ field("email", "이메일", value=email, error=errors.email, type="email", inputmode="email",
           autocomplete="email", placeholder="you@example.com",
           autofocus=errors.email or (not errors and not server_error)) }}
  {{ field("password", "비밀번호", error=errors.password, password=True, autocomplete="current-password",
           placeholder="비밀번호", autofocus=(errors.password and not errors.email) or server_error) }}
  {% if server_error %}<div class="alert" role="alert">{{ server_error }}</div>{% endif %}
  <button type="submit" class="btn btn-primary">로그인</button>
</form>
<p class="switch soft">아직 계정이 없나요? <a href="/signup">회원가입</a></p>

{% if USE_MOCK %}
<div class="mock">
  <strong>Mock 모드</strong>
  <p class="soft">백엔드 없이 동작 중이에요. 데모 계정: {{ DEMO_EMAIL }} / {{ DEMO_PASSWORD }}</p>
  <button type="button" class="link-btn" id="fill-demo">데모 계정으로 채우기</button>
</div>
{% endif %}
{% endblock %}
{% block script %}
<script>
  const demo = document.getElementById("fill-demo");
  if (demo) demo.addEventListener("click", () => {
    document.getElementById("email").value = "{{ DEMO_EMAIL }}";
    document.getElementById("password").value = "{{ DEMO_PASSWORD }}";
  });
</script>
{% endblock %}
"""

SIGNUP_HTML = """{% extends "auth_base.html" %}
{% from "macros.html" import field %}
{% block title %}회원가입{% endblock %}
{% block form %}
<h1>회원가입</h1>
<form class="stack" method="post" action="/signup" novalidate data-loading="가입 중…">
  {{ field("email", "이메일", value=email, error=errors.email, type="email", inputmode="email",
           autocomplete="email", placeholder="you@example.com",
           autofocus=errors.email or (not errors and not server_error)) }}
  {{ field("password", "비밀번호", error=errors.password, password=True, autocomplete="new-password",
           placeholder="비밀번호", hint="영문과 숫자를 섞어 8자 이상",
           autofocus=errors.password and not errors.email) }}
  {{ field("password_confirm", "비밀번호 확인", error=errors.password_confirm, password=True,
           autocomplete="new-password", placeholder="비밀번호 다시 입력",
           autofocus=errors.password_confirm and not errors.email and not errors.password) }}
  {% if server_error %}<div class="alert" role="alert">{{ server_error }}</div>{% endif %}
  <button type="submit" class="btn btn-primary">가입하기</button>
</form>
<p class="switch soft">이미 계정이 있나요? <a href="/login">로그인</a></p>
{% endblock %}
"""

PROFILE_HTML = """{% extends "app_base.html" %}
{% from "macros.html" import field %}
{% from "photo_picker.html" import silhouette %}
{% block title %}프로필{% endblock %}
{% block page_head %}
<style>
  .profile { max-width: 480px; margin: 0 auto; }
  .profile .lead { margin: 8px 0 0; }
  .bare .profile h1 { margin-top: 40px; }
  .profile form { margin-top: 32px; }
  .row { display: grid; gap: 20px; }
  .actions { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
  @media (min-width: 480px) { .row { grid-template-columns: repeat(3, 1fr); gap: 12px; } }
  .step { margin: 0 0 6px; font-size: .8125rem; font-weight: 600; color: var(--ink-soft); }
  .bare .profile .step { margin-top: 40px; }
  .bare .profile .step + h1 { margin-top: 0; }

  /* 전신 사진 (착샷용) */
  .body-card { display: grid; grid-template-columns: 88px 1fr; gap: 16px; margin-top: 28px; padding: 16px;
               border: 1px solid var(--line); border-radius: var(--radius); background: #fff; }
  .bp-thumb { width: 88px; aspect-ratio: 2 / 3; border-radius: 10px; overflow: hidden; background: #f5f5f5; }
  .bp-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .bp-thumb svg { width: 100%; height: 100%; display: block; }
  .bp-thumb .sil { fill: #e5e5e5; stroke: #a3a3a3; stroke-width: 2; }
  .bp-thumb .line { fill: none; stroke: var(--muted); stroke-width: 3; stroke-dasharray: 8 5; }
  .body-card h2 { margin: 0; font-size: 1rem; display: flex; align-items: center; gap: 6px; }
  .tag { padding: 2px 8px; border-radius: 6px; background: #f5f5f5; font-size: .75rem; font-weight: 600; color: var(--ink-soft); }
  .body-card p { margin: 4px 0 12px; font-size: .875rem; }
  .bp-btns { display: flex; flex-wrap: wrap; gap: 6px; align-items: flex-start; }
  .bp-btns > .btn, .bp-btns summary.btn { height: 38px; padding: 0 12px; font-size: .875rem; }
  details.danger summary { list-style: none; }
  details.danger summary::-webkit-details-marker { display: none; }
  details.danger summary.btn { color: var(--danger); }
  details.danger[open] summary { display: none; }
  details.danger[open] { flex-basis: 100%; }
  .confirm-box { scroll-margin-bottom: 96px; padding: 12px; border: 1px solid #f4c7c3; border-radius: var(--radius); background: var(--danger-soft); }
  .confirm-box p { margin: 0 0 10px; }
  .confirm-box form { margin: 0; }
  .confirm-box .two { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .confirm-box .btn { height: 38px; font-size: .875rem; width: 100%; }
  .btn-danger { background: var(--danger); color: #fff; }
  .btn-danger:hover { background: #912018; }
</style>
{% endblock %}
{% block content %}
<div class="profile">
  {% if notice %}<div class="notice" role="status" style="margin-bottom:16px">{{ notice }}</div>{% endif %}
  {% if welcome %}
  <p class="step">1 / 2 · 신체 정보</p>
  <h1>프로필</h1>
  <p class="lead soft">코디 추천에 참고할 정보</p>
  {% else %}
  <h1>프로필</h1>
  <p class="lead soft">코디 추천에 참고할 정보</p>

  <section class="body-card" aria-labelledby="bp-title">
    <div class="bp-thumb">
      {% if body_photo %}<img src="/body-photo?v={{ body_photo | urlencode }}" alt="내 전신 사진">
      {% else %}{{ silhouette() }}{% endif %}
    </div>
    <div>
      <h2 id="bp-title">전신 사진</h2>
      {% if not body_photo %}<p class="soft">착샷을 보려면 필요해요.</p>{% endif %}
      <div class="bp-btns">
        <a class="btn {{ 'btn-secondary' if body_photo else 'btn-primary' }}" href="/profile/body-photo">{{ "다시 올리기" if body_photo else "전신 사진 올리기" }}</a>
        {% if body_photo %}
        <details class="danger" ontoggle="if (this.open) this.querySelector('.confirm-box').scrollIntoView({ block: 'nearest', behavior: 'smooth' })">
          <summary class="btn btn-ghost">삭제</summary>
          <div class="confirm-box" role="alertdialog" aria-label="전신 사진 삭제 확인">
            <p>전신 사진을 삭제할까요?</p>
            <div class="two">
              <button type="button" class="btn btn-secondary" onclick="this.closest('details').open = false">취소</button>
              <form method="post" action="/profile/body-photo/delete"><button class="btn btn-danger">삭제</button></form>
            </div>
          </div>
        </details>
        {% endif %}
      </div>
    </div>
  </section>
  {% endif %}

  <form class="stack" method="post" action="/profile" novalidate data-loading="저장 중…">
    <input type="hidden" name="welcome" value="{{ 1 if welcome else 0 }}">
    <div class="row">
      {{ field("height_cm", "키", value=form.height_cm, error=errors.height_cm, inputmode="decimal",
               placeholder="165", suffix="cm", autofocus=errors.height_cm) }}
      {{ field("weight_kg", "몸무게", value=form.weight_kg, error=errors.weight_kg, inputmode="decimal",
               placeholder="55", suffix="kg", autofocus=errors.weight_kg and not errors.height_cm) }}
      {{ field("age", "나이", value=form.age, error=errors.age, inputmode="numeric",
               placeholder="24", suffix="세", autofocus=errors.age and not errors.height_cm and not errors.weight_kg) }}
    </div>

    <fieldset class="field">
      <legend>성별</legend>
      <div class="chips">
        {% for value, label in GENDERS %}
        <label class="chip">
          <input type="radio" name="gender" value="{{ value }}" {% if form.gender == value %}checked{% endif %}>
          <span>{{ label }}</span>
        </label>
        {% endfor %}
      </div>
      {% if errors.gender %}<p class="field-error">{{ errors.gender }}</p>{% endif %}
    </fieldset>

    {% if server_error %}<div class="alert" role="alert">{{ server_error }}</div>{% endif %}

    <div class="actions">
      <button type="submit" class="btn btn-primary">{{ "저장하고 시작하기" if welcome else "저장" }}</button>
      <a class="btn btn-ghost" href="{{ '/profile/body-photo?welcome=1' if welcome else '/wardrobe' }}">{{ "건너뛰기" if welcome else "취소" }}</a>
    </div>
  </form>
</div>
{% endblock %}
"""

BODY_PHOTO_HTML = """{% extends "app_base.html" %}
{% from "photo_picker.html" import picker_css, picker_js, silhouette, body_guide %}
{% block title %}전신 사진{% endblock %}
{% block page_head %}
{{ picker_css() }}
<style>
  .reg { max-width: 480px; margin: 0 auto; --ar: 2 / 3; }
  .bare .reg { padding-top: 24px; }
  .step { margin: 0 0 6px; font-size: .8125rem; font-weight: 600; color: var(--ink-soft); }
  .reg form { margin-top: 20px; }
  /* 세로 2:3 틀이 한 화면에 들어오게 (찍기·자르기 중에는 설명 문장 숨김) */
  .reg .frame { width: clamp(220px, calc((100dvh - 380px) * 2 / 3), 360px); max-width: 100%; }
  .reg:not([data-state="choose"]) .lead { display: none; }
  .reg .drop { aspect-ratio: auto; padding: 32px 20px; }
  .drop .mini { display: block; height: 132px; margin: 0 auto; }
  .drop .mini .sil { fill: #f5f5f5; stroke: var(--ink-soft); stroke-width: 2; }
  .drop .mini .line { fill: none; stroke: var(--muted); stroke-width: 2.5; stroke-dasharray: 8 5; }
  .guide { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
  .guide .sil { fill: rgb(255 255 255 / .08); stroke: rgb(255 255 255 / .8); stroke-width: 1.1; stroke-dasharray: 4 3; }
  .guide .line { fill: none; stroke: #fcd34d; stroke-width: 1; stroke-dasharray: 5 3; }
  .guide text { fill: #fff; font-size: 7.5px; font-weight: 600; paint-order: stroke; stroke: rgb(0 0 0 / .5); stroke-width: 2px; }
  .cam-bar .right .btn { padding: 0 10px; gap: 4px; }
  .cam-bar svg { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; }
  .center { margin: 0; text-align: center; }
  .check { font-weight: 600; }
  .privacy { font-size: .8125rem; color: var(--ink-soft); }
</style>
{% endblock %}
{% block content %}
<div class="reg" id="reg" data-state="choose">
  {% if welcome %}<p class="step">2 / 2 · 전신 사진</p>{% endif %}
  <h1>{{ "전신 사진 바꾸기" if has_photo else "전신 사진" }}</h1>
  <p class="soft lead" style="margin:6px 0 0">머리끝부터 발끝까지 나오게 찍어 주세요.</p>

  <form class="stack" method="post" action="/profile/body-photo" enctype="multipart/form-data"
        novalidate data-loading="저장 중…" id="upload-form">
    <input type="hidden" name="welcome" value="{{ 1 if welcome else 0 }}">
    <!-- 실제로 서버에 보내는 세로 2:3 사진 (1080x1620 JPEG) -->
    <input type="file" name="file" id="file" accept="image/jpeg" hidden>
    <input type="file" id="pick" accept="image/jpeg,image/png" hidden>
    <input type="file" id="snap" accept="image/jpeg,image/png" capture="environment" hidden>

    <section class="s-choose">
      <div class="drop{% if file_error %} invalid{% endif %}" id="drop">
        <div>
          {{ silhouette("mini") }}
          <strong>머리끝부터 발끝까지 나오게</strong>
          <small>세로 사진 · jpg, png · 10MB 이하</small>
          <div class="pick-btns">
            <button type="button" class="btn btn-primary" id="btn-camera">카메라로 찍기</button>
            <button type="button" class="btn btn-secondary" id="btn-album">앨범에서 고르기</button>
          </div>
        </div>
      </div>
    </section>

    <section class="s-camera" aria-label="카메라">
      <div class="frame cam"><video id="video" playsinline muted autoplay></video>{{ body_guide() }}<div class="count" id="countdown" aria-live="assertive"></div></div>
      <div class="cam-bar">
        <button type="button" class="btn btn-ghost left" id="cam-cancel">취소</button>
        <button type="button" class="shutter" id="shutter" aria-label="사진 찍기"></button>
        <div class="right">
          <button type="button" class="btn btn-ghost" id="cam-timer" aria-pressed="false" aria-label="타이머">
            <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2M9.5 2.5h5"/></svg><span>끔</span></button>
          <button type="button" class="btn btn-ghost" id="cam-flip" hidden>전환</button>
        </div>
      </div>
      <p class="hint center">머리끝·발끝을 노란 선 안에</p>
    </section>

    <section class="s-crop" aria-label="사진 자르기">
      <div class="frame crop" id="crop"><img id="crop-img" alt="자를 사진">{{ body_guide() }}</div>
      <p class="hint center">끌어서 옮기고 확대해서 노란 선 안에 맞추기</p>
      <div class="zoom-row">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14"/></svg>
        <input type="range" id="zoom" min="1" max="4" step="0.01" value="1" aria-label="확대">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M12 5v14"/></svg>
      </div>
      <div class="two">
        <button type="button" class="btn btn-secondary" id="crop-cancel">다른 사진</button>
        <button type="button" class="btn btn-primary" id="crop-ok">이대로 자르기</button>
      </div>
    </section>

    <section class="s-done" aria-label="올릴 사진">
      <div class="frame"><img class="result" id="result" alt="올릴 전신 사진 미리보기">{{ body_guide(sil=False) }}</div>
      <p class="center check">머리끝부터 발끝(신발)까지 다 나왔나요?</p>
      <button type="button" class="btn btn-secondary" id="redo">다시 찍기·고르기</button>
    </section>

    <p class="field-error" id="file-error" role="alert" {% if not file_error %}hidden{% endif %}>{{ file_error or "" }}</p>

    <p class="tips">정면 · 팔 살짝 벌리기 · 단순한 배경 · 혼자 찍을 땐 타이머</p>

    <div class="actions">
      <button type="submit" class="btn btn-primary" id="upload-btn">저장</button>
      <a class="btn btn-ghost" href="{{ '/wardrobe' if welcome else '/profile' }}">{{ "건너뛰기" if welcome else "취소" }}</a>
    </div>
  </form>
</div>
{% endblock %}
{% block script %}
{{ picker_js(1080, 1620, "body.jpg", 512) }}
{% endblock %}
"""

WARDROBE_HTML = """{% extends "app_base.html" %}
{% from "macros.html" import garment_art %}
{% block title %}내 옷장{% endblock %}
{% block page_head %}
<style>
  .page-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; }
  .page-head p { margin: 4px 0 0; }
  .add-btn { height: 40px; padding: 0 14px; font-size: .9375rem; flex-shrink: 0; }
  .add-btn svg { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 2.2; stroke-linecap: round; }
  .filters { margin-top: 20px; display: flex; flex-direction: column; gap: 10px; }
  .search { position: relative; display: flex; align-items: center; margin-top: 20px; }
  .search svg { position: absolute; left: 14px; width: 18px; height: 18px; fill: none; stroke: var(--muted);
                stroke-width: 2; stroke-linecap: round; pointer-events: none; }
  .search input { width: 100%; height: 44px; padding: 0 40px 0 42px; border: 1px solid var(--line); border-radius: 6px;
                  background: #fff; font: inherit; font-size: .9375rem; color: var(--ink); }
  .search input::placeholder { color: var(--muted); }
  .search input:focus { outline: none; border-color: var(--ink); box-shadow: 0 0 0 3px rgb(23 23 23 / .1); }
  .search input::-webkit-search-cancel-button { display: none; }
  .search .clear { position: absolute; right: 8px; display: grid; place-items: center; width: 28px; height: 28px;
                   border-radius: 50%; color: var(--ink-soft); text-decoration: none; font-size: 1.125rem; }
  .search .clear:hover { background: #f5f5f5; color: var(--ink); }
  .result-count { margin: 12px 0 0; font-size: .875rem; color: var(--ink-soft); }
  .result-count b { color: var(--ink); }
  .loc { margin-top: 4px; display: flex; align-items: center; gap: 4px; font-size: .75rem; color: var(--muted);
         white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .loc svg { width: 12px; height: 12px; flex-shrink: 0; fill: none; stroke: currentColor; stroke-width: 2; }
  .card-body h2 { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .frow { display: flex; gap: 8px; overflow-x: auto; scrollbar-width: none; margin: 0 -16px; padding: 0 16px; }
  .frow::-webkit-scrollbar { display: none; }
  .fchip { flex-shrink: 0; display: inline-flex; align-items: center; gap: 6px; height: 36px; padding: 0 14px;
           border: 1px solid var(--line); border-radius: 6px; background: #fff; text-decoration: none;
           font-size: .875rem; color: var(--ink); }
  .fchip:hover { border-color: var(--ink-soft); }
  .fchip small { color: var(--muted); font-size: .8125rem; }
  .fchip[aria-current="true"] { background: var(--ink); border-color: var(--ink); color: #fff; }
  .fchip[aria-current="true"] small { color: #d4d4d4; }
  .frow.sub .fchip { height: 32px; border-color: transparent; background: none; color: var(--ink-soft); padding: 0 10px; }
  .frow.sub .fchip[aria-current="true"] { background: #eeeeee; color: var(--ink); font-weight: 600; }

  .grid { margin-top: 20px; display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }
  .card { background: #fff; border: 1px solid var(--line); border-radius: 10px; overflow: hidden; transition: border-color .15s, box-shadow .15s; }
  .card:hover { border-color: #d4d4d4; box-shadow: 0 4px 16px rgb(23 23 23 / .06); }
  .card-link { display: block; color: inherit; text-decoration: none; }
  .card-link:focus-visible { outline: 2px solid var(--ink); outline-offset: -2px; border-radius: 10px; }
  .thumb { position: relative; aspect-ratio: 1; display: grid; place-items: center; background: #f5f5f5; }
  .thumb svg { width: 64%; height: 64%; }
  .thumb img { width: 100%; height: 100%; object-fit: cover; }
  .card.off .thumb > :not(.badge) { opacity: .45; }
  .badge { position: absolute; top: 8px; left: 8px; padding: 3px 8px; border-radius: 6px; font-size: .75rem; font-weight: 600; }
  .badge.laundry { background: var(--ink); color: #fff; }
  .badge.inactive { background: #e5e5e5; color: var(--ink-soft); }
  .card-body { padding: 12px; }
  .card-body h2 { margin: 0; font-size: .9375rem; font-weight: 600; }
  .meta { margin-top: 4px; display: flex; align-items: center; gap: 6px; font-size: .8125rem; color: var(--ink-soft); }
  .dot { width: 10px; height: 10px; border-radius: 50%; border: 1px solid rgb(0 0 0 / .15); flex-shrink: 0; }
  .dot.multi { background: conic-gradient(#c0392b, #f2c94c, #2f7d4f, #2f5fb3, #c0392b); }
  .tags { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 4px; }
  .tags span { padding: 2px 8px; border-radius: 6px; background: #f5f5f5; font-size: .75rem; color: var(--ink-soft); }

  .empty { margin-top: 24px; padding: 48px 24px; border: 1px dashed var(--line); border-radius: 10px;
           background: #fff; text-align: center; }
  .empty svg { width: 56px; height: 56px; color: var(--muted); fill: none; stroke: currentColor; stroke-width: 1.5;
               stroke-linecap: round; stroke-linejoin: round; }
  .empty h2 { margin: 12px 0 0; font-size: 1.125rem; }
  .empty p { margin: 6px 0 20px; }

  @media (min-width: 640px) { .grid { grid-template-columns: repeat(3, 1fr); gap: 16px; } }
  @media (min-width: 768px) { .frow { margin: 0; padding: 0; flex-wrap: wrap; } }
  @media (min-width: 900px) { .grid { grid-template-columns: repeat(4, 1fr); } }
  @media (min-width: 1100px) { .grid { grid-template-columns: repeat(5, 1fr); } }
</style>
{% endblock %}
{% block content %}
{% if notice %}<div class="notice" role="status" style="margin-bottom:20px">{{ notice }}</div>{% endif %}
<div class="page-head">
  <div>
    <h1>내 옷장</h1>
    <p class="soft">{{ total }}벌</p>
  </div>
  <a class="btn btn-primary add-btn" href="/garments/new">
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>옷 등록
  </a>
</div>

{% if server_error %}<div class="alert" role="alert" style="margin-top:20px">{{ server_error }}</div>{% endif %}

{% if total == 0 and not server_error %}
<section class="empty">
  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 5a2 2 0 1 1 2 2v1.5"/><path d="M12 8.5 3.4 15.2a1 1 0 0 0 .6 1.8h16a1 1 0 0 0 .6-1.8z"/></svg>
  <h2>아직 등록한 옷이 없어요</h2>
  <a class="btn btn-primary" href="/garments/new">옷 등록하기</a>
</section>
{% elif total > 0 %}
<form class="search" method="get" action="/wardrobe" role="search">
  {% if category %}<input type="hidden" name="category" value="{{ category }}">{% endif %}
  {% if status_filter %}<input type="hidden" name="status" value="{{ status_filter }}">{% endif %}
  <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></svg>
  <input type="search" name="q" value="{{ q }}" placeholder="이름, 니트, 네이비, 위치로 검색" aria-label="옷 검색"
         enterkeyhint="search" autocomplete="off">
  {% if q %}<a class="clear" href="{{ wardrobe_url(category, status_filter) }}" aria-label="검색어 지우기">×</a>{% endif %}
</form>
{% if q %}<p class="result-count" role="status">‘<b>{{ q }}</b>’ 검색 결과 {{ all_count }}벌</p>{% endif %}
<div class="filters">
  <nav class="frow" aria-label="종류">
    <a class="fchip" href="{{ wardrobe_url('', status_filter, q) }}" {% if not category %}aria-current="true"{% endif %}>전체 <small>{{ all_count }}</small></a>
    {% for c in CATEGORY_ORDER %}
    <a class="fchip" href="{{ wardrobe_url(c, status_filter, q) }}" {% if category == c %}aria-current="true"{% endif %}>{{ c | ko("category") }} <small>{{ counts[c] }}</small></a>
    {% endfor %}
  </nav>
  <nav class="frow sub" aria-label="상태">
    <a class="fchip" href="{{ wardrobe_url(category, '', q) }}" {% if not status_filter %}aria-current="true"{% endif %}>모든 상태</a>
    {% for s in ["available", "laundry", "inactive"] %}
    <a class="fchip" href="{{ wardrobe_url(category, s, q) }}" {% if status_filter == s %}aria-current="true"{% endif %}>{{ s | ko("status") }}</a>
    {% endfor %}
  </nav>
</div>

{% if garments %}
<ul class="grid" role="list" style="list-style:none;padding:0">
  {% for g in garments %}
  <li class="card{% if g.status != 'available' %} off{% endif %}">
    <a class="card-link" href="/garments/{{ g.garment_id }}">
    <div class="thumb">
      {% if g.status != "available" %}<span class="badge {{ g.status }}">{{ g.status | ko("status") }}</span>{% endif %}
      {{ garment_art(g) }}
    </div>
    <div class="card-body">
      <h2>{{ garment_name(g) }}</h2>
      <div class="meta">
        <span class="dot{% if g.color == 'multi' %} multi{% endif %}" style="background-color: {{ color_hex(g.color) }}"></span>
        {{ (g.color or "unknown") | ko("color") }}{% if g.name and g.subcategory %} · {{ g.subcategory | ko("subcategory") }}{% elif g.pattern not in ("solid", "unknown") %} · {{ g.pattern | ko("pattern") }}{% endif %}
      </div>
      {% if g.location %}<div class="loc"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21s7-6.1 7-12a7 7 0 0 0-14 0c0 5.9 7 12 7 12Z"/><circle cx="12" cy="9" r="2.5"/></svg>{{ g.location }}</div>{% endif %}
      {% if g.style_candidates %}
      <div class="tags">{% for s in g.style_candidates[:2] %}<span>{{ s | ko("style") }}</span>{% endfor %}</div>
      {% endif %}
    </div>
    </a>
  </li>
  {% endfor %}
</ul>
{% else %}
<section class="empty">
  {% if q %}
  <h2>‘{{ q }}’에 맞는 옷이 없어요</h2>
  <a class="btn btn-secondary" href="/wardrobe">검색·필터 초기화</a>
  {% else %}
  <h2>조건에 맞는 옷이 없어요</h2>
  <a class="btn btn-secondary" href="/wardrobe">필터 초기화</a>
  {% endif %}
</section>
{% endif %}
{% endif %}
{% endblock %}
"""

PHOTO_PICKER_HTML = """{# 사진 올리기 공통: 카메라(틀 비율 그대로 찍기)·앨범(틀 비율로 자르기). 옷 등록(1:1)·전신 사진(2:3)에서 사용.
   화면에 필요한 id: reg(data-state) file pick snap drop btn-camera btn-album video cam-cancel shutter cam-flip
                    crop crop-img zoom crop-cancel crop-ok result redo file-error upload-btn (선택: cam-timer countdown) #}
{% macro picker_css() %}
<style>
  .reg section { display: none; }
  .reg[data-state="choose"] .s-choose, .reg[data-state="camera"] .s-camera,
  .reg[data-state="crop"] .s-crop, .reg[data-state="done"] .s-done { display: flex; flex-direction: column; gap: 12px; }
  .reg:not([data-state="choose"]) .tips { display: none; }
  .reg:not([data-state="done"]) #upload-btn { display: none; }
  .reg[data-state="camera"] .actions, .reg[data-state="crop"] .actions { display: none; }

  /* 사진 틀 (카메라·자르기·결과 공통), 비율은 --ar */
  .frame { position: relative; width: 100%; aspect-ratio: var(--ar, 1); margin: 0 auto; border-radius: 10px;
           overflow: hidden; background: #171717; }
  .frame > img.result { width: 100%; height: 100%; object-fit: cover; display: block; }
  .thirds { position: absolute; inset: 0; pointer-events: none; border: 1px solid rgb(255 255 255 / .5); border-radius: 10px;
            background:
              linear-gradient(to right, transparent 33%, rgb(255 255 255 / .35) 33%, rgb(255 255 255 / .35) calc(33% + 1px),
                              transparent calc(33% + 1px), transparent 66.6%, rgb(255 255 255 / .35) 66.6%,
                              rgb(255 255 255 / .35) calc(66.6% + 1px), transparent calc(66.6% + 1px)),
              linear-gradient(to bottom, transparent 33%, rgb(255 255 255 / .35) 33%, rgb(255 255 255 / .35) calc(33% + 1px),
                              transparent calc(33% + 1px), transparent 66.6%, rgb(255 255 255 / .35) 66.6%,
                              rgb(255 255 255 / .35) calc(66.6% + 1px), transparent calc(66.6% + 1px)); }

  /* 1) 고르기 */
  .drop { display: grid; place-items: center; width: 100%; aspect-ratio: var(--ar, 1); margin: 0 auto; padding: 24px;
          border: 1.5px dashed #d4d4d4; border-radius: 10px; background: #fff; text-align: center;
          transition: border-color .15s, background .15s; }
  .drop.over { border-color: var(--ink); background: #fafafa; }
  .drop.invalid { border-color: var(--danger); }
  .drop .ico { width: 44px; height: 44px; fill: none; stroke: var(--ink-soft); stroke-width: 1.5;
               stroke-linecap: round; stroke-linejoin: round; }
  .drop strong { display: block; margin-top: 12px; font-size: 1rem; }
  .drop small { display: block; margin-top: 4px; color: var(--ink-soft); }
  .pick-btns { display: flex; gap: 8px; justify-content: center; margin-top: 20px; flex-wrap: wrap; }
  .pick-btns .btn { height: 44px; }

  /* 2-a) 카메라 */
  .cam video { width: 100%; height: 100%; object-fit: cover; display: block; }
  .cam video.mirror { transform: scaleX(-1); }
  .count { position: absolute; inset: 0; display: grid; place-items: center; font-size: 5rem; font-weight: 700; color: #fff;
           text-shadow: 0 2px 16px rgb(0 0 0 / .55); pointer-events: none; }
  .count:empty { display: none; }
  .cam-bar { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: 8px; }
  .cam-bar .btn { height: 40px; padding: 0 12px; font-size: .9375rem; }
  .cam-bar .left { justify-self: start; }
  .cam-bar .right { justify-self: end; display: flex; gap: 4px; }
  .cam-bar #cam-timer[aria-pressed="true"] { background: var(--ink); color: #fff; }
  .shutter { width: 68px; height: 68px; border-radius: 50%; border: 4px solid var(--ink); background: #fff;
             box-shadow: inset 0 0 0 4px #fff, inset 0 0 0 30px var(--ink); cursor: pointer; }
  .shutter:active { transform: scale(.94); }
  .shutter:focus-visible { outline: 2px solid var(--ink); outline-offset: 4px; }
  .shutter.counting { box-shadow: inset 0 0 0 4px #fff, inset 0 0 0 30px var(--danger); border-color: var(--danger); }

  /* 2-b) 자르기 */
  .crop { touch-action: none; cursor: grab; user-select: none; }
  .crop:active { cursor: grabbing; }
  .crop img { position: absolute; left: 0; top: 0; max-width: none; transform-origin: 0 0; pointer-events: none; }
  .zoom-row { display: flex; align-items: center; gap: 12px; }
  .zoom-row svg { width: 18px; height: 18px; flex-shrink: 0; fill: none; stroke: var(--ink-soft); stroke-width: 2; stroke-linecap: round; }
  .zoom-row input { flex: 1; accent-color: var(--ink); }
  .two { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }

  .tips { margin: 0; font-size: .8125rem; color: var(--ink-soft); text-align: center; line-height: 1.6; }
  .actions { display: flex; flex-direction: column; gap: 8px; }
</style>
{% endmacro %}

{# out_w x out_h: 올리는 JPEG 크기 (틀 비율과 같아야 함), min_side: 너무 작은 사진 거르기(px, 0이면 안 함) #}
{% macro picker_js(out_w, out_h, filename, min_side=0) %}
<script>
(() => {
  const OUT_W = {{ out_w }}, OUT_H = {{ out_h }}, RATIO = OUT_W / OUT_H, MIN_SIDE = {{ min_side }};
  const MAX = 10 * 1024 * 1024, TYPES = ["image/jpeg", "image/png"];
  const $ = (id) => document.getElementById(id);
  const reg = $("reg"), fileInput = $("file"), pick = $("pick"), snap = $("snap");
  const err = $("file-error"), drop = $("drop"), uploadBtn = $("upload-btn");

  function setState(s) { reg.dataset.state = s; uploadBtn.disabled = s !== "done"; }
  function showError(msg) { err.textContent = msg; err.hidden = !msg; drop.classList.toggle("invalid", !!msg); }

  // ---- 결과 캔버스 → 업로드할 파일로 ------------------------------------
  function finish(canvas) {
    canvas.toBlob((blob) => {
      const dt = new DataTransfer();
      dt.items.add(new File([blob], {{ filename | tojson }}, { type: "image/jpeg" }));
      fileInput.files = dt.files;
      $("result").src = URL.createObjectURL(blob);
      setState("done");
    }, "image/jpeg", 0.9);
  }
  function blank() {
    const c = document.createElement("canvas"); c.width = OUT_W; c.height = OUT_H;
    const ctx = c.getContext("2d"); ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, OUT_W, OUT_H);
    return [c, ctx];
  }
  $("redo").addEventListener("click", () => { fileInput.value = ""; showError(""); setState("choose"); });

  // ---- 앨범·드래그 → 자르기 ---------------------------------------------
  $("btn-album").addEventListener("click", () => pick.click());
  pick.addEventListener("change", () => loadFile(pick.files[0]));
  snap.addEventListener("change", () => loadFile(snap.files[0]));
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, () => drop.classList.remove("over")));
  drop.addEventListener("drop", (e) => { e.preventDefault(); loadFile(e.dataTransfer.files[0]); });

  function loadFile(f) {
    pick.value = ""; snap.value = "";
    if (!f) return;
    if (!TYPES.includes(f.type)) return showError("jpg 또는 png 사진만 올릴 수 있어요.");
    if (f.size > MAX) return showError("사진이 너무 커요. 10MB 이하로 올려 주세요.");
    showError("");
    startCrop(URL.createObjectURL(f));
  }

  // ---- 자르기: 끌기·확대(막대·휠·두 손가락) -----------------------------
  const cropBox = $("crop"), cropImg = $("crop-img"), zoom = $("zoom");
  let bw = 0, bh = 0, base = 1, scale = 1, x = 0, y = 0;
  const pts = new Map();

  function startCrop(url) {
    cropImg.onload = () => {
      if (Math.min(cropImg.naturalWidth, cropImg.naturalHeight) < MIN_SIDE) {
        setState("choose");
        return showError("사진이 너무 작아요. 더 크게 찍은 사진을 골라 주세요.");
      }
      setState("crop"); layout();
    };
    cropImg.onerror = () => showError("사진을 열 수 없어요. 다른 사진을 골라 주세요.");
    cropImg.src = url;
  }
  function layout() {
    bw = cropBox.clientWidth; bh = cropBox.clientHeight;
    const w = cropImg.naturalWidth, h = cropImg.naturalHeight;
    cropImg.style.width = w + "px"; cropImg.style.height = h + "px";
    base = Math.max(bw / w, bh / h);            // 틀을 꽉 채우는 최소 배율
    scale = base; zoom.value = 1;
    x = (bw - w * scale) / 2; y = (bh - h * scale) / 2;
    apply();
  }
  function apply() {
    const w = cropImg.naturalWidth * scale, h = cropImg.naturalHeight * scale;
    x = Math.min(0, Math.max(bw - w, x)); y = Math.min(0, Math.max(bh - h, y));   // 빈 곳이 안 생기게
    cropImg.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
  }
  function zoomTo(z, cx = bw / 2, cy = bh / 2) {
    z = Math.min(4, Math.max(1, z));
    const next = base * z;
    x = cx - (cx - x) * next / scale; y = cy - (cy - y) * next / scale;
    scale = next; zoom.value = z; apply();
  }
  zoom.addEventListener("input", () => zoomTo(+zoom.value));
  cropBox.addEventListener("wheel", (e) => {
    e.preventDefault();
    const r = cropBox.getBoundingClientRect();
    zoomTo(+zoom.value * (e.deltaY < 0 ? 1.08 : 1 / 1.08), e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });
  cropBox.addEventListener("pointerdown", (e) => { cropBox.setPointerCapture(e.pointerId); pts.set(e.pointerId, [e.clientX, e.clientY]); });
  cropBox.addEventListener("pointermove", (e) => {
    if (!pts.has(e.pointerId)) return;
    const prev = pts.get(e.pointerId);
    if (pts.size === 1) {
      x += e.clientX - prev[0]; y += e.clientY - prev[1]; apply();
    } else if (pts.size === 2) {
      const other = [...pts.entries()].find(([id]) => id !== e.pointerId)[1];
      const d0 = Math.hypot(prev[0] - other[0], prev[1] - other[1]);
      const d1 = Math.hypot(e.clientX - other[0], e.clientY - other[1]);
      const r = cropBox.getBoundingClientRect();
      if (d0 > 0) zoomTo(+zoom.value * d1 / d0, (e.clientX + other[0]) / 2 - r.left, (e.clientY + other[1]) / 2 - r.top);
    }
    pts.set(e.pointerId, [e.clientX, e.clientY]);
  });
  ["pointerup", "pointercancel"].forEach((t) => cropBox.addEventListener(t, (e) => pts.delete(e.pointerId)));
  window.addEventListener("resize", () => { if (reg.dataset.state === "crop") layout(); });

  $("crop-cancel").addEventListener("click", () => setState("choose"));
  $("crop-ok").addEventListener("click", () => {
    const [c, ctx] = blank();
    ctx.drawImage(cropImg, -x / scale, -y / scale, bw / scale, bh / scale, 0, 0, OUT_W, OUT_H);
    finish(c);
  });

  // ---- 카메라: 틀 비율 그대로 찍기 (+ 타이머가 있으면 3초·10초) ----------
  const video = $("video"), flip = $("cam-flip"), shutter = $("shutter");
  const timerBtn = $("cam-timer"), countEl = $("countdown");
  const DELAYS = [0, 3, 10];
  let stream = null, facing = "environment", delay = 0, ticking = null;

  async function startCamera() {
    // https가 아니거나(휴대폰에서 IP로 접속 등) 카메라 API가 없으면 휴대폰 기본 카메라 → 자르기
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) return snap.click();
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: facing }, width: { ideal: 1920 }, height: { ideal: 1920 } }, audio: false,
      });
    } catch {
      return showError("카메라를 쓸 수 없어요. 권한을 허용하거나 앨범에서 골라 주세요.");
    }
    video.srcObject = stream;
    video.classList.toggle("mirror", facing === "user");
    showError(""); setState("camera");
    const cams = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput");
    flip.hidden = cams.length < 2;
  }
  function stopCount() {
    clearInterval(ticking); ticking = null;
    if (countEl) countEl.textContent = "";
    shutter.classList.remove("counting"); shutter.setAttribute("aria-label", "사진 찍기");
  }
  function stopCamera() { stopCount(); stream?.getTracks().forEach((t) => t.stop()); stream = null; video.srcObject = null; }

  function capture() {
    const vw = video.videoWidth, vh = video.videoHeight;
    if (!vw || !vh) return;
    // 화면에 보이는 가운데 부분(틀 비율)만 사용
    const sw = vw / vh > RATIO ? vh * RATIO : vw, sh = sw / RATIO;
    const [c, ctx] = blank();
    if (facing === "user") { ctx.translate(OUT_W, 0); ctx.scale(-1, 1); }
    ctx.drawImage(video, (vw - sw) / 2, (vh - sh) / 2, sw, sh, 0, 0, OUT_W, OUT_H);
    stopCamera(); finish(c);
  }

  $("btn-camera").addEventListener("click", startCamera);
  $("cam-cancel").addEventListener("click", () => { stopCamera(); setState("choose"); });
  flip.addEventListener("click", async () => { facing = facing === "environment" ? "user" : "environment"; stopCamera(); await startCamera(); });
  timerBtn?.addEventListener("click", () => {
    delay = DELAYS[(DELAYS.indexOf(delay) + 1) % DELAYS.length];
    timerBtn.querySelector("span").textContent = delay ? `${delay}초` : "끔";
    timerBtn.setAttribute("aria-pressed", String(delay > 0));
  });
  shutter.addEventListener("click", () => {
    if (ticking) return stopCount();               // 세는 중에 다시 누르면 취소
    if (!delay || !countEl) return capture();
    let left = delay;
    countEl.textContent = left;
    shutter.classList.add("counting"); shutter.setAttribute("aria-label", "타이머 취소");
    ticking = setInterval(() => {
      left -= 1;
      if (left > 0) { countEl.textContent = left; return; }
      stopCount(); capture();
    }, 1000);
  });
  window.addEventListener("pagehide", stopCamera);

  setState("choose");
})();
</script>
{% endmacro %}
{# 전신 사진용: 사람 실루엣 (viewBox 200x300 = 2:3) #}
{% macro silhouette(cls="") -%}
<svg class="{{ cls }}" viewBox="0 0 200 300" aria-hidden="true"><g class="sil">
  <ellipse cx="100" cy="42" rx="15" ry="18"/>
  <path d="M92 60Q100 64 108 60L109 66 126 71Q133 73 134 81L146 152Q147 159 141 160L137 159 124 98 121 150 118 278H103L100 178 97 278H82L79 150 76 98 63 159 59 160Q53 159 54 152L66 81Q67 73 74 71L91 66Z"/>
</g><path class="line" d="M0 14H200M0 287H200"/></svg>
{%- endmacro %}

{# 카메라·자르기·결과 위에 겹치는 가이드: 머리끝·발끝 선 (+ 실루엣) #}
{% macro body_guide(sil=True) -%}
<svg class="guide{{ '' if sil else ' no-sil' }}" viewBox="0 0 200 300" aria-hidden="true">
  {% if sil %}<g class="sil"><ellipse cx="100" cy="42" rx="15" ry="18"/>
  <path d="M92 60Q100 64 108 60L109 66 126 71Q133 73 134 81L146 152Q147 159 141 160L137 159 124 98 121 150 118 278H103L100 178 97 278H82L79 150 76 98 63 159 59 160Q53 159 54 152L66 81Q67 73 74 71L91 66Z"/></g>{% endif %}
  <path class="line" d="M0 14H200M0 287H200"/>
  <text x="12" y="10">머리끝은 이 선 아래</text>
  <text x="12" y="297">발끝은 이 선 위</text>
</svg>
{%- endmacro %}
"""

GARMENT_NEW_HTML = """{% extends "app_base.html" %}
{% from "photo_picker.html" import picker_css, picker_js %}
{% block title %}옷 등록{% endblock %}
{% block page_head %}
{{ picker_css() }}
<style>
  .reg { max-width: 480px; margin: 0 auto; }
  .step { margin: 0 0 6px; font-size: .8125rem; font-weight: 600; color: var(--ink-soft); }
  .reg form { margin-top: 24px; }
</style>
{% endblock %}
{% block content %}
<div class="reg" id="reg" data-state="choose">
  <p class="step">1 / 2 · 사진 올리기</p>
  <h1>옷 등록</h1>

  <form class="stack" method="post" action="/garments/new" enctype="multipart/form-data"
        novalidate data-loading="사진 분석 중…" id="upload-form">
    <!-- 실제로 서버에 보내는 1:1 사진 (자르기·촬영 결과를 여기에 넣음) -->
    <input type="file" name="file" id="file" accept="image/jpeg" hidden>
    <input type="file" id="pick" accept="image/jpeg,image/png" hidden>
    <input type="file" id="snap" accept="image/jpeg,image/png" capture="environment" hidden>

    <section class="s-choose">
      <div class="drop{% if file_error %} invalid{% endif %}" id="drop">
        <div>
          <svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M4 9.3h16M4 14.7h16M9.3 4v16M14.7 4v16" opacity=".35"/></svg>
          <strong>옷 사진</strong>
          <small>1:1 · jpg, png · 10MB 이하</small>
          <div class="pick-btns">
            <button type="button" class="btn btn-primary" id="btn-camera">카메라로 찍기</button>
            <button type="button" class="btn btn-secondary" id="btn-album">앨범에서 고르기</button>
          </div>
        </div>
      </div>
    </section>

    <section class="s-camera" aria-label="카메라">
      <div class="frame cam"><video id="video" playsinline muted autoplay></video><div class="thirds"></div></div>
      <div class="cam-bar">
        <button type="button" class="btn btn-ghost left" id="cam-cancel">취소</button>
        <button type="button" class="shutter" id="shutter" aria-label="사진 찍기"></button>
        <div class="right"><button type="button" class="btn btn-ghost" id="cam-flip" hidden>카메라 전환</button></div>
      </div>
    </section>

    <section class="s-crop" aria-label="사진 자르기">
      <div class="frame crop" id="crop"><img id="crop-img" alt="자를 사진"><div class="thirds"></div></div>
      <p class="hint">끌어서 옮기고 확대해서 맞추기</p>
      <div class="zoom-row">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14"/></svg>
        <input type="range" id="zoom" min="1" max="4" step="0.01" value="1" aria-label="확대">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M12 5v14"/></svg>
      </div>
      <div class="two">
        <button type="button" class="btn btn-secondary" id="crop-cancel">다른 사진</button>
        <button type="button" class="btn btn-primary" id="crop-ok">이대로 자르기</button>
      </div>
    </section>

    <section class="s-done" aria-label="올릴 사진">
      <div class="frame"><img class="result" id="result" alt="올릴 사진 미리보기"></div>
      <button type="button" class="btn btn-secondary" id="redo">다시 찍기·고르기</button>
    </section>

    <p class="field-error" id="file-error" role="alert" {% if not file_error %}hidden{% endif %}>{{ file_error or "" }}</p>

    <p class="tips">한 장에 한 벌 · 바닥에 펼치거나 옷걸이에 걸어서 · 단순한 배경</p>

    <div class="actions">
      <button type="submit" class="btn btn-primary" id="upload-btn">다음</button>
      <a class="btn btn-ghost" href="/wardrobe">취소</a>
    </div>
  </form>
</div>
{% endblock %}
{% block script %}
{{ picker_js(1080, 1080, "garment.jpg") }}
{% endblock %}
"""

GARMENT_CONFIRM_HTML = """{% extends "app_base.html" %}
{% from "macros.html" import garment_art, field %}
{% block title %}옷 정보 확인{% endblock %}
{% block page_head %}
<style>
  .confirm { max-width: 960px; margin: 0 auto; }
  .step { margin: 0 0 6px; font-size: .8125rem; font-weight: 600; color: var(--ink-soft); }
  .layout { margin-top: 24px; display: grid; gap: 24px; }
  .photo { display: grid; place-items: center; aspect-ratio: 1; border-radius: 10px; background: #f5f5f5;
           overflow: hidden; }
  .photo img { width: 100%; height: 100%; object-fit: cover; }
  .photo svg { width: 50%; height: 60%; }
  .auto { margin-top: 12px; font-size: .875rem; }
  .back { display: inline-block; margin-bottom: 12px; color: var(--ink-soft); text-decoration: none; font-size: .9375rem; }
  .back:hover { color: var(--ink); }
  legend { display: flex; gap: 6px; align-items: baseline; }
  legend b { font-weight: 600; color: var(--ink); }
  legend small { color: var(--muted); font-weight: 400; }
  .chip input[type=checkbox] { position: absolute; opacity: 0; pointer-events: none; }
  .chip input:checked + span { background: var(--ink); border-color: var(--ink); color: #fff; }
  select { height: 48px; padding: 0 12px; border: 1px solid var(--line); border-radius: var(--radius);
           background: #fff; font: inherit; font-size: 1rem; color: var(--ink); }
  select:focus { outline: none; border-color: var(--ink); box-shadow: 0 0 0 3px rgb(23 23 23 / .1); }
  .swatches { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 8px; }
  .swatch { position: relative; }
  .swatch input { position: absolute; opacity: 0; pointer-events: none; }
  .swatch span { display: block; width: 32px; height: 32px; border-radius: 50%; border: 1px solid rgb(0 0 0 / .15);
                 cursor: pointer; transition: transform .1s; }
  .swatch span:hover { transform: scale(1.08); }
  .swatch input:checked + span { box-shadow: 0 0 0 2px var(--paper), 0 0 0 4px var(--ink); }
  .swatch input:focus-visible + span { outline: 2px solid var(--ink); outline-offset: 4px; }
  .swatch .multi { background: conic-gradient(#c0392b, #f2c94c, #2f7d4f, #2f5fb3, #c0392b); }
  .swatch .unknown { display: grid; place-items: center; background: #fff; border-style: dashed;
                     color: var(--ink-soft); font-size: .875rem; font-weight: 600; }
  .actions { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
  @media (min-width: 768px) {
    .layout { grid-template-columns: 320px 1fr; gap: 40px; align-items: start; }
    .side { position: sticky; top: 88px; }
  }
</style>
{% endblock %}
{% block content %}
{% set chip_label = {"unknown": "모름"} %}
<div class="confirm">
  {% if mode == "edit" %}
  <a class="back" href="/garments/{{ g.garment_id }}">← 옷 상세</a>
  <h1>정보 수정</h1>
  {% else %}
  <p class="step">2 / 2 · 정보 확인</p>
  <h1>이 옷이 맞나요?</h1>
  {% endif %}

  <div class="layout">
    <div class="side">
      <div class="photo">{{ garment_art(g) }}</div>
      {% if mode == "edit" %}
      {% elif USE_MOCK %}
      <p class="auto soft">Mock 모드: 자동 분석 없이 기본값</p>
      {% else %}
      <p class="auto soft">틀린 부분만 고쳐 주세요.</p>
      {% endif %}
    </div>

    <div>
      <form class="stack" method="post" action="/garments/{{ g.garment_id }}/{{ 'edit' if mode == 'edit' else 'confirm' }}" novalidate data-loading="저장 중…">
        <fieldset class="field">
          <legend>종류</legend>
          <div class="chips">
            {% for c in CATEGORY_ORDER %}
            <label class="chip"><input type="radio" name="category" value="{{ c }}" {% if form.category == c %}checked{% endif %}><span>{{ c | ko("category") }}</span></label>
            {% endfor %}
          </div>
          {% if errors.category %}<p class="field-error">{{ errors.category }}</p>{% endif %}
        </fieldset>

        <div class="field">
          <label for="subcategory">세부 종류</label>
          <select id="subcategory" name="subcategory">
            <option value="">선택 안 함</option>
            {% for c in CATEGORY_ORDER %}
            <optgroup label="{{ c | ko('category') }}" data-category="{{ c }}">
              {% for sub in SUBCATEGORIES[c] %}
              <option value="{{ sub }}" {% if form.category == c and form.subcategory == sub %}selected{% endif %}>{{ sub | ko("subcategory") }}</option>
              {% endfor %}
            </optgroup>
            {% endfor %}
          </select>
          {% if errors.subcategory %}<p class="field-error">{{ errors.subcategory }}</p>{% endif %}
        </div>

        {{ field("name", "이름", value=form.name, error=errors.name, maxlength=30,
                 placeholder="예: 유니클로 네이비 니트", hint="비워 두면 세부 종류(니트 등)로 보여요.",
                 autofocus=errors.name) }}

        <fieldset class="field">
          <legend>색상 <b id="color-name">{{ form.color | ko("color") }}</b></legend>
          <div class="swatches">
            {% for c in LABELS["color"] %}
            <label class="swatch" title="{{ c | ko('color') }}">
              <input type="radio" name="color" value="{{ c }}" aria-label="{{ c | ko('color') }}"
                     data-name="{{ c | ko('color') }}" {% if form.color == c %}checked{% endif %}>
              {% if c == "multi" %}<span class="multi"></span>
              {% elif c == "unknown" %}<span class="unknown">?</span>
              {% else %}<span style="background: {{ color_hex(c) }}"></span>{% endif %}
            </label>
            {% endfor %}
          </div>
          {% if errors.color %}<p class="field-error">{{ errors.color }}</p>{% endif %}
        </fieldset>

        <fieldset class="field">
          <legend>패턴</legend>
          <div class="chips">
            {% for pt in LABELS["pattern"] %}
            <label class="chip"><input type="radio" name="pattern" value="{{ pt }}" {% if form.pattern == pt %}checked{% endif %}><span>{{ chip_label.get(pt) or (pt | ko("pattern")) }}</span></label>
            {% endfor %}
          </div>
          {% if errors.pattern %}<p class="field-error">{{ errors.pattern }}</p>{% endif %}
        </fieldset>

        <fieldset class="field">
          <legend>스타일 <small>최대 {{ MAX_STYLES }}개</small></legend>
          <div class="chips" id="styles">
            {% for st in LABELS["style"] %}
            <label class="chip"><input type="checkbox" name="styles" value="{{ st }}" {% if st in form.styles %}checked{% endif %}><span>{{ st | ko("style") }}</span></label>
            {% endfor %}
          </div>
          {% if errors.styles %}<p class="field-error">{{ errors.styles }}</p>{% endif %}
        </fieldset>

        <fieldset class="field">
          <legend>두께</legend>
          <div class="chips">
            <label class="chip"><input type="radio" name="thermal_level" value="" {% if not form.thermal_level %}checked{% endif %}><span>모름</span></label>
            {% for lv in range(1, 6) %}
            <label class="chip"><input type="radio" name="thermal_level" value="{{ lv }}" {% if form.thermal_level == lv | string %}checked{% endif %}><span>{{ lv | ko("thermal_level") }}</span></label>
            {% endfor %}
          </div>
          {% if errors.thermal_level %}<p class="field-error">{{ errors.thermal_level }}</p>{% endif %}
        </fieldset>

        {{ field("location", "보관 위치", value=form.location, error=errors.location, maxlength=30,
                 placeholder="예: 안방 옷장 2칸, 서랍, 본가", suggestions=locations,
                 hint="이전에 쓴 위치는 입력할 때 추천돼요." if locations else None, autofocus=errors.location) }}

        <div class="actions">
          <button type="submit" class="btn btn-primary">{{ "저장" if mode == "edit" else "옷장에 저장" }}</button>
        </div>
      </form>
      {% if mode == "edit" %}
      <a class="btn btn-ghost" href="/garments/{{ g.garment_id }}" style="width:100%;margin-top:8px">취소</a>
      {% else %}
      <form method="post" action="/garments/{{ g.garment_id }}/cancel" style="margin-top:8px">
        <button class="btn btn-ghost" style="width:100%">등록 취소</button>
      </form>
      {% endif %}
    </div>
  </div>
</div>
{% endblock %}
{% block script %}
<script>
  // 고른 종류의 세부 종류만 보여주기
  const sub = document.getElementById("subcategory");
  function syncSub() {
    const cat = document.querySelector("input[name=category]:checked")?.value;
    sub.querySelectorAll("optgroup").forEach(g => {
      const on = g.dataset.category === cat;
      g.hidden = !on; g.disabled = !on;
    });
    if (sub.selectedOptions[0]?.parentElement.disabled) sub.value = "";
  }
  document.querySelectorAll("input[name=category]").forEach(r => r.addEventListener("change", syncSub));
  syncSub();

  // 색상 이름 표시
  document.querySelectorAll("input[name=color]").forEach(r => r.addEventListener("change", () => {
    document.getElementById("color-name").textContent = r.dataset.name;
  }));

  // 스타일 최대 개수
  const boxes = document.querySelectorAll("#styles input");
  function limit() {
    const n = [...boxes].filter(b => b.checked).length;
    boxes.forEach(b => { b.disabled = !b.checked && n >= {{ MAX_STYLES }}; b.parentElement.style.opacity = b.disabled ? .45 : 1; });
  }
  boxes.forEach(b => b.addEventListener("change", limit)); limit();
</script>
{% endblock %}
"""

GARMENT_DETAIL_HTML = """{% extends "app_base.html" %}
{% from "macros.html" import garment_art %}
{% block title %}{{ garment_name(g) }}{% endblock %}
{% block page_head %}
<style>
  .detail { max-width: 960px; margin: 0 auto; }
  .back { display: inline-block; margin-bottom: 16px; color: var(--ink-soft); text-decoration: none; font-size: .9375rem; }
  .back:hover { color: var(--ink); }
  .layout { display: grid; gap: 24px; }
  .photo { position: relative; display: grid; place-items: center; aspect-ratio: 1; border-radius: 10px;
           background: #f5f5f5; overflow: hidden; }
  .photo img { width: 100%; height: 100%; object-fit: cover; }
  .photo svg { width: 60%; height: 60%; }
  .photo .badge { position: absolute; top: 12px; left: 12px; padding: 4px 10px; border-radius: 6px;
                  font-size: .8125rem; font-weight: 600; }
  .badge.laundry { background: var(--ink); color: #fff; }
  .badge.inactive { background: #e5e5e5; color: var(--ink-soft); }
  .kicker { margin: 0 0 4px; font-size: .875rem; color: var(--ink-soft); }
  .props { margin: 20px 0 0; display: grid; grid-template-columns: 72px 1fr; gap: 12px 16px; font-size: .9375rem; }
  .props dt { color: var(--ink-soft); }
  .props dd { margin: 0; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
  .dot { width: 14px; height: 14px; border-radius: 50%; border: 1px solid rgb(0 0 0 / .15); }
  .dot.multi { background: conic-gradient(#c0392b, #f2c94c, #2f7d4f, #2f5fb3, #c0392b); }
  .tag { padding: 2px 10px; border-radius: 6px; background: #f5f5f5; font-size: .8125rem; }
  .muted { color: var(--muted); }
  .block { margin-top: 28px; padding-top: 24px; border-top: 1px solid var(--line); }
  .block h2 { margin: 0 0 12px; font-size: 1rem; }
  .seg { display: grid; grid-template-columns: repeat(3, 1fr); gap: 4px; padding: 4px; border-radius: 10px; background: #f0eeec; }
  .seg button { height: 40px; border: 0; border-radius: 10px; background: none; font: inherit; font-size: .9375rem;
                color: var(--ink-soft); cursor: pointer; }
  .seg button:hover { color: var(--ink); }
  .seg button[aria-pressed="true"] { background: #fff; color: var(--ink); font-weight: 600;
                                     box-shadow: 0 1px 3px rgb(23 23 23 / .12); cursor: default; }
  .seg button:focus-visible { outline: 2px solid var(--ink); outline-offset: 1px; }
  .help { margin: 8px 0 0; font-size: .8125rem; color: var(--ink-soft); }
  .actions { display: flex; flex-direction: column; gap: 8px; }
  details.danger summary { list-style: none; }
  details.danger summary::-webkit-details-marker { display: none; }
  details.danger summary.btn { width: 100%; color: var(--danger); }
  details.danger[open] summary { display: none; }
  .confirm-box { scroll-margin-bottom: 96px; padding: 16px; border: 1px solid #f4c7c3; border-radius: var(--radius); background: var(--danger-soft); }
  .confirm-box p { margin: 0 0 12px; font-size: .9375rem; }
  .confirm-box .two { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .btn-danger { background: var(--danger); color: #fff; }
  .btn-danger:hover { background: #912018; }
  @media (min-width: 768px) {
    .layout { grid-template-columns: minmax(280px, 400px) 1fr; gap: 48px; align-items: start; }
  }
</style>
{% endblock %}
{% block content %}

<div class="detail">
  <a class="back" href="/wardrobe">← 옷장</a>
  {% if notice %}<div class="notice" role="status" style="margin-bottom:16px">{{ notice }}</div>{% endif %}

  <div class="layout">
    <div class="photo">
      {% if g.status != "available" %}<span class="badge {{ g.status }}">{{ g.status | ko("status") }}</span>{% endif %}
      {{ garment_art(g) }}
    </div>

    <div>
      <p class="kicker">{{ g.category | ko("category") }}{% if g.name and g.subcategory %} · {{ g.subcategory | ko("subcategory") }}{% endif %}</p>
      <h1>{{ garment_name(g) }}</h1>

      <dl class="props">
        <dt>색상</dt>
        <dd><span class="dot{% if g.color == 'multi' %} multi{% endif %}" style="background-color: {{ color_hex(g.color) }}"></span>{{ (g.color or "unknown") | ko("color") }}</dd>
        <dt>패턴</dt>
        <dd>{{ g.pattern | ko("pattern") }}</dd>
        <dt>스타일</dt>
        <dd>{% for s in g.style_candidates %}<span class="tag">{{ s | ko("style") }}</span>{% else %}<span class="muted">없음</span>{% endfor %}</dd>
        <dt>두께</dt>
        <dd>{% if g.thermal_level %}{{ g.thermal_level | ko("thermal_level") }}{% else %}<span class="muted">모름</span>{% endif %}</dd>
        <dt>위치</dt>
        <dd>{% if g.location %}{{ g.location }}{% else %}<span class="muted">미입력</span>{% endif %}</dd>
      </dl>

      <section class="block">
        <h2 id="status-title">상태</h2>
        <form method="post" action="/garments/{{ g.garment_id }}/status" class="seg" role="group" aria-labelledby="status-title">
          {% for s in ["available", "laundry", "inactive"] %}
          <button name="status" value="{{ s }}" aria-pressed="{{ 'true' if g.status == s else 'false' }}"
                  {% if g.status == s %}type="button"{% endif %}>{{ s | ko("status") }}</button>
          {% endfor %}
        </form>
      </section>

      <section class="block actions">
        <a class="btn btn-secondary" href="/garments/{{ g.garment_id }}/edit">정보 수정</a>
        <details class="danger" ontoggle="if (this.open) this.querySelector('.confirm-box').scrollIntoView({ block: 'nearest', behavior: 'smooth' })">
          <summary class="btn btn-ghost">삭제</summary>
          <div class="confirm-box" role="alertdialog" aria-label="삭제 확인">
            <p>이 옷을 삭제할까요? 되돌릴 수 없어요.</p>
            <div class="two">
              <button type="button" class="btn btn-secondary" onclick="this.closest('details').open = false">취소</button>
              <form method="post" action="/garments/{{ g.garment_id }}/delete">
                <button class="btn btn-danger" style="width:100%">삭제</button>
              </form>
            </div>
          </div>
        </details>
      </section>
    </div>
  </div>
</div>
{% endblock %}
"""

register_templates(
    {
        "auth_base.html": AUTH_BASE_HTML,
        "login.html": LOGIN_HTML,
        "signup.html": SIGNUP_HTML,
        "profile.html": PROFILE_HTML,
        "body_photo.html": BODY_PHOTO_HTML,
        "wardrobe.html": WARDROBE_HTML,
        "photo_picker.html": PHOTO_PICKER_HTML,
        "garment_new.html": GARMENT_NEW_HTML,
        "garment_confirm.html": GARMENT_CONFIRM_HTML,
        "garment_detail.html": GARMENT_DETAIL_HTML,
    },
    GENDERS=GENDERS,
    SUBCATEGORIES=SUBCATEGORIES,
    MAX_STYLES=MAX_STYLES,
    wardrobe_url=wardrobe_url,
    DEMO_EMAIL=auth.DEMO_EMAIL,
    DEMO_PASSWORD=auth.DEMO_PASSWORD,
)
