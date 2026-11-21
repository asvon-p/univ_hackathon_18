"""추천 생성 POST(요청 키)와 저장 결과·진행 상태 GET을 분리하는 연결 계층.

실제 모드는 JWT 소유권 검사와 PostgreSQL 멱등성 기록을 사용하는 백엔드에 연결합니다.
Mock 모드에서도 생성한 결과를 사용자별로 보관해 같은 화면 흐름을 지원합니다.
"""

from __future__ import annotations

import asyncio
import uuid

from pydantic import BaseModel, Field

from shared.contracts import FeedbackEvent, ReferenceMatch, UserContext
from shared.day_plans import WearingStep
from shared.recommendation_constraints import required_by_category
from shared.wardrobes import in_wardrobe

from . import USE_MOCK, ApiError, request
from .garment_storage import Garment, mock_closet


class OutfitItems(BaseModel):
    """contracts.OutfitItems와 같지만 shoes가 선택."""

    top: str
    bottom: str
    outerwear: str | None = None
    shoes: str | None = None  # 추천에서 신발은 뺌 — 와도 화면에 안 씀


class Outfit(BaseModel):
    outfit_id: str
    items: OutfitItems
    style_tags: list[str] = Field(default_factory=list)
    reason: str = ""
    score: float | None = None
    wearing_plan: list[WearingStep] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    score_components: dict[str, float] = Field(default_factory=dict)
    policy_version: str | None = None
    reference_matches: list[ReferenceMatch] = Field(default_factory=list)


class RecommendationResult(BaseModel):
    recommendation_id: str
    forecast_token: str | None = None
    outfits: list[Outfit] = Field(default_factory=list, max_length=3)
    provider: str = "mock"
    is_fallback: bool = False
    context: UserContext | None = None
    garments: dict[str, Garment] = Field(default_factory=dict)


async def recommend(
    token: str, context: UserContext, *, request_key: str | None = None, forecast_token: str | None = None
) -> RecommendationResult:
    if USE_MOCK:
        if request_key and (token, request_key) in _MOCK_REQUESTS:
            return _MOCK_RESULTS[token, _MOCK_REQUESTS[token, request_key]]
        result = await _mock_recommend(token, context)
        result.context = context
        result.garments = {g.garment_id: g for g in mock_closet(token)}
        _MOCK_RESULTS[token, result.recommendation_id] = result
        if request_key:
            _MOCK_REQUESTS[token, request_key] = result.recommendation_id
        return result
    data = await request(
        "POST",
        "/recommendations",
        token=token,
        headers={"Idempotency-Key": request_key} if request_key else {},
        json={
            **context.model_dump(mode="json", exclude={"day_plan", "style_reference", "weather_forecast"}),
            "forecast_token": forecast_token,
        },
    )
    return RecommendationResult(**data)


async def get_result(token: str, recommendation_id: str) -> RecommendationResult:
    if USE_MOCK:
        result = _MOCK_RESULTS.get((token, recommendation_id))
        if result is None:
            raise ApiError(404, "추천 결과를 찾을 수 없어요", "RECOMMENDATION_NOT_FOUND")
        return result
    return RecommendationResult(**await request("GET", f"/recommendations/{recommendation_id}", token=token))


async def get_request(token: str, request_key: str) -> dict:
    if USE_MOCK:
        rid = _MOCK_REQUESTS.get((token, request_key))
        if rid is None:
            raise ApiError(404, "추천 요청을 찾을 수 없어요", "RECOMMENDATION_NOT_FOUND")
        return {"status": "completed", "recommendation_id": rid}
    return await request("GET", f"/recommendations/requests/{request_key}", token=token)


async def recent_results(token: str) -> list[RecommendationResult]:
    if USE_MOCK:
        return [r for (owner, _), r in reversed(list(_MOCK_RESULTS.items())) if owner == token][:20]
    data = await request("GET", "/recommendations", token=token, params={"limit": 20})
    return [RecommendationResult(**row) for row in data["items"]]


async def feedback_states(token: str, recommendation_id: str) -> dict:
    if USE_MOCK:
        events = [
            e.model_dump()
            for owner, e in reversed(_MOCK_FEEDBACK)
            if owner == token and e.recommendation_id == recommendation_id
        ]
    else:
        events = await request(
            "GET", f"/recommendations/{recommendation_id}/feedback", token=token, params={"limit": 100}
        )
    if isinstance(events, dict):
        events = events.get("items", [])
    latest = {}
    for event in events:
        latest.setdefault(event["outfit_id"], event)
    return latest


async def send_feedback(token: str, event: FeedbackEvent) -> dict:
    if USE_MOCK:
        _MOCK_FEEDBACK.append((token, event))
        return event.model_dump()
    return await request(
        "POST",
        f"/recommendations/{event.recommendation_id}/feedback",
        token=token,
        json=event.model_dump(exclude={"garments", "temperature_c"}),
    )


# ---- Mock: 입을 수 있는 옷으로 단순 조합 (A의 실제 추천 규칙·LLM과 무관) -----------
_MOCK_FEEDBACK: list[tuple[str, FeedbackEvent]] = []
_MOCK_RESULTS: dict[tuple[str, str], RecommendationResult] = {}
_MOCK_REQUESTS: dict[tuple[str, str], str] = {}


async def _mock_recommend(token: str, context: UserContext) -> RecommendationResult:
    await asyncio.sleep(0.6)
    usable = [
        g
        for g in mock_closet(token)
        if g.status == "available"
        and g.garment_id not in context.excluded_items
        and in_wardrobe(g, context.wardrobe)
    ]
    by_cat = {c: [g.garment_id for g in usable if g.category == c] for c in ("top", "bottom", "outerwear")}
    try:
        required = required_by_category(context, usable)
    except ValueError:
        raise ApiError(422, "선택한 옷을 사용할 수 없습니다", "REQUIRED_GARMENT_UNAVAILABLE") from None
    for category, garment in required.items():
        by_cat[category] = [garment.garment_id]
    tops, bottoms, outers = by_cat["top"], by_cat["bottom"], by_cat["outerwear"]
    pairs = [(t, b) for b in bottoms for t in tops][:3]  # 상의·하의 조합 앞에서 3개
    outfits = [
        Outfit(
            outfit_id=str(uuid.uuid4()),
            items=OutfitItems(
                top=t,
                bottom=b,
                outerwear=outers[i % len(outers)]
                if outers and (context.temperature_c < 15 or "outerwear" in required)
                else None,
            ),
            reason=f"(Mock) {context.temperature_c:g}°C에 입을 수 있는 옷으로 만든 조합",
        )
        for i, (t, b) in enumerate(pairs)
    ]
    return RecommendationResult(recommendation_id=str(uuid.uuid4()), outfits=outfits)
