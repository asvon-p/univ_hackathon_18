"""feat/recommendation-ui (D) — 코디 추천 요청·결과 화면.

다른 팀원과 연결되는 부분은 connect/recommendation.py 에만 있습니다:
- recommend(token, UserContext) → RecommendationResult   ↔ A feat/recommendation-rules·ranking (백엔드 C 경유)
- send_feedback(token, FeedbackEvent)                    ↔ A+C feat/recommendation-feedback

화면 흐름: /recommend (조건 고르기) → /recommend/result?... (코디 최대 3개, GET이라 새로고침·뒤로 가기 가능)
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from shared.contracts import FeedbackEvent, UserContext

from .common import (
    LABELS,
    TOKEN_COOKIE,
    go_login,
    http_status,
    logged_out,
    register_templates,
    render,
)
from .connect import ApiError
from .connect import garment_storage as closet
from .connect import recommendation as rec
from .connect import weather as weather_api

router = APIRouter()

# DECISIONS 기온 구간: ≤2 / 2~10 / 10~20 / ≥20°C → 구간마다 대표 기온으로 보냄
TEMP_BANDS = [
    ("freezing", "한파", "2°C 이하", 0),
    ("cold", "추움", "2~10°C", 6),
    ("mild", "선선", "10~20°C", 15),
    ("hot", "더움", "20°C 이상", 25),
]
BAND_TEMP = {key: temp for key, _, _, temp in TEMP_BANDS}
MAX_PREF_STYLES = 3
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
        form={"band": "mild", "styles": [], "exclude": []},
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
    exclude: Annotated[list[str] | None, Query()] = None,
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
    elif band in BAND_TEMP:
        temperature = BAND_TEMP[band]
    else:
        errors["temp"] = "오늘 기온을 골라 주세요."
    if occasion and occasion not in LABELS["occasion"]:
        errors["occasion"] = "상황을 다시 골라 주세요."
    if any(s not in LABELS["style"] for s in styles) or len(styles) > MAX_PREF_STYLES:
        errors["styles"] = f"스타일은 {MAX_PREF_STYLES}개까지 고를 수 있어요."

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
            preferred_styles=styles,
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
        band=temp_band(temperature),
        form=form,
        missing=missing,
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
  .bands { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-top: 8px; }
  .band input { position: absolute; opacity: 0; pointer-events: none; }
  .band span { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px;
               height: 64px; border: 1px solid var(--line); border-radius: var(--radius); background: #fff;
               cursor: pointer; transition: all .15s; }
  .band b { font-size: .9375rem; }
  .band small { font-size: .75rem; color: var(--ink-soft); }
  .band input:checked + span { background: var(--ink); border-color: var(--ink); color: #fff; }
  .band input:checked + span small { color: #d6d3d1; }
  .band input:focus-visible + span { outline: 2px solid var(--ink); outline-offset: 2px; }
  .weather { margin-top: 6px; display: flex; align-items: center; flex-wrap: wrap; gap: 6px 8px; padding: 10px 12px;
             border-radius: var(--radius); background: var(--camel-soft); font-size: .875rem; }
  .weather[hidden] { display: none; }
  .weather svg { width: 18px; height: 18px; flex-shrink: 0; fill: none; stroke: var(--camel); stroke-width: 2; stroke-linecap: round; }
  .weather .link-btn { font-size: .8125rem; }
  .exact { margin-top: 10px; display: flex; align-items: center; gap: 8px; font-size: .875rem; color: var(--ink-soft); }
  .exact .input-box { width: 120px; }
  .exact .input-box input { height: 40px; }
  .chip input[type=checkbox] { position: absolute; opacity: 0; pointer-events: none; }
  .chip input:checked + span { background: var(--ink); border-color: var(--ink); color: #fff; }
  details.ex { border: 1px solid var(--line); border-radius: var(--radius); background: #fff; }
  details.ex summary { padding: 14px 16px; cursor: pointer; font-size: .9375rem; font-weight: 500; list-style: none;
                       display: flex; justify-content: space-between; align-items: center; }
  details.ex summary::-webkit-details-marker { display: none; }
  details.ex summary::after { content: "펼치기"; font-size: .8125rem; color: var(--ink-soft); font-weight: 400; }
  details.ex[open] summary::after { content: "접기"; }
  .ex-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; padding: 0 16px 16px; }
  .ex-item { position: relative; cursor: pointer; }
  .ex-item input { position: absolute; opacity: 0; pointer-events: none; }
  .ex-thumb { position: relative; display: grid; place-items: center; aspect-ratio: 1; border-radius: 10px;
              background: #f5f5f4; overflow: hidden; border: 2px solid transparent; }
  .ex-thumb img { width: 100%; height: 100%; object-fit: cover; }
  .ex-thumb svg { width: 64%; height: 64%; }
  .ex-item small { display: block; margin-top: 4px; font-size: .75rem; color: var(--ink-soft);
                   white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .ex-item input:checked + .ex-thumb { border-color: var(--danger); }
  .ex-item input:checked + .ex-thumb::after { content: "빼기"; position: absolute; inset: 0; display: grid; place-items: center;
                                              background: rgb(255 255 255 / .7); color: var(--danger); font-weight: 700; font-size: .8125rem; }
  .ex-item input:focus-visible + .ex-thumb { outline: 2px solid var(--ink); outline-offset: 2px; }
  .warn { padding: 16px; border-radius: var(--radius); background: var(--camel-soft); font-size: .9375rem; }
  .warn p { margin: 0 0 12px; }
  @media (min-width: 640px) { .ex-grid { grid-template-columns: repeat(6, 1fr); } }
</style>
{% endblock %}
{% block content %}
{% from "macros.html" import garment_art %}
<div class="rec">
  <h1>코디 추천</h1>
  <p class="soft" style="margin:6px 0 0">내 옷장에 있는 옷으로만 코디를 최대 3개 골라 드려요.</p>

  {% if server_error %}<div class="alert" role="alert" style="margin-top:20px">{{ server_error }}</div>{% endif %}

  {% if missing %}
  <div class="warn" style="margin-top:20px" role="status">
    <p><b>{{ with_iga(missing | map("ko", "category") | list) }}</b> 없어서 아직 코디를 만들 수 없어요.
       상의·하의를 한 벌씩은 등록해 주세요. (세탁 중·추천 제외인 옷은 빠져요)</p>
    <a class="btn btn-primary" href="/garments/new" style="height:40px">옷 등록하기</a>
  </div>
  {% endif %}

  <form class="stack" method="get" action="/recommend/result" novalidate data-loading="코디 고르는 중…">
    <fieldset class="field">
      <legend>오늘 기온 <small>필수</small></legend>
      <div class="weather" {% if not autofill %}hidden{% endif %}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>
        <span id="weather-status" aria-live="polite">오늘 기온을 불러오는 중…</span>
        <button type="button" class="link-btn" id="weather-reload" hidden>다시 불러오기</button>
      </div>
      <div class="bands">
        {% for key, name, range_, t in TEMP_BANDS %}
        <label class="band"><input type="radio" name="band" value="{{ key }}" {% if form.band == key and not form.temp %}checked{% endif %}>
          <span><b>{{ name }}</b><small>{{ range_ }}</small></span></label>
        {% endfor %}
      </div>
      <div class="exact">
        <label for="temp">또는 직접 입력</label>
        <div class="input-box{% if errors.temp %} invalid{% endif %}">
          <input id="temp" name="temp" inputmode="decimal" placeholder="예: 7" value="{{ form.temp or '' }}"
                 {% if errors.temp %}aria-invalid="true" aria-describedby="temp-error" autofocus{% endif %}>
          <span class="suffix">°C</span>
        </div>
      </div>
      {% if errors.temp %}<p class="field-error" id="temp-error">{{ errors.temp }}</p>{% endif %}
    </fieldset>

    <fieldset class="field">
      <legend>상황 <small>선택</small></legend>
      <div class="chips">
        <label class="chip"><input type="radio" name="occasion" value="" {% if not form.occasion %}checked{% endif %}><span>상관없음</span></label>
        {% for o in LABELS["occasion"] %}
        <label class="chip"><input type="radio" name="occasion" value="{{ o }}" {% if form.occasion == o %}checked{% endif %}><span>{{ o | ko("occasion") }}</span></label>
        {% endfor %}
      </div>
      {% if errors.occasion %}<p class="field-error">{{ errors.occasion }}</p>{% endif %}
    </fieldset>

    <fieldset class="field">
      <legend>원하는 스타일 <small>선택 · 최대 {{ MAX_PREF_STYLES }}개</small></legend>
      <div class="chips" id="pref-styles">
        {% for s in LABELS["style"] %}
        <label class="chip"><input type="checkbox" name="styles" value="{{ s }}" {% if s in form.styles %}checked{% endif %}><span>{{ s | ko("style") }}</span></label>
        {% endfor %}
      </div>
      {% if errors.styles %}<p class="field-error">{{ errors.styles }}</p>{% endif %}
    </fieldset>

    {% if usable %}
    <details class="ex" {% if form.exclude %}open{% endif %}>
      <summary>오늘 입기 싫은 옷 빼기 <small class="soft" id="ex-count">{% if form.exclude %}{{ form.exclude | length }}벌 뺌{% endif %}</small></summary>
      <div class="ex-grid">
        {% for g in usable %}
        <label class="ex-item" title="{{ garment_name(g) }}">
          <input type="checkbox" name="exclude" value="{{ g.garment_id }}" {% if g.garment_id in form.exclude %}checked{% endif %}
                 aria-label="{{ garment_name(g) }} 빼기">
          <span class="ex-thumb">{{ garment_art(g) }}</span>
          <small>{{ garment_name(g) }}</small>
        </label>
        {% endfor %}
      </div>
    </details>
    {% endif %}

    <button type="submit" class="btn btn-primary" {% if missing %}disabled{% endif %}>코디 추천받기</button>
  </form>
</div>
{% endblock %}
{% block script %}
<script>
  // 직접 입력하면 기온 구간 선택 해제, 구간 고르면 직접 입력 비우기
  const temp = document.getElementById("temp");
  const bands = document.querySelectorAll("input[name=band]");
  temp.addEventListener("input", () => { if (temp.value.trim()) bands.forEach((b) => (b.checked = false)); });
  bands.forEach((b) => b.addEventListener("change", () => { temp.value = ""; }));

  // 오늘 기온 자동 입력: 위치 허용 → 현재 위치, 거부·실패·응답 없음 → 서울 기준
  const wStatus = document.getElementById("weather-status"), wReload = document.getElementById("weather-reload");
  async function fillWeather(lat, lon, isFallback) {
    const res = await fetch(`/weather?lat=${lat}&lon=${lon}`, { headers: { "X-Requested-With": "fetch" } });
    if (!res.ok) throw new Error("weather");
    const w = await res.json();
    temp.value = w.temperature_c; bands.forEach((b) => (b.checked = b.value === w.band));  // 맞는 구간 표시 (보낼 때는 숫자가 우선)
    const place = isFallback ? "서울" : (w.location_name || "현재 위치");
    const cond = w.condition && w.condition !== "Mock" ? ` · ${w.condition}` : "";
    wStatus.textContent = `${place} 기온 ${w.temperature_c}°C${cond}를 넣었어요.` + (isFallback ? " (위치를 몰라서 서울 기준)" : "");
  }
  function autoWeather() {
    let done = false;
    wStatus.textContent = "오늘 기온을 불러오는 중…"; wReload.hidden = true;
    const finish = () => { done = true; wReload.hidden = false; };
    const fallback = () => {
      if (done) return; done = true;
      fillWeather(37.57, 126.98, true)
        .catch(() => { wStatus.textContent = "기온을 불러오지 못했어요. 구간을 고르거나 직접 입력해 주세요."; })
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
  // 뺀 옷 개수
  const ex = document.querySelectorAll("input[name=exclude]"), exCount = document.getElementById("ex-count");
  ex.forEach((b) => b.addEventListener("change", () => {
    const n = [...ex].filter((x) => x.checked).length; exCount.textContent = n ? n + "벌 뺌" : "";
  }));
</script>
{% endblock %}
"""

