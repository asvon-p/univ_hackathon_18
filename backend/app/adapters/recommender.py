"""Recommendation 연결부 (팀원 A).

- MockRecommender : 규칙 기반 간단 추천. A 의 코드가 실패했을 때 대체 추천으로도 사용
- LocalRecommender: recommendation.recommend() 호출
- SafeRecommender : 시간 제한·예외 처리·결과 재검증·대체 추천을 담당
"""

import importlib
import itertools
import logging
import math
import time

from pydantic import ValidationError

from backend.app.adapters._timeout import AdapterTimeout, run_with_timeout
from backend.app.adapters.base import RecommenderPort, RecommendOutcome
from shared.contracts import (
    RECOMMENDATION_CATEGORIES,
    GarmentAttributes,
    Outfit,
    OutfitItems,
    RecommendationResult,
    UserContext,
)

log = logging.getLogger(__name__)

MAX_OUTFITS = 3
OUTERWEAR_BELOW_C = 15.0  # 이 기온 미만이면 아우터 포함 (A 의 규칙이 우선, 이 값은 대체 추천용)


def usable_garments(context: UserContext, garments: list[GarmentAttributes]) -> list[GarmentAttributes]:
    excluded = set(context.excluded_items)
    return [
        g
        for g in garments
        if g.status == "available"
        and g.garment_id not in excluded
        and g.category in RECOMMENDATION_CATEGORIES
    ]


class MockRecommender:
    name = "mock"

    def recommend(self, context: UserContext, garments: list[GarmentAttributes]) -> RecommendationResult:
        usable = usable_garments(context, garments)
        by_cat = {c: [g for g in usable if g.category == c] for c in RECOMMENDATION_CATEGORIES}
        if not (by_cat["top"] and by_cat["bottom"]):
            return RecommendationResult(recommendation_id="mock", outfits=[])

        prefs = set(context.preferred_styles)
        need_outer = context.temperature_c < OUTERWEAR_BELOW_C and bool(by_cat["outerwear"])

        def style_score(*items: GarmentAttributes) -> int:
            return sum(len(prefs & set(g.style_candidates)) for g in items)

        # 점수가 슬롯별 합이므로 각 슬롯 상위 3개만으로 전체 상위 3개를 만들 수 있습니다.
        # 옷장이 커져도 대체 추천이 모든 조합을 메모리에 쌓지 않습니다.
        for category in ("top", "bottom"):
            by_cat[category] = sorted(by_cat[category], key=lambda g: -style_score(g))[:MAX_OUTFITS]
        outer = max(by_cat["outerwear"], key=lambda o: o.thermal_level or 0) if need_outer else None
        combos = []
        for top, bottom in itertools.product(by_cat["top"], by_cat["bottom"]):
            items = [g for g in (top, bottom, outer) if g]
            combos.append((style_score(*items), top, bottom, outer))
        combos.sort(key=lambda c: -c[0])

        outfits = []
        for i, (score, top, bottom, outer) in enumerate(combos[:MAX_OUTFITS], start=1):
            tags = (
                sorted(prefs & set(top.style_candidates + bottom.style_candidates))
                or top.style_candidates[:1]
            )
            outfits.append(
                Outfit(
                    outfit_id=f"mock-{i}",
                    items=OutfitItems(
                        top=top.garment_id,
                        bottom=bottom.garment_id,
                        outerwear=outer.garment_id if outer else None,
                    ),
                    style_tags=tags,
                    reason=f"{context.temperature_c:g}°C 기준 사용 가능한 옷으로 만든 기본 조합",
                    score=float(score),
                )
            )
        return RecommendationResult(recommendation_id="mock", outfits=outfits)


class LocalRecommender:
    name = "local"

    def __init__(self, module_name: str = "recommendation"):
        self._module = importlib.import_module(module_name)

    def warmup(self) -> None:
        warm = getattr(self._module, "warmup", None)
        if callable(warm):
            warm()

    def recommend(self, context: UserContext, garments: list[GarmentAttributes]) -> RecommendationResult:
        return self._module.recommend(context, garments)


