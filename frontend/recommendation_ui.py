"""feat/recommendation-ui (D) — 코디 추천 요청·결과 화면.

다른 팀원과 연결되는 부분은 connect/recommendation.py 에만 있습니다:
- recommend(token, UserContext) → RecommendationResult   ↔ A feat/recommendation-rules·ranking (백엔드 C 경유)
- send_feedback(token, FeedbackEvent)                    ↔ A+C feat/recommendation-feedback

화면 흐름: /recommend (조건 고르기) → /recommend/result?... (코디 최대 3개, GET이라 새로고침·뒤로 가기 가능)
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from shared.contracts import FeedbackEvent, UserContext

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

router = APIRouter()

# DECISIONS 기온 구간: ≤2 / 2~10 / 10~20 / ≥20°C
# 화면은 기온 숫자만 받음 (2026-11-07 시아: 구간 버튼 없앰). 아래는 예전 ?band= 주소용
TEMP_BANDS = [
    ("freezing", "한파", "2°C 이하", 0),
    ("cold", "추움", "2~10°C", 6),
    ("mild", "선선", "10~20°C", 15),
    ("hot", "더움", "20°C 이상", 25),
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
    if temp <= 2:
        return "freezing"
    if temp < 10:
        return "cold"
    if temp < 20:
        return "mild"
    return "hot"


async def wearable_garments(token: str):
    """추천에 쓸 수 있는 옷(입을 수 있음)과 종류별 개수."""
    garments = await closet.list_garments(token)
    usable = [g for g in garments if g.status == "available"]
    counts = {c: sum(1 for g in usable if g.category == c) for c, _ in SLOTS}
    return garments, usable, counts


# ---- 0) 홈: 오늘 날짜·기온 + 오늘의 코디 + 내 옷장 요약 --------------------------
LOC_COOKIE = (
    "fitjang_loc"  # 브라우저 위치 "37.57,126.98"(소수 둘째 자리까지) 또는 "none"(거부)
)
WEEKDAYS = "월화수목금토일"
_HOME_CACHE: dict[
    tuple, tuple[float, object]
] = {}  # 같은 조건이면 오늘의 코디를 다시 안 부름
HOME_CACHE_SEC = 60 * 60


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


async def todays_outfit(token: str, temperature: float, usable) -> object | None:
    """오늘의 코디 1개 (추천 결과의 첫 번째). 한 시간 동안은 같은 결과를 보여줌."""
    key = (token, round(temperature), tuple(sorted(g.garment_id for g in usable)))
    hit = _HOME_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < HOME_CACHE_SEC:
        return hit[1]
    result = await rec.recommend(
        token,
        UserContext(
            user_id="me", temperature_c=temperature
        ),  # TODO(C): 토큰에서 사용자
    )
    outfit = result.outfits[0] if result.outfits else None
    if len(_HOME_CACHE) > 200:
        _HOME_CACHE.clear()
    _HOME_CACHE[key] = (time.monotonic(), outfit)
    return outfit


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
    outfit, outfit_error = None, None
    if weather and not missing:
        try:
            outfit = await todays_outfit(token, weather.temperature_c, usable)
        except ApiError as e:
            if e.status == 401:
                return logged_out()
            outfit_error = e.message
    try:
        profile = await auth.get_profile(token)  # 착샷(전신 사진 유무)용
    except ApiError:
        profile = None
    place = (weather.location_name if weather else None) or (
        "서울" if is_fallback else "현재 위치"
    )
    return render(
        request,
        "home.html",
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
        cat_counts={
            c: sum(1 for g in garments if g.category == c) for c in CATEGORY_ORDER
        },
        laundry=sum(1 for g in garments if g.status == "laundry"),
    )


# ---- 1) 조건 고르기 ------------------------------------------------------------
@router.get("/recommend", response_class=HTMLResponse)
async def recommend_page(request: Request):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return go_login(request)
    try:
        _, usable, counts = await wearable_garments(token)
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
    return render(
        request,
        "recommend_form.html",
        active="recommend",
        usable=usable,
        counts=counts,
        missing=missing,
        form={"temp": "", "styles": [], "style_text": "", "exclude": []},
        autofill=True,  # 처음 열 때만 오늘 기온 자동 입력
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
            {"detail": e.message, "code": e.code}, status_code=http_status(e)
        )
    return {
        "temperature_c": w.temperature_c,
        "band": temp_band(w.temperature_c),
        "condition": w.condition,
        "location_name": w.location_name,
    }


# ---- 2) 결과 -------------------------------------------------------------------
@router.get("/recommend/result", response_class=HTMLResponse)
async def recommend_result(
    request: Request,
    band: str = "",
    temp: str = "",
    occasion: str = "",
    styles: Annotated[list[str] | None, Query()] = None,
    style_text: str = "",
    exclude: Annotated[
        list[str] | None, Query()
    ] = None,  # 화면에서는 없앰 (예전 주소용)
):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return go_login(request)
    styles, exclude = styles or [], exclude or []
    form = {
        "band": band,
        "temp": temp.strip(),
        "occasion": occasion,
        "styles": styles,
        "style_text": style_text.strip()[:MAX_STYLE_TEXT],
        "exclude": exclude,
    }

    # 입력 검사 (화면에서 고르는 값이라 보통은 통과)
    errors: dict[str, str] = {}
    temperature: float | None = None
    if form["temp"]:
        try:
            temperature = round(float(form["temp"]), 1)
            if not -30 <= temperature <= 45:
                errors["temp"] = "기온은 -30~45°C 사이로 입력해 주세요."
        except ValueError:
            errors["temp"] = "기온은 숫자로 입력해 주세요."
    elif band in BAND_TEMP:  # 예전 주소
        temperature = BAND_TEMP[band]
    else:
        errors["temp"] = "오늘 기온을 입력해 주세요."
    if occasion and occasion not in LABELS["occasion"]:
        errors["occasion"] = "상황을 다시 골라 주세요."
    if any(s not in LABELS["style"] for s in styles) or len(styles) > MAX_PREF_STYLES:
        errors["styles"] = f"스타일은 {MAX_PREF_STYLES}개까지 고를 수 있어요."
    # 버튼 + 직접 입력 (겹치는 건 한 번만)
    all_styles = list(dict.fromkeys(styles + parse_style_text(form["style_text"])))
    form["all_styles"] = all_styles

    try:
        garments, usable, counts = await wearable_garments(token)
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
            )
        context = UserContext(
            user_id="me",  # TODO(C): 로그인(JWT) 후에는 백엔드가 토큰에서 사용자 확인
            temperature_c=temperature,
            occasion=occasion or None,
            preferred_styles=all_styles,
            excluded_items=exclude,
        )
        result = await rec.recommend(token, context)
    except ApiError as e:
        if e.status == 401:
            return logged_out()
        return render(
            request,
            "placeholder.html",
            status=http_status(e),
            active="recommend",
            title="추천을 받지 못했어요",
            message=e.message,
            back="/recommend",
        )

    by_id = {g.garment_id: g for g in garments}
    try:
        profile = await auth.get_profile(token)  # 착샷(전신 사진 유무)용 (없어도 됨)
    except ApiError:
        profile = None
    missing = [
        c
        for c in NEEDED
        if sum(1 for g in usable if g.category == c and g.garment_id not in exclude)
        == 0
    ]
    return render(
        request,
        "recommend_result.html",
        active="recommend",
        result=result,
        by_id=by_id,
        temperature=temperature,
        form=form,
        missing=missing,
        profile=profile,
    )


# ---- 3) 피드백 (좋아요 / 별로예요) ----------------------------------------------
@router.post("/recommend/feedback")
async def recommend_feedback(
    request: Request,
    recommendation_id: str = Form(""),
    outfit_id: str = Form(""),
    accepted: str = Form(""),
    back: str = Form("/recommend"),
):
    token = request.cookies.get(TOKEN_COOKIE)
    by_fetch = request.headers.get("x-requested-with") == "fetch"
    if not token:
        return (
            JSONResponse({"ok": False}, status_code=401)
            if by_fetch
            else go_login(request)
        )
    ok = bool(recommendation_id and outfit_id and accepted in ("1", "0"))
    if ok:
        try:
            await rec.send_feedback(
                token,
                FeedbackEvent(
                    recommendation_id=recommendation_id,
                    outfit_id=outfit_id,
                    accepted=accepted == "1",
                ),
            )
        except ApiError as e:
            if e.status == 401 and not by_fetch:
                return logged_out()
            ok = False
    if by_fetch:
        return JSONResponse({"ok": ok}, status_code=200 if ok else 400)
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
</style>
{% endblock %}
{% block content %}
{% from "macros.html" import garment_art %}
<div class="rec">
  <h1>코디 추천</h1>

  {% if server_error %}<div class="alert" role="alert" style="margin-top:20px">{{ server_error }}</div>{% endif %}

  {% if missing %}
  <div class="warn" style="margin-top:20px" role="status">
    <p>입을 수 있는 <b>{{ with_iga(missing | map("ko", "category") | list) }}</b> 없어서 코디를 만들 수 없어요.</p>
    <a class="btn btn-primary" href="/garments/new" style="height:40px">옷 등록하기</a>
  </div>
  {% endif %}

  <form class="stack" method="get" action="/recommend/result" novalidate data-loading="코디 고르는 중…">
    <fieldset class="field">
      <legend><label for="temp">오늘 기온</label></legend>
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
        <button type="button" class="link-btn" id="weather-reload" {% if autofill %}hidden{% endif %}>지금 기온 불러오기</button></p>
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
        <label class="chip"><input type="checkbox" name="styles" value="{{ s }}" {% if s in form.styles %}checked{% endif %}><span>{{ s | ko("style") }}</span></label>
        {% endfor %}
      </div>
      <div class="input-box style-text">
        <input name="style_text" maxlength="{{ MAX_STYLE_TEXT }}" value="{{ form.style_text or '' }}" autocomplete="off"
               placeholder="직접 입력 (예: 빈티지, 꾸안꾸)" aria-label="원하는 스타일 직접 입력">
      </div>
      {% if errors.styles %}<p class="field-error">{{ errors.styles }}</p>{% endif %}
    </fieldset>

    <button type="submit" class="btn btn-primary" {% if missing %}disabled{% endif %}>코디 추천받기</button>
  </form>
</div>
{% endblock %}
{% block script %}
<script>
  // 기온 1도씩 올리기·내리기
  const temp = document.getElementById("temp");
  document.querySelectorAll(".step-btn").forEach((b) => b.addEventListener("click", () => {
    const now = parseFloat(temp.value);
    const next = (Number.isFinite(now) ? now : 15) + Number(b.dataset.step);
    temp.value = Math.min(45, Math.max(-30, Math.round(next * 10) / 10));
  }));

  // 오늘 기온 자동 입력: 위치 허용 → 현재 위치, 거부·실패·응답 없음 → 서울 기준
  const wStatus = document.getElementById("weather-status"), wReload = document.getElementById("weather-reload");
  async function fillWeather(lat, lon, isFallback) {
    const res = await fetch(`/weather?lat=${lat}&lon=${lon}`, { headers: { "X-Requested-With": "fetch" } });
    if (!res.ok) throw new Error("weather");
    const w = await res.json();
    temp.value = w.temperature_c;
    const place = isFallback ? "서울" : (w.location_name || "현재 위치");
    const cond = w.condition && w.condition !== "Mock" ? ` · ${w.condition}` : "";
    wStatus.textContent = `${place} 기준${cond}`;
  }
  function autoWeather() {
    let done = false;
    wStatus.textContent = "지금 기온 불러오는 중…"; wReload.hidden = true;
    const finish = () => { done = true; wReload.hidden = false; };
    const fallback = () => {
      if (done) return; done = true;
      fillWeather(37.57, 126.98, true)
        .catch(() => { wStatus.textContent = "불러오지 못했어요. 직접 입력해 주세요."; })
        .finally(finish);
    };
    if (!window.isSecureContext || !navigator.geolocation) return fallback();
    setTimeout(fallback, 8000);  // 위치 허용 창에 답이 없을 때
    navigator.geolocation.getCurrentPosition(
      (p) => { if (done) return; done = true;
        fillWeather(p.coords.latitude.toFixed(2), p.coords.longitude.toFixed(2), false)
          .catch(() => { done = false; fallback(); }).finally(() => { if (done) finish(); }); },
      () => fallback(),
      { timeout: 6000, maximumAge: 10 * 60 * 1000 },
    );
  }
  wReload.addEventListener("click", autoWeather);
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

{% macro outfit_fit(items, by_id) %}
{% from "macros.html" import garment_art %}
      <div class="fit">
        <div class="mq" hidden></div>
        <div class="plist">
          {% for slot, slot_name in SLOTS %}
          {% set gid = items[slot] %}
          {% if gid and by_id.get(gid) %}{% set g = by_id[gid] %}
          <a class="prow" href="/garments/{{ gid }}">
            <span class="pthumb">{{ garment_art(g) }}</span>
            <span><small>{{ slot_name }}</small><b>{{ garment_name(g) }}</b></span>
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
  .fb button[aria-pressed="true"].down { background: #f5f5f5; color: var(--ink); border-color: var(--ink-soft); }
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
{% from "macros.html" import garment_art %}
{% from "tryon.html" import tryon_button, tryon_dialog with context %}
<div class="res">
  <div class="head">
    <div>
      <h1>추천 코디</h1>
      <div class="conds" aria-label="고른 조건">
        <span>{{ "%g" | format(temperature) }}°C</span>
        {% if form.occasion %}<span>{{ form.occasion | ko("occasion") }}</span>{% endif %}
        {% for s in form.all_styles %}<span>{{ s | ko("style") }}</span>{% endfor %}
        {% if form.exclude %}<span>{{ form.exclude | length }}벌 뺌</span>{% endif %}
      </div>
    </div>
    <a class="btn btn-secondary again" href="/recommend">조건 바꾸기</a>
  </div>

  {% if result.outfits %}
  <div class="outfits">
    {% for o in result.outfits %}
    <article class="outfit" aria-labelledby="o{{ loop.index }}">
      <h2 id="o{{ loop.index }}">코디 {{ loop.index }}</h2>
      {% if o.style_tags %}<div class="tags">{% for s in o.style_tags %}<span>{{ s | ko("style") }}</span>{% endfor %}</div>{% endif %}
      {{ outfit_fit(o["items"], by_id) }}
      {{ tryon_button(o["items"], loop.index, profile) }}
      {% if o.reason %}<p class="reason">{{ o.reason }}</p>{% endif %}
      <form class="fb" method="post" action="/recommend/feedback" data-fb>
        <input type="hidden" name="recommendation_id" value="{{ result.recommendation_id }}">
        <input type="hidden" name="outfit_id" value="{{ o.outfit_id }}">
        <input type="hidden" name="back" value="{{ request.url.path }}?{{ request.url.query }}">
        <button class="up" name="accepted" value="1" aria-pressed="false">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 10v11H4V10zM7 10l4-7c1.7 0 2.5 1 2.2 2.6L12.6 9H19a2 2 0 0 1 2 2.3l-1.3 7.5A2.5 2.5 0 0 1 17.2 21H7"/></svg>좋아요</button>
        <button class="down" name="accepted" value="0" aria-pressed="false">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M17 14V3h3v11zM17 14l-4 7c-1.7 0-2.5-1-2.2-2.6l.6-3.4H5a2 2 0 0 1-2-2.3l1.3-7.5A2.5 2.5 0 0 1 6.8 3H17"/></svg>별로예요</button>
      </form>
      <p class="fb-msg" aria-live="polite"></p>
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
{{ tryon_dialog() }}
{% endblock %}
{% block script %}
<script>
  // 피드백은 페이지 이동 없이 보내기 (JS가 꺼져 있으면 일반 제출 후 돌아옴)
  document.querySelectorAll("form[data-fb]").forEach((form) => {
    const msg = form.nextElementSibling;
    form.querySelectorAll("button").forEach((btn) => btn.addEventListener("click", async (e) => {
      e.preventDefault();
      const body = new FormData(form); body.set("accepted", btn.value);
      try {
        const res = await fetch(form.action, { method: "POST", body, headers: { "X-Requested-With": "fetch" } });
        if (!res.ok) throw new Error();
        form.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
        msg.textContent = "";
      } catch {
        msg.textContent = "보내지 못했어요. 다시 눌러 주세요.";
      }
    }));
  });
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
    <p class="temp">{{ "%g" | format(weather.temperature_c) }}°C</p>
    <p class="wmeta"><span>{{ place }}{% if weather.condition and weather.condition != "Mock" %} · {{ weather.condition }}{% endif %}</span>
      {% if is_fallback %}<button type="button" class="link-btn" id="loc-again">내 위치로</button>{% endif %}</p>
    {% else %}
    <p class="temp">-°C</p>
    <p class="wmeta">기온을 불러오지 못했어요.</p>
    {% endif %}
  </section>

  <section class="sec" aria-labelledby="today-title">
    <div class="sec-head">
      <h2 id="today-title">오늘의 코디</h2>
      {% if weather %}<a href="/recommend/result?temp={{ '%g' | format(weather.temperature_c) }}">다른 코디</a>{% endif %}
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
      <p>{{ outfit_error or "오늘의 코디를 불러오지 못했어요." }}</p>
      <a class="btn btn-secondary" href="/recommend">조건 골라서 추천받기</a>
    </div>
    {% endif %}
  </section>

  <section class="sec" aria-labelledby="closet-title">
    <div class="sec-head">
      <h2 id="closet-title">내 옷장 <span class="soft" style="font-weight:400;font-size:.9375rem">{{ total }}벌</span></h2>
      <a href="/wardrobe">전체 보기</a>
    </div>
    <div class="stats">
      {% for c in ["top", "bottom", "outerwear"] %}
      <a href="/wardrobe?category={{ c }}"><small>{{ c | ko("category") }}</small><b>{{ cat_counts[c] }}</b></a>
      {% endfor %}
      <a href="/wardrobe?status=laundry"><small>세탁 중</small><b>{{ laundry }}</b></a>
    </div>
    <a class="btn btn-secondary add" href="/garments/new">옷 등록</a>
  </section>
</div>
{{ tryon_dialog() }}
{% endblock %}
{% block script %}
<script>
  // 내 위치 → 쿠키(소수 둘째 자리) → 새로고침하면 서버가 그 위치 기온으로 다시 그림. 거부하면 서울 기준
  (() => {
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

register_templates(
    {
        "recommend_form.html": RECOMMEND_FORM_HTML,
        "recommend_result.html": RECOMMEND_RESULT_HTML,
        "outfit_card.html": OUTFIT_CARD_HTML,
        "home.html": HOME_HTML,
    },
    TEMP_BANDS=TEMP_BANDS,
    MAX_PREF_STYLES=MAX_PREF_STYLES,
    QUICK_STYLES=QUICK_STYLES,
    MAX_STYLE_TEXT=MAX_STYLE_TEXT,
    LOC_COOKIE=LOC_COOKIE,
    SLOTS=SLOTS,
    with_iga=with_iga,
)
