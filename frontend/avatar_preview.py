"""feat/avatar-2d-preview (D) — 코디를 내 몸에 입혀 보여주기: 착샷(FASHN VTON).

(2026-11-07 시아: 처음엔 그림 마네킹을 코디 카드 왼쪽에 바로 보여줬는데, 어색해서 없앰. 이제 착샷만)

착샷 (2026-11-07): 코디 카드의 "착샷 만들기" → 내 전신 사진에 하의 → 상의 → 아우터를 차례로 입힘
- 프론트 서버가 뒤에서 착샷 서버(connect/tryon.py)에 한 벌씩 맡기고, 결과를 다음 사람 사진으로 넣음
- 화면은 2초마다 진행 상황을 물어봄 (GET /tryon/{id}). 창을 닫거나 새로고침해도 계속 만들고, 끝난 착샷은 기억
- 다 되면 코디 카드 왼쪽에 착샷이 생김. 실패하거나 서버가 없으면 옷 목록만
- 진행 상황은 프론트 메모리, 완성 결과는 백엔드의 계정별 사진 보관 정책에 따라 처리

사용법 (템플릿):  {% from "tryon.html" import tryon_button, tryon_dialog with context %}
다른 팀원과 연결: connect/tryon.py (착샷 서버), 전신 사진·옷 사진은 connect/auth_profile·garment_storage
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from shared.tryon_contract import RENDER_VERSION, layering_warnings
from shared.tryon_maintenance import read_state
from shared.wardrobes import single_wardrobe

from .common import TOKEN_COOKIE, garment_name, register_templates, render
from .connect import ApiError
from .connect import auth_profile as auth
from .connect import garment_storage as closet
from .connect import request as api_request
from .connect import request_bytes as api_bytes
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
    durable: bool = False  # Backend-managed; policy may be persistent or session-only.
    task: asyncio.Task | None = field(default=None, repr=False)
    eta: dict | None = None
    finalizing: bool = False
    layering_status: str = "not_requested"

    def to_json(self) -> dict:
        total = len(self.steps)
        eta = self.eta or {}
        elapsed, duration = eta.get("step_elapsed_seconds") or 0, eta.get("step_seconds")
        if self.status == "done":
            progress = 1.0
        elif self.finalizing:
            progress = 0.98
        else:
            fraction = min(elapsed / duration, 0.95) if duration and self.status == "running" else 0
            progress = (self.step + fraction) / max(total, 1)
        return {
            "id": self.id,
            "status": self.status,
            "step": min(self.step + 1, total),
            "total": total,
            "step_name": SLOT_NAMES[self.steps[min(self.step, total - 1)][0]],
            "names": [f"{SLOT_NAMES[s]} · {n}" for s, _, n in self.steps],
            "queue_position": self.queue_position,
            "progress": round(progress, 3),
            "seconds_left": 0 if self.status == "done" else eta.get("remaining_seconds"),
            "eta": self.eta,
            "finalizing": self.finalizing,
            "skipped": self.skipped,
            "render_version": RENDER_VERSION,
            "layering_status": self.layering_status,
            "warnings": layering_warnings(self.layering_status),
            "error": self.error,
            "image": f"/tryon/{self.id}/image" if self.status == "done" else None,
        }


_TRYONS: dict[str, TryOn] = {}
_TASKS: set[asyncio.Task] = set()


def tryon_items(items) -> dict[str, str]:
    """코디 옷 (dict 또는 OutfitItems) → {"top", "bottom", "outerwear"} 문자열."""
    get = items.get if isinstance(items, dict) else lambda k: getattr(items, k, None)
    return {s: get(s) or "" for s in ORDER}


def tryon_wardrobe(items, by_id) -> str:
    ids = [gid for gid in tryon_items(items).values() if gid]
    if not ids or any(gid not in by_id for gid in ids):
        return "missing"
    return single_wardrobe(by_id[gid] for gid in ids) or "mixed"


def _owner(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:20]


def _profile_owner(token: str, profile) -> str:
    return getattr(profile, "user_id", None) or _owner(token)


def _key(owner: str, items: dict, photo_version: str | None) -> tuple:
    return (owner, photo_version, *(items.get(s) or "" for s in ORDER), RENDER_VERSION)


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
    if t and t.durable and t.status == "done":
        saved = next((row for row in getattr(profile, "saved_tryons", []) if row["id"] == t.id), None)
        if saved is None or not saved.get("inputs_current", True):
            t = None
    if t:
        return t.to_json()
    normalized = {k: v for k, v in tryon_items(items).items() if v}
    for saved in getattr(profile, "saved_tryons", []):
        if (
            saved["status"] == "done"
            and saved.get("inputs_current", True)
            and saved.get("render_version") == RENDER_VERSION
            and saved["items"] == normalized
            and saved["photo_version"].replace("+00:00", "Z") == version.replace("+00:00", "Z")
        ):
            return saved
    return None


def _json_error(status: int, message: str, **extra) -> JSONResponse:
    retry = extra.get("retry_after")
    headers = {"Cache-Control": "no-store"}
    if retry:
        headers["Retry-After"] = str(retry)
    return JSONResponse({"error": message, **extra}, status_code=status, headers=headers)


@router.get("/tryon/service-status")
async def tryon_service_status():
    state = read_state()
    return JSONResponse(
        {**state, "enabled": vton.enabled(), "active": sum(not task.done() for task in _TASKS)},
        headers={"Cache-Control": "no-store"},
    )


def maintenance_error():
    state = read_state()
    if not state["available"]:
        return JSONResponse(
            {"error": state["message"], "code": "TRYON_PAUSED", "phase": state["phase"]},
            status_code=503,
            headers={"Retry-After": "30", "Cache-Control": "no-store"},
        )


@router.post("/tryon")
async def tryon_start(
    request: Request,
    top: Annotated[str, Form()] = "",
    bottom: Annotated[str, Form()] = "",
    outerwear: Annotated[str, Form()] = "",
    wardrobe: Annotated[Literal["personal", "example"] | None, Form()] = None,
):
    """착샷 만들기 시작 (같은 코디·같은 전신 사진이면 하던 것/만든 것을 돌려줌)."""
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return _json_error(401, "로그인이 필요해요.", login=True)
    if not vton.enabled():
        return _json_error(503, "착샷 서버가 설정되지 않았어요.")
    if response := maintenance_error():
        return response
    try:
        profile = await auth.get_profile(token)
    except ApiError as e:
        return _json_error(401 if e.status == 401 else 502, e.message, login=e.status == 401)
    version = profile.body_photo_updated_at
    if not version:
        return _json_error(409, "전신 사진을 먼저 올려 주세요.", need_photo=True)

    items = {"top": top, "bottom": bottom, "outerwear": outerwear}
    key = _key(_profile_owner(token, profile), items, version)
    steps, skipped, garments = [], [], []
    for slot in ORDER:
        if not items[slot]:
            continue
        try:
            g = await closet.get_garment(token, items[slot])
        except ApiError as e:
            return _json_error(e.status or 502, e.message, code=e.code)
        if g.category != slot:
            return _json_error(404, "본인 옷장에서 착용 역할에 맞는 의류를 선택해 주세요.")
        garments.append(g)
        name = garment_name(g)
        if closet.may_have_photo(g):
            steps.append((slot, g.garment_id, name))
        else:
            skipped.append(name)
    if garments:
        selected_wardrobe = single_wardrobe(garments)
        if selected_wardrobe is None:
            return _json_error(
                422,
                "내 옷장과 테스트용 옷장의 의류를 함께 입힐 수 없어요. 같은 옷장에서 선택해 주세요.",
                code="TRYON_WARDROBE_MIXED",
            )
        if wardrobe is not None and wardrobe != selected_wardrobe:
            return _json_error(
                422,
                "선택한 옷장과 의류가 일치하지 않아요. 같은 옷장에서 다시 선택해 주세요.",
                code="TRYON_WARDROBE_MISMATCH",
            )
    if not steps:
        return _json_error(
            422,
            "사진이 있는 옷이 없어서 착샷을 만들 수 없어요. 옷을 사진으로 등록해 주세요.",
        )

    # Validate the full selection before returning a legacy/in-memory cached result,
    # including clothes without an image that are omitted from the GPU steps.
    if saved := tryon_state(request, items, profile):
        return saved

    # Recheck after awaited profile/garment requests so a concurrent pause wins.
    if response := maintenance_error():
        return response
    try:
        saved = await api_request(
            "POST",
            "/tryon-results",
            token=token,
            json={"items": {slot: gid for slot, gid, _ in steps}, "wardrobe": selected_wardrobe},
        )
    except ApiError as e:
        return _json_error(
            e.status or 502,
            e.message,
            code=e.code,
            retry_after=(5 if e.code == "TRYON_USER_BUSY" else 10) if e.status == 429 else None,
        )
    t = TryOn(saved["id"], key[0], key, steps, skipped, durable=True)
    _TRYONS[t.id] = t
    while len(_TRYONS) > MAX_KEEP:
        completed = next((key for key, item in _TRYONS.items() if item.status in ("done", "error")), None)
        if completed is None:
            break
        _TRYONS.pop(completed)
    task = asyncio.create_task(_run(t, token))
    t.task = task
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return t.to_json()


async def _run(t: TryOn, token: str) -> None:
    """한 벌씩 착샷 서버에 맡기고, 결과를 다음 사람 사진으로."""
    gpu_job = None
    try:
        if t.durable:
            await api_request("POST", f"/tryon-results/{t.id}/heartbeat", token=token)
        person = await auth.fetch_body_photo(token)
        stages = {}
        done_any = False
        top_applied = False
        for i, (slot, gid, name) in enumerate(t.steps):
            t.step, t.step_started, t.queue_position = i, None, None
            t.eta, t.status = None, "queued"
            try:
                from .connect import request_bytes

                garment = await request_bytes("GET", f"/garments/{gid}/layer-image", token=token)
            except ApiError as e:
                if e.status != 404:
                    raise
                t.skipped.append(name)  # 사진이 없는 옷은 빼고 계속
                continue
            deadline = time.monotonic() + STEP_TIMEOUT
            request_id = str(uuid4())
            future_has_top = top_applied or any(s == "top" for s, _, _ in t.steps[i:])
            remaining = tuple(
                "outerwear" if s == "outerwear" and future_has_top else vton.CATEGORY[s]
                for s, _, _ in t.steps[i + 1 :]
            )
            while True:
                if t.durable:
                    await api_request("POST", f"/tryon-results/{t.id}/heartbeat", token=token)
                try:
                    job = await vton.submit(
                        person,
                        garment,
                        vton.CATEGORY[slot],
                        preserve_inner=slot == "outerwear" and top_applied,
                        remaining_categories=remaining,
                        **({"owner_id": t.owner, "request_id": request_id} if t.durable else {}),
                    )
                    break
                except ApiError as e:
                    if e.status not in (0, 429) or time.monotonic() >= deadline:
                        raise
                    t.status = "queued"
                    await asyncio.sleep(POLL_SECONDS)
            gpu_job = job["job_id"]
            while True:
                status = job.get("status")
                t.eta = job.get("eta")
                if status == "done":
                    t.finalizing = i == len(t.steps) - 1
                    break
                if status not in ("queued", "running"):
                    if job.get("error_code") == "GARMENT_LENGTH_MISMATCH":
                        raise ApiError(
                            422,
                            "선택한 바지의 길이가 착샷에 맞게 반영되지 않았어요. "
                            "밑단까지 보이는 의류 사진과 전신 사진인지 확인한 뒤 다시 시도해 주세요.",
                            "GARMENT_LENGTH_MISMATCH",
                        )
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
                if t.durable:
                    await api_request("POST", f"/tryon-results/{t.id}/heartbeat", token=token)
                job = await vton.get_job(job["job_id"])
            person = (await vton.get_result(job["job_id"]), "image/png")
            stages[f"stage_{slot}"] = (f"{slot}.png", person[0], "image/png")
            if slot == "top":
                top_applied = True
            if slot == "outerwear" and top_applied:
                # Never silently accept an older worker that ignores preservation.
                if (job.get("layering") or {}).get("status") not in (
                    "applied",
                    "skipped",
                    "approximate",
                    "not_requested",
                ):
                    raise ApiError(502, "아우터 보존 기능을 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.")
                t.layering_status = job["layering"]["status"]
            if t.durable:
                await vton.delete_job(gpu_job)
                gpu_job = None
            done_any = True
        if not done_any:
            raise ApiError(422, "사진이 있는 옷이 없어서 착샷을 만들 수 없어요.")
        if t.durable:
            t.finalizing, t.eta = True, None
            await api_request(
                "PUT",
                f"/tryon-results/{t.id}/image",
                token=token,
                files={"file": ("result.png", person[0], "image/png"), **stages},
                data={"layering_status": t.layering_status, "render_version": RENDER_VERSION},
            )
        t.image, t.step, t.status = (None if t.durable else person[0]), len(t.steps) - 1, "done"
        t.finalizing = False
    except asyncio.CancelledError:
        t.status, t.error = "error", "착샷 생성을 취소했습니다."
        raise
    except ApiError as e:
        t.status, t.error = "error", e.message
    except Exception:
        log.exception("착샷 실패")
        t.status, t.error = (
            "error",
            "착샷을 만들지 못했어요. 잠시 후 다시 시도해 주세요.",
        )
    finally:
        if gpu_job and t.durable:
            try:
                await vton.delete_job(gpu_job)
            except ApiError:
                pass  # GPU expiry cleanup is a second line of cleanup.
        if t.durable and t.status != "done":
            try:
                await api_request("POST", f"/tryon-results/{t.id}/cancel", token=token)
            except ApiError:
                pass  # Lost frontend/auth/DB: the shared admission expires within 120s.


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


@router.get("/tryon/history")
async def tryon_history(request: Request):
    token = request.cookies.get(TOKEN_COOKIE)
    try:
        profile = await auth.get_profile(token)
    except ApiError:
        return RedirectResponse("/login", status_code=303)
    return render(request, "tryon_history.html", rows=profile.saved_tryons, profile=profile, active="profile")


@router.post("/tryon/history/{result_id}/delete")
async def delete_saved(request: Request, result_id: str):
    try:
        await api_request("DELETE", f"/tryon-results/{result_id}", token=request.cookies.get(TOKEN_COOKIE))
    except ApiError as e:
        return _json_error(e.status or 502, e.message)
    _TRYONS.pop(result_id, None)
    return RedirectResponse("/tryon/history", status_code=303)


@router.get("/tryon/{tryon_id}")
async def tryon_status(request: Request, tryon_id: str):
    cached = _TRYONS.get(tryon_id)
    if cached is None or cached.durable:
        try:
            saved = await api_request(
                "GET", f"/tryon-results/{tryon_id}", token=request.cookies.get(TOKEN_COOKIE)
            )
            if saved.get("status") == "interrupted":
                if cached and cached.task and not cached.task.done():
                    cached.task.cancel()
                return {**saved, "status": "error"}
            return cached.to_json() if cached and cached.status in ("queued", "running", "error") else saved
        except ApiError:
            _TRYONS.pop(tryon_id, None)
            return _json_error(404, "착샷을 찾을 수 없어요.")
    t = await _mine(request, tryon_id)
    if not t:
        return _json_error(404, "착샷을 찾을 수 없어요. 다시 만들어 주세요.")
    return t.to_json()


@router.post("/tryon/{tryon_id}/cancel")
async def tryon_cancel(request: Request, tryon_id: str):
    try:
        await api_request(
            "POST", f"/tryon-results/{tryon_id}/cancel", token=request.cookies.get(TOKEN_COOKIE)
        )
    except ApiError as e:
        return _json_error(e.status or 502, e.message)
    cached = _TRYONS.get(tryon_id)
    if cached and cached.task and not cached.task.done():
        cached.task.cancel()
    return JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})


@router.get("/tryon/{tryon_id}/image")
async def tryon_image(request: Request, tryon_id: str, download: int = 0):
    cached = _TRYONS.get(tryon_id)
    if cached is None or cached.durable:
        try:
            data, kind = await api_bytes(
                "GET", f"/tryon-results/{tryon_id}/image", token=request.cookies.get(TOKEN_COOKIE)
            )
        except ApiError:
            return Response(status_code=404)
        headers = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
        if download:
            headers["Content-Disposition"] = 'attachment; filename="fitjang-tryon.jpg"'
        return Response(data, media_type=kind, headers=headers)
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
  {% set its = tryon_items(items) %}
  {% set scope = tryon_wardrobe(items, by_id | default({})) %}
  {% if scope == 'mixed' %}
  <p class="hint" data-tryon-blocked="mixed">내 옷장과 테스트용 옷장이 섞인 코디는 새 착샷을 만들 수 없어요. 같은 옷장에서 다시 추천받아 주세요.</p>
  <a class="btn btn-secondary tryon-btn" href="/recommend">옷장 선택하고 다시 추천받기</a>
  {% elif scope == 'missing' %}
  <p class="hint" data-tryon-blocked="missing">삭제되었거나 확인할 수 없는 옷이 있어요. 다시 추천받아 주세요.</p>
  {% elif profile and profile.body_photo_updated_at %}
  <p class="hint" data-tryon-wardrobe="{{scope}}">{{ '테스트용 옷장' if scope == 'example' else '내 옷장' }}의 의류만 입혀요.</p>
  <button type="button" class="btn btn-secondary tryon-btn" data-tryon="{{ n }}" data-title="{{ title or ('코디 ' ~ n ~ ' 착샷') }}" data-items='{{ its | tojson }}'
          data-wardrobe="{{scope}}"
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
  .tfoot [hidden] { display: none; }
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
      <small>현재 접수 기준의 예상 시간입니다. 새 요청에 따라 달라질 수 있어요. 닫아도 계속 만들어요.</small>
    </div>
    <div class="terr" hidden><p role="alert"></p><button type="button" class="btn btn-secondary" data-retry>다시 만들기</button></div>
  </div>
  <p class="tnote tskip" hidden></p>
  <div class="tfoot">
    <a class="btn btn-secondary" data-save hidden>사진 저장</a>
    <button type="button" class="btn btn-secondary" data-cancel hidden>생성 취소</button>
    <button type="button" class="btn btn-primary" data-close>닫기</button>
  </div>
</dialog>
<script nonce="{{ request.state.csp_nonce | default('') }}">
(() => {
  const dlg = document.getElementById("tryon-dlg");
  const $ = (s) => dlg.querySelector(s);
  const img = $(".tv img"), wait = $(".twait"), err = $(".terr"), msg = $(".tmsg"), bar = $(".tbar i");
  const stepsEl = $(".tsteps"), save = $("[data-save]"), skip = $(".tskip");
  const state = new Map();   // 버튼 → 서버가 알려 준 상태
  let current = null;        // 창에 보이는 버튼
  let serviceAvailable = {{ tryon_service_available() | tojson }};

  const busy = (s) => s && (s.status === "queued" || s.status === "running");
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  function duration(seconds) {
    if (seconds < 60) return `${Math.ceil(seconds)}초`;
    const minutes = Math.floor(seconds / 60), rest = Math.ceil(seconds % 60);
    return `${minutes}분${rest ? ' ' + rest + '초' : ''}`;
  }
  function waitText(s) {
    if (s.finalizing) return "완성된 착샷을 정리하고 있어요…";
    const phase = s.status === "queued"
      ? (s.queue_position != null ? `옷 작업 대기 ${s.queue_position}번째` : "착샷 준비 중")
      : `${s.step_name} 입히는 중 ${s.step}/${s.total}`;
    const eta = s.eta;
    if (!eta || eta.remaining_seconds == null) return `${phase} · 예상 시간 계산 중`;
    if (eta.overdue) return `${phase} · 예상보다 처리가 지연되고 있어요. 시간을 다시 계산 중입니다.`;
    const wait = s.status === "queued" ? ` · 시작까지 약 ${duration(eta.wait_seconds)}` : "";
    const range = `${duration(eta.remaining_low_seconds)}~${duration(eta.remaining_high_seconds)}`;
    return `${phase}${wait} · 코디 완성까지 약 ${range}${eta.basis === 'warming' ? ' (초기 예상)' : ''}`;
  }

  function paint(btn) {
    const s = state.get(btn);
    const label = btn.querySelector(".tb-label");
    btn.disabled = !serviceAvailable && !busy(s) && s?.status !== "done";
    btn.dataset.status = s ? s.status : "";
    if (!s) { label.textContent = serviceAvailable ? "착샷 보기" : "착샷 일시 중지"; return; }
    if (busy(s)) {
      label.textContent = s.status === "queued" ? "착샷 대기 중…" : `착샷 만드는 중 ${s.step}/${s.total}`;
      btn.style.setProperty("--p", Math.round(s.progress * 100) + "%");
    } else if (s.status === "done") {
      label.textContent = "착샷 크게 보기";
      const mq = btn.closest(".outfit")?.querySelector(".mq");
      if (mq && !mq.querySelector(".shot")) {
        mq.hidden = false; mq.closest(".fit")?.classList.add("with-shot");
        const shot = document.createElement('button'), photo = document.createElement('img');
        shot.type = 'button'; shot.className = 'shot';
        shot.setAttribute('aria-label', `${btn.dataset.title} 크게 보기`);
        photo.alt = btn.dataset.title; photo.src = s.image;
        shot.append(photo); mq.replaceChildren(shot);
        shot.addEventListener('click', () => open(btn));
      }
    } else if (s.status === "error") {
      label.textContent = "착샷 다시 만들기";
    }
    if (current === btn) show(s, btn);
  }

  function show(s, btn) {
    $("#tryon-title").textContent = btn.dataset.title;
    const done = s && s.status === "done", failed = !s || s.status === "error";
    $("[data-cancel]").hidden = !busy(s) || !s.id;
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
    skip.textContent = [s?.skipped?.length ? `사진이 없어 뺀 옷: ${s.skipped.join(", ")}` : "",
      ...(s?.warnings || [])].filter(Boolean).join(" ");
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
    if (!serviceAvailable) return;
    if ([...state.entries()].some(([other,s]) => other !== btn && busy(s))) {
      state.set(btn, {status:"error", error:"이미 진행 중인 착샷이 있습니다. 완료하거나 취소한 후 다시 요청해 주세요."});
      paint(btn); return;
    }
    state.set(btn, { status: "queued", step: 1, total: 1, step_name: "", names: [], progress: 0, seconds_left: null, eta: null, skipped: [] });
    paint(btn);
    try {
      const items = JSON.parse(btn.dataset.items);
      items.wardrobe = btn.dataset.wardrobe;
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
    paint(btn);
    btn.addEventListener("click", () => {
      const s = state.get(btn);
      if (!s || s.status === "error") start(btn);
      open(btn);
    });
  });
  $("[data-retry]").addEventListener("click", () => current && start(current));
  $("[data-cancel]").addEventListener("click", async () => {
    const btn = current, s = state.get(btn);
    if (!s?.id || !busy(s)) return;
    try {
      const res = await fetch(`/tryon/${s.id}/cancel`, {method:"POST", headers:{Accept:"application/json"}});
      const data = await res.json();
      state.set(btn, {status:"error", error:res.ok ? "착샷 생성을 취소했습니다." : data.error});
    } catch { state.set(btn, {status:"error", error:"취소 요청을 확인하지 못했어요. 잠시 후 다시 확인해 주세요."}); }
    paint(btn);
  });
  $("[data-retry]").disabled = !serviceAvailable;
  window.addEventListener("tryon-service", (event) => {
    serviceAvailable = event.detail.available;
    document.querySelectorAll("button.tryon-btn").forEach(paint);
    $("[data-retry]").disabled = !serviceAvailable;
  });
  dlg.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => dlg.close()));
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });   // 바깥 누르면 닫기
  dlg.addEventListener("close", () => { current = null; });
})();
</script>
{% endif %}
{%- endmacro %}"""