class SafeRecommender:
    def __init__(self, inner: RecommenderPort, timeout_sec: float, fallback: RecommenderPort | None = None):
        self.inner = inner
        self.name = inner.name
        self.timeout_sec = timeout_sec
        self.fallback = fallback or MockRecommender()

    def warmup(self) -> None:
        warm = getattr(self.inner, "warmup", None)
        if callable(warm):
            warm()

    def recommend(self, context: UserContext, garments: list[GarmentAttributes]) -> RecommendOutcome:
        start = time.perf_counter()
        provider, is_fallback, dropped = self.name, False, []
        try:
            raw = run_with_timeout(
                self.inner.recommend,
                self.timeout_sec,
                context.model_copy(deep=True),
                [g.model_copy(deep=True) for g in garments],
            )
            result, dropped = validate_result(raw, context, garments)
            if not result.outfits:
                raise ValueError("유효한 코디 없음")
        except Exception as e:  # noqa: BLE001
            reason = "timeout" if isinstance(e, AdapterTimeout) else type(e).__name__
            log.warning("추천(%s) 실패 → 대체 추천 사용: %s", self.name, reason)
            fallback_raw = self.fallback.recommend(
                context.model_copy(deep=True), [g.model_copy(deep=True) for g in garments]
            )
            result, _ = validate_result(fallback_raw, context, garments)
            provider, is_fallback = "fallback", True
        latency = int((time.perf_counter() - start) * 1000)
        return RecommendOutcome(
            result=result, provider=provider, is_fallback=is_fallback, latency_ms=latency, dropped=dropped
        )


def validate_result(raw, context: UserContext, garments: list[GarmentAttributes]):
    """A 의 결과를 믿지 않고 서버에서 다시 확인합니다.

    - 모든 garment_id 가 요청에 넘긴 옷 중 '사용 가능한' 옷인지
    - 각 슬롯에 맞는 category 인지 (top 슬롯에 신발이 들어가면 제거)
    - 같은 조합 중복 제거, 최대 3개
    """
    if isinstance(raw, RecommendationResult):
        raw = raw.model_dump()
    if not isinstance(raw, dict) or not isinstance(raw.get("outfits"), list):
        raise TypeError(f"잘못된 반환 타입: {type(raw).__name__}")
    if not isinstance(raw.get("recommendation_id"), str):
        raise ValueError("recommendation_id 없음")

    usable = {g.garment_id: g for g in usable_garments(context, garments)}
    kept, dropped, seen = [], [], set()
    for index, item in enumerate(raw["outfits"]):
        try:
            # model_construct나 실행 중 변경된 Pydantic 객체도 새로 검증합니다.
            outfit = Outfit.model_validate(item.model_dump() if isinstance(item, Outfit) else item)
            # 구 버전 엔진의 신발 출력은 버리고 세 슬롯만 검증·저장합니다.
            outfit.items.shoes = None
            if outfit.score is not None and not math.isfinite(outfit.score):
                raise ValueError("score는 유한한 숫자여야 합니다")
        except (ValidationError, ValueError, TypeError):
            dropped.append(f"outfit[{index}]: 잘못된 코디 형식")
            continue
        slots = outfit.items.model_dump()
        problem = None
        for slot, gid in slots.items():
            if gid is None:
                continue
            g = usable.get(gid)
            if g is None:
                problem = f"{slot}={gid} 사용 불가/미보유"
            elif g.category != slot:
                problem = f"{slot} 슬롯에 {g.category}"
            if problem:
                break
        key = tuple(slots.values())
        if problem is None and key in seen:
            problem = "중복 조합"
        if problem:
            dropped.append(f"{outfit.outfit_id}: {problem}")
            continue
        seen.add(key)
        kept.append(outfit)
        if len(kept) == MAX_OUTFITS:
            break
    if dropped:
        log.warning("추천 결과 검증에서 제거: %s", dropped)
    return RecommendationResult(recommendation_id=raw["recommendation_id"], outfits=kept), dropped
