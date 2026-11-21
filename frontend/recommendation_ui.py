"""feat/recommendation-ui (D) — 코디 추천 요청·결과 화면.

다른 팀원과 연결되는 부분은 connect/recommendation.py 에만 있습니다:
- recommend(token, UserContext) → RecommendationResult   ↔ A feat/recommendation-rules·ranking (백엔드 C 경유)
- send_feedback(token, FeedbackEvent)                    ↔ A+C feat/recommendation-feedback

화면 흐름: GET /recommend → POST /recommend (생성) → GET /recommend/result/{id} (조회)
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID, uuid4

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from shared.contracts import FeedbackEvent, UserContext
from shared.day_plans import ENVIRONMENT_LABELS, PURPOSE_LABELS
from shared.feedback import FEEDBACK_LABELS
from shared.seasonal_weather import (
    MONTHLY_TEMPERATURES,
    SEASON_MONTHS,
    SEASONS,
    current_month,
    season_for_month,
    seasonal_temperature,
)
from shared.thermal_policy import temperature_policy

from .common import (
    CATEGORY_ORDER,
    LABELS,
    TOKEN_COOKIE,
    go_login,
    http_status,
    logged_out,
    register_templates,
    render,
)
from .connect import ApiError
from .connect import auth_profile as auth
from .connect import garment_storage as closet
from .connect import recommendation as rec
from .connect import weather as weather_api
from .day_plans import list_plans
from .style_references import list_references

router = APIRouter()

# 보온 정책과 같은 구간: <0 / 0~5 / >5~15 / >15~22 / >22°C
# 화면은 기온 숫자만 받음 (2026-11-07 시아: 구간 버튼 없앰). 아래는 예전 ?band= 주소용
TEMP_BANDS = [
    ("freezing", "매우 추움", "0°C 미만", -3),
    ("cold", "추움", "0~5°C", 3),
    ("cool", "쌀쌀", "5°C 초과~15°C", 10),
    ("mild", "선선", "15°C 초과~22°C", 18),
    ("hot", "더움", "22°C 초과", 25),
]
BAND_TEMP = {key: temp for key, _, _, temp in TEMP_BANDS}
MAX_PREF_STYLES = 3  # 버튼으로 고르는 스타일 최대 개수
# 버튼으로 보여줄 대표 스타일 (2026-11-07 시아: 나머지는 글로 입력)
QUICK_STYLES = ["casual", "minimal", "street", "formal"]
MAX_STYLE_TEXT = 40  # 직접 입력 글자 수
# 직접 입력한 말 → 스타일 코드 (모르는 말은 그대로 보냄. preferred_styles는 list[str])
STYLE_WORDS = {
    **{code: code for code in LABELS["style"]},
    **{label: code for code, label in LABELS["style"].items()},
    "스트리트": "street",
    "스포츠": "sporty",
    "아메리칸캐주얼": "amekaji",
}


def parse_style_text(text: str) -> list[str]:
    """ "빈티지, 꾸안꾸" → ["vintage", "꾸안꾸"] (쉼표로 나눔, 아는 말은 코드로)."""
    out = []
    for word in re.split(r"[,，、/]", text[:MAX_STYLE_TEXT]):
        word = " ".join(word.split())
        if word:
            out.append(STYLE_WORDS.get(word.lower().replace(" ", ""), word[:20]))
    return out[:3]


# 추천 코디에 보여줄 종류 — 신발은 추천에서 뺌 (2026-11-07 D 결정, 옷장 등록은 그대로)
SLOTS = [("outerwear", "아우터"), ("top", "상의"), ("bottom", "하의")]
NEEDED = ["top", "bottom"]  # 코디를 만들려면 꼭 있어야 하는 종류


def with_iga(words: list[str]) -> str:
    """["상의", "신발"] → "상의, 신발이" (마지막 글자 받침에 따라 이/가)."""
    text = ", ".join(words)
    last = text[-1:] if text else ""
    has_final = "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28 != 0
    return text + ("이" if has_final else "가")


def temp_band(temp: float) -> str:
    return temperature_policy(temp).band


async def wearable_garments(token: str, wardrobe="auto"):
    """추천에 쓸 수 있는 옷(입을 수 있음)과 종류별 개수."""
    garments = await closet.list_garments(token)
    from shared.wardrobes import default_wardrobe, in_wardrobe

    wardrobe = default_wardrobe(garments) if wardrobe == "auto" else wardrobe
    garments = [g for g in garments if in_wardrobe(g, wardrobe)]
    usable = [g for g in garments if g.status == "available" and g.analysis_status == "done"]
    counts = {c: sum(1 for g in usable if g.category == c) for c, _ in SLOTS}
    return garments, usable, counts


# ---- 0) 홈: 오늘 날짜·기온 + 오늘의 코디 + 내 옷장 요약 --------------------------
LOC_COOKIE = "fitjang_loc"  # 브라우저 위치 "37.57,126.98"(소수 둘째 자리까지) 또는 "none"(거부)
WEEKDAYS = "월화수목금토일"


def home_location(request: Request) -> tuple[float, float, bool]:
    """(위도, 경도, 서울 기준인지). 위치 쿠키가 없거나 이상하면 서울."""
    raw = request.cookies.get(LOC_COOKIE, "")
    try:
        lat, lon = (float(x) for x in raw.split(","))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return round(lat, 2), round(lon, 2), False
    except ValueError:
        pass
    return round(weather_api.SEOUL[0], 2), round(weather_api.SEOUL[1], 2), True


async def latest_outfit(token: str, usable) -> object | None:
    """홈도 읽기 전용입니다. 현재 입을 수 있는 옷으로 저장된 최근 코디만 표시합니다."""
    available = {g.garment_id for g in usable}
    for result in await rec.recent_results(token):
        for outfit in result.outfits:
            if {v for v in outfit.items.model_dump().values() if v} <= available:
                return outfit
    return None


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return go_login(request)
    now = datetime.now(UTC) + timedelta(hours=9)  # 한국 시간
    lat, lon, is_fallback = home_location(request)
    try:
        weather = await weather_api.get_weather(token, lat, lon)
    except ApiError as e:
        if e.status == 401:
            return logged_out()
        weather = None
    try:
        garments, usable, counts = await wearable_garments(token)
    except ApiError as e:
        if e.status == 401:
            return logged_out()
        garments, usable, counts = [], [], {c: 0 for c, _ in SLOTS}
    missing = [c for c in NEEDED if counts[c] == 0]
    try:
        profile = await auth.get_profile(token)  # 선호 스타일과 착샷 전신 사진 유무
    except ApiError as e:
        if e.status == 401:
            return logged_out()
        profile = None
    outfit, outfit_error = None, None
    if not missing:
        try:
            outfit = await latest_outfit(token, usable)
        except ApiError as e:
            if e.status == 401:
                return logged_out()
            outfit_error = e.message
    place = (weather.location_name if weather else None) or ("서울" if is_fallback else "현재 위치")
    return render(
        request,
        "home.html",
        wardrobe="personal" if any(not g.is_example for g in garments) else "example",
        active="home",
        date_label=f"{now.month}월 {now.day}일 {WEEKDAYS[now.weekday()]}요일",
        weather=weather,
        place=place,
        is_fallback=is_fallback,
        ask_location=LOC_COOKIE not in request.cookies,
        outfit=outfit,
        outfit_error=outfit_error,
        missing=missing,
        by_id={g.garment_id: g for g in garments},
        profile=profile,
        total=len(garments),
        cat_counts={c: sum(1 for g in garments if g.category == c) for c in CATEGORY_ORDER},
        laundry=sum(1 for g in garments if g.status == "laundry"),
    )


# ---- 1) 조건 고르기 ------------------------------------------------------------
def required_selection_error(required: list[str], usable) -> str | None:
    if len(required) > 3 or len(required) != len(set(required)):
        return "필수 의류는 중복 없이 최대 세 벌까지 선택해 주세요."
    by_id = {g.garment_id: g for g in usable}
    if any(gid not in by_id for gid in required):
        return "선택한 옷을 사용할 수 없어요. 아래에서 필수 의류를 다시 선택해 주세요."
    categories = [by_id[gid].category for gid in required]
    if len(categories) != len(set(categories)):
        return "상의·하의·아우터에서 각각 한 벌만 선택해 주세요."
    return None


@router.get("/recommend", response_class=HTMLResponse)
async def recommend_page(
    request: Request,
    wardrobe: str = "auto",
    temp: str = "",
    weather_mode: str = "temperature",
    clothing_season: str = "",
    forecast_token: str = "",
    season: str = "",
    month: str = "",
    occasion: str = "",
    styles: Annotated[list[str] | None, Query()] = None,
    style_text: str = "",
    required: Annotated[list[str] | None, Query()] = None,
    exclude: Annotated[list[str] | None, Query()] = None,
    schedule_id: str = "",
    schedule_version: str = "",
    outerwear_policy: str = "always_worn",
    reference_id: str = "",
    reference_version: str = "",
):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return go_login(request)
    try:
        from shared.wardrobes import default_wardrobe

        wardrobe = wardrobe if wardrobe in ("personal", "example", "all") else "auto"
        garments, usable, counts = await wearable_garments(token, wardrobe)
        if wardrobe == "auto":
            wardrobe = default_wardrobe(garments)
        plans = await list_plans(token)
        refs = await list_references(token)
    except ApiError as e:
        if e.status == 401:
            return logged_out()
        return render(
            request,
            "recommend_form.html",
            status=http_status(e),
            active="recommend",
            usable=[],
            counts={},
            missing=[],
            server_error=e.message,
            form={},
        )
    missing = [c for c in NEEDED if counts[c] == 0]
    selected = [gid for gid in (required or []) if gid]
    selection_error = required_selection_error(selected, usable)
    return render(
        request,
        "recommend_form.html",
        active="recommend",
        usable=usable,
        counts=counts,
        missing=missing,
        status=422 if selection_error else 200,
        form={
            "wardrobe": wardrobe,
            "temp": temp,
            "weather_mode": weather_mode,
            "clothing_season": clothing_season or season_for_month(current_month()),
            "forecast_token": forecast_token,
            "season": season,
            "month": month,
            "occasion": occasion,
            "styles": styles or [],
            "style_text": style_text[:MAX_STYLE_TEXT],
            "exclude": exclude or [],
            "required": selected,
        },
        errors={"required": selection_error} if selection_error else {},
        autofill=not temp and weather_mode != "seasonal",
        refs=refs,
        reference_id=reference_id,
        reference_version=reference_version,
        plans=plans,
        schedule_id=schedule_id,
        schedule_version=schedule_version,
        outerwear_policy=outerwear_policy,
    )


# ---- 오늘 기온 (C의 날씨 API → connect/weather.py) -----------------------------
@router.get("/weather")
async def weather(request: Request, lat: float, lon: float):
    """화면 JS가 위치(없으면 서울)로 부름 → {temperature_c, band, condition, location_name}."""
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return JSONResponse({"detail": "로그인이 필요해요."}, status_code=401)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return JSONResponse({"detail": "위치가 올바르지 않아요."}, status_code=400)
    try:
        w = await weather_api.get_weather(token, round(lat, 2), round(lon, 2))
    except ApiError as e:
        return JSONResponse(
            {"detail": e.message, "code": e.code, "retry_after_sec": 3},
            status_code=http_status(e),
            headers={"Cache-Control": "private, no-store", "Retry-After": "3"},
        )
    return {
        "temperature_c": w.temperature_c,
        "forecast": w.forecast,
        "forecast_token": w.forecast_token,
        "band": temp_band(w.temperature_c),
        "condition": w.condition,
        "location_name": w.location_name,
        "is_stale": w.is_stale,
        "fetched_at": w.fetched_at,
        "weather_time": w.weather_time,
        "age_seconds": w.age_seconds,
        "notice": w.notice,
    }


# ---- 2) 결과 -------------------------------------------------------------------
@router.get("/recommend/result")
async def legacy_recommend_result(request: Request):
    # Old bookmarked links return to conditions; GET must never generate a recommendation.
    return RedirectResponse("/recommend?" + request.url.query, status_code=303)


@router.post("/recommend", response_class=HTMLResponse)
async def submit_recommendation(request: Request):
    if not request.cookies.get(TOKEN_COOKIE):
        return go_login(request)
    values = await request.form()
    key = str(values.get("request_key", ""))
    try:
        key = str(UUID(key))
    except ValueError:
        return render(
            request,
            "recommend_status.html",
            status=422,
            message="추천 조건 화면을 다시 열어 요청해 주세요.",
            waiting=False,
        )
    scalar_names = (
        "wardrobe",
        "band",
        "temp",
        "weather_mode",
        "clothing_season",
        "forecast_token",
        "season",
        "month",
        "occasion",
        "style_text",
        "schedule_id",
        "schedule_version",
        "reference_id",
        "reference_version",
    )
    fields = {name: str(values.get(name, "")) for name in scalar_names}
    fields["outerwear_policy"] = str(values.get("outerwear_policy", "always_worn"))
    fields.update({name: values.getlist(name) for name in ("styles", "exclude", "required")})
    return await generate_recommendation(request, request_key=key, **fields)


async def generate_recommendation(
    request: Request,
    wardrobe: str = "auto",
    band: str = "",
    temp: str = "",
    weather_mode: str = "temperature",
    clothing_season: str = "",
    forecast_token: str = "",
    season: str = "",
    month: str = "",
    occasion: str = "",
    styles: Annotated[list[str] | None, Query()] = None,
    style_text: str = "",
    exclude: Annotated[list[str] | None, Query()] = None,  # 화면에서는 없앰 (예전 주소용)
    required: Annotated[list[str] | None, Query()] = None,
    schedule_id: str = "",
    schedule_version: str = "",
    outerwear_policy: str = "always_worn",
    reference_id: str = "",
    reference_version: str = "",
    request_key: str = "",
):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return go_login(request)
    styles, exclude = styles or [], exclude or []
    form = {
        "wardrobe": wardrobe,
        "band": band,
        "temp": temp.strip(),
        "weather_mode": weather_mode or "temperature",
        "clothing_season": clothing_season or season_for_month(current_month()),
        "forecast_token": forecast_token,
        "season": season,
        "month": month,
        "occasion": occasion,
        "styles": styles,
        "style_text": style_text.strip()[:MAX_STYLE_TEXT],
        "exclude": exclude,
        "required": [gid for gid in (required or []) if gid],
    }

    # 입력 검사 (화면에서 고르는 값이라 보통은 통과)
    errors: dict[str, str] = {}
    if form["clothing_season"] not in SEASONS:
        errors["clothing_season"] = "실내 복장에 적용할 계절을 선택해 주세요."
    if form["weather_mode"] == "forecast" and not forecast_token:
        errors["temp"] = "남은 시간 예보를 다시 조회해 주세요."
    ref_version = None
    if reference_id:
        try:
            from uuid import UUID

            UUID(reference_id)
            ref_version = int(reference_version)
            if ref_version < 1:
                raise ValueError
        except ValueError:
            errors["reference"] = "확인한 참고 사진을 다시 선택해 주세요"
    version = None
    if schedule_id:
        try:
            from uuid import UUID

            UUID(schedule_id)
            version = int(schedule_version)
            if version < 1:
                raise ValueError
        except ValueError:
            errors["schedule"] = "저장한 일정을 다시 선택해 주세요"
    if outerwear_policy not in ("always_worn", "removable"):
        errors["schedule"] = "아우터 착용 방식을 다시 선택해 주세요"
    temperature: float | None = None
    if form["weather_mode"] == "seasonal":
        try:
            resolved_month, temperature = seasonal_temperature(season, int(month) if month else None)
            form["month"] = str(resolved_month)
        except (ValueError, TypeError):
            errors["temp"] = "계절과 해당 월을 선택해 주세요."
    elif form["weather_mode"] not in ("temperature", "forecast"):
        errors["temp"] = "기온 입력 방식을 다시 선택해 주세요."
    elif form["temp"]:
        try:
            temperature = round(float(form["temp"]), 1)
            if not -30 <= temperature <= 45:
                errors["temp"] = "기온은 -30~45°C 사이로 입력해 주세요."
        except ValueError:
            errors["temp"] = "기온은 숫자로 입력해 주세요."
    elif band in BAND_TEMP:  # 예전 주소
        temperature = BAND_TEMP[band]
    else:
        errors["temp"] = "기온을 입력하거나 계절·월 평균으로 추천받기를 선택해 주세요."
    if occasion and occasion not in LABELS["occasion"]:
        errors["occasion"] = "상황을 다시 골라 주세요."
    if any(s not in LABELS["style"] for s in styles) or len(styles) > MAX_PREF_STYLES:
        errors["styles"] = f"스타일은 {MAX_PREF_STYLES}개까지 고를 수 있어요."
    # 버튼 + 직접 입력 (겹치는 건 한 번만)
    all_styles = list(dict.fromkeys(styles + parse_style_text(form["style_text"])))
    form["all_styles"] = all_styles

    usable, counts, plans, refs = [], {}, [], []
    try:
        from shared.wardrobes import default_wardrobe

        wardrobe = wardrobe if wardrobe in ("personal", "example", "all") else "auto"
        garments, usable, counts = await wearable_garments(token, wardrobe)
        if wardrobe == "auto":
            wardrobe = default_wardrobe(garments)
        form["wardrobe"] = wardrobe
        plans = await list_plans(token)
        refs = await list_references(token)
        schedule_ui = dict(
            refs=refs,
            reference_id=reference_id,
            reference_version=reference_version,
            plans=plans,
            schedule_id=schedule_id,
            schedule_version=schedule_version,
            outerwear_policy=outerwear_policy,
        )
        selection_error = required_selection_error(form["required"], usable)
        if selection_error:
            errors["required"] = selection_error
        if set(form["required"]) & set(exclude):
            errors["required"] = "필수 의류와 제외 의류에 같은 옷이 있어요. 조건을 다시 선택해 주세요."
        if errors:
            missing = [c for c in NEEDED if counts[c] == 0]
            return render(
                request,
                "recommend_form.html",
                status=422,
                active="recommend",
                usable=usable,
                counts=counts,
                missing=missing,
                form=form,
                errors=errors,
                **schedule_ui,
            )
        context = UserContext(
            wardrobe=wardrobe,
            user_id="me",  # TODO(C): 로그인(JWT) 후에는 백엔드가 토큰에서 사용자 확인
            temperature_c=temperature,
            clothing_season=form["clothing_season"],
            season=season if form["weather_mode"] == "seasonal" else None,
            month=int(form["month"]) if form["weather_mode"] == "seasonal" else None,
            occasion=occasion or None,
            preferred_styles=all_styles,
            excluded_items=exclude,
            required_garment_ids=form["required"],
            reference_id=reference_id or None,
            reference_version=ref_version,
            schedule_id=schedule_id or None,
            schedule_version=version,
            outerwear_policy=outerwear_policy,
        )
        result = await rec.recommend(
            token,
            context,
            request_key=request_key,
            forecast_token=forecast_token if form["weather_mode"] == "forecast" else None,
        )
    except ApiError as e:
        if e.status == 401:
            return logged_out()
        if e.status == 0 or e.code == "RECOMMENDATION_IN_PROGRESS":
            return RedirectResponse(f"/recommend/requests/{request_key}", status_code=303)
        return render(
            request,
            "recommend_form.html",
            status=http_status(e),
            active="recommend",
            usable=usable,
            counts=counts,
            missing=[c for c in NEEDED if not counts.get(c)],
            form=form,
            server_error=e.message,
            refs=refs,
            reference_id=reference_id,
            reference_version=reference_version,
            plans=plans,
            schedule_id=schedule_id,
            schedule_version=schedule_version,
            outerwear_policy=outerwear_policy,
        )

    return RedirectResponse(f"/recommend/result/{result.recommendation_id}", status_code=303)


@router.get("/recommend/requests/{request_key}", response_class=HTMLResponse)
async def recommendation_progress(request: Request, request_key: UUID):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return go_login(request)
    try:
        state = await rec.get_request(token, str(request_key))
    except ApiError as exc:
        if exc.status == 401:
            return logged_out()
        return render(
            request,
            "recommend_status.html",
            status=http_status(exc),
            message=exc.message + " 요청을 자동으로 다시 보내지 않습니다.",
            waiting=False,
        )
    if state["status"] == "completed":
        return RedirectResponse(f"/recommend/result/{state['recommendation_id']}", status_code=303)
    waiting = state["status"] == "pending"
    message = (
        "이미 접수한 추천을 만들고 있어요. 완료되면 결과를 보여드려요."
        if waiting
        else (
            state.get("detail")
            or "추천이 중단되었거나 결과가 삭제되었습니다. 조건 화면에서 새 추천을 시작해 주세요."
        )
    )
    if state["status"] == "interrupted":
        message = "처리 상태 확인이 지연되고 있어요. 잠시 후 이 화면을 새로고침해 결과를 확인해 주세요."
    return render(request, "recommend_status.html", message=message, waiting=waiting, active="recommend")


@router.get("/recommend/result/{recommendation_id}", response_class=HTMLResponse)
async def saved_recommendation(request: Request, recommendation_id: UUID):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return go_login(request)
    try:
        result = await rec.get_result(token, str(recommendation_id))
    except ApiError as exc:
        if exc.status == 401:
            return logged_out()
        return render(
            request, "recommend_status.html", status=http_status(exc), message=exc.message, waiting=False
        )
    try:
        feedback_states = await rec.feedback_states(token, str(recommendation_id))
        feedback_warning = None
    except ApiError:
        feedback_states = {}
        feedback_warning = "기존 피드백을 불러오지 못했어요. 잠시 후 새로고침해 주세요."
    context = result.context
    form = {
        "wardrobe": context.wardrobe,
        "temp": context.temperature_c,
        "weather_mode": "forecast"
        if context.weather_forecast
        else "seasonal"
        if context.season
        else "temperature",
        "clothing_season": context.clothing_season or context.season or season_for_month(current_month()),
        "forecast_token": result.forecast_token or "",
        "season": context.season or "",
        "month": str(context.month or ""),
        "occasion": context.occasion or "",
        "styles": [s for s in context.preferred_styles if s in QUICK_STYLES],
        "style_text": ", ".join(s for s in context.preferred_styles if s not in QUICK_STYLES),
        "all_styles": context.preferred_styles,
        "exclude": context.excluded_items,
        "required": context.required_garment_ids,
    }
    edit = {k: v for k, v in form.items() if k != "all_styles"}
    edit.update(
        schedule_id=context.schedule_id or "",
        schedule_version=context.schedule_version or "",
        reference_id=context.reference_id or "",
        reference_version=context.reference_version or "",
        outerwear_policy=context.outerwear_policy,
    )
    try:
        profile = await auth.get_profile(token)
    except ApiError:
        profile = None
    return render(
        request,
        "recommend_result.html",
        active="recommend",
        result=result,
        by_id=result.garments,
        temperature=context.temperature_c,
        form=form,
        missing=[] if result.outfits else NEEDED,
        profile=profile,
        feedback_by_outfit=feedback_states,
        feedback_warning=feedback_warning,
        edit_url="/recommend?" + urlencode(edit, doseq=True),
        regenerate_fields=edit,
        reference_id=context.reference_id,
        schedule_id=context.schedule_id,
        purposes=PURPOSE_LABELS,
        environments=ENVIRONMENT_LABELS,
        outer_actions={
            "wear": "아우터 입기",
            "carry": "아우터 벗고 소지",
            "none": "아우터 없음",
            "adjust": "구간에 따라 아우터 조절",
        },
    )


# ---- 3) 피드백 (좋아요 / 별로예요) ----------------------------------------------
@router.post("/recommend/feedback")
async def recommend_feedback(
    request: Request,
    recommendation_id: str = Form(""),
    outfit_id: str = Form(""),
    accepted: str = Form(""),
    reason: str = Form(""),
    reason_codes: Annotated[list[str] | None, Form()] = None,
    back: str = Form("/recommend"),
):
    token = request.cookies.get(TOKEN_COOKIE)
    by_fetch = request.headers.get("x-requested-with") == "fetch"
    if not token:
        return JSONResponse({"ok": False}, status_code=401) if by_fetch else go_login(request)
    ok = bool(
        recommendation_id
        and outfit_id
        and accepted in ("1", "0")
        and len(reason) <= 1000
        and "\x00" not in reason
        and all(c in FEEDBACK_LABELS for c in (reason_codes or []))
    )
    saved_codes = reason_codes or []
    if ok:
        try:
            saved_feedback = await rec.send_feedback(
                token,
                FeedbackEvent(
                    recommendation_id=recommendation_id,
                    outfit_id=outfit_id,
                    accepted=accepted == "1",
                    reason=reason.strip() or None,
                    reason_codes=list(dict.fromkeys(reason_codes or [])),
                ),
            )
            if saved_feedback:
                saved_codes = saved_feedback.get("reason_codes", saved_codes)
        except ApiError as e:
            if e.status == 401 and not by_fetch:
                return logged_out()
            ok = False
    if by_fetch:
        return JSONResponse({"ok": ok, "reason_codes": saved_codes}, status_code=200 if ok else 400)
    safe_back = back if back.startswith("/recommend") else "/recommend"
    return RedirectResponse(safe_back, status_code=303)


# =============================================================================
# 템플릿 (이 브랜치 화면)
# =============================================================================
RECOMMEND_FORM_HTML = """{% extends "app_base.html" %}
{% block title %}코디 추천{% endblock %}
{% block page_head %}
<style>
  .rec { max-width: 640px; margin: 0 auto; }
  .rec form { margin-top: 24px; }
  legend { display: flex; gap: 6px; align-items: baseline; }
  legend small { color: var(--muted); font-weight: 400; }
  .temp-row { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
  .temp-row .input-box { width: 132px; }
  .temp-row .input-box input { height: 52px; font-size: 1.375rem; font-weight: 600; text-align: center; }
  .step-btn { width: 52px; height: 52px; border: 1px solid var(--line); border-radius: var(--radius); background: #fff;
              font: inherit; font-size: 1.375rem; color: var(--ink); cursor: pointer; }
  .step-btn:hover { border-color: var(--ink-soft); }
  .wline { margin: 8px 0 0; display: flex; flex-wrap: wrap; align-items: center; gap: 4px 10px; font-size: .8125rem; color: var(--ink-soft); }
  .wline .link-btn { font-size: .8125rem; }
  .chip input[type=checkbox] { position: absolute; opacity: 0; pointer-events: none; }
  .chip input:checked + span { background: var(--ink); border-color: var(--ink); color: #fff; }
  .style-text { margin-top: 10px; }
  .warn { padding: 16px; border-radius: var(--radius); background: #f5f5f5; font-size: .9375rem; }
  .warn p { margin: 0 0 12px; }
  .required-slots { display: grid; gap: 12px; }
  .required-slot select { width: 100%; min-width: 0; min-height: 44px; padding: 10px;
    border: 1px solid var(--line); border-radius: 10px; background: #fff; font: inherit; }
  .required-slot label { display: block; margin-bottom: 6px; }
</style>
{% endblock %}
{% block content %}
{% from "macros.html" import garment_art, term_guide, term_chip %}
<div class="rec">
  <h1>코디 추천</h1>

  {% if server_error %}<div class="alert" role="alert" style="margin-top:20px">{{ server_error }}</div>{% endif %}

  {% if missing %}
  <div class="warn" style="margin-top:20px" role="status">
    <p>입을 수 있는 <b>{{ with_iga(missing | map("ko", "category") | list) }}</b> 없어서 코디를 만들 수 없어요.</p>
    <a class="btn btn-primary" href="/garments/new" style="height:40px">옷 등록하기</a>
  </div>
  {% endif %}

<form class="stack" method="post" action="/recommend" novalidate data-loading="코디 고르는 중…">
  <input type="hidden" name="request_key" value="{{ new_recommendation_key() }}">
  <input type="hidden" name="forecast_token" id="forecast-token" value="{{form.forecast_token or ''}}">
    <fieldset class="field required-slot">
      <legend><label for="clothing-season">실내 복장에 적용할 계절</label></legend>
      <select id="clothing-season" name="clothing_season" required>
      {% for value,label in SEASONS.items() %}<option value="{{value}}" {% if form.clothing_season==value %}selected{% endif %}>{{label}}</option>{% endfor %}
      </select>
      <p class="hint">실내도 실외 기온을 기준으로 골라요. 겨울 실내는 상의 최소 보온도만 한 단계 낮추고, 여름 실내는 냉방 대비 얇은 아우터를 우선해요. 일정에 실내·실외를 지정해 주세요.</p>
      {% if errors.clothing_season %}<p class="field-error">{{errors.clothing_season}}</p>{% endif %}
    </fieldset>
    <fieldset class="field required-slot"><legend><label for="wardrobe-select">추천할 옷장</label></legend>
      <select id="wardrobe-select" name="wardrobe">
        <option value="personal" {% if form.wardrobe == 'personal' %}selected{% endif %}>내 옷장</option>
        <option value="example" {% if form.wardrobe == 'example' %}selected{% endif %}>테스트용 옷장</option>
        {% if form.wardrobe == 'all' %}<option value="all" selected>전체 옷장 (이전 추천)</option>{% endif %}
      </select>
      <p class="hint">선택한 옷장 안에서만 추천해요. 옷장을 바꾸면 필수·제외 의류는 다시 선택해 주세요.</p>
    </fieldset>
    <fieldset class="field required-slot"><legend><label for="reference-id">저장한 스타일 재현 (선택)</label></legend>
      <select id="reference-id" name="reference_id"><option value="">참고 사진 없이 추천</option>{% for r in refs if r.status=='ready' %}<option value="{{r.id}}" data-version="{{r.version}}" {% if reference_id==r.id %}selected{% endif %}>참고 스타일 {{loop.index}} · {{r.features|length}}개 의류 특징</option>{% endfor %}</select>
      <input type="hidden" id="reference-version" name="reference_version" value="{{reference_version or ''}}">
      <p class="hint">확인한 사진 특징을 내 옷과 비교해요. 필수 의류·하루 일정 조건은 그대로 유지돼요.</p>
      <a id="reference-manage" href="/style-references?back={{('/recommend?' ~ request.url.query)|urlencode}}">사진 올리고 특징 확인하기</a>
      {% if errors.reference %}<p class="field-error" role="alert">{{errors.reference}}</p>{% endif %}
    </fieldset>
    <fieldset class="field required-slot">
      <legend><label for="schedule-id">하루 일정 (선택)</label></legend>
      <select id="schedule-id" name="schedule_id"><option value="">일정 없이 추천</option>{% for p in plans %}<option value="{{p.id}}" data-version="{{p.version}}" {% if schedule_id==p.id %}selected{% endif %}>{{p.date}} · 일정 {{p.events|length}}개</option>{% endfor %}</select>
      <input id="schedule-version" type="hidden" name="schedule_version" value="{{schedule_version or ''}}">
      <p class="hint">실내외 모두 남은 시간 예보 또는 입력 기온을 기준으로 골라요. 직접 입력한 일정 기온이 더 춥거나 더우면 함께 고려해요.</p>
      <a id="schedule-manage" href="/day-plans?back={{('/recommend?' ~ request.url.query)|urlencode}}">하루 일정 입력·관리</a>
      <label for="outerwear-policy" style="margin-top:12px">아우터 착용 방식</label>
      <select id="outerwear-policy" name="outerwear_policy"><option value="always_worn">계속 입기</option><option value="removable" {% if outerwear_policy=='removable' %}selected{% endif %}>구간별로 벗고 소지 가능</option></select>
      <p class="hint">벗는 구간에도 필수 선택 아우터는 코디와 소지 의류에 유지돼요.</p>
      {% if errors.schedule %}<p class="field-error" role="alert">{{errors.schedule}}</p>{% endif %}
    </fieldset>
    <fieldset class="field">
      <legend>꼭 입고 싶은 옷</legend>
      <p class="hint">선택한 옷은 모든 추천에 포함돼요. 상의·하의·아우터에서 각각 한 벌까지 고를 수 있어요.</p>
      <div class="required-slots">
        {% for category in ["top", "bottom", "outerwear"] %}
        <div class="required-slot">
          <label for="required-{{ category }}">필수 {{ category | ko("category") }}</label>
          <select id="required-{{ category }}" name="required" {% if errors.required %}aria-describedby="required-error"{% endif %}>
            <option value="">선택하지 않음</option>
            {% for g in usable if g.category == category %}
            <option value="{{ g.garment_id }}" {% if g.garment_id in (form.required or []) %}selected{% endif %}>{{ garment_name(g) }}</option>
            {% endfor %}
          </select>
        </div>
        {% endfor %}
      </div>
      {% if errors.required %}<p class="field-error" id="required-error" role="alert">{{ errors.required }}</p>{% endif %}
      {% for gid in form.exclude or [] %}<input type="hidden" name="exclude" value="{{ gid }}">{% endfor %}
    </fieldset>
    <fieldset class="field">
      <legend><label for="temp">오늘 기온</label></legend>
      <div class="chips">
        <label class="chip"><input type="radio" name="weather_mode" value="forecast" {% if form.weather_mode == 'forecast' %}checked{% endif %}><span>지금부터 밤까지 예보</span></label>
        <label class="chip"><input type="radio" name="weather_mode" value="temperature" {% if form.weather_mode not in ['seasonal','forecast'] %}checked{% endif %}><span>현재 기온 · 직접 입력</span></label>
        <label class="chip"><input type="radio" name="weather_mode" value="seasonal" {% if form.weather_mode == 'seasonal' %}checked{% endif %}><span>계절·월 평균으로 추천받기</span></label>
      </div>
      {% set selected_month = (form.month or current_month()) | int %}
      {% set selected_season = form.season or season_for_month(selected_month) %}
      <div id="season-controls" style="margin:12px 0">
        <label for="season">계절</label>
        <select id="season" name="season">{% for key, label in SEASONS.items() %}<option value="{{key}}" {% if key == selected_season %}selected{% endif %}>{{label}}</option>{% endfor %}</select>
        <label for="month">월</label>
        <select id="month" name="month">{% for m, value in MONTHLY_TEMPERATURES.items() %}<option value="{{m}}" {% if m == selected_month %}selected{% endif %}>{{m}}월 · {{value}}°C</option>{% endfor %}</select>
        <p class="hint" id="season-estimate">기상청 전국 월평균(1991–2020)을 사용해요. 실제 현재 기온과 다를 수 있어요.</p>
      </div>
      <div class="temp-row">
        <button type="button" class="step-btn" data-step="-1" aria-label="1도 내리기">−</button>
        <div class="input-box{% if errors.temp %} invalid{% endif %}">
          <input id="temp" name="temp" inputmode="decimal" placeholder="15" value="{{ form.temp or '' }}" autocomplete="off"
                 {% if errors.temp %}aria-invalid="true" aria-describedby="temp-error" autofocus{% endif %}>
          <span class="suffix">°C</span>
        </div>
        <button type="button" class="step-btn" data-step="1" aria-label="1도 올리기">+</button>
      </div>
      <p class="wline"><span id="weather-status" aria-live="polite">{% if autofill %}지금 기온 불러오는 중…{% endif %}</span>
        <button type="button" class="link-btn" id="weather-reload" {% if autofill %}hidden{% endif %}>기온·남은 시간 예보 다시 조회</button></p>
      <p class="hint" id="forecast-summary">예보는 현재 시각부터 오늘 24시 전까지(한국 시간)의 시간별 기온으로 계산해요. 과거 새벽과 다음 날 기온은 범위에서 제외해요. 예보는 30분 후 또는 자정에 다시 조회해야 해요.</p>
      {% if not USE_MOCK %}<p class="hint">Weather data by <a href="https://open-meteo.com/" target="_blank" rel="noopener noreferrer">Open-Meteo.com</a></p>{% endif %}
      {% if errors.temp %}<p class="field-error" id="temp-error">{{ errors.temp }}</p>{% endif %}
    </fieldset>

    <fieldset class="field">
      <legend>상황</legend>
      <div class="chips">
        <label class="chip"><input type="radio" name="occasion" value="" {% if not form.occasion %}checked{% endif %}><span>상관없음</span></label>
        {% for o in LABELS["occasion"] %}
        <label class="chip"><input type="radio" name="occasion" value="{{ o }}" {% if form.occasion == o %}checked{% endif %}><span>{{ o | ko("occasion") }}</span></label>
        {% endfor %}
      </div>
      {% if errors.occasion %}<p class="field-error">{{ errors.occasion }}</p>{% endif %}
    </fieldset>

    <fieldset class="field">
      <legend>원하는 스타일</legend>
      <div class="chips" id="pref-styles">
        {% for s in QUICK_STYLES %}
        {{ term_chip("style", s, "styles", s in form.styles) }}
        {% endfor %}
      </div>
      <div class="input-box style-text">
        <input name="style_text" maxlength="{{ MAX_STYLE_TEXT }}" value="{{ form.style_text or '' }}" autocomplete="off"
               placeholder="직접 입력 (예: 빈티지, 꾸안꾸)" aria-label="원하는 스타일 직접 입력">
      </div>
      {{ term_guide("style") }}
      {% if errors.styles %}<p class="field-error">{{ errors.styles }}</p>{% endif %}
    </fieldset>

    <button type="submit" class="btn btn-primary" {% if missing %}disabled{% endif %}>코디 추천받기</button>
  </form>
</div>
{% endblock %}
{% block script %}
<script nonce="{{ request.state.csp_nonce | default('') }}">
  // 기온 1도씩 올리기·내리기
  const wardrobeSelect=document.getElementById('wardrobe-select');
  wardrobeSelect.addEventListener('change',()=>{
    const params=new URLSearchParams(new FormData(wardrobeSelect.form));
    for(const key of ['required','exclude','request_key']) params.delete(key);
    location.assign('/recommend?'+params.toString());
  });
  const scheduleSelect=document.getElementById('schedule-id');
  const referenceSelect=document.getElementById('reference-id');
  referenceSelect.addEventListener('change',()=>{document.getElementById('reference-version').value=referenceSelect.selectedOptions[0].dataset.version||'';});
  for(const [id,path] of [['reference-manage','/style-references'],['schedule-manage','/day-plans']]){
    const link=document.getElementById(id);
    const remember=()=>{const back='/recommend?'+new URLSearchParams(new FormData(scheduleSelect.form));link.href=path+'?'+new URLSearchParams({back});};
    link.addEventListener('click',remember);
    link.addEventListener('contextmenu',remember);
    scheduleSelect.form.addEventListener('input',remember);
    scheduleSelect.form.addEventListener('change',remember);
  }
  scheduleSelect.addEventListener('change',()=>{document.getElementById('schedule-version').value=scheduleSelect.selectedOptions[0].dataset.version||'';});
  const temp = document.getElementById("temp");
  document.querySelectorAll(".step-btn").forEach((b) => b.addEventListener("click", () => {
    const now = parseFloat(temp.value);
    const next = (Number.isFinite(now) ? now : 15) + Number(b.dataset.step);
    temp.value = Math.min(45, Math.max(-30, Math.round(next * 10) / 10));
  }));

  // Location and network both have deadlines. A late response never overwrites a user's choice.
  const wStatus=document.getElementById('weather-status'), wReload=document.getElementById('weather-reload');
  const seasonSelect=document.getElementById('season'), monthSelect=document.getElementById('month');
  const seasonMonths={{ SEASON_MONTHS | tojson }}, monthTemps={{ MONTHLY_TEMPERATURES | tojson }};
  const modes=[...document.querySelectorAll('input[name="weather_mode"]')];
  const forecastToken=document.getElementById('forecast-token');
  const forecastMode=()=>modes.find(b=>b.checked)?.value==='forecast';
  let weatherGeneration=0;
  const seasonal=()=>modes.find(b=>b.checked)?.value==='seasonal';
  function syncWeatherMode(){
    const active=seasonal();
    document.getElementById('season-controls').hidden=!active;
    temp.readOnly=active||forecastMode();
    document.querySelectorAll('.step-btn').forEach(b=>b.disabled=active||forecastMode());
    if(active){
      const months=seasonMonths[seasonSelect.value];
      if(!months.includes(Number(monthSelect.value)))monthSelect.value=String(months[1]);
      [...monthSelect.options].forEach(o=>{o.hidden=!months.includes(Number(o.value));o.disabled=o.hidden;});
      temp.value=monthTemps[monthSelect.value];
      document.getElementById('season-estimate').textContent=`${monthSelect.value}월 전국 평균 ${temp.value}°C로 추천해요. 기상청 1991–2020 평년값이며 실제 현재 기온과 다를 수 있어요.`;
    }
  }
  function userChange(){weatherGeneration++;wReload.hidden=false;syncWeatherMode();wStatus.textContent=seasonal()?'계절·월 평균 대체 기온을 사용해요.':forecastMode()?'남은 시간 예보를 조회해 주세요.':'기온을 직접 확인해 주세요.';}
  modes.forEach(b=>b.addEventListener('change',userChange));
  seasonSelect.addEventListener('change',userChange);monthSelect.addEventListener('change',userChange);
  temp.addEventListener('input',()=>{weatherGeneration++;wReload.hidden=false;});
  document.querySelectorAll('.step-btn').forEach(b=>b.addEventListener('click',()=>{weatherGeneration++;wReload.hidden=false;}));
  syncWeatherMode();
  async function fillWeather(lat,lon){
    const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),7000);
    try{
      const res=await fetch(`/weather?lat=${lat}&lon=${lon}`,{signal:controller.signal,headers:{'X-Requested-With':'fetch'}});
      if(!res.ok)throw new Error('weather');
      const w=await res.json();
      if(!Number.isFinite(w.temperature_c)||w.temperature_c < -30||w.temperature_c > 45)throw new Error('temperature');
      return w;
    }finally{clearTimeout(timer);}
  }
  async function autoWeather(){
    const generation=++weatherGeneration;
    wStatus.textContent='지금 기온 불러오는 중…';wReload.hidden=true;
    const location=await new Promise(resolve=>{
      const timer=setTimeout(()=>resolve(null),6500);
      if(!window.isSecureContext||!navigator.geolocation){clearTimeout(timer);resolve(null);return;}
      navigator.geolocation.getCurrentPosition(p=>{clearTimeout(timer);resolve(p.coords);},()=>{clearTimeout(timer);resolve(null);},{timeout:6000,maximumAge:600000});
    });
    if(generation!==weatherGeneration)return;
    try{
      let w;
      if(location){try{w=await fillWeather(location.latitude.toFixed(2),location.longitude.toFixed(2));}catch{}}
      if(generation!==weatherGeneration)return;
      const fallback=!w;
      if(!w)w=await fillWeather(37.57,126.98);
      if(generation!==weatherGeneration)return;
      forecastToken.value=w.forecast_token||'';
      modes.forEach(b=>b.checked=b.value===(w.forecast&&w.forecast_token?'forecast':'temperature'));syncWeatherMode();temp.value=w.temperature_c;
      document.getElementById('forecast-summary').textContent=w.forecast&&w.forecast_token?
        `지금부터 오늘 24시 전까지 예상 최저 ${w.forecast.minimum_c}°C · 최고 ${w.forecast.maximum_c}°C · 한국 시간, 시간별 예보 기준. 30분 후에는 다시 조회해 주세요.`:
        '남은 시간 예보를 받지 못해 현재 기온만 사용해요. 예보 다시 조회 또는 직접 입력이 가능해요.';
      const place=fallback?'서울':(w.location_name||'현재 위치');
      wStatus.textContent=w.is_stale?`${place} · ${Math.ceil((w.age_seconds||0)/60)}분 전 확인한 기온이에요. 현재와 다를 수 있어요. 잠시 후 다시 조회해 주세요.`:`${place} 기준`;
      if(w.fetched_at){const stamp=document.createElement('time');stamp.dateTime=w.fetched_at;stamp.textContent=' · 확인 '+new Date(w.fetched_at).toLocaleTimeString('ko-KR');wStatus.append(stamp);}
    }catch{
      if(generation!==weatherGeneration)return;
      modes.forEach(b=>b.checked=b.value==='seasonal');syncWeatherMode();
      wStatus.textContent='기온을 불러오지 못해 계절·월 평균으로 전환했어요. 계절과 월을 확인하거나 기온을 직접 입력해 주세요.';
    }finally{if(generation===weatherGeneration)wReload.hidden=false;}
  }
  wReload.addEventListener('click',autoWeather);
  {% if autofill %}autoWeather();{% endif %}
  // 스타일 최대 개수
  const boxes = document.querySelectorAll("#pref-styles input");
  function limit() {
    const n = [...boxes].filter((b) => b.checked).length;
    boxes.forEach((b) => { b.disabled = !b.checked && n >= {{ MAX_PREF_STYLES }}; b.parentElement.style.opacity = b.disabled ? .45 : 1; });
  }
  boxes.forEach((b) => b.addEventListener("change", limit)); limit();
</script>
{% endblock %}
"""

OUTFIT_CARD_HTML = """{# 코디 카드 가운데: 옷 목록(아우터·상의·하의) + 착샷이 생기면 왼쪽에 사진 (홈·추천 결과 공통) #}
{% macro outfit_css() %}
<style>
  .fit { margin-top: 12px; display: grid; grid-template-columns: 1fr; gap: 14px; align-items: center; }
  .fit.with-shot { grid-template-columns: 112px 1fr; }   /* 착샷이 있으면 왼쪽에 */
  .mq { aspect-ratio: 2 / 3; border-radius: 12px; overflow: hidden; }
  .mq[hidden] { display: none; }
  .plist { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
  .prow { display: grid; grid-template-columns: 52px 1fr; gap: 10px; align-items: center; color: inherit; text-decoration: none;
          padding: 4px; margin: -4px; border-radius: 10px; }
  a.prow:hover { background: #f5f5f5; }
  .pthumb { display: grid; place-items: center; aspect-ratio: 1; border-radius: 10px; background: #f5f5f5; overflow: hidden; }
  .pthumb img { width: 100%; height: 100%; object-fit: cover; }
  .pthumb svg { width: 64%; height: 64%; }
  .prow.none .pthumb { border: 1.5px dashed #d4d4d4; background: none; color: var(--muted); font-size: .6875rem; }
  .prow small { display: block; font-size: .75rem; color: var(--muted); }
  .prow b { display: block; font-size: .875rem; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .prow.none b { font-weight: 400; color: var(--ink-soft); }
</style>
{% endmacro %}

{% macro outfit_fit(items, by_id, required=[]) %}
{% from "macros.html" import garment_art, term_guide %}
      <div class="fit">
        <div class="mq" hidden></div>
        <div class="plist">
          {% for slot, slot_name in SLOTS %}
          {% set gid = items[slot] %}
          {% if gid and by_id.get(gid) %}{% set g = by_id[gid] %}
          <a class="prow" href="/garments/{{ gid }}">
            <span class="pthumb">{{ garment_art(g, thumbnail=true) }}</span>
            <span><small>{{ slot_name }}{% if gid in required %} · 필수 포함{% endif %}</small><b>{{ garment_name(g) }}</b></span>
          </a>
          {% elif gid %}
          <div class="prow none">
            <span class="pthumb">?</span>
            <span><small>{{ slot_name }}</small><b>찾을 수 없음</b></span>
          </div>
          {% endif %}
          {% endfor %}
        </div>
      </div>
{% endmacro %}
"""

RECOMMEND_RESULT_HTML = """{% extends "app_base.html" %}
{% from "outfit_card.html" import outfit_css, outfit_fit %}
{% set feedback_scope_name = {'personal': '내 옷장', 'example': '테스트용 옷장'}.get(form.wardrobe, '전체 옷장') %}
{% block title %}추천 코디{% endblock %}
{% block page_head %}
{{ outfit_css() }}
<style>
  .res { max-width: 1040px; margin: 0 auto; }
  .head { display: flex; justify-content: space-between; align-items: flex-end; gap: 12px; flex-wrap: wrap; }
  .conds { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 6px; }
  .conds span { padding: 4px 10px; border-radius: 6px; background: #eeeeee; font-size: .8125rem; }
  .again { height: 40px; padding: 0 14px; font-size: .9375rem; }
  .outfits { margin-top: 24px; display: grid; gap: 16px; }
  .outfit { display: flex; flex-direction: column; background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 16px; }
  .outfit h2 { margin: 0; font-size: 1rem; display: flex; align-items: center; gap: 8px; }
  .tags { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; }
  .tags span { padding: 2px 8px; border-radius: 6px; background: #f5f5f5; font-size: .75rem; color: var(--ink-soft); }
  .reason { margin: 14px 0 0; font-size: .9375rem; line-height: 1.6; flex: 1; }
  .fb { margin-top: 8px; display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .fb button { height: 40px; border: 1px solid var(--line); border-radius: 10px; background: #fff; font: inherit;
               font-size: .875rem; cursor: pointer; display: inline-flex; align-items: center; justify-content: center; gap: 6px; }
  .fb button:hover { border-color: var(--ink-soft); }
  .fb button svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
  .fb button[aria-pressed="true"].up { background: var(--ink); color: #fff; border-color: var(--ink); }
  .fb button[aria-pressed="true"].down { background: var(--ink); color: #fff; border-color: var(--ink); }
  .fb-msg { margin: 8px 0 0; font-size: .8125rem; color: var(--ink-soft); min-height: 1.2em; }
  .empty { margin-top: 24px; padding: 40px 24px; border: 1px dashed var(--line); border-radius: 10px; background: #fff; text-align: center; }
  .empty h2 { margin: 0; font-size: 1.125rem; }
  .empty p { margin: 8px 0 20px; }
  @media (min-width: 900px) {
    .outfits { grid-template-columns: repeat(3, 1fr); }
  }
</style>
{% endblock %}
{% block content %}
{% from "macros.html" import garment_art, term_guide %}
{% from "tryon.html" import tryon_button, tryon_dialog with context %}
<div class="res">
  <div class="head">
    <div>
      <h1>추천 코디</h1>
      <p class="hint">저장된 추천이에요. 새로고침은 결과 조회만 해요. 의견을 남긴 뒤 ‘피드백 반영해 다시 추천’을 눌러 주세요.</p>
      <p class="hint" data-feedback-scope="{{form.wardrobe}}">좋아요·별로예요는 다음 {{feedback_scope_name}} 추천에만 반영해요.{% if form.wardrobe == 'example' %} 내 옷장 추천에는 영향을 주지 않아요.{% elif form.wardrobe == 'personal' %} 테스트용 옷장 피드백은 섞이지 않아요.{% endif %}</p>
      {% if result.is_fallback %}<p class="hint" role="status">자동 판단을 사용할 수 없어 기본 추천 규칙으로 골랐어요. 필수 의류·일정·참고 스타일 조건은 유지했어요.</p>
      {% elif result.provider == 'openai' %}<p class="hint" role="status">AI가 검증된 코디 후보의 순서와 이유를 정했어요. 필수 의류·일정·참고 스타일 조건은 유지했어요.</p>{% endif %}
      <div class="conds" aria-label="고른 조건">
        {% if result.context.clothing_season %}<span>실내 복장: {{SEASONS[result.context.clothing_season]}}</span>{% endif %}
        {% if result.context.weather_forecast %}<span>추천 당시~오늘 24시 전 예상 {{result.context.weather_forecast.minimum_c}}~{{result.context.weather_forecast.maximum_c}}°C · 한국 시간</span>{% endif %}
        <span>{{ "%g" | format(temperature) }}°C{% if form.season %} · {{form.month}}월 전국 평균 대체값{% endif %}</span>
        {% if form.occasion %}<span>{{ form.occasion | ko("occasion") }}</span>{% endif %}
        {% for s in form.all_styles %}<span title="{{ s | term_description("style") }}">{{ s | ko("style") }}</span>{% endfor %}
        <span>{{ '테스트용 옷장' if form.wardrobe == 'example' else '내 옷장' if form.wardrobe == 'personal' else '전체 옷장' }}</span>
        {% if form.exclude %}<span>{{ form.exclude | length }}벌 뺌</span>{% endif %}
        {% if form.required %}<span>선택한 옷 {{ form.required | length }}벌 필수 포함</span>{% endif %}
        {% if schedule_id %}<span>하루 일정 반영</span>{% endif %}
        {% if reference_id %}<span>참고 스타일 재현</span>{% endif %}
      </div>
    </div>
    <div>
      <form method="post" action="/recommend" id="regenerate-form">
        <input type="hidden" name="request_key" value="{{ new_recommendation_key() }}">
        {% for name,value in regenerate_fields.items() %}
          {% if value is iterable and value is not string %}
            {% for item in value %}<input type="hidden" name="{{name}}" value="{{item}}">{% endfor %}
          {% else %}<input type="hidden" name="{{name}}" value="{{value}}">{% endif %}
        {% endfor %}
        <button class="btn btn-primary" type="submit">피드백 반영해 다시 추천</button>
      </form>
      <a class="btn btn-secondary again" href="{{ edit_url }}">조건 바꾸기</a>
    </div>
  </div>

  {% if result.outfits %}
  <div class="outfits">
    {% for o in result.outfits %}
    <article class="outfit" aria-labelledby="o{{ loop.index }}">
      <h2 id="o{{ loop.index }}">코디 {{ loop.index }}</h2>
      {% if o.style_tags %}<div class="tags">{% for s in o.style_tags %}<span title="{{ s | term_description("style") }}">{{ s | ko("style") }}</span>{% endfor %}</div>{% endif %}
      {{ outfit_fit(o["items"], by_id, form.required or []) }}
      {{ tryon_button(o["items"], loop.index, profile) }}
      {% if o.reason %}<p class="reason">{{ o.reason }}</p>{% endif %}
      {% if o.reference_matches %}<section class="reference-matches"><h3 style="font-size:1rem">참고 사진과 비교</h3>{% for m in o.reference_matches %}<div style="padding:10px 0;border-top:1px solid var(--line)"><strong>{{m.category|ko('category')}}{% if m.fixed %} · 필수 선택 유지{% endif %}</strong>{% for text in m.matched %}<p class="hint">닮은 점: {{text}}</p>{% endfor %}{% for text in m.different %}<p class="hint">다른 점: {{text}}</p>{% endfor %}</div>{% endfor %}</section>{% endif %}
      {% if o.limitations and not o.wearing_plan %}<details><summary>재현의 한계 확인</summary>{% for limitation in o.limitations %}<p class="hint">{{limitation}}</p>{% endfor %}</details>{% endif %}
      {% if o.wearing_plan %}<section class="wearing-plan"><h3 style="font-size:1rem">하루 착용 안내</h3><p class="hint">상의·하의는 하루 동안 그대로 유지해요. 착샷은 위 전체 코디를 미리 보여줘요.</p>
      {% for step in o.wearing_plan %}<div style="padding:10px 0;border-top:1px solid var(--line)"><strong>{{step.start_at.strftime('%H:%M')}}–{{step.end_at.strftime('%H:%M')}} · {{purposes[step.purpose]}}</strong><br><span>{{environments[step.environment]}} · {{outer_actions[step.outerwear_action]}}</span><p class="hint">{{step.note}}</p></div>{% endfor %}
      <details><summary>추천의 한계 확인</summary>{% for limitation in o.limitations %}<p class="hint">{{limitation}}</p>{% endfor %}</details></section>{% endif %}
      {% set previous = feedback_by_outfit.get(o.outfit_id) if feedback_by_outfit else none %}
      <form class="fb" method="post" action="/recommend/feedback" data-fb data-reason="{{previous.reason or '' if previous else ''}}" data-codes="{{(previous.reason_codes if previous else [])|tojson|forceescape}}">
        <input type="hidden" name="recommendation_id" value="{{ result.recommendation_id }}">
        <input type="hidden" name="outfit_id" value="{{ o.outfit_id }}">
        <input type="hidden" name="back" value="{{ request.url.path }}?{{ request.url.query }}">
        <button class="up" name="accepted" value="1" aria-pressed="{{'true' if previous and previous.accepted else 'false'}}">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 10v11H4V10zM7 10l4-7c1.7 0 2.5 1 2.2 2.6L12.6 9H19a2 2 0 0 1 2 2.3l-1.3 7.5A2.5 2.5 0 0 1 17.2 21H7"/></svg>좋아요</button>
        <button class="down" name="accepted" value="0" aria-pressed="{{'true' if previous and not previous.accepted else 'false'}}">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M17 14V3h3v11zM17 14l-4 7c-1.7 0-2.5-1-2.2-2.6l.6-3.4H5a2 2 0 0 1-2-2.3l1.3-7.5A2.5 2.5 0 0 1 6.8 3H17"/></svg>별로예요</button>
      </form>
      <p class="fb-msg" aria-live="polite">{{feedback_warning or ""}}</p>
    </article>
    {% endfor %}
  </div>
  {% else %}
  <section class="empty">
    <h2>조건에 맞는 코디를 찾지 못했어요</h2>
    {% if missing %}
    <p class="soft">입을 수 있는 {{ with_iga(missing | map("ko", "category") | list) }} 없어요.</p>
    <a class="btn btn-primary" href="/garments/new">옷 등록하기</a>
    {% else %}

    <a class="btn btn-secondary" href="/recommend">조건 바꾸기</a>
    {% endif %}
  </section>
  {% endif %}
</div>
<dialog id="feedback-dialog" aria-labelledby="feedback-title" style="box-sizing:border-box;width:min(92vw,460px);border:1px solid #ddd;border-radius:14px;padding:24px;max-height:85vh">
  <form id="feedback-details">
    <h2 id="feedback-title">어떤 점이 아쉬웠나요?</h2>
    <p class="hint">선택한 이유와 의견은 다음 {{feedback_scope_name}} 추천에만 반영해요. 필수 의류와 일정 조건은 유지해요.</p>
    <div class="chips">{% for code,label in FEEDBACK_LABELS.items() %}<label class="chip"><input type="checkbox" name="reason_codes" value="{{code}}"><span>{{label}}</span></label>{% endfor %}</div>
    <label for="feedback-reason" style="display:block;margin-top:16px">자세한 의견 (선택)</label>
    <textarea id="feedback-reason" name="reason" rows="4" maxlength="1000" style="box-sizing:border-box;width:100%;font:inherit" placeholder="예: 밝은 색 조합보다 차분한 색이 좋아요"></textarea>
    <p class="hint">‘추워요’, ‘영하인데 반팔이에요’ 같은 명확한 의견은 보온 조건에도 반영해요. 가장 정확하게 반영하려면 위 이유를 함께 선택해 주세요. 개인정보는 적지 말아 주세요.</p>
    <p id="feedback-error" role="alert"></p>
    <div style="display:flex;gap:8px"><button class="btn btn-primary" type="submit">피드백 저장</button><button class="btn btn-secondary" type="button" id="feedback-cancel">취소</button></div>
  </form>
</dialog>
{{ tryon_dialog() }}
{% endblock %}
{% block script %}
<script nonce="{{ request.state.csp_nonce | default('') }}">
  const dialog=document.getElementById('feedback-dialog'), details=document.getElementById('feedback-details');
  const feedbackError=document.getElementById('feedback-error');
  let activeForm=null, saving=false;
  document.getElementById('feedback-cancel').addEventListener('click',()=>{if(!saving)dialog.close();});
  dialog.addEventListener('cancel',e=>{if(saving)e.preventDefault();});
  async function saveFeedback(form, accepted, reason='', codes=[]){
    const body=new FormData(form);body.set('accepted',accepted);body.set('reason',reason);codes.forEach(c=>body.append('reason_codes',c));
    const buttons=[...form.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);
    const regenerate=document.querySelector('#regenerate-form button');if(regenerate)regenerate.disabled=true;
    try{
      const res=await fetch(form.action,{method:'POST',body,headers:{'X-Requested-With':'fetch'}});
      if(!res.ok)throw new Error('save');
      const saved=await res.json();codes=saved.reason_codes||codes;
      buttons.forEach(b=>b.setAttribute('aria-pressed',String(b.value===accepted)));
      form.dataset.reason=reason;form.dataset.codes=JSON.stringify(codes);
      const thermal=codes.includes('too_cold')?' 더 따뜻한 조합을 찾도록 반영했어요.':codes.includes('too_warm')?' 더 가벼운 조합을 찾도록 반영했어요.':'';
      form.nextElementSibling.textContent={{('피드백을 저장했어요. 다음 ' ~ feedback_scope_name ~ ' 추천부터 반영해요.')|tojson}}+thermal+' 위의 ‘피드백 반영해 다시 추천’을 눌러 주세요.';
    }finally{buttons.forEach(b=>b.disabled=false);if(regenerate)regenerate.disabled=false;}
  }
  document.querySelectorAll('form[data-fb]').forEach(form=>{
    form.addEventListener('submit',async e=>{
      e.preventDefault();const accepted=e.submitter?.value;
      if(accepted==='0'){
        activeForm=form;details.reset();feedbackError.textContent='';
        document.getElementById('feedback-reason').value=form.dataset.reason||'';
        const codes=JSON.parse(form.dataset.codes||'[]');details.querySelectorAll('[name="reason_codes"]').forEach(b=>b.checked=codes.includes(b.value));
        dialog.showModal();return;
      }
      if(accepted!=='1')return;
      try{await saveFeedback(form,'1');}catch{form.nextElementSibling.textContent='보내지 못했어요. 다시 눌러 주세요.';}
    });
  });
  details.addEventListener('submit',async e=>{
    e.preventDefault();if(saving||!activeForm)return;saving=true;feedbackError.textContent='';
    details.querySelector('[type="submit"]').disabled=true;
    try{
      const values=new FormData(details);
      await saveFeedback(activeForm,'0',String(values.get('reason')||''),values.getAll('reason_codes'));
      dialog.close();
    }catch{feedbackError.textContent='저장하지 못했어요. 입력한 내용은 유지되니 다시 시도해 주세요.';}
    finally{saving=false;details.querySelector('[type="submit"]').disabled=false;}
  });
  document.getElementById('regenerate-form')?.addEventListener('submit',e=>{e.currentTarget.querySelector('button').disabled=true;});
</script>
{% endblock %}
"""

HOME_HTML = """{% extends "app_base.html" %}
{% from "outfit_card.html" import outfit_css, outfit_fit %}
{% from "tryon.html" import tryon_button, tryon_dialog with context %}
{% block title %}홈{% endblock %}
{% block page_head %}
{{ outfit_css() }}
<style>
  .home { max-width: 640px; margin: 0 auto; }
  .date { margin: 0; font-size: .9375rem; color: var(--ink-soft); }
  .temp { margin: 2px 0 0; font-size: 2.75rem; font-weight: 700; line-height: 1.15; letter-spacing: -.02em; }
  .wmeta { margin: 2px 0 0; display: flex; flex-wrap: wrap; gap: 4px 10px; font-size: .875rem; color: var(--ink-soft); }
  .wmeta .link-btn { font-size: .8125rem; font-weight: 500; }
  .sec { margin-top: 32px; }
  .sec-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
  .sec-head h2 { margin: 0; font-size: 1.0625rem; }
  .sec-head a { font-size: .875rem; color: var(--ink-soft); text-decoration: none; }
  .sec-head a:hover { color: var(--ink); text-decoration: underline; }
  .outfit { margin-top: 12px; padding: 16px; background: #fff; border: 1px solid var(--line); border-radius: 10px; }
  .outfit .fit { margin-top: 0; }
  .empty { margin-top: 12px; padding: 20px 16px; background: #fff; border: 1px solid var(--line); border-radius: 10px; }
  .empty p { margin: 0 0 12px; font-size: .9375rem; }
  .empty .btn { height: 40px; }
  .stats { margin-top: 12px; display: grid; grid-template-columns: repeat(4, 1fr); border: 1px solid var(--line);
           border-radius: 10px; background: #fff; overflow: hidden; }
  .stats a { display: flex; flex-direction: column; gap: 2px; padding: 14px 12px; color: inherit; text-decoration: none; }
  .stats a + a { border-left: 1px solid var(--line); }
  .stats a:hover { background: #f5f5f5; }
  .stats small { font-size: .75rem; color: var(--ink-soft); }
  .stats b { font-size: 1.25rem; }
  .add { margin-top: 12px; width: 100%; height: 44px; }
</style>
{% endblock %}
{% block content %}
<div class="home">
  <section aria-label="오늘 날씨">
    <p class="date">{{ date_label }}</p>
    {% if weather %}
    <p class="temp" id="home-temperature">{{ "%g" | format(weather.temperature_c) }}°C</p>
    <p class="wmeta"><span>{{ place }}{% if weather.condition and weather.condition != "Mock" %} · {{ weather.condition }}{% endif %}</span>
      {% if is_fallback %}<button type="button" class="link-btn" id="loc-again">내 위치로</button>{% endif %}</p>
    {% else %}
    <p class="temp" id="home-temperature">-°C</p>
    {% endif %}
    <p class="wmeta" id="home-weather-status" role="status">{% if not weather %}날씨 조회가 지연되고 있어요. 다시 시도하거나 계절·월 평균으로 추천받을 수 있어요.{% elif weather.is_stale %}최근 {{ ((weather.age_seconds or 0) / 60) | round(0, 'ceil') | int }}분 전 확인한 기온이에요. 현재와 다를 수 있어요.{% endif %}</p>
    {% if weather and weather.fetched_at %}<p class="hint" id="home-weather-time">확인 시각 <time datetime="{{ weather.fetched_at }}">{{ weather.fetched_at }}</time></p>{% endif %}
    <div style="display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin-top:10px">
      <button type="button" class="link-btn" style="min-height:40px" id="home-weather-retry">기온 다시 조회</button>
      <a class="link-btn" style="display:block;line-height:1.5;padding:8px 0" href="/recommend?weather_mode=seasonal">계절·월 평균으로 추천받기</a>
    </div>
    {% if weather and not USE_MOCK %}<p class="hint">Weather data by <a href="https://open-meteo.com/" target="_blank" rel="noopener noreferrer">Open-Meteo.com</a></p>{% endif %}
  </section>

  <section class="sec" aria-labelledby="today-title">
    <div class="sec-head">
      <h2 id="today-title">최근 추천 코디</h2>
      {% if weather %}<a href="/recommend">다른 코디</a>{% endif %}
    </div>
    {% if outfit %}
    <article class="outfit">
      {{ outfit_fit(outfit["items"], by_id) }}
      {{ tryon_button(outfit["items"], 1, profile, "오늘의 코디 착샷") }}
    </article>
    {% elif missing %}
    <div class="empty">
      <p>입을 수 있는 {{ with_iga(missing | map("ko", "category") | list) }} 없어서 코디를 만들 수 없어요.</p>
      <a class="btn btn-primary" href="/garments/new">옷 등록</a>
    </div>
    {% else %}
    <div class="empty">
      <p>{{ outfit_error or "저장된 코디가 없어요. 조건을 골라 첫 추천을 받아 보세요." }}</p>
      <a class="btn btn-secondary" href="/recommend">조건 골라서 추천받기</a>
    </div>
    {% endif %}
  </section>

  <section class="sec" aria-labelledby="closet-title">
    <div class="sec-head">
      <h2 id="closet-title">{{ "테스트용 옷장" if wardrobe == "example" else "내 옷장" }} <span class="soft" style="font-weight:400;font-size:.9375rem">{{ total }}벌</span></h2>
      <a href="/wardrobe?wardrobe={{ wardrobe }}">전체 보기</a>
    </div>
    <div class="stats">
      {% for c in ["top", "bottom", "outerwear"] %}
      <a href="/wardrobe?wardrobe={{ wardrobe }}&category={{ c }}"><small>{{ c | ko("category") }}</small><b>{{ cat_counts[c] }}</b></a>
      {% endfor %}
      <a href="/wardrobe?wardrobe={{ wardrobe }}&status=laundry"><small>세탁 중</small><b>{{ laundry }}</b></a>
    </div>
    <a class="btn btn-secondary add" href="/garments/new">옷 등록</a>
  </section>
</div>
{{ tryon_dialog() }}
{% endblock %}
{% block script %}
<script nonce="{{ request.state.csp_nonce | default('') }}">
  // 내 위치 → 쿠키(소수 둘째 자리) → 새로고침하면 서버가 그 위치 기온으로 다시 그림. 거부하면 서울 기준
  (() => {
    document.querySelectorAll('#home-weather-time time').forEach(el=>{el.textContent=new Date(el.dateTime).toLocaleTimeString('ko-KR');});
    const retry=document.getElementById('home-weather-retry'), status=document.getElementById('home-weather-status');
    retry.addEventListener('click',async()=>{
      retry.disabled=true;status.textContent='기온을 다시 확인하고 있어요…';
      const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),7000);
      try{
        let coords=[37.57,126.98];
        const loc=document.cookie.split('; ').find(v=>v.startsWith('{{ LOC_COOKIE }}='));
        if(loc){const p=decodeURIComponent(loc.split('=')[1]).split(',').map(Number);if(p.length===2&&p.every(Number.isFinite)&&Math.abs(p[0])<=90&&Math.abs(p[1])<=180)coords=p;}
        const response=await fetch(`/weather?lat=${coords[0]}&lon=${coords[1]}`,{signal:controller.signal});
        if(response.status===401){status.textContent='로그인이 만료됐어요. 다시 로그인해 주세요.';return;}
        if(!response.ok)throw new Error('weather');
        const data=await response.json();
        if(!Number.isFinite(data.temperature_c))throw new Error('temperature');
        document.getElementById('home-temperature').textContent=`${data.temperature_c}°C`;
        document.getElementById('home-weather-time')?.remove();
        status.textContent=data.is_stale?`최근 ${Math.ceil((data.age_seconds||0)/60)}분 전 기온이에요. 현재와 다를 수 있어요.`:'기온을 다시 확인했어요.';
        if(data.fetched_at){const stamp=document.createElement('time');stamp.dateTime=data.fetched_at;stamp.textContent=' · 확인 '+new Date(data.fetched_at).toLocaleTimeString('ko-KR');status.append(stamp);}
      }catch{document.getElementById('home-temperature').textContent='-°C';document.getElementById('home-weather-time')?.remove();status.textContent='날씨 조회가 지연되고 있어요. 잠시 후 다시 시도하거나 계절·월 평균으로 추천받아 주세요.';}
      finally{clearTimeout(timer);setTimeout(()=>{retry.disabled=false;},3000);}
    });
    const save = (v) => { document.cookie = `{{ LOC_COOKIE }}=${v}; path=/; max-age=${60 * 60 * 24 * 7}; samesite=lax`; };
    function ask() {
      if (!window.isSecureContext || !navigator.geolocation) return save("none");
      navigator.geolocation.getCurrentPosition(
        (p) => { save(`${p.coords.latitude.toFixed(2)},${p.coords.longitude.toFixed(2)}`); location.reload(); },
        () => save("none"),
        { timeout: 8000, maximumAge: 10 * 60 * 1000 },
      );
    }
    document.getElementById("loc-again")?.addEventListener("click", ask);
    {% if ask_location %}ask();{% endif %}
  })();
</script>
{% endblock %}
"""

RECOMMEND_STATUS_HTML = """{% extends "app_base.html" %}
{% block title %}추천 요청 확인{% endblock %}
{% block content %}<h1>추천 요청 확인</h1><p role="status">{{ message }}</p>
{% if waiting %}<p class="muted">같은 요청을 다시 생성하지 않고 진행 상태만 확인합니다.</p>
<script nonce="{{ request.state.csp_nonce | default('') }}">setTimeout(() => window.location.reload(), 2000);</script>{% endif %}
<a class="btn" href="/recommend">추천 조건으로</a>{% endblock %}"""

register_templates(
    {
        "recommend_form.html": RECOMMEND_FORM_HTML,
        "recommend_status.html": RECOMMEND_STATUS_HTML,
        "recommend_result.html": RECOMMEND_RESULT_HTML,
        "outfit_card.html": OUTFIT_CARD_HTML,
        "home.html": HOME_HTML,
    },
    SEASONS=SEASONS,
    SEASON_MONTHS=SEASON_MONTHS,
    MONTHLY_TEMPERATURES=MONTHLY_TEMPERATURES,
    current_month=current_month,
    season_for_month=season_for_month,
    FEEDBACK_LABELS=FEEDBACK_LABELS,
    TEMP_BANDS=TEMP_BANDS,
    new_recommendation_key=lambda: str(uuid4()),
    MAX_PREF_STYLES=MAX_PREF_STYLES,
    QUICK_STYLES=QUICK_STYLES,
    MAX_STYLE_TEXT=MAX_STYLE_TEXT,
    LOC_COOKIE=LOC_COOKIE,
    SLOTS=SLOTS,
    with_iga=with_iga,
)
