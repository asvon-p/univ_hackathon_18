"""설정(.env)에 따라 구현을 고릅니다. 구현 교체 = .env 값 변경.

    VISION_PROVIDER=mock|local
    RECOMMENDER_PROVIDER=mock|local|openai
    WEATHER_PROVIDER=manual|open_meteo

새 구현(예: RemoteVision)을 추가하면 아래 딕셔너리에 한 줄만 추가하면 됩니다.
테스트에서는 app.dependency_overrides[get_vision] = ... 로 바꿔 끼웁니다.
"""

from functools import lru_cache

from backend.app.adapters.base import WeatherPort
from backend.app.adapters.openai_recommender import LocalFallback, OpenAIRecommender
from backend.app.adapters.recommender import LocalRecommender, MockRecommender, SafeRecommender
from backend.app.adapters.vision import LocalVision, MockVision, SafeVision
from backend.app.adapters.weather import ManualWeather, OpenMeteoWeather
from backend.app.core.config import get_settings

VISION_IMPLS = {"mock": MockVision, "local": LocalVision}
RECOMMENDER_IMPLS = {"mock": MockRecommender, "local": LocalRecommender}


@lru_cache
def get_vision() -> SafeVision:
    s = get_settings()
    return SafeVision(VISION_IMPLS[s.VISION_PROVIDER](), timeout_sec=s.VISION_TIMEOUT_SEC)


@lru_cache
def get_recommender() -> SafeRecommender:
    s = get_settings()
    if s.RECOMMENDER_PROVIDER == "openai":
        return SafeRecommender(
            OpenAIRecommender(s),
            timeout_sec=min(s.RECOMMEND_TIMEOUT_SEC, s.OPENAI_TIMEOUT_SEC + 1),
            fallback=LocalFallback(),
        )
    return SafeRecommender(RECOMMENDER_IMPLS[s.RECOMMENDER_PROVIDER](), timeout_sec=s.RECOMMEND_TIMEOUT_SEC)


@lru_cache
def get_weather() -> WeatherPort:
    settings = get_settings()
    return OpenMeteoWeather(settings) if settings.WEATHER_PROVIDER == "open_meteo" else ManualWeather()


def warmup_all() -> None:
    """서버 시작 시 1회: 모델 로딩 등."""
    get_vision().warmup()
    get_recommender().warmup()
    get_weather()


def reset() -> None:
    for f in (get_vision, get_recommender, get_weather):
        f.cache_clear()
