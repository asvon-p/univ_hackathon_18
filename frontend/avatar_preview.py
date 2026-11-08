"""feat/avatar-2d-preview (D) — 코디를 내 몸에 입혀 보여주기: 착샷(FASHN VTON).

(2026-11-07 시아: 처음엔 그림 마네킹을 코디 카드 왼쪽에 바로 보여줬는데, 어색해서 없앰. 이제 착샷만)

착샷 (2026-11-07): 코디 카드의 "착샷 만들기" → 내 전신 사진에 하의 → 상의 → 아우터를 차례로 입힘
- 프론트 서버가 뒤에서 착샷 서버(connect/tryon.py)에 한 벌씩 맡기고, 결과를 다음 사람 사진으로 넣음
- 화면은 2초마다 진행 상황을 물어봄 (GET /tryon/{id}). 창을 닫거나 새로고침해도 계속 만들고, 끝난 착샷은 기억
- 다 되면 코디 카드 왼쪽에 착샷이 생김. 실패하거나 서버가 없으면 옷 목록만
- 진행 상황·결과는 프론트 서버 메모리에만 (서버를 끄면 사라짐)

사용법 (템플릿):  {% from "tryon.html" import tryon_button, tryon_dialog with context %}
다른 팀원과 연결: connect/tryon.py (착샷 서버), 전신 사진·옷 사진은 connect/auth_profile·garment_storage
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from .common import TOKEN_COOKIE, garment_name, register_templates
from .connect import ApiError
from .connect import auth_profile as auth
from .connect import garment_storage as closet
from .connect import tryon as vton

router = APIRouter()
log = logging.getLogger(__name__)


@router.get("/avatar")
async def avatar_page():
    """예전 '입혀 보기' 주소 → 코디 추천으로 (착샷은 추천 결과 안에 나옴)."""
    return RedirectResponse("/recommend", status_code=303)


# =============================================================================
# 착샷 (FASHN VTON v1.5)
# =============================================================================
ORDER = ["bottom", "top", "outerwear"]  # 입히는 순서
SLOT_NAMES = {"bottom": "하의", "top": "상의", "outerwear": "아우터"}
STEP_SECONDS = 40  # 옷 한 벌 예상 시간 (측정값 약 38~40초)
STEP_TIMEOUT = 10 * 60
POLL_SECONDS = 2
MAX_KEEP = 40  # 메모리에 기억할 착샷 수


@dataclass
class TryOn:
    id: str
    owner: str
    key: tuple
    steps: list[tuple[str, str, str]]  # (slot, garment_id, 이름)
    skipped: list[str] = field(default_factory=list)
    status: str = "queued"  # queued | running | done | error
    step: int = 0
    queue_position: int | None = None
    step_started: float | None = None
    error: str | None = None
    image: bytes | None = None

    def to_json(self) -> dict:
        total = len(self.steps)
        elapsed = time.monotonic() - self.step_started if self.step_started else 0
        if self.status == "done":
            progress = 1.0
        else:
            progress = (self.step + min(elapsed / STEP_SECONDS, 0.95)) / max(total, 1)
        return {
            "id": self.id,
            "status": self.status,
            "step": min(self.step + 1, total),
            "total": total,
            "step_name": SLOT_NAMES[self.steps[min(self.step, total - 1)][0]],
            "names": [f"{SLOT_NAMES[s]} · {n}" for s, _, n in self.steps],
            "queue_position": self.queue_position,
            "progress": round(progress, 3),
            "seconds_left": max(5, int((total - self.step) * STEP_SECONDS - elapsed)),
            "skipped": self.skipped,
            "error": self.error,
            "image": f"/tryon/{self.id}/image" if self.status == "done" else None,
        }


_TRYONS: dict[str, TryOn] = {}
_TASKS: set[asyncio.Task] = set()


def tryon_items(items) -> dict[str, str]:
    """코디 옷 (dict 또는 OutfitItems) → {"top", "bottom", "outerwear"} 문자열."""
    get = items.get if isinstance(items, dict) else lambda k: getattr(items, k, None)
    return {s: get(s) or "" for s in ORDER}


def _owner(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:20]


def _profile_owner(token: str, profile) -> str:
    return getattr(profile, "user_id", None) or _owner(token)


def _key(owner: str, items: dict, photo_version: str | None) -> tuple:
    return (owner, photo_version, *(items.get(s) or "" for s in ORDER))


def _find(key: tuple) -> TryOn | None:
    for t in reversed(_TRYONS.values()):
        if t.key == key and t.status != "error":
            return t
    return None


def tryon_state(request: Request, items: dict, profile) -> dict | None:
    """템플릿용: 이 코디로 이미 만들었거나 만드는 중인 착샷."""
    token = request.cookies.get(TOKEN_COOKIE)
    version = getattr(profile, "body_photo_updated_at", None)
    if not token or not version:
        return None
    t = _find(_key(_profile_owner(token, profile), tryon_items(items), version))
    return t.to_json() if t else None


def _json_error(status: int, message: str, **extra) -> JSONResponse:
    return JSONResponse({"error": message, **extra}, status_code=status)


@router.post("/tryon")
async def tryon_start(
    request: Request,
    top: Annotated[str, Form()] = "",
    bottom: Annotated[str, Form()] = "",
    outerwear: Annotated[str, Form()] = "",
):
    """착샷 만들기 시작 (같은 코디·같은 전신 사진이면 하던 것/만든 것을 돌려줌)."""
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return _json_error(401, "로그인이 필요해요.", login=True)
    if not vton.enabled():
        return _json_error(503, "착샷 서버가 설정되지 않았어요.")
    try:
        profile = await auth.get_profile(token)
    except ApiError as e:
        return _json_error(401 if e.status == 401 else 502, e.message, login=e.status == 401)
    version = profile.body_photo_updated_at
    if not version:
        return _json_error(409, "전신 사진을 먼저 올려 주세요.", need_photo=True)

    items = {"top": top, "bottom": bottom, "outerwear": outerwear}
    key = _key(_profile_owner(token, profile), items, version)
    if t := _find(key):
        return t.to_json()

    steps, skipped = [], []
    for slot in ORDER:
        if not items[slot]:
            continue
        try:
            g = await closet.get_garment(token, items[slot])
        except ApiError:
            continue
        name = garment_name(g)
        if closet.may_have_photo(g):
            steps.append((slot, g.garment_id, name))
        else:
            skipped.append(name)
    if not steps:
        return _json_error(
            422,
            "사진이 있는 옷이 없어서 착샷을 만들 수 없어요. 옷을 사진으로 등록해 주세요.",
        )

    t = TryOn(uuid.uuid4().hex[:12], key[0], key, steps, skipped)
    _TRYONS[t.id] = t
    while len(_TRYONS) > MAX_KEEP:
        _TRYONS.pop(next(iter(_TRYONS)))
    task = asyncio.create_task(_run(t, token))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return t.to_json()


async def _run(t: TryOn, token: str) -> None:
    """한 벌씩 착샷 서버에 맡기고, 결과를 다음 사람 사진으로."""
    try:
        person = await auth.fetch_body_photo(token)
        done_any = False
        for i, (slot, gid, name) in enumerate(t.steps):
            t.step, t.step_started, t.queue_position = i, None, None
            try:
                garment = await closet.fetch_image(token, gid, kinds=("original", "cutout"))
            except ApiError as e:
                if e.status != 404:
                    raise
                t.skipped.append(name)  # 사진이 없는 옷은 빼고 계속
                continue
            job = await vton.submit(person, garment, vton.CATEGORY[slot])
            deadline = time.monotonic() + STEP_TIMEOUT
            while True:
                status = job.get("status")
                if status == "done":
                    break
                if status not in ("queued", "running"):
                    # 착샷 서버는 실패를 "failed"로 알려 줌 (그 밖의 모르는 상태도 실패로)
                    log.warning("착샷 서버 작업 실패 %s: %s", job.get("job_id"), job)
                    raise ApiError(
                        500,
                        f"착샷 서버에서 {SLOT_NAMES[slot]}를 입히다 오류가 났어요. "
                        "잠시 후 다시 시도해 주세요.",
                    )
                if status == "running" and t.step_started is None:
                    t.step_started = time.monotonic()
                t.status = "running" if status == "running" else "queued"
                t.queue_position = job.get("queue_position")
                if time.monotonic() > deadline:
                    raise ApiError(504, "착샷이 너무 오래 걸려요. 잠시 후 다시 시도해 주세요.")
                await asyncio.sleep(POLL_SECONDS)
                job = await vton.get_job(job["job_id"])
            person = (await vton.get_result(job["job_id"]), "image/png")
            done_any = True
        if not done_any:
            raise ApiError(422, "사진이 있는 옷이 없어서 착샷을 만들 수 없어요.")
        t.image, t.step, t.status = person[0], len(t.steps) - 1, "done"
    except ApiError as e:
        t.status, t.error = "error", e.message
    except Exception:
        log.exception("착샷 실패")
        t.status, t.error = (
            "error",
            "착샷을 만들지 못했어요. 잠시 후 다시 시도해 주세요.",
        )


async def _mine(request: Request, tryon_id: str) -> TryOn | None:
    token = request.cookies.get(TOKEN_COOKIE)
    t = _TRYONS.get(tryon_id)
    if not token or not t:
        return None
    try:
        profile = await auth.get_profile(token)
    except ApiError:
        return None
    if t.owner != _profile_owner(token, profile):
        return None
    if profile.body_photo_updated_at != t.key[1]:
        _TRYONS.pop(tryon_id, None)
        return None
    return t


@router.get("/tryon/{tryon_id}")
async def tryon_status(request: Request, tryon_id: str):
    t = await _mine(request, tryon_id)
    if not t:
        return _json_error(404, "착샷을 찾을 수 없어요. 다시 만들어 주세요.")
    return t.to_json()


@router.get("/tryon/{tryon_id}/image")
async def tryon_image(request: Request, tryon_id: str, download: int = 0):
    t = await _mine(request, tryon_id)
    if not t or not t.image:
        return Response(status_code=404)
    headers = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
    if download:
        headers["Content-Disposition"] = 'attachment; filename="fitjang-tryon.png"'
    return Response(t.image, media_type="image/png", headers=headers)


# =============================================================================
# 템플릿: 착샷 버튼·창 (recommendation_ui.py 결과 화면에서 사용)
# =============================================================================
TRYON_HTML = """{# 코디 카드의 착샷 버튼. items: {"top","bottom","outerwear"} → garment_id, n: 코디 번호 #}
{% macro tryon_button(items, n, profile, title="") -%}
{% if TRYON_ON %}
  {% if profile and profile.body_photo_updated_at %}
  {% set its = tryon_items(items) %}
  <button type="button" class="btn btn-secondary tryon-btn" data-tryon="{{ n }}" data-title="{{ title or ('코디 ' ~ n ~ ' 착샷') }}" data-items='{{ its | tojson }}'
          data-state='{{ tryon_state(request, its, profile) | tojson }}'><span class="tb-label">착샷 보기</span></button>
  {% else %}
  <a class="btn btn-secondary tryon-btn" href="/profile/body-photo"><span class="tb-label">전신 사진 올리고 착샷 보기</span></a>
  {% endif %}
{% endif %}
{%- endmacro %}

{% macro tryon_dialog() -%}
{% if TRYON_ON %}
<style>
  .tryon-btn { width: 100%; margin-top: 12px; height: 44px; font-size: .9375rem; }
  .tryon-btn[data-status="queued"], .tryon-btn[data-status="running"] {
       background-image: linear-gradient(90deg, #ededed var(--p, 0%), #fff var(--p, 0%)); }
  .tryon-btn[data-status="error"] { color: var(--danger); }
  .mq .shot { display: block; width: 100%; height: 100%; padding: 0; border: 0; border-radius: 12px; overflow: hidden; cursor: zoom-in; background: none; }
  .mq .shot img { width: 100%; height: 100%; object-fit: cover; object-position: top; display: block; }

  .tdlg { width: min(92vw, 440px); max-height: 94dvh; padding: 0; border: 0; border-radius: 12px; background: #fff; color: var(--ink);
          box-shadow: 0 20px 60px rgb(0 0 0 / .25); }
  .tdlg::backdrop { background: rgb(23 23 23 / .55); }
  .tdlg[open] { display: flex; flex-direction: column; }
  .thead { display: flex; align-items: center; justify-content: space-between; padding: 14px 16px 10px 20px; }
  .thead h2 { margin: 0; font-size: 1.0625rem; }
  .tx { width: 36px; height: 36px; border: 0; border-radius: 50%; background: #f5f5f5; font-size: 1.25rem; line-height: 1; cursor: pointer; color: var(--ink); }
  .tv { position: relative; height: min(58dvh, 540px); aspect-ratio: 2 / 3; max-width: calc(100% - 32px); margin: 0 auto;
        border-radius: 10px; overflow: hidden; background: linear-gradient(#f3f3f3, #ececec); }
  .tv img { width: 100%; height: 100%; object-fit: contain; display: block; background: #fff; }
  .twait, .terr { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center;
                  gap: 12px; padding: 20px; text-align: center; }
  .twait[hidden], .terr[hidden], .tv img[hidden] { display: none; }
  .spin { width: 40px; height: 40px; border: 3px solid rgb(23 23 23 / .12); border-top-color: var(--ink); border-radius: 50%; animation: tspin 1s linear infinite; }
  @keyframes tspin { to { transform: rotate(360deg); } }
  @media (prefers-reduced-motion: reduce) { .spin { animation-duration: 3s; } }
  .tmsg { margin: 0; font-weight: 600; }
  .tbar { width: 80%; height: 6px; border-radius: 3px; background: rgb(23 23 23 / .1); overflow: hidden; }
  .tbar i { display: block; height: 100%; width: 0; background: var(--ink); transition: width .8s ease; }
  .tsteps { margin: 4px 0 0; padding: 0; list-style: none; font-size: .8125rem; color: var(--ink-soft); text-align: left; }
  .tsteps li { display: flex; gap: 6px; align-items: center; padding: 2px 0; }
  .tsteps li::before { content: ""; width: 14px; height: 14px; flex-shrink: 0; border: 1.5px solid #a3a3a3; border-radius: 50%; box-sizing: border-box; }
  .tsteps li.now { color: var(--ink); font-weight: 600; }
  .tsteps li.now::before { border-color: var(--ink); border-top-color: transparent; animation: tspin 1s linear infinite; }
  .tsteps li.ok::before { border: 0; background: var(--ink) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 14 14'%3E%3Cpath d='M3.5 7.2 6 9.6l4.5-5' fill='none' stroke='%23fff' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E") center / contain; }
  .twait small, .tnote { font-size: .75rem; color: var(--ink-soft); }
  .tnote[hidden] { display: none; }
  .terr p { margin: 0; color: var(--danger); font-size: .9375rem; }
  .tnote { margin: 10px 20px 0; text-align: center; }
  .tfoot { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; padding: 12px 16px 16px; }
  .tfoot .btn { height: 44px; }
  .tfoot a[hidden] { display: none; }
  .tfoot:has(a[hidden]) { grid-template-columns: 1fr; }
</style>
<dialog class="tdlg" id="tryon-dlg" aria-labelledby="tryon-title">
  <div class="thead"><h2 id="tryon-title">착샷</h2><button type="button" class="tx" data-close aria-label="닫기">×</button></div>
  <div class="tv">
    <img alt="" hidden>
    <div class="twait">
      <div class="spin" aria-hidden="true"></div>
      <p class="tmsg" aria-live="polite">준비 중…</p>
      <div class="tbar" aria-hidden="true"><i></i></div>
      <ol class="tsteps"></ol>
      <small>닫아도 계속 만들어요.</small>
    </div>
    <div class="terr" hidden><p role="alert"></p><button type="button" class="btn btn-secondary" data-retry>다시 만들기</button></div>
  </div>
  <p class="tnote tskip" hidden></p>
  <div class="tfoot">
    <a class="btn btn-secondary" data-save hidden>사진 저장</a>
    <button type="button" class="btn btn-primary" data-close>닫기</button>
  </div>
</dialog>
<script>
(() => {
  const dlg = document.getElementById("tryon-dlg");
  const $ = (s) => dlg.querySelector(s);
  const img = $(".tv img"), wait = $(".twait"), err = $(".terr"), msg = $(".tmsg"), bar = $(".tbar i");
  const stepsEl = $(".tsteps"), save = $("[data-save]"), skip = $(".tskip");
  const state = new Map();   // 버튼 → 서버가 알려 준 상태
  let current = null;        // 창에 보이는 버튼

  const busy = (s) => s && (s.status === "queued" || s.status === "running");
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  function waitText(s) {
    if (s.status === "queued" && s.queue_position != null) return "착샷 서버에서 차례를 기다리는 중…";
    const left = s.seconds_left >= 60 ? `약 ${Math.ceil(s.seconds_left / 60)}분` : `약 ${s.seconds_left}초`;
    return `${s.step_name} 입히는 중 ${s.step}/${s.total} · ${left} 남음`;
  }

  function paint(btn) {
    const s = state.get(btn);
    const label = btn.querySelector(".tb-label");
    btn.dataset.status = s ? s.status : "";
    if (!s) return;
    if (busy(s)) {
      label.textContent = s.status === "queued" ? "착샷 대기 중…" : `착샷 만드는 중 ${s.step}/${s.total}`;
      btn.style.setProperty("--p", Math.round(s.progress * 100) + "%");
    } else if (s.status === "done") {
      label.textContent = "착샷 크게 보기";
      const mq = btn.closest(".outfit")?.querySelector(".mq");
      if (mq && !mq.querySelector(".shot")) {
        mq.hidden = false; mq.closest(".fit")?.classList.add("with-shot");
        mq.innerHTML = `<button type="button" class="shot" aria-label="${btn.dataset.title} 크게 보기"><img alt="${btn.dataset.title}" src="${s.image}"></button>`;
        mq.querySelector(".shot").addEventListener("click", () => open(btn));
      }
    } else if (s.status === "error") {
      label.textContent = "착샷 다시 만들기";
    }
    if (current === btn) show(s, btn);
  }

  function show(s, btn) {
    $("#tryon-title").textContent = btn.dataset.title;
    const done = s && s.status === "done", failed = !s || s.status === "error";
    wait.hidden = done || failed; err.hidden = !failed; img.hidden = !done;
    save.hidden = !done;
    if (done) {
      if (img.getAttribute("src") !== s.image) img.src = s.image;
      img.alt = btn.dataset.title; save.href = s.image + "?download=1";
    } else if (failed) {
      err.querySelector("p").textContent = (s && s.error) || "착샷을 만들지 못했어요.";
    } else {
      msg.textContent = waitText(s);
      bar.style.width = Math.round(s.progress * 100) + "%";
      stepsEl.innerHTML = s.names.map((n, i) => `<li class="${i + 1 < s.step ? "ok" : i + 1 === s.step ? "now" : ""}"></li>`).join("");
      stepsEl.querySelectorAll("li").forEach((li, i) => li.append(s.names[i]));
    }
    skip.textContent = s && s.skipped && s.skipped.length ? `사진이 없어 뺀 옷: ${s.skipped.join(", ")}` : "";
    skip.hidden = !skip.textContent;
  }

  async function poll(btn) {
    if (btn.dataset.polling) return;
    btn.dataset.polling = "1";
    let misses = 0;
    while (busy(state.get(btn))) {
      await sleep(2000);
      try {
        const res = await fetch(`/tryon/${state.get(btn).id}`, { headers: { Accept: "application/json" } });
        const data = await res.json();
        state.set(btn, res.ok ? data : { status: "error", error: data.error });
        misses = 0;
      } catch {
        if (++misses >= 5) state.set(btn, { status: "error", error: "진행 상황을 확인하지 못했어요. 새로고침해 주세요." });
      }
      paint(btn);
    }
    delete btn.dataset.polling;
  }

  async function start(btn) {
    state.set(btn, { status: "queued", step: 1, total: 1, step_name: "", names: [], progress: 0, seconds_left: 40, skipped: [] });
    paint(btn);
    try {
      const items = JSON.parse(btn.dataset.items);
      const res = await fetch("/tryon", { method: "POST", body: new URLSearchParams(items), headers: { Accept: "application/json" } });
      const data = await res.json();
      if (data.login) return (location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search));
      if (data.need_photo) return (location.href = "/profile/body-photo");
      state.set(btn, res.ok ? data : { status: "error", error: data.error });
    } catch {
      state.set(btn, { status: "error", error: "착샷을 시작하지 못했어요. 잠시 후 다시 시도해 주세요." });
    }
    paint(btn); poll(btn);
  }

  function open(btn) {
    current = btn; show(state.get(btn), btn);
    if (!dlg.open) dlg.showModal();
  }

  document.querySelectorAll("button.tryon-btn").forEach((btn) => {
    const saved = JSON.parse(btn.dataset.state || "null");
    if (saved) { state.set(btn, saved); paint(btn); poll(btn); }
    btn.addEventListener("click", () => {
      const s = state.get(btn);
      if (!s || s.status === "error") start(btn);
      open(btn);
    });
  });
  $("[data-retry]").addEventListener("click", () => current && start(current));
  dlg.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => dlg.close()));
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });   // 바깥 누르면 닫기
  dlg.addEventListener("close", () => { current = null; });
})();
</script>
{% endif %}
{%- endmacro %}"""

register_templates(
    {"tryon.html": TRYON_HTML},
    TRYON_ON=vton.enabled(),
    tryon_state=tryon_state,
    tryon_items=tryon_items,
)
