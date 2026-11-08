"""FASHN VTON v1.5 로컬 시착 HTTP API (FastAPI).

- 서버 시작 시 모델을 GPU 에 한 번 올리고 상주.
- GPU 1장이므로 워커 스레드 1개가 큐에서 작업을 하나씩 처리.
- POST /tryon  -> job_id (비동기) 또는 ?sync=true 로 결과 PNG 바로 반환
- GET  /jobs/{job_id} -> 상태 / 결과 URL
- GET  /results/{job_id}.png -> 결과 파일
- 인증: TRYON_API_KEY 설정 및 X-API-Key 헤더 필수.

실행:  python -m scripts.frontend_preview --tryon (프로젝트 루트)
문서:  http://127.0.0.1:8010/docs
"""

from __future__ import annotations

import hmac
import io
import logging
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image, UnidentifiedImageError

from . import local_tryon as lt

log = logging.getLogger("tryon-api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

API_KEY = os.getenv("TRYON_API_KEY", "")
if not API_KEY:
    raise RuntimeError("TRYON_API_KEY is required; use scripts.frontend_preview --tryon")
RESULT_DIR = lt.OUT_DIR / "api"
RESULT_DIR.mkdir(parents=True, exist_ok=True)
JOB_TTL_SEC = int(os.getenv("TRYON_JOB_TTL_SEC", str(6 * 3600)))  # 결과 보관 시간
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
SYNC_TIMEOUT_SEC = 600


# ----------------------------------------------------------------------------- jobs
@dataclass
class Job:
    id: str
    person: Image.Image
    garment: Image.Image
    params: dict
    status: str = "queued"  # queued | running | done | failed
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    elapsed_sec: float | None = None
    garment_photo_type: str | None = None
    category: str | None = None
    error: str | None = None
    result_path: Path | None = None
    done_event: threading.Event = field(default_factory=threading.Event)

    def public(self, request: Request | None = None) -> dict:
        d = {
            "job_id": self.id,
            "status": self.status,
            "created_at": self.created_at,
            "queue_position": queue_position(self.id) if self.status == "queued" else None,
            "elapsed_sec": self.elapsed_sec,
            "category": self.category,
            "garment_photo_type": self.garment_photo_type,
            "params": {k: v for k, v in self.params.items()},
            "error": self.error,
            "result_url": None,
        }
        if self.status == "done" and self.result_path:
            base = str(request.base_url).rstrip("/") if request else ""
            d["result_url"] = f"{base}/results/{self.id}.png"
        return d


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()
JOB_QUEUE: queue.Queue[Job] = queue.Queue(maxsize=8)
PIPELINE_READY = threading.Event()
PIPELINE_ERROR: str | None = None


def queue_position(job_id: str) -> int | None:
    with JOB_QUEUE.mutex:
        for i, j in enumerate(JOB_QUEUE.queue):
            if j.id == job_id:
                return i + 1
    return None


def worker_loop():
    global PIPELINE_ERROR
    try:
        lt.get_pipeline()
        PIPELINE_READY.set()
        log.info("pipeline ready: %s", lt.gpu_info())
    except Exception as e:  # noqa: BLE001
        PIPELINE_ERROR = f"{type(e).__name__}: {e}"
        log.exception("pipeline load failed")
        return
    while True:
        job = JOB_QUEUE.get()
        job.status, job.started_at = "running", time.time()
        try:
            res = lt.run_tryon(job.person, job.garment, **job.params)
            out = RESULT_DIR / f"{job.id}.png"
            res.images[0].save(out)
            job.result_path = out
            job.elapsed_sec = round(res.elapsed_sec, 1)
            job.garment_photo_type = res.garment_photo_type
            job.category = res.category
            job.status = "done"
            log.info(
                "job %s done in %.1fs (%s/%s)", job.id, res.elapsed_sec, res.category, res.garment_photo_type
            )
        except Exception as e:  # noqa: BLE001
            job.status = "failed"
            job.error = f"{type(e).__name__}: {str(e)[:300]}"
            log.exception("job %s failed", job.id)
        finally:
            job.finished_at = time.time()
            job.person = job.garment = None  # 메모리 해제
            job.done_event.set()
            JOB_QUEUE.task_done()
            cleanup_old_jobs()


def cleanup_old_jobs():
    now = time.time()
    with JOBS_LOCK:
        for jid in [j.id for j in JOBS.values() if j.finished_at and now - j.finished_at > JOB_TTL_SEC]:
            j = JOBS.pop(jid)
            if j.result_path and j.result_path.exists():
                try:
                    j.result_path.unlink()
                except OSError:
                    pass


# ----------------------------------------------------------------------------- app
app = FastAPI(title="FASHN VTON v1.5 Local Try-On API", version="0.1.0")


@app.on_event("startup")
def _startup():
    threading.Thread(target=worker_loop, name="tryon-worker", daemon=True).start()


def require_key(x_api_key: str | None = Header(default=None)):
    if not x_api_key or not hmac.compare_digest(x_api_key, API_KEY):
        raise HTTPException(401, "invalid or missing X-API-Key")


async def read_image(upload: UploadFile, name: str) -> Image.Image:
    data = await upload.read(MAX_UPLOAD_BYTES + 1)
    if not data:
        raise HTTPException(400, f"{name}: empty file")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"{name}: file too large (max {MAX_UPLOAD_BYTES // 1024 // 1024}MB)")
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
        return im.convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise HTTPException(400, f"{name}: not an image") from None


