"""연결: 코디 추천·피드백 ↔ A의 feat/recommendation-rules·ranking, A+C의 feat/recommendation-feedback
(상태: 미완성 · Mock으로 동작. 화면(recommendation_ui.py)도 아직 만들기 전)

백엔드 Mock에 이미 있음 (backend/app/main.py) — 백엔드 안에서 A의 recommend()를 부름:
POST /recommendations                       JSON UserContext          → 200 RecommendationResult (코디 최대 3개)
POST /recommendations/{id}/feedback          JSON FeedbackEvent        → 200

TODO(C 확인): UserContext.user_id를 body로 보내게 돼 있음 → 로그인(JWT) 후에는 토큰에서 꺼내는 게 맞음
TODO(결정 필요): UserContext.temperature_c(필수) — 사용자가 고를지, 날씨 API로 채울지
TODO(화면): OutfitItems는 garment_id만 오므로 garment_storage.list_garments()와 맞춰서 사진·이름 표시
"""

from __future__ import annotations

import asyncio
import uuid

from shared.contracts import (
    FeedbackEvent,
    Outfit,
    OutfitItems,
    RecommendationResult,
    UserContext,
)

from . import USE_MOCK, request
from .garment_storage import mock_closet


async def recommend(token: str, context: UserContext) -> RecommendationResult:
    if USE_MOCK:
        return await _mock_recommend(token, context)
    return RecommendationResult(
        **await request(
            "POST", "/recommendations", token=token, json=context.model_dump()
        )
    )


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
    by_cat = {
        c: [g.garment_id for g in usable if g.category == c]
        for c in ("top", "bottom", "shoes", "outerwear")
    }
    outfits: list[Outfit] = []
    for i in range(3):
        if not (by_cat["top"] and by_cat["bottom"] and by_cat["shoes"]):
            break
        pick = {c: ids[i % len(ids)] if ids else None for c, ids in by_cat.items()}
        items = OutfitItems(
            top=pick["top"],
            bottom=pick["bottom"],
            shoes=pick["shoes"],
            outerwear=pick["outerwear"] if context.temperature_c < 15 else None,
        )
        if any(o.items == items for o in outfits):
            break
        outfits.append(
            Outfit(
                outfit_id=str(uuid.uuid4()),
                items=items,
                reason="(Mock) 입을 수 있는 옷으로 만든 조합",
            )
        )
    return RecommendationResult(recommendation_id=str(uuid.uuid4()), outfits=outfits)
