"""팀원 B — Computer Vision.

C(Backend)는 아래 extract() 함수만 호출합니다. 시그니처는 유지하고 내부를 교체하세요.
무거운 모델 로딩은 함수 안이 아니라 모듈 최초 1회만 하도록 구현하는 것을 권장합니다.
"""

from shared.contracts import GarmentAttributes, VisionResult


def extract(image_path: str, garment_id: str) -> VisionResult:
    """임시 구현: 항상 'unknown top' 을 반환. B 가 교체합니다."""
    return VisionResult(
        success=True,
        attributes=GarmentAttributes(garment_id=garment_id, category="top"),
        model_version="stub-0",
    )
