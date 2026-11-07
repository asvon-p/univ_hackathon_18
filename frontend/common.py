"""공통 화면 틀 — 모든 브랜치가 함께 씀 (base·메뉴·매크로·한글 표시·render).

브랜치별 화면 파일은 여기의 render()와 register_templates()를 사용합니다.
이 파일을 바꿀 때는 D의 다른 브랜치 화면도 같이 확인하세요.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import DictLoader, Environment, select_autoescape

from .connect import USE_MOCK, ApiError
from .connect import auth_profile as auth
from .connect import garment_storage as closet

TOKEN_COOKIE = "fitjang_token"
EMAIL_COOKIE = "fitjang_email"

# 공통 단어(shared/contracts.py, DECISIONS §3) → 화면 한글 표시. 목록에 없는 값은 그대로 보여줌
LABELS: dict[str, dict] = {
    "category": {
        "top": "상의",
        "bottom": "하의",
        "outerwear": "아우터",
        "shoes": "신발",
    },
    "color": {
        "black": "블랙",
        "white": "화이트",
        "gray": "그레이",
        "ivory": "아이보리",
        "beige": "베이지",
        "brown": "브라운",
        "camel": "카멜",
        "khaki": "카키",
        "navy": "네이비",
        "blue": "블루",
        "sky_blue": "스카이블루",
        "denim": "데님",
        "green": "그린",
        "olive": "올리브",
        "red": "레드",
        "burgundy": "버건디",
        "pink": "핑크",
        "purple": "퍼플",
        "yellow": "옐로",
        "orange": "오렌지",
        "multi": "멀티",
        "unknown": "색상 미정",
    },
    "pattern": {
        "solid": "무지",
        "stripe": "스트라이프",
        "check": "체크",
        "dot": "도트",
        "floral": "플라워",
        "graphic": "그래픽",
        "other": "기타 패턴",
        "unknown": "패턴 미정",
    },
    "style": {
        "casual": "캐주얼",
        "minimal": "미니멀",
        "street": "스트릿",
        "formal": "포멀",
        "dandy": "댄디",
        "sporty": "스포티",
        "vintage": "빈티지",
        "feminine": "페미닌",
        "amekaji": "아메카지",
    },
    "occasion": {
        "daily": "일상",
        "work": "출근",
        "date": "데이트",
        "interview": "면접",
        "exercise": "운동",
        "travel": "여행",
    },
    "status": {
        "available": "입을 수 있음",
        "laundry": "세탁 중",
        "inactive": "추천 제외",  # inactive의 뜻은 문서에 없음 — 실제 동작(추천에서 빠짐)대로 표시
    },
    "thermal_level": {
        1: "매우 얇음",
        2: "얇음",
        3: "보통",
        4: "두꺼움",
        5: "매우 두꺼움",
    },
    "subcategory": {
        "t_shirt": "티셔츠",
        "shirt": "셔츠",
        "blouse": "블라우스",
        "sweater": "니트",
        "knit": "니트",
        "sweatshirt": "맨투맨",
        "hoodie": "후드티",
        "cardigan": "가디건",
        "vest": "조끼",
        "jeans": "청바지",
        "slacks": "슬랙스",
        "pants": "바지",
        "shorts": "반바지",
        "skirt": "치마",
        "jogger": "조거 팬츠",
        "coat": "코트",
        "jacket": "재킷",
        "padding": "패딩",
        "jumper": "점퍼",
        "blazer": "블레이저",
        "sneakers": "스니커즈",
        "loafers": "로퍼",
        "boots": "부츠",
        "sandals": "샌들",
        "heels": "힐",
        "dress_shoes": "구두",
    },
}
COLOR_HEX = {
    "black": "#1f1f1f",
    "white": "#fafafa",
    "gray": "#9ca3af",
    "ivory": "#f4efe1",
    "beige": "#d9c5a3",
    "brown": "#7b5236",
    "camel": "#b07d4f",
    "khaki": "#8b8a5c",
    "navy": "#1f2a44",
    "blue": "#2f5fb3",
    "sky_blue": "#8ec5ea",
    "denim": "#4a6b8a",
    "green": "#2f7d4f",
    "olive": "#6b6b2f",
    "red": "#c0392b",
    "burgundy": "#7a1f2b",
    "pink": "#f2a7b8",
    "purple": "#7a4fa0",
    "yellow": "#f2c94c",
    "orange": "#e67e22",
}
CATEGORY_ORDER = ["top", "bottom", "outerwear", "shoes"]


def ko(value, kind: str) -> str:
    """영어 단어 → 한글 표시. 예: {{ g.color | ko("color") }}"""
    if value is None:
        return "-"
    key = value.replace("-", "_") if isinstance(value, str) else value
    return LABELS.get(kind, {}).get(key, str(value))


def color_hex(color: str | None) -> str:
    return COLOR_HEX.get(color or "", "#d4d4d4")


def garment_name(g) -> str:
    """화면에 보일 옷 이름: 사용자가 붙인 이름 → 세부 종류(니트) → 종류(상의)."""
    if getattr(g, "name", None):
        return g.name
    if g.subcategory:
        return ko(g.subcategory, "subcategory")
    return ko(g.category, "category")


# =============================================================================
# 로그인 상태·이동 도우미
# =============================================================================
def current_user(request: Request) -> str | None:
    """로그인했으면 이메일, 아니면 None. 토큰 만료는 쿠키 max_age(=JWT 만료)로 처리."""
    if request.cookies.get(TOKEN_COOKIE):
        return request.cookies.get(EMAIL_COOKIE) or ""
    return None


def safe_next(next_url: str | None) -> str:
    """로그인 후 돌아갈 주소. 다른 사이트로 튕기지 않게 내부 경로만 허용."""
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return "/"


def go_login(request: Request) -> RedirectResponse:
    return RedirectResponse(f"/login?next={request.url.path}", status_code=303)


def logged_in(redirect_to: str, token: auth.Token, email: str) -> RedirectResponse:
    res = RedirectResponse(redirect_to, status_code=303)
    cookie = {"max_age": token.expires_in, "httponly": True, "samesite": "lax"}
    res.set_cookie(TOKEN_COOKIE, token.access_token, **cookie)
    res.set_cookie(EMAIL_COOKIE, email, **cookie)
    return res


def logged_out() -> RedirectResponse:
    res = RedirectResponse("/login", status_code=303)
    res.delete_cookie(TOKEN_COOKIE)
    res.delete_cookie(EMAIL_COOKIE)
    return res


def http_status(e: ApiError) -> int:
    return e.status if e.status >= 400 else 503


# =============================================================================
# 공통 템플릿 (HTML·CSS·JS)
# =============================================================================
BASE_HTML = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#171717">
  <title>{% block title %}핏장{% endblock %} · 핏장</title>
  <link rel="icon" href="data:image/svg+xml,{{ LOGO_SVG | urlencode }}">
  <style>
    :root {
      --ink: #171717; --ink-soft: #525252; --muted: #a3a3a3; --paper: #fafafa; --line: #e5e5e5;
      --danger: #b42318; --danger-soft: #fef3f2;
      --ok: #067647; --ok-soft: #ecfdf3;
      --radius: 8px;
      font-family: "Pretendard", "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR", system-ui, sans-serif;
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; margin: 0; }
    body { background: var(--paper); color: var(--ink); -webkit-font-smoothing: antialiased; }
    a { color: inherit; }
    h1 { font-size: 1.5rem; margin: 0; }
    .soft { color: var(--ink-soft); }

    /* 로고 */
    .brand { display: flex; align-items: center; gap: 10px; font-size: 1.25rem; font-weight: 700; }
    .brand-mark { display: grid; place-items: center; width: 36px; height: 36px; border-radius: var(--radius);
                  background: var(--ink); color: #fff; }
    .brand-mark svg { width: 20px; height: 20px; }
    .brand.inverted .brand-mark { background: #fff; color: var(--ink); }
    .brand.inverted { color: #fff; }

    /* 입력칸 */
    form.stack { display: flex; flex-direction: column; gap: 20px; }
    .field { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
    .field label, .field legend { font-size: .875rem; font-weight: 500; }
    .input-box { display: flex; align-items: center; background: #fff; border: 1px solid var(--line);
                 border-radius: var(--radius); transition: border-color .15s, box-shadow .15s; }
    .input-box:focus-within { border-color: var(--ink); box-shadow: 0 0 0 3px rgb(23 23 23 / .1); }
    .input-box.invalid { border-color: var(--danger); }
    .input-box.invalid:focus-within { box-shadow: 0 0 0 3px rgb(180 35 24 / .15); }
    .input-box input { flex: 1; min-width: 0; height: 48px; padding: 0 16px; border: 0; background: transparent;
                       font: inherit; font-size: 1rem; color: var(--ink); border-radius: var(--radius); }
    .input-box input:focus { outline: none; }
    .input-box input::placeholder { color: var(--muted); }
    .suffix { padding-right: 16px; color: var(--ink-soft); font-size: .9375rem; }
    .field-error { margin: 0; font-size: .875rem; color: var(--danger); }
    .hint { margin: 0; font-size: .875rem; color: var(--ink-soft); }

    /* 비밀번호 보기 버튼 (눈 아이콘) */
    .toggle { display: grid; place-items: center; width: 40px; height: 40px; margin-right: 4px; padding: 0;
              border: 0; border-radius: 8px; background: none; color: var(--muted); cursor: pointer; }
    .toggle:hover { background: #f5f5f5; color: var(--ink); }
    .toggle svg { width: 20px; height: 20px; fill: none; stroke: currentColor; stroke-width: 2;
                  stroke-linecap: round; stroke-linejoin: round; }
    .toggle .icon-eye { display: none; }
    .toggle[aria-pressed="true"] .icon-eye { display: block; }
    .toggle[aria-pressed="true"] .icon-eye-off { display: none; }

    /* 선택 버튼 (성별 등) */
    fieldset { margin: 0; padding: 0; border: 0; min-width: 0; }
    .chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 6px; }
    .chip input { position: absolute; opacity: 0; pointer-events: none; }
    .chip span { display: inline-flex; align-items: center; height: 40px; padding: 0 16px; border: 1px solid var(--line);
                 border-radius: 6px; background: #fff; font-size: .9375rem; cursor: pointer; transition: all .15s; }
    .chip span:hover { border-color: var(--ink-soft); }
    .chip input:checked + span { background: var(--ink); border-color: var(--ink); color: #fff; }
    .chip input:focus-visible + span { outline: 2px solid var(--ink); outline-offset: 2px; }

    /* 버튼 */
    .btn { display: inline-flex; align-items: center; justify-content: center; gap: 8px; height: 48px;
           padding: 0 20px; border-radius: var(--radius); font: inherit; font-weight: 600; font-size: 1rem;
           cursor: pointer; border: 1px solid transparent; transition: background .15s; text-decoration: none; }
    .btn-primary { background: var(--ink); color: #fff; }
    .btn-primary:hover { background: #262626; }
    .btn-secondary { background: #fff; color: var(--ink); border-color: var(--line); }
    .btn-secondary:hover { background: #f5f5f5; }
    .btn-ghost { background: none; color: var(--ink-soft); }
    .btn-ghost:hover { color: var(--ink); background: #f5f5f5; }
    .btn:focus-visible, .toggle:focus-visible, .link-btn:focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; }
    .btn[disabled] { background: var(--muted); color: #fff; cursor: not-allowed; }
    .spinner { width: 16px; height: 16px; border: 2px solid currentColor; border-right-color: transparent;
               border-radius: 50%; animation: spin .7s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }
    .link-btn { padding: 0; border: 0; background: none; font: inherit; font-weight: 600;
                text-decoration: underline; text-underline-offset: 4px; cursor: pointer; color: var(--ink); }

    /* 사진 없는 옷: 그림 대신 색 동그라미만 */
    .noimg { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px;
             width: 100%; height: 100%; container-type: inline-size; }
    .noimg i { width: min(28%, 44px); aspect-ratio: 1; border-radius: 50%; border: 1px solid rgb(0 0 0 / .12); }
    .noimg .cap { font-size: .75rem; color: var(--muted); }
    @container (max-width: 140px) { .noimg .cap { display: none; } }
    .alert { padding: 12px 16px; border-radius: var(--radius); background: var(--danger-soft);
             color: var(--danger); font-size: .875rem; }
    .notice { padding: 12px 16px; border-radius: var(--radius); background: var(--ok-soft);
              color: var(--ok); font-size: .875rem; }

    /* 단순 화면 */
    .simple { max-width: 384px; margin: 0 auto; min-height: 100%; padding: 40px 16px;
              display: flex; flex-direction: column; justify-content: center; }
  </style>
  {% block head %}{% endblock %}
</head>
<body>
{% block body %}{% endblock %}
<script>
  // 비밀번호 보기/숨기기 (data-target = 입력칸 id)
  document.querySelectorAll(".toggle[data-target]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const input = document.getElementById(btn.dataset.target);
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      btn.setAttribute("aria-label", show ? "비밀번호 숨기기" : "비밀번호 보기");
      btn.setAttribute("aria-pressed", String(show));
    });
  });
  // 제출하면 버튼을 잠그고 로딩 표시 (두 번 누르기 방지)
  document.querySelectorAll("form[data-loading]").forEach((form) => {
    form.addEventListener("submit", () => {
      const btn = form.querySelector("button[type=submit]");
      btn.disabled = true;
      btn.setAttribute("aria-busy", "true");
      btn.innerHTML = '<span class="spinner" aria-hidden="true"></span>' + form.dataset.loading;
    });
  });
</script>
{% block script %}{% endblock %}
</body>
</html>
"""