RECOMMEND_RESULT_HTML = """{% extends "app_base.html" %}
{% block title %}추천 코디{% endblock %}
{% block page_head %}
<style>
  .res { max-width: 1040px; margin: 0 auto; }
  .head { display: flex; justify-content: space-between; align-items: flex-end; gap: 12px; flex-wrap: wrap; }
  .conds { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 6px; }
  .conds span { padding: 4px 10px; border-radius: 999px; background: #ece9e6; font-size: .8125rem; }
  .again { height: 40px; padding: 0 14px; font-size: .9375rem; }
  .outfits { margin-top: 24px; display: grid; gap: 16px; }
  .outfit { display: flex; flex-direction: column; background: #fff; border: 1px solid var(--line); border-radius: 16px; padding: 16px; }
  .outfit h2 { margin: 0; font-size: 1rem; display: flex; align-items: center; gap: 8px; }
  .outfit h2 .num { display: grid; place-items: center; width: 24px; height: 24px; border-radius: 50%;
                    background: var(--ink); color: #fff; font-size: .8125rem; }
  .tags { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; }
  .tags span { padding: 2px 8px; border-radius: 999px; background: #f5f5f4; font-size: .75rem; color: var(--ink-soft); }
  .pieces { margin-top: 14px; display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
  .piece { display: block; color: inherit; text-decoration: none; }
  .pthumb { position: relative; display: grid; place-items: center; aspect-ratio: 1; border-radius: 12px; background: #f5f5f4; overflow: hidden; }
  .pthumb img { width: 100%; height: 100%; object-fit: cover; }
  .pthumb svg { width: 62%; height: 62%; }
  .piece:hover .pthumb { box-shadow: inset 0 0 0 2px var(--line); }
  .piece small { display: block; margin-top: 6px; font-size: .75rem; color: var(--muted); }
  .piece b { display: block; font-size: .875rem; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .piece.none .pthumb { border: 1.5px dashed #d6d3d1; background: none; color: var(--muted); font-size: .8125rem; }
  .reason { margin: 14px 0 0; font-size: .9375rem; line-height: 1.6; flex: 1; }
  .fb { margin-top: 14px; display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .fb button { height: 40px; border: 1px solid var(--line); border-radius: 10px; background: #fff; font: inherit;
               font-size: .875rem; cursor: pointer; display: inline-flex; align-items: center; justify-content: center; gap: 6px; }
  .fb button:hover { border-color: var(--ink-soft); }
  .fb button svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
  .fb button[aria-pressed="true"].up { background: var(--ink); color: #fff; border-color: var(--ink); }
  .fb button[aria-pressed="true"].down { background: #f5f5f4; color: var(--ink); border-color: var(--ink-soft); }
  .fb-msg { margin: 8px 0 0; font-size: .8125rem; color: var(--ink-soft); min-height: 1.2em; }
  .empty { margin-top: 24px; padding: 40px 24px; border: 1px dashed var(--line); border-radius: 16px; background: #fff; text-align: center; }
  .empty h2 { margin: 0; font-size: 1.125rem; }
  .empty p { margin: 8px 0 20px; }
  @media (min-width: 900px) {
    .outfits { grid-template-columns: repeat(3, 1fr); }
  }
</style>
{% endblock %}
{% block content %}
{% from "macros.html" import garment_art %}
{% set band_name = {"freezing": "한파", "cold": "추움", "mild": "선선", "hot": "더움"} %}
<div class="res">
  <div class="head">
    <div>
      <h1>추천 코디</h1>
      <div class="conds" aria-label="고른 조건">
        <span>{{ band_name[band] }} · {{ "%g" | format(temperature) }}°C</span>
        {% if form.occasion %}<span>{{ form.occasion | ko("occasion") }}</span>{% endif %}
        {% for s in form.styles %}<span>{{ s | ko("style") }}</span>{% endfor %}
        {% if form.exclude %}<span>{{ form.exclude | length }}벌 뺌</span>{% endif %}
      </div>
    </div>
    <a class="btn btn-secondary again" href="/recommend">조건 바꾸기</a>
  </div>

  {% if result.outfits %}
  <div class="outfits">
    {% for o in result.outfits %}
    <article class="outfit" aria-labelledby="o{{ loop.index }}">
      <h2 id="o{{ loop.index }}"><span class="num">{{ loop.index }}</span>코디 {{ loop.index }}</h2>
      {% if o.style_tags %}<div class="tags">{% for s in o.style_tags %}<span>{{ s | ko("style") }}</span>{% endfor %}</div>{% endif %}
      <div class="pieces">
        {% for slot, slot_name in SLOTS %}
        {% set gid = o["items"][slot] %}
        {% if gid and by_id.get(gid) %}{% set g = by_id[gid] %}
        <a class="piece" href="/garments/{{ gid }}">
          <span class="pthumb">{{ garment_art(g) }}</span>
          <small>{{ slot_name }}</small><b>{{ garment_name(g) }}</b>
        </a>
        {% elif gid %}
        <div class="piece none"><span class="pthumb">찾을 수 없음</span><small>{{ slot_name }}</small><b>-</b></div>
        {% else %}
        <div class="piece none"><span class="pthumb">없음</span><small>{{ slot_name }}</small><b>{{ "필요 없어요" if slot == "outerwear" else "-" }}</b></div>
        {% endif %}
        {% endfor %}
      </div>
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
  {% if result.outfits | length < 3 %}
  <p class="soft" style="margin-top:16px;font-size:.875rem">입을 수 있는 옷이 더 많으면 더 다양한 코디를 볼 수 있어요.</p>
  {% endif %}
  {% else %}
  <section class="empty">
    <h2>조건에 맞는 코디를 찾지 못했어요</h2>
    {% if missing %}
    <p class="soft">입을 수 있는 {{ with_iga(missing | map("ko", "category") | list) }} 없어요. 옷을 등록하거나, 뺀 옷·세탁 중인 옷을 확인해 보세요.</p>
    <a class="btn btn-primary" href="/garments/new">옷 등록하기</a>
    {% else %}
    <p class="soft">기온이나 스타일 조건을 바꾸거나, 뺀 옷을 줄여 보세요.</p>
    <a class="btn btn-secondary" href="/recommend">조건 바꾸기</a>
    {% endif %}
  </section>
  {% endif %}
</div>
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
        msg.textContent = btn.value === "1" ? "좋아요를 남겼어요. 다음 추천에 반영돼요." : "알려 주셔서 고마워요. 다음엔 다르게 골라 볼게요.";
      } catch {
        msg.textContent = "의견을 보내지 못했어요. 잠시 후 다시 눌러 주세요.";
      }
    }));
  });
</script>
{% endblock %}
"""

register_templates(
    {
        "recommend_form.html": RECOMMEND_FORM_HTML,
        "recommend_result.html": RECOMMEND_RESULT_HTML,
    },
    TEMP_BANDS=TEMP_BANDS,
    MAX_PREF_STYLES=MAX_PREF_STYLES,
    SLOTS=SLOTS,
    with_iga=with_iga,
)
