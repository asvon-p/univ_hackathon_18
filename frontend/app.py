"""팀원 D — 프론트엔드 화면 서버 (FastAPI + Jinja2).

화면(HTML 템플릿·CSS)과 화면 이동은 모두 이 파일 하나에 있습니다.
백엔드(C) 호출은 api_client.py 에서만 합니다.

실행 (저장소 루트에서):
    uvicorn frontend.app:app --reload --port 3000
    → http://localhost:3000
"""

from __future__ import annotations

import re

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import DictLoader, Environment, select_autoescape

try:  # uvicorn frontend.app:app (저장소 루트에서 실행)
    from . import api_client
except ImportError:  # uvicorn app:app (frontend 폴더에서 실행)
    import api_client  # type: ignore[no-redef]

TOKEN_COOKIE = "fitjang_token"
EMAIL_COOKIE = "fitjang_email"
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
GENDERS = [("", "선택 안 함"), ("female", "여성"), ("male", "남성"), ("other", "기타")]

app = FastAPI(title="Fitjang Frontend", docs_url=None, redoc_url=None, openapi_url=None)


# =============================================================================
# 공통 도우미
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


def logged_in(redirect_to: str, token: api_client.Token, email: str) -> RedirectResponse:
    res = RedirectResponse(redirect_to, status_code=303)
    cookie = dict(max_age=token.expires_in, httponly=True, samesite="lax")
    res.set_cookie(TOKEN_COOKIE, token.access_token, **cookie)
    res.set_cookie(EMAIL_COOKIE, email, **cookie)
    return res


def logged_out() -> RedirectResponse:
    res = RedirectResponse("/login", status_code=303)
    res.delete_cookie(TOKEN_COOKIE)
    res.delete_cookie(EMAIL_COOKIE)
    return res


def check_email(email: str) -> str | None:
    if not email:
        return "이메일을 입력해 주세요."
    if not EMAIL_RE.match(email):
        return "이메일 형식이 올바르지 않아요."
    return None


def http_status(e: api_client.ApiError) -> int:
    return e.status if e.status >= 400 else 503


# =============================================================================
# 화면 이동 (라우트)
# =============================================================================
@app.get("/", response_class=HTMLResponse)
async def home(request: Request, saved: int = 0):
    email = current_user(request)
    if email is None:
        return go_login(request)
    # TODO(D): 홈/옷장 화면 — 지금은 로그인 확인용 임시 화면
    return render(request, "home.html", email=email, saved=bool(saved))


# ---- 로그인 -----------------------------------------------------------------
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str | None = None):
    if current_user(request) is not None:
        return RedirectResponse(safe_next(next), status_code=303)
    return render(request, "login.html", next=safe_next(next))


@app.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    next: str = Form("/"),
):
    email = email.strip()
    errors = {k: v for k, v in {
        "email": check_email(email),
        "password": None if password else "비밀번호를 입력해 주세요.",
    }.items() if v}
    if errors:
        return render(request, "login.html", status=422, email=email, errors=errors, next=safe_next(next))

    try:
        token = await api_client.login(email, password)
    except api_client.ApiError as e:
        # 비밀번호 칸은 비우고 서버 메시지만 보여줌
        return render(request, "login.html", status=http_status(e), email=email,
                      server_error=e.message, next=safe_next(next))
    return logged_in(safe_next(next), token, email)


@app.post("/logout")
async def logout():
    return logged_out()


# ---- 회원가입 ---------------------------------------------------------------
@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    if current_user(request) is not None:
        return RedirectResponse("/", status_code=303)
    return render(request, "signup.html")


@app.post("/signup", response_class=HTMLResponse)
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
    elif len(password) < 8 or not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        errors["password"] = "영문과 숫자를 섞어 8자 이상 입력해 주세요."
    if not password_confirm:
        errors["password_confirm"] = "비밀번호를 한 번 더 입력해 주세요."
    elif password and password != password_confirm:
        errors["password_confirm"] = "비밀번호가 서로 달라요."
    if errors:
        return render(request, "signup.html", status=422, email=email, errors=errors)

    try:
        token = await api_client.signup(email, password)
    except api_client.ApiError as e:
        if e.code == "EMAIL_ALREADY_EXISTS":
            return render(request, "signup.html", status=http_status(e), email=email, errors={"email": e.message})
        return render(request, "signup.html", status=http_status(e), email=email, server_error=e.message)
    # 가입하면 바로 로그인 → 프로필 입력(건너뛰기 가능)
    return logged_in("/profile?welcome=1", token, email)


