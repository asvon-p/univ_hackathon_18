"""연결: 코디 추천·피드백 ↔ A의 feat/recommendation-rules·ranking, A+C의 feat/recommendation-feedback
(상태: 미완성 · Mock으로 동작)

백엔드 Mock에 이미 있음 (backend/app/main.py) — 백엔드 안에서 A의 recommend()를 부름:
POST /recommendations                       JSON UserContext          → 200 RecommendationResult (코디 최대 3개)
POST /recommendations/{id}/feedback          JSON FeedbackEvent        → 200

⚠ 신발은 추천에서 뺌 (2026-11-07 D 결정). contracts.py의 OutfitItems.shoes는 아직 "필수"라서
   프론트는 아래 OutfitItems/Outfit/RecommendationResult로 shoes 없이도 읽음.
   C에게 요청: OutfitItems.shoes를 선택(Optional)으로 바꾸거나 지우기 → 그러면 이 파일의 모델은 contracts.py 것으로 교체.

TODO(C 확인): UserContext.user_id를 body로 보내게 돼 있음 → 로그인(JWT) 후에는 토큰에서 꺼내는 게 맞음
"""

from __future__ import annotations

import asyncio
import uuid

from pydantic import BaseModel, Field

from shared.contracts import FeedbackEvent, UserContext

from . import USE_MOCK, request
from .garment_storage import mock_closet


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


class RecommendationResult(BaseModel):
    recommendation_id: str
    outfits: list[Outfit] = Field(default_factory=list, max_length=3)


async def recommend(token: str, context: UserContext) -> RecommendationResult:
    if USE_MOCK:
        return await _mock_recommend(token, context)
    data = await request("POST", "/recommendations", token=token, json=context.model_dump())
    return RecommendationResult(**data)


async def send_feedback(token: str, event: FeedbackEvent) -> None:
    if USE_MOCK:
        _MOCK_FEEDBACK.append(event)
        return
    await request(
        "POST",
        f"/recommendations/{event.recommendation_id}/feedback",
        token=token,
        json=event.model_dump(),
    )


# ---- Mock: 입을 수 있는 옷으로 단순 조합 (A의 실제 추천 규칙·LLM과 무관) -----------
_MOCK_FEEDBACK: list[FeedbackEvent] = []


async def _mock_recommend(token: str, context: UserContext) -> RecommendationResult:
    await asyncio.sleep(0.6)
    usable = [
        g
        for g in mock_closet(token)
        if g.status == "available" and g.garment_id not in context.excluded_items
    ]
    by_cat = {c: [g.garment_id for g in usable if g.category == c] for c in ("top", "bottom", "outerwear")}
    tops, bottoms, outers = by_cat["top"], by_cat["bottom"], by_cat["outerwear"]
    pairs = [(t, b) for b in bottoms for t in tops][:3]  # 상의·하의 조합 앞에서 3개
    outfits = [
        Outfit(
            outfit_id=str(uuid.uuid4()),
            items=OutfitItems(
                top=t,
                bottom=b,
                outerwear=outers[i % len(outers)] if outers and context.temperature_c < 15 else None,
            ),
            reason=f"(Mock) {context.temperature_c:g}°C에 입을 수 있는 옷으로 만든 조합",
        )
        for i, (t, b) in enumerate(pairs)
    ]
    return RecommendationResult(recommendation_id=str(uuid.uuid4()), outfits=outfits)
