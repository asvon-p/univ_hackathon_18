"""Vision 연결부 (팀원 B).

- MockVision : 파일 이름에 들어 있는 단어로 속성을 추정 (예: "top_navy_sweater.jpg")
- LocalVision: vision.extract() 를 호출 (GPU 사용, 서버 시작 시 warmup)
- SafeVision : 위 구현을 감싸서 시간 제한·예외·결과 검증·GPU 동시 실행 제한을 담당
"""

import importlib
import logging
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import get_args

from backend.app.adapters._timeout import AdapterTimeout, run_with_timeout
from backend.app.adapters.base import VisionOutcome, VisionPort
from shared.contracts import Category, Color, GarmentAttributes, Pattern, StyleTag, VisionResult

log = logging.getLogger(__name__)

_CATEGORY_WORDS = {
    "top": "top",
    "shirt": "top",
    "tee": "top",
    "sweater": "top",
    "hoodie": "top",
    "bottom": "bottom",
    "pants": "bottom",
    "jeans": "bottom",
    "skirt": "bottom",
    "slacks": "bottom",
    "outer": "outerwear",
    "coat": "outerwear",
    "jacket": "outerwear",
    "padding": "outerwear",
    "shoes": "shoes",
    "sneakers": "shoes",
    "boots": "shoes",
    "loafers": "shoes",
}


class MockVision:
    name = "mock"

    def extract(self, image_path: str, garment_id: str) -> VisionResult:
        words = Path(image_path).stem.lower().replace("-", "_").split("_")
        category = next((_CATEGORY_WORDS[w] for w in words if w in _CATEGORY_WORDS), "top")
        color = next((w for w in words if w in get_args(Color)), "unknown")
        pattern = next((w for w in words if w in get_args(Pattern)), "unknown")
        styles = [w for w in words if w in get_args(StyleTag)] or ["casual"]
        subcategory = next((w for w in words if w in _CATEGORY_WORDS and w != category), None)
        return VisionResult(
            success=True,
            attributes=GarmentAttributes(
                garment_id=garment_id,
                category=category,
                subcategory=subcategory,
                color=color,
                pattern=pattern,
                style_candidates=styles,
            ),
            model_version="mock-1",
        )


class LocalVision:
    """팀원 B 의 vision 패키지를 그대로 호출합니다."""

    name = "local"

    def __init__(self, module_name: str = "vision", **options):
        self._module = importlib.import_module(module_name)
        if options:
            self._module = self._module.create_pipeline(**options)

    def warmup(self) -> None:
        warm = getattr(self._module, "warmup", None)
        if callable(warm):
            log.info("vision.warmup() 실행 (모델 로딩)")
            warm()

    def extract(self, image_path: str, garment_id: str) -> VisionResult:
        return self._module.extract(image_path, garment_id)


class SafeVision:
    """어떤 구현이 와도 예외를 밖으로 내보내지 않고 VisionResult 로 돌려줍니다."""

    def __init__(self, inner: VisionPort, timeout_sec: float):
        self.inner = inner
        self.name = inner.name
        self.timeout_sec = timeout_sec
        self._gpu_lock = threading.Lock()  # GPU 모델은 한 번에 하나만 실행

    def warmup(self) -> None:
        warm = getattr(self.inner, "warmup", None)
        if callable(warm):
            warm()

    def _locked_extract(self, image_path: str, garment_id: str) -> VisionResult:
        with self._gpu_lock:
            return self.inner.extract(image_path, garment_id)

    def extract(self, image_path: str, garment_id: str) -> VisionOutcome:
        start = time.perf_counter()
        try:
            raw = run_with_timeout(self._locked_extract, self.timeout_sec, image_path, garment_id)
            result = self._validate(raw, garment_id)
        except AdapterTimeout as e:
            result = VisionResult(success=False, error=f"timeout: {e}")
        except Exception as e:  # noqa: BLE001 - 팀원 코드의 어떤 예외도 서비스 장애로 번지지 않게
            log.exception("vision.extract 실패")
            result = VisionResult(success=False, error=f"{type(e).__name__}: {e}")
        latency = int((time.perf_counter() - start) * 1000)
        if not result.success:
            log.warning("vision 분석 실패 (%s): %s", self.name, result.error)
        return VisionOutcome(result=result, provider=self.name, latency_ms=latency)

    def extract_isolated(
        self, image: bytes, user_id: str, garment_id: str, max_bytes: int
    ) -> tuple[VisionOutcome, bytes | None]:
        """업로드·재분석을 복사본에서 실행하며 timeout 후에도 작업 스레드가 임시 폴더를 정리합니다."""
        start = time.perf_counter()
        cutout = None
        try:
            result, cutout = run_with_timeout(
                self._isolated_extract, self.timeout_sec, image, user_id, garment_id, max_bytes
            )
        except AdapterTimeout as exc:
            result = VisionResult(success=False, error=f"timeout: {exc}")
        except Exception as exc:
            log.exception("vision 복사본 분석 실패")
            result = VisionResult(success=False, error=f"{type(exc).__name__}: {exc}")
        latency = int((time.perf_counter() - start) * 1000)
        return VisionOutcome(result=result, provider=self.name, latency_ms=latency), cutout

    def _isolated_extract(
        self, image: bytes, user_id: str, garment_id: str, max_bytes: int
    ) -> tuple[VisionResult, bytes | None]:
        # 임시 폴더의 수명은 응답 timeout이 아니라 실제 모델 작업 종료까지입니다.
        with TemporaryDirectory(prefix="fitjang-reanalysis-") as root:
            folder = Path(root) / user_id / garment_id
            folder.mkdir(parents=True)
            original = folder / "original.jpg"
            original.write_bytes(image)
            result = self._validate(self._locked_extract(str(original), garment_id), garment_id)
            expected = f"{user_id}/{garment_id}/cutout.png"
            if not result.success or result.attributes is None or result.attributes.cutout_path != expected:
                return result, None
            path = folder / "cutout.png"
            try:
                if not path.resolve().is_relative_to(Path(root).resolve()):
                    return result, None
                with path.open("rb") as stream:
                    data = stream.read(max_bytes + 1)
                return result, data if len(data) <= max_bytes else None
            except OSError:
                return result, None

    @staticmethod
    def _validate(raw, garment_id: str) -> VisionResult:
        if isinstance(raw, dict):
            raw = VisionResult.model_validate(raw)
        if not isinstance(raw, VisionResult):
            return VisionResult(success=False, error=f"잘못된 반환 타입: {type(raw).__name__}")
        if raw.success:
            if raw.attributes is None:
                return VisionResult(success=False, model_version=raw.model_version, error="attributes 없음")
            if raw.attributes.category not in get_args(Category):
                return VisionResult(success=False, model_version=raw.model_version, error="category 없음")
            # garment_id 는 서버가 정한 값으로 고정
            raw.attributes.garment_id = garment_id
        return raw
