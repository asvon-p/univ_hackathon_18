"""Run the existing U2-Net ONNX weights without rembg's optional matting/JIT imports.

Uses the U2-Net normalization/mask convention documented by rembg's U2netSession.
Model: https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx
No user images leave this process. No native-library policy is changed.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import urlopen

import numpy as np
from PIL import Image

MODEL_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx"
MODEL_SHA256 = "8d10d2f3bb75ae3b6d527c77944fc5e7dcd94b29809d47a739a7a728a912b491"
MAX_MODEL_BYTES = 200_000_000


def _verify(path: Path) -> None:
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != MODEL_SHA256:
        raise ValueError("U2-Net model checksum mismatch; existing file was left unchanged")


def model_path() -> Path:
    home = Path(os.getenv("U2NET_HOME", Path(__file__).resolve().parents[1] / ".demo/vision-runtime/rembg"))
    target = home / "models/u2net/u2net.onnx"
    for existing in (target, home / "u2net.onnx"):
        if existing.is_file():
            _verify(existing)
            return existing
    # New installations only: a fixed official URL and pinned hash; no image upload.
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(dir=target.parent, prefix="u2net-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            with urlopen(MODEL_URL, timeout=60) as response:  # noqa: S310 - fixed HTTPS URL
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_MODEL_BYTES:
                        raise ValueError("U2-Net download exceeds expected size")
                    stream.write(chunk)
        _verify(temporary)
        temporary.replace(target)
        return target
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def input_tensor(image: Image.Image) -> np.ndarray:
    pixels = np.asarray(image.convert("RGB").resize((320, 320), Image.Resampling.LANCZOS), dtype=np.float64)
    pixels /= max(float(pixels.max()), 1e-6)
    pixels = (pixels - np.array([0.485, 0.456, 0.406])) / np.array([0.229, 0.224, 0.225])
    return np.ascontiguousarray(pixels.transpose(2, 0, 1)[None], dtype=np.float32)


def output_mask(prediction: np.ndarray, size: tuple[int, int]) -> Image.Image:
    values = np.asarray(prediction, dtype=np.float32)
    if values.shape != (1, 1, 320, 320) or not np.isfinite(values).all():
        raise ValueError("Invalid U2-Net output")
    low, high = float(values.min()), float(values.max())
    if high - low <= 1e-8:
        raise ValueError("U2-Net produced an empty or constant mask")
    normalized = ((values[0, 0] - low) / (high - low)).clip(0, 1)
    return Image.fromarray((normalized * 255).astype(np.uint8)).resize(size, Image.Resampling.LANCZOS)


class U2NetCutout:
    def __init__(self):
        import onnxruntime as ort

        options = ort.SessionOptions()
        # Leave CPU capacity/GPU memory for simultaneous web traffic and the try-on server.
        options.intra_op_num_threads = min(4, os.cpu_count() or 1)
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(model_path()), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self.input_name = self.session.get_inputs()[0].name
        self.output_name = self.session.get_outputs()[0].name

    def remove(self, image: Image.Image) -> Image.Image:
        rgb = image.convert("RGB")
        prediction = self.session.run([self.output_name], {self.input_name: input_tensor(rgb)})[0]
        mask = output_mask(prediction, rgb.size)
        # Preserve straight RGB at soft boundaries instead of darkening it twice with alpha.
        result = rgb.convert("RGBA")
        result.putalpha(mask)
        return result