MACROS_HTML = """{% macro brand(inverted=False) -%}
<div class="brand{% if inverted %} inverted{% endif %}">
  <span class="brand-mark" aria-hidden="true">{{ LOGO_PATH_SVG | safe }}</span>
  <span>핏장</span>
</div>
{%- endmacro %}

{# 입력칸 하나. password=True면 눈 아이콘 버튼이 붙음 #}
{% macro field(name, label, value="", error=None, type="text", placeholder="", autocomplete="off",
               inputmode=None, suffix=None, hint=None, password=False, autofocus=False,
               maxlength=None, suggestions=None) -%}
<div class="field">
  <label for="{{ name }}">{{ label }}</label>
  <div class="input-box{% if error %} invalid{% endif %}">
    <input id="{{ name }}" name="{{ name }}" type="{{ 'password' if password else type }}"
           {% if not password %}value="{{ value }}"{% endif %} placeholder="{{ placeholder }}"
           autocomplete="{{ autocomplete }}" {% if inputmode %}inputmode="{{ inputmode }}"{% endif %}
           {% if error %}aria-invalid="true" aria-describedby="{{ name }}-error"{% elif hint %}aria-describedby="{{ name }}-hint"{% endif %}
           {% if maxlength %}maxlength="{{ maxlength }}"{% endif %}
           {% if suggestions %}list="{{ name }}-list"{% endif %}
           {% if autofocus %}autofocus{% endif %}>
    {% if suggestions %}<datalist id="{{ name }}-list">{% for s in suggestions %}<option value="{{ s }}">{% endfor %}</datalist>{% endif %}
    {% if suffix %}<span class="suffix">{{ suffix }}</span>{% endif %}
    {% if password %}
    <button type="button" class="toggle" data-target="{{ name }}" aria-label="비밀번호 보기" aria-pressed="false">
      <svg class="icon-eye-off" viewBox="0 0 24 24" aria-hidden="true"><path d="M10.6 5.1A10.5 10.5 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-2.2 3.2"/><path d="M6.6 6.6C3.8 8.4 2 12 2 12s3.5 7 10 7a9.9 9.9 0 0 0 5.4-1.6"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/><path d="m2 2 20 20"/></svg>
      <svg class="icon-eye" viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>
    </button>
    {% endif %}
  </div>
  {% if error %}<p class="field-error" id="{{ name }}-error">{{ error }}</p>
  {% elif hint %}<p class="hint" id="{{ name }}-hint">{{ hint }}</p>{% endif %}
</div>
{%- endmacro %}

{# 옷 사진: 있으면 사진, 없으면 옷 색 동그라미만 (2026-11-08: 그림 옷 아이콘은 AI 티가 나서 없앰) #}
{% macro garment_art(g) -%}
{% set url = image_url(g) %}
{% if url %}<img src="{{ url }}" alt="" loading="lazy">{% else %}
<span class="noimg" aria-hidden="true"><i style="background:{{ 'conic-gradient(#c0392b, #f2c94c, #2f5fb3, #c0392b)' if g.color == 'multi' else color_hex(g.color) }}"></i><span class="cap">사진 없음</span></span>{% endif %}
{%- endmacro %}"""

