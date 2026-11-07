"""FASHN VTON v1.5 로컬 추론 공용 모듈 (GPU, 이 PC에서 실행).

- 파이프라인을 한 번만 로드해 재사용한다 (가중치 ~2GB, 로드에 수십 초).
- Windows 에서 onnxruntime-gpu 가 CUDA/cuDNN DLL 을 찾도록 torch 를 먼저 import 한다.
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# GPU 가 여러 장일 때 사용할 GPU 번호(nvidia-smi 기준). torch import 전에 지정해야 적용된다.
if os.getenv("FASHN_GPU_INDEX") and not os.getenv("CUDA_VISIBLE_DEVICES"):
    os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    os.environ["CUDA_VISIBLE_DEVICES"] = os.environ["FASHN_GPU_INDEX"]

import torch  # noqa: E402,F401  (onnxruntime 보다 먼저 import: CUDA/cuDNN DLL 선적재)

try:
    import onnxruntime as ort

    if hasattr(ort, "preload_dlls"):
        ort.preload_dlls()
    ort.set_default_logger_severity(3)  # shape 연산이 CPU 로 배정된다는 무해한 경고 숨김
except Exception:  # noqa: BLE001
    pass

from PIL import Image

ROOT = Path(__file__).resolve().parent
REPO_DIR = ROOT / "fashn-vton-1.5"
WEIGHTS_DIR = Path(os.getenv("FASHN_WEIGHTS_DIR", REPO_DIR / "weights"))
OUT_DIR = ROOT / "outputs"

CATEGORIES = ("tops", "bottoms", "one-pieces")
GARMENT_PHOTO_TYPES = ("model", "flat-lay")

_pipeline = None


def gpu_info() -> str:
    if not torch.cuda.is_available():
        return "CUDA 사용 불가 (CPU 모드: 매우 느림)"
    name = torch.cuda.get_device_name(0)
    total = torch.cuda.get_device_properties(0).total_memory / 1024**3
    bf16 = torch.cuda.is_bf16_supported()
    return f"{name} / VRAM {total:.1f}GB / torch {torch.__version__} / CUDA {torch.version.cuda} / bf16={bf16}"


def onnx_providers() -> list[str]:
    try:
        import onnxruntime as ort

        return ort.get_available_providers()
    except Exception as e:  # noqa: BLE001
        return [f"onnxruntime 오류: {e}"]


def get_pipeline(device: str | None = None):
    """TryOnPipeline 싱글턴."""
    global _pipeline
    if _pipeline is None:
        from fashn_vton import TryOnPipeline

        if not (WEIGHTS_DIR / "model.safetensors").exists():
            raise FileNotFoundError(
                f"가중치 없음: {WEIGHTS_DIR}\n"
                f"다운로드: python fashn-vton-1.5/scripts/download_weights.py --weights-dir {WEIGHTS_DIR}"
            )
        t0 = time.time()
        _pipeline = TryOnPipeline(weights_dir=str(WEIGHTS_DIR), device=device)
        print(f"[local_tryon] pipeline loaded in {time.time() - t0:.1f}s on {_pipeline.device}", file=sys.stderr)
    return _pipeline


def detect_garment_photo_type(garment: Image.Image, device: str | None = None) -> str:
    """옷 사진에 사람이 검출되면 'model', 아니면 'flat-lay'. (DWPose 의 YOLOX 사람 검출기 재사용)"""
    import numpy as np
    from fashn_vton.dwpose.onnxdet import inference_detector

    pipe = get_pipeline(device)
    img = pipe.pre_resize(garment.convert("RGB"), allow_upsampling=False)
    bgr = np.array(img)[..., ::-1]
    boxes = inference_detector(pipe.pose_model.pose_estimation.session_det, bgr)
    return "model" if len(boxes) > 0 else "flat-lay"


def category_from_path(path: str | os.PathLike) -> str | None:
    """samples/garment/<category>/xxx.jpg 처럼 상위 폴더명이 카테고리면 그 값을 반환."""
    for part in reversed(Path(path).parts[:-1]):
        if part in CATEGORIES:
            return part
    return None


@dataclass
class LocalResult:
    images: list[Image.Image]
    elapsed_sec: float
    peak_vram_gb: float | None
    saved: list[Path] = field(default_factory=list)
    category: str = ""
    garment_photo_type: str = ""


def run_tryon(
    person: Image.Image | str | os.PathLike,
    garment: Image.Image | str | os.PathLike,
    category: str = "tops",
    garment_photo_type: str = "model",
    num_samples: int = 1,
    num_timesteps: int = 30,
    guidance_scale: float = 1.5,
    seed: int = 42,
    segmentation_free: bool = True,
    save_prefix: str | None = None,
    device: str | None = None,
) -> LocalResult:
    if category == "auto" and not isinstance(garment, Image.Image):
        category = category_from_path(garment) or "auto"
    if category not in CATEGORIES:
        raise ValueError(f"category 는 {CATEGORIES} 중 하나 (받은 값: {category!r}). "
                         "옷 사진을 samples/garment/<카테고리>/ 에 넣으면 자동 인식됨")
    if garment_photo_type not in GARMENT_PHOTO_TYPES + ("auto",):
        raise ValueError(f"garment_photo_type 은 {GARMENT_PHOTO_TYPES} 또는 auto")

    if not isinstance(person, Image.Image):
        person = Image.open(person)
    if not isinstance(garment, Image.Image):
        garment = Image.open(garment)
    person = person.convert("RGB")
    garment = garment.convert("RGB")

    pipe = get_pipeline(device)
    if garment_photo_type == "auto":
        garment_photo_type = detect_garment_photo_type(garment, device)
    if pipe.device.type == "cuda":
        torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    out = pipe(
        person_image=person,
        garment_image=garment,
        category=category,
        garment_photo_type=garment_photo_type,
        num_samples=int(num_samples),
        num_timesteps=int(num_timesteps),
        guidance_scale=float(guidance_scale),
        seed=int(seed),
        segmentation_free=bool(segmentation_free),
    )
    elapsed = time.time() - t0
    peak = torch.cuda.max_memory_allocated() / 1024**3 if pipe.device.type == "cuda" else None

    saved: list[Path] = []
    if save_prefix is not None:
        OUT_DIR.mkdir(exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        for i, im in enumerate(out.images):
            p = OUT_DIR / f"{stamp}_{save_prefix}_{category}_s{seed}_{i}.png"
            im.save(p)
            saved.append(p)
    return LocalResult(images=list(out.images), elapsed_sec=elapsed, peak_vram_gb=peak, saved=saved,
                       category=category, garment_photo_type=garment_photo_type)