# ---- 프로필 (키·몸무게·나이·성별, 모두 선택) ----------------------------------
@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, welcome: int = 0):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return go_login(request)
    try:
        profile = await api_client.get_profile(token)
    except api_client.ApiError as e:
        if e.status == 401:
            return logged_out()
        return render(request, "profile.html", status=http_status(e), welcome=bool(welcome),
                      form=ProfileForm.empty(), server_error=e.message)
    return render(request, "profile.html", welcome=bool(welcome), form=ProfileForm.from_profile(profile))


@app.post("/profile", response_class=HTMLResponse)
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
    form = ProfileForm(height_cm=height_cm.strip(), weight_kg=weight_kg.strip(), age=age.strip(), gender=gender)
    profile, errors = form.validate()
    if errors:
        return render(request, "profile.html", status=422, welcome=bool(welcome), form=form, errors=errors)
    try:
        await api_client.save_profile(token, profile)
    except api_client.ApiError as e:
        if e.status == 401:
            return logged_out()
        return render(request, "profile.html", status=http_status(e), welcome=bool(welcome), form=form,
                      server_error=e.message)
    return RedirectResponse("/?saved=1", status_code=303)


class ProfileForm:
    """화면 입력값(문자열) ↔ api_client.Profile 변환과 검사."""

    def __init__(self, height_cm: str = "", weight_kg: str = "", age: str = "", gender: str = ""):
        self.height_cm, self.weight_kg, self.age, self.gender = height_cm, weight_kg, age, gender

    @classmethod
    def empty(cls) -> "ProfileForm":
        return cls()

    @classmethod
    def from_profile(cls, p: api_client.Profile) -> "ProfileForm":
        def num(v: float | int | None) -> str:
            if v is None:
                return ""
            return str(int(v)) if float(v).is_integer() else str(v)

        return cls(num(p.height_cm), num(p.weight_kg), num(p.age), p.gender or "")

    def validate(self) -> tuple[api_client.Profile, dict[str, str]]:
        errors: dict[str, str] = {}

        def number(name: str, raw: str, lo: float, hi: float, unit: str, integer: bool = False):
            if not raw:
                return None
            try:
                value = int(raw) if integer else round(float(raw), 1)
            except ValueError:
                errors[name] = "정수로 입력해 주세요." if integer else "숫자로 입력해 주세요."
                return None
            if not lo <= value <= hi:
                errors[name] = f"{lo:g}~{hi:g}{unit} 사이로 입력해 주세요."
                return None
            return value

        profile = api_client.Profile(
            height_cm=number("height_cm", self.height_cm, 100, 230, "cm"),
            weight_kg=number("weight_kg", self.weight_kg, 20, 200, "kg"),
            age=number("age", self.age, 10, 100, "세", integer=True),
            gender=self.gender or None,
        )
        if self.gender not in {g for g, _ in GENDERS}:
            errors["gender"] = "성별을 다시 선택해 주세요."
        return profile, errors