@app.get("/health")
def health():
    return {
        "status": "ok" if PIPELINE_READY.is_set() else ("error" if PIPELINE_ERROR else "loading"),
        "pipeline_error": PIPELINE_ERROR,
        "gpu": lt.gpu_info(),
        "queued": JOB_QUEUE.qsize(),
        "jobs_total": len(JOBS),
    }


@app.post("/tryon", dependencies=[Depends(require_key)])
async def create_tryon(
    request: Request,
    person: UploadFile = File(..., description="전신 사진"),
    garment: UploadFile = File(..., description="의상 사진"),
    category: Literal["tops", "bottoms", "one-pieces"] = Form(...),
    garment_photo_type: Literal["auto", "model", "flat-lay"] = Form("auto"),
    num_timesteps: int = Form(30, ge=10, le=50),
    guidance_scale: float = Form(1.5, ge=1.0, le=3.0),
    seed: int = Form(42, ge=0),
    segmentation_free: bool = Form(True),
    sync: bool = False,
):
    """시착 작업 등록. 기본은 비동기(job_id 반환). `?sync=true` 면 완료까지 기다렸다가 PNG 반환."""
    if PIPELINE_ERROR:
        raise HTTPException(503, f"pipeline failed to load: {PIPELINE_ERROR}")
    if not PIPELINE_READY.is_set():
        raise HTTPException(503, "pipeline is still loading")
    person_im = await read_image(person, "person")
    garment_im = await read_image(garment, "garment")
    job = Job(
        id=uuid.uuid4().hex[:12],
        person=person_im,
        garment=garment_im,
        params=dict(
            category=category,
            garment_photo_type=garment_photo_type,
            num_timesteps=num_timesteps,
            guidance_scale=guidance_scale,
            seed=seed,
            segmentation_free=segmentation_free,
        ),
    )
    with JOBS_LOCK:
        try:
            JOB_QUEUE.put_nowait(job)
        except queue.Full:
            raise HTTPException(429, "GPU queue is full; retry later") from None
        JOBS[job.id] = job
    log.info("job %s queued (position %s)", job.id, queue_position(job.id))

    if not sync:
        return JSONResponse(job.public(request), status_code=202)

    # 동기 모드: 스레드 이벤트를 블로킹하지 않도록 스레드풀에서 대기
    import anyio

    finished = await anyio.to_thread.run_sync(job.done_event.wait, SYNC_TIMEOUT_SEC)
    if not finished:
        raise HTTPException(504, f"timeout after {SYNC_TIMEOUT_SEC}s; poll /jobs/{job.id}")
    if job.status != "done":
        raise HTTPException(500, job.error or "failed")
    return FileResponse(
        job.result_path,
        media_type="image/png",
        filename=f"{job.id}.png",
        headers={"X-Job-Id": job.id, "X-Elapsed-Sec": str(job.elapsed_sec)},
    )


@app.get("/jobs/{job_id}", dependencies=[Depends(require_key)])
def get_job(job_id: str, request: Request):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "job not found (expired or wrong id)")
    return job.public(request)


@app.get("/results/{job_id}.png", dependencies=[Depends(require_key)])
def get_result(job_id: str):
    job = JOBS.get(job_id)
    if not job or job.status != "done" or not job.result_path or not job.result_path.exists():
        raise HTTPException(404, "result not available")
    return FileResponse(job.result_path, media_type="image/png", filename=f"{job_id}.png")


@app.get("/jobs", dependencies=[Depends(require_key)])
def list_jobs(request: Request, limit: int = 50):
    jobs = sorted(JOBS.values(), key=lambda j: j.created_at, reverse=True)[:limit]
    return [j.public(request) for j in jobs]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("tryon_server.server:app", host="127.0.0.1", port=int(os.getenv("TRYON_PORT", "8010")))
