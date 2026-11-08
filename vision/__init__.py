"""AITEEN 팀원 B: 사진 기반 의류 속성 추출.

백엔드의 호출 규격은 유지합니다. pipeline.py만 교체해 정확도를 개선할 수 있습니다.
"""

from shared.contracts import VisionResult

from .pipeline import create_pipeline, warmup
from .pipeline import extract as _extract


def extract(image_path: str, garment_id: str) -> VisionResult:
    return _extract(image_path, garment_id)


__all__ = ["extract", "warmup", "create_pipeline"]
