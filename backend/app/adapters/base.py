"""백엔드가 A·B·외부 서비스에 기대하는 모양(포트).

서비스·라우터는 이 Protocol 만 알고, 실제 구현(mock/local/remote)은 registry 가 .env 값으로 고릅니다.
"""

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from backend.app.schemas.weather import WeatherResponse
from shared.contracts import GarmentAttributes, RecommendationResult, UserContext, VisionResult


@runtime_checkable
class VisionPort(Protocol):
    name: str

    def extract(self, image_path: str, garment_id: str) -> VisionResult: ...


@runtime_checkable
class RecommenderPort(Protocol):
    name: str

    def recommend(self, context: UserContext, garments: list[GarmentAttributes]) -> RecommendationResult: ...


@runtime_checkable
class WeatherPort(Protocol):
    name: str

    def current(self, lat: float, lon: float) -> WeatherResponse | None: ...


@dataclass
class VisionOutcome:
    result: VisionResult
    provider: str
    latency_ms: int


@dataclass
class RecommendOutcome:
    result: RecommendationResult
    provider: str  # 실제로 결과를 만든 구현 (mock / local / fallback)
    is_fallback: bool
    latency_ms: int
    dropped: list[str] = field(default_factory=list)  # 검증에서 제거된 outfit_id 와 사유
