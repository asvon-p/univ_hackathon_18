"""FITJANG B의 당일 시연용 Vision 구현.

정확성에 대한 약속:
- demo: CNN이 *아닙니다*. 빠른 파이프라인 검증용이며 분류는 demo 기본값입니다.
  사용자는 업로드 뒤 카테고리를 수동 수정해야 합니다.
- clip: 실제 이미지-텍스트 모델로 의류 유형을 자동 추정. CNN은 아니며 후속 교체용.
- flat: 단색 배경에서만 근사 누끼를 만듭니다. 실제 옷/촬영 상황에는 rembg 권장.
"""
from __future__ import annotations

import colorsys
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import get_args

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from shared.contracts import Color, GarmentAttributes, VisionResult

log = logging.getLogger(__name__)
VERSION = "vision-baseline-0.1"

# 1:1 의류 사진의 분류 후보. 모델이 없는 demo 모드에서는 사용하지 않습니다.
_PROMPTS: dict[str, tuple[str, str]] = {
    "a photo of a t-shirt": ("top", "t-shirt"),
    "a photo of a button-down shirt": ("top", "shirt"),
    "a photo of a blouse": ("top", "blouse"),
    "a photo of a hoodie": ("top", "hoodie"),
    "a photo of a sweater": ("top", "sweater"),
    "a photo of a sweatshirt": ("top", "sweatshirt"),
    "a photo of a knitted vest": ("top", "knit_vest"),
    "a photo of jeans": ("bottom", "jeans"),
    "a photo of tailored trousers": ("bottom", "slacks"),
    "a photo of cotton pants": ("bottom", "cotton_pants"),
    "a photo of shorts": ("bottom", "shorts"),
    "a photo of a skirt": ("bottom", "skirt"),
    "a photo of jogger pants": ("bottom", "jogger"),
    "a photo of a coat": ("outerwear", "coat"),
    "a photo of a puffer jacket": ("outerwear", "padding"),
    "a photo of a jacket": ("outerwear", "jacket"),
    "a photo of a cardigan": ("outerwear", "cardigan"),
    "a photo of a blazer": ("outerwear", "blazer"),
    "a photo of a windbreaker jacket": ("outerwear", "windbreaker"),
    "a photo of sneakers": ("shoes", "sneakers"),
    "a photo of boots": ("shoes", "boots"),
    "a photo of loafers": ("shoes", "loafers"),
}


def _classifier() -> str:
    return os.getenv("VISION_CLASSIFIER", "demo").strip().lower()


def _segmentation() -> str:
    return os.getenv("VISION_SEGMENTATION", "flat").strip().lower()


@lru_cache(maxsize=1)
def _clip_model():
    """선택형 zero-shot 모델. 첫 사용 시 Hugging Face 가중치 다운로드."""
    import torch
    from transformers import CLIPModel, CLIPProcessor

    name = "openai/clip-vit-base-patch32"
    device = "cuda" if torch.cuda.is_available() else ("mps" if torch.backends.mps.is_available() else "cpu")
    model = CLIPModel.from_pretrained(name).to(device).eval()
    processor = CLIPProcessor.from_pretrained(name)
    return model, processor, device


@lru_cache(maxsize=1)
def _rembg_session():
    from rembg import new_session
    return new_session("u2net")


def warmup() -> None:
    """서버 startup에서 한 번 호출. 실패 시 로그와 오류를 통해 상태를 확인."""
    if _classifier() == "clip":
        _clip_model()
    if _segmentation() == "rembg":
        _rembg_session()


def _predict(image: Image.Image) -> tuple[str, str | None, str]:
    mode = _classifier()
    if mode == "demo":
        # 백엔드는 category를 반드시 받아야 하므로 데모 기본값을 명시해 반환.
        # 업로드 시 사용자가 슬롯을 선택해 PATCH로 올바른 값을 설정해야 합니다.
        category = os.getenv("VISION_DEMO_CATEGORY", "top").strip().lower()
        if category not in {"top", "bottom", "outerwear", "shoes"}:
            raise ValueError("VISION_DEMO_CATEGORY must be top/bottom/outerwear/shoes")
        return category, None, "demo-manual-category"
    if mode != "clip":
        raise ValueError(f"unsupported VISION_CLASSIFIER: {mode}")
    model, processor, device = _clip_model()
    import torch
    prompts = list(_PROMPTS)
    tensors = processor(text=prompts, images=image.convert("RGB"), return_tensors="pt", padding=True)
    tensors = {k: v.to(device) for k, v in tensors.items()}
    with torch.inference_mode():
        logits = model(**tensors).logits_per_image[0]
    idx = int(logits.argmax().item())
    category, subcategory = _PROMPTS[prompts[idx]]
    return category, subcategory, "clip-vit-base-patch32"