APP_BASE_HTML = """{% extends "base.html" %}
{% from "macros.html" import brand %}
{% block head %}
<style>
  .topbar { position: sticky; top: 0; z-index: 10; background: rgb(250 250 250 / .92);
            backdrop-filter: blur(8px); border-bottom: 1px solid var(--line); }
  .topbar-inner { max-width: 1120px; margin: 0 auto; height: 64px; padding: 0 16px;
                  display: flex; align-items: center; gap: 24px; }
  .topbar-inner > a { text-decoration: none; }
  .topnav { display: none; gap: 4px; margin-left: 8px; }
  .topnav a { padding: 8px 14px; border-radius: 6px; text-decoration: none; color: var(--ink-soft); font-weight: 500; }
  .topnav a:hover { color: var(--ink); background: #f5f5f5; }
  .topnav a[aria-current="page"] { color: var(--ink); background: #eeeeee; font-weight: 600; }
  .logout { margin-left: auto; }
  .logout .btn { height: 36px; padding: 0 12px; font-size: .875rem; font-weight: 500; }
  .content { max-width: 1120px; margin: 0 auto; padding: 24px 16px 104px; }
  .content.bare { max-width: 480px; padding-top: 40px; padding-bottom: 40px; }
  .tabbar { position: fixed; inset: auto 0 0 0; z-index: 10; display: grid; grid-template-columns: repeat(4, 1fr);
            height: calc(64px + env(safe-area-inset-bottom)); padding-bottom: env(safe-area-inset-bottom);
            background: #fff; border-top: 1px solid var(--line); }
  .tabbar a { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px;
              text-decoration: none; color: var(--muted); font-size: .75rem; font-weight: 500; }
  .tabbar a[aria-current="page"] { color: var(--ink); font-weight: 600; }
  .tabbar svg { width: 24px; height: 24px; fill: none; stroke: currentColor; stroke-width: 1.8;
                stroke-linecap: round; stroke-linejoin: round; }
  @media (min-width: 768px) {
    .topnav { display: flex; }
    .tabbar { display: none; }
    .content { padding: 32px 24px 64px; }
    .topbar-inner { padding: 0 24px; }
  }
</style>
{% block page_head %}{% endblock %}
{% endblock %}
{% block body %}
{% set menu = [("home", "/", "홈"), ("wardrobe", "/wardrobe", "옷장"), ("recommend", "/recommend", "코디 추천"), ("profile", "/profile", "프로필")] %}
{% if not bare %}
<header class="topbar">
  <div class="topbar-inner">
    <a href="/" aria-label="핏장 홈">{{ brand() }}</a>
    <nav class="topnav" aria-label="주 메뉴">
      {% for key, href, label in menu %}
      <a href="{{ href }}" {% if active == key %}aria-current="page"{% endif %}>{{ label }}</a>
      {% endfor %}
    </nav>
    <form class="logout" method="post" action="/logout">
      <button class="btn btn-ghost">로그아웃</button>
    </form>
  </div>
</header>
{% endif %}
<main class="content{% if bare %} bare{% endif %}">
  {% if bare %}{{ brand() }}{% endif %}
  {% block content %}{% endblock %}
</main>
{% if not bare %}
<nav class="tabbar" aria-label="주 메뉴">
  {% for key, href, label in menu %}
  <a href="{{ href }}" {% if active == key %}aria-current="page"{% endif %}>
    {% if key == "home" %}<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.5 10.5 12 3.5l8.5 7"/><path d="M5.5 9v11h13V9"/></svg>
    {% elif key == "wardrobe" %}<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 5a2 2 0 1 1 2 2v1.5"/><path d="M12 8.5 3.4 15.2a1 1 0 0 0 .6 1.8h16a1 1 0 0 0 .6-1.8z"/></svg>
    {% elif key == "recommend" %}<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3.5 4 6 2.5 10.5 5.5 12V20.5h13V12l3-1.5L20 6l-5-2.5c-.5 1.5-1.5 2.3-3 2.3s-2.5-.8-3-2.3Z"/></svg>
    {% else %}<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-7 8-7s8 3 8 7"/></svg>{% endif %}
    {{ label }}
  </a>
  {% endfor %}
</nav>
{% endif %}
{% endblock %}
"""