register_templates(
    {
        "tryon.html": TRYON_HTML,
        "tryon_history.html": """{% extends "app_base.html" %}
{% block title %}내 착샷{% endblock %}{% block content %}
<h1>내 착샷</h1>
{% if profile.photo_policy == 'session' %}<p>전신 사진과 착샷은 서버 메모리에서만 처리합니다.
로그아웃하거나 30분간 사용하지 않으면 삭제됩니다. 옷과 추천·피드백은 유지됩니다.</p>
{% else %}<p>저장한 착샷은 재접속 후에도 확인할 수 있습니다.</p>{% endif %}
{% for row in rows %}<article class="card" style="padding:16px;margin:12px 0">
<p>{{ row.names | join(' · ') }}</p><img src="{{ row.image }}" alt="저장된 착샷" style="max-width:100%;width:288px">
{% for warning in row.warnings | default([]) %}<p>{{ warning }}</p>{% endfor %}
<form method="post" action="/tryon/history/{{ row.id }}/delete"><button class="btn btn-secondary">착샷 삭제</button></form>
</article>{% else %}<p>완성된 착샷이 없습니다.</p>{% endfor %}{% endblock %}""",
    },
    TRYON_ON=vton.enabled(),
    tryon_state=tryon_state,
    tryon_wardrobe=tryon_wardrobe,
    tryon_items=tryon_items,
    tryon_service_available=lambda: read_state()["available"],
)