def _flat_background_cutout(image: Image.Image) -> Image.Image | None:
    """단색 배경만 처리하는 안전한 데모 누끼. 복잡한 배경은 무리하게 지우지 않음."""
    rgb = np.asarray(image.convert("RGB"), dtype=np.int16)
    height, width, _ = rgb.shape
    if min(height, width) < 32:
        return None
    stride_y = max(1, height // 180)
    stride_x = max(1, width // 180)
    border = np.concatenate(
        (rgb[:4, ::stride_x].reshape(-1, 3), rgb[-4:, ::stride_x].reshape(-1, 3),
         rgb[::stride_y, :4].reshape(-1, 3), rgb[::stride_y, -4:].reshape(-1, 3)), axis=0
    )
    bg = np.median(border, axis=0)
    edge_dist = np.max(np.abs(border - bg), axis=1)
    if np.percentile(edge_dist, 80) > 26:
        return None
    dist = np.max(np.abs(rgb - bg), axis=2)
    foreground = dist > 42
    ratio = float(np.mean(foreground))
    if ratio < 0.04 or ratio > 0.88:
        return None
    rgba = np.asarray(image.convert("RGBA")).copy()
    rgba[:, :, 3] = np.where(foreground, 255, 0).astype(np.uint8)
    return Image.fromarray(rgba, mode="RGBA")


def _get_cutout(image: Image.Image) -> Image.Image | None:
    mode = _segmentation()
    if mode == "off":
        return None
    if mode == "flat":
        return _flat_background_cutout(image)
    if mode == "rembg":
        from rembg import remove
        out = remove(image.convert("RGB"), session=_rembg_session())
        return out.convert("RGBA")
    raise ValueError(f"unsupported VISION_SEGMENTATION: {mode}")


def _dominant_color(image: Image.Image, cutout: Image.Image | None) -> str:
    # 누끼가 있으면 의류 픽셀만, 아니면 중앙을 샘플링 (배경 영향 가능).
    source = cutout if cutout is not None else image.convert("RGBA")
    source.thumbnail((256, 256))
    rgb = np.asarray(source.convert("RGB"), dtype=np.uint8)
    alpha = np.asarray(source.getchannel("A"), dtype=np.uint8)
    if cutout is None:
        h, w = rgb.shape[:2]
        sample = rgb[h // 4 : 3 * h // 4, w // 4 : 3 * w // 4].reshape(-1, 3)
    else:
        sample = rgb[alpha > 180]
    if len(sample) < 20:
        return "unknown"
    # 너무 밝은 하이라이트/어두운 그림자 영향을 줄이기 위한 중앙값.
    r, g, b = np.median(sample, axis=0).astype(int).tolist()
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    if v < 0.16:
        name = "black"
    elif s < 0.12:
        name = "white" if v > 0.89 else "gray"
    elif v < 0.40 and s < 0.34:
        name = "gray"
    elif 0.07 <= h < 0.19 and s < 0.35 and v > 0.70:
        name = "beige"
    elif 0.045 <= h < 0.14 and v < 0.68:
        name = "brown"
    elif h < 0.042 or h >= 0.96:
        name = "red"
    elif h < 0.10:
        name = "orange"
    elif h < 0.17:
        name = "yellow"
    elif h < 0.46:
        name = "green"
    elif h < 0.53:
        name = "sky_blue"
    elif h < 0.72:
        name = "navy" if v < 0.46 else "blue"
    elif h < 0.82:
        name = "purple"
    elif h < 0.96:
        name = "pink"
    else:
        name = "unknown"
    return name if name in get_args(Color) else "unknown"


def extract(image_path: str, garment_id: str) -> VisionResult:
    """FastAPI와 합의한 시그니처. 입력은 저장된 이미지 절대 경로."""
    version = f"{VERSION}/{_classifier()}/{_segmentation()}"
    try:
        path = Path(image_path)
        if not path.is_file():
            return VisionResult(success=False, model_version=version, error="image file not found")
        with Image.open(path) as raw:
            raw.verify()
        with Image.open(path) as raw:
            image = ImageOps.exif_transpose(raw).convert("RGB")
        if image.width > 2048 or image.height > 2048:
            image.thumbnail((2048, 2048))
        category, subcategory, model_name = _predict(image)
        cutout = None
        try:
            cutout = _get_cutout(image)
        except Exception as exc:
            log.warning("cutout unavailable: %s", exc)
        cutout_relative = None
        # 오직 백엔드가 넘긴 임시 작업폴더에 작성 (실제 storage를 건드리지 않음).
        if cutout is not None and path.parent.parent != path.parent:
            target = path.parent / "cutout.png"
            cutout.save(target, format="PNG")
            cutout_relative = f"{path.parent.parent.name}/{garment_id}/cutout.png"
        attrs = GarmentAttributes(
            garment_id=garment_id,
            category=category,
            subcategory=subcategory,
            color=_dominant_color(image, cutout),
            pattern="unknown",
            style_candidates=[],
            thermal_level=None,
            cutout_path=cutout_relative,
        )
        return VisionResult(success=True, attributes=attrs, model_version=f"{version}/{model_name}")
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        return VisionResult(success=False, model_version=version, error=f"invalid image or settings: {exc}")
    except Exception as exc:
        log.exception("vision extraction failed")
        return VisionResult(success=False, model_version=version, error=f"{type(exc).__name__}: {exc}")