PLACEHOLDER_HTML = """{% extends "app_base.html" %}
{% block title %}{{ title }}{% endblock %}
{% block content %}
<h1>{{ title }}</h1>
<p class="soft">{{ message }}</p>
<a href="{{ back }}" style="font-weight:600;text-underline-offset:4px">돌아가기</a>
{% endblock %}
"""

LOGO_PATH_SVG = (
    '<svg viewBox="0 0 24 24" fill="currentColor">'
    '<path d="M8 3l4 2.4L16 3l4 3.2-2.4 3.2-1.6-1.2V21H8V8.2L6.4 9.4 4 6.2z"/></svg>'
)
LOGO_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#171717"/>'
    '<path d="M22 14l10 6 10-6 10 8-6 8-4-3v23H22V27l-4 3-6-8z" fill="#fafafa"/></svg>'
)

_TEMPLATES: dict[str, str] = {
    "base.html": BASE_HTML,
    "macros.html": MACROS_HTML,
    "app_base.html": APP_BASE_HTML,
    "placeholder.html": PLACEHOLDER_HTML,
}
env = Environment(
    loader=DictLoader(_TEMPLATES), autoescape=select_autoescape(default=True)
)
env.globals.update(
    LOGO_SVG=LOGO_SVG,
    LOGO_PATH_SVG=LOGO_PATH_SVG,
    CATEGORY_ORDER=CATEGORY_ORDER,
    LABELS=LABELS,
    color_hex=color_hex,
    garment_name=garment_name,
    image_url=closet.image_url,
    USE_MOCK=USE_MOCK,
)
env.filters["ko"] = ko
templates = Jinja2Templates(env=env)


def register_templates(new: dict[str, str], **globals_) -> None:
    """브랜치별 화면 파일이 자기 템플릿·전역값을 등록."""
    clash = set(new) & set(_TEMPLATES)
    assert not clash, f"템플릿 이름이 겹쳐요: {clash}"
    _TEMPLATES.update(new)
    env.globals.update(globals_)


def render(request: Request, name: str, status: int = 200, **context) -> HTMLResponse:
    context.setdefault("errors", {})
    context.setdefault("email", "")
    context.setdefault("server_error", None)
    context.setdefault("active", None)
    context.setdefault("bare", False)
    return templates.TemplateResponse(request, name, context, status_code=status)