# =============================================================================
# 템플릿 (HTML·CSS·JS) — 파일을 나누지 않고 여기에 모아 둡니다
# =============================================================================
BASE_HTML = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#1c1917">
  <title>{% block title %}핏장{% endblock %} — 내 옷장 코디</title>
  <link rel="icon" href="data:image/svg+xml,{{ LOGO_SVG | urlencode }}">
  <style>
    :root {
      --ink: #1c1917; --ink-soft: #57534e; --muted: #a8a29e; --paper: #fafaf9; --line: #e7e5e4;
      --camel: #b07d4f; --camel-soft: #f3e9df; --danger: #b42318; --danger-soft: #fef3f2;
      --ok: #067647; --ok-soft: #ecfdf3;
      --radius: 12px;
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
    .input-box:focus-within { border-color: var(--ink); box-shadow: 0 0 0 3px rgb(28 25 23 / .1); }
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
    .toggle:hover { background: #f5f5f4; color: var(--ink); }
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
                 border-radius: 999px; background: #fff; font-size: .9375rem; cursor: pointer; transition: all .15s; }
    .chip span:hover { border-color: var(--ink-soft); }
    .chip input:checked + span { background: var(--ink); border-color: var(--ink); color: #fff; }
    .chip input:focus-visible + span { outline: 2px solid var(--ink); outline-offset: 2px; }

    /* 버튼 */
    .btn { display: inline-flex; align-items: center; justify-content: center; gap: 8px; height: 48px;
           padding: 0 20px; border-radius: var(--radius); font: inherit; font-weight: 600; font-size: 1rem;
           cursor: pointer; border: 1px solid transparent; transition: background .15s; text-decoration: none; }
    .btn-primary { background: var(--ink); color: #fff; }
    .btn-primary:hover { background: #292524; }
    .btn-secondary { background: #fff; color: var(--ink); border-color: var(--line); }
    .btn-secondary:hover { background: #f5f5f4; }
    .btn-ghost { background: none; color: var(--ink-soft); }
    .btn-ghost:hover { color: var(--ink); background: #f5f5f4; }
    .btn:focus-visible, .toggle:focus-visible, .link-btn:focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; }
    .btn[disabled] { background: var(--muted); color: #fff; cursor: not-allowed; }
    .spinner { width: 16px; height: 16px; border: 2px solid currentColor; border-right-color: transparent;
               border-radius: 50%; animation: spin .7s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }
    .link-btn { padding: 0; border: 0; background: none; font: inherit; font-weight: 600;
                text-decoration: underline; text-underline-offset: 4px; cursor: pointer; color: var(--ink); }

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
               inputmode=None, suffix=None, hint=None, password=False, autofocus=False) -%}
<div class="field">
  <label for="{{ name }}">{{ label }}</label>
  <div class="input-box{% if error %} invalid{% endif %}">
    <input id="{{ name }}" name="{{ name }}" type="{{ 'password' if password else type }}"
           {% if not password %}value="{{ value }}"{% endif %} placeholder="{{ placeholder }}"
           autocomplete="{{ autocomplete }}" {% if inputmode %}inputmode="{{ inputmode }}"{% endif %}
           {% if error %}aria-invalid="true" aria-describedby="{{ name }}-error"{% elif hint %}aria-describedby="{{ name }}-hint"{% endif %}
           {% if autofocus %}autofocus{% endif %}>
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
{%- endmacro %}"""

# 로그인·회원가입 공통: PC에서는 왼쪽 소개 + 오른쪽 폼, 모바일에서는 한 줄
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
  .mock { margin-top: 40px; padding: 16px; border: 1px dashed rgb(176 125 79 / .5); border-radius: var(--radius);
          background: rgb(243 233 223 / .6); font-size: .875rem; }
  .mock strong { color: var(--camel); }
  .mock p { margin: 4px 0 8px; }

  @media (min-width: 640px) { .form-side { padding: 40px 32px; } }
  @media (min-width: 1024px) {
    .auth { display: grid; grid-template-columns: 1.1fr 1fr; }
    .form-side { justify-content: center; }
    .mobile-brand { display: none; }
    .intro { position: relative; overflow: hidden; display: flex; flex-direction: column;
             justify-content: space-between; padding: 48px; background: var(--ink); color: #fff; }
    .intro h2 { margin: 0; font-size: 2.25rem; line-height: 1.25; }
    .intro ul { margin: 32px 0 0; padding: 0; list-style: none; color: #d6d3d1; }
    .intro li { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
    .intro li::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: var(--camel); }
    .intro .copy { position: relative; z-index: 1; max-width: 28rem; }
    .intro small { position: relative; z-index: 1; color: #78716c; }
    .hanger { position: absolute; right: 48px; bottom: 112px; width: 20rem; color: #44403c; pointer-events: none; }
  }
  @media (min-width: 1280px) { .hanger { width: 24rem; } }
</style>
{% endblock %}
{% block body %}
<div class="auth">
  <aside class="intro">
    {{ brand(inverted=True) }}
    <div class="copy">
      <h2>내 옷장 속 옷으로,<br>오늘의 코디</h2>
      <ul>
        <li>사진 한 장으로 옷 등록</li>
        <li>가진 옷으로만 코디 추천</li>
        <li>오늘 날씨에 맞춘 조합</li>
      </ul>
    </div>
    <small>© 2026 Fitjang</small>
    <svg class="hanger" aria-hidden="true" viewBox="0 0 400 240" fill="none" stroke="currentColor"
         stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
      <path d="M176 44c0-13 11-24 24-24s24 11 24 24c0 12-9 18-20 24l-4 4"/>
      <path d="M200 72 30 200c-8 6-4 16 6 16h328c10 0 14-10 6-16L200 72Z"/>
      <path d="M70 216h260" stroke="#b07d4f"/>
    </svg>
  </aside>
  <main class="form-side">
    <div class="form-wrap">
      <div class="mobile-brand">
        {{ brand() }}
        <p class="soft">내 옷장 속 옷으로, 오늘의 코디</p>
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

PROFILE_HTML = """{% extends "base.html" %}
{% from "macros.html" import brand, field %}
{% block title %}프로필{% endblock %}
{% block head %}
<style>
  .page { max-width: 480px; margin: 0 auto; padding: 40px 16px; }
  .page h1 { margin-top: 40px; }
  .page .lead { margin: 8px 0 0; }
  .page form { margin-top: 32px; }
  .row { display: grid; gap: 20px; }
  .actions { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
  @media (min-width: 480px) { .row { grid-template-columns: repeat(3, 1fr); gap: 12px; } }
</style>
{% endblock %}
{% block body %}
<main class="page">
  {{ brand() }}
  {% if welcome %}
  <h1>가입을 환영해요!</h1>
  <p class="lead soft">코디 추천에 참고할 정보예요. 모두 선택이라 비워 둬도 괜찮아요.</p>
  {% else %}
  <h1>프로필</h1>
  <p class="lead soft">코디 추천에 참고할 정보예요. 모두 선택이에요.</p>
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
      <a class="btn btn-ghost" href="/">{{ "건너뛰기" if welcome else "취소" }}</a>
    </div>
  </form>
</main>
{% endblock %}
"""

HOME_HTML = """{% extends "base.html" %}
{% from "macros.html" import brand %}
{% block title %}홈{% endblock %}
{% block body %}
<main class="simple">
  {{ brand() }}
  {% if saved %}<div class="notice" role="status" style="margin-top:32px">프로필을 저장했어요.</div>{% endif %}
  <h1 style="margin-top:40px">로그인했어요</h1>
  <p class="soft">{{ email }} 계정으로 들어왔어요. 옷장 화면은 다음 작업에서 만들어요.</p>
  <a class="btn btn-secondary" href="/profile" style="margin-top:32px">프로필 수정</a>
  <form method="post" action="/logout" style="margin-top:8px">
    <button class="btn btn-ghost" style="width:100%">로그아웃</button>
  </form>
</main>
{% endblock %}
"""

LOGO_PATH_SVG = (
    '<svg viewBox="0 0 24 24" fill="currentColor">'
    '<path d="M8 3l4 2.4L16 3l4 3.2-2.4 3.2-1.6-1.2V21H8V8.2L6.4 9.4 4 6.2z"/></svg>'
)
LOGO_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#1c1917"/>'
    '<path d="M22 14l10 6 10-6 10 8-6 8-4-3v23H22V27l-4 3-6-8z" fill="#fafaf9"/></svg>'
)

env = Environment(
    loader=DictLoader(
        {
            "base.html": BASE_HTML,
            "macros.html": MACROS_HTML,
            "auth_base.html": AUTH_BASE_HTML,
            "login.html": LOGIN_HTML,
            "signup.html": SIGNUP_HTML,
            "profile.html": PROFILE_HTML,
            "home.html": HOME_HTML,
        }
    ),
    autoescape=select_autoescape(default=True),
)
env.globals.update(
    LOGO_SVG=LOGO_SVG,
    LOGO_PATH_SVG=LOGO_PATH_SVG,
    GENDERS=GENDERS,
    USE_MOCK=api_client.USE_MOCK,
    DEMO_EMAIL=api_client.DEMO_EMAIL,
    DEMO_PASSWORD=api_client.DEMO_PASSWORD,
)
templates = Jinja2Templates(env=env)


def render(request: Request, name: str, status: int = 200, **context) -> HTMLResponse:
    context.setdefault("errors", {})
    context.setdefault("email", "")
    context.setdefault("server_error", None)
    return templates.TemplateResponse(request, name, context, status_code=status)
