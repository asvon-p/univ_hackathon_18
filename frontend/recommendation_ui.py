"""feat/recommendation-ui (D) — 코디 추천 요청·결과 화면.  (상태: 아직 만들기 전, 자리만)

다른 팀원과 연결되는 부분은 connect/recommendation.py 에만 있습니다:
- recommend(token, UserContext) → RecommendationResult   ↔ A feat/recommendation-rules·ranking (백엔드 C 경유)
- send_feedback(token, FeedbackEvent)                    ↔ A+C feat/recommendation-feedback

TODO(D): 요청 폼(기온·상황·스타일·제외할 옷), 결과 카드(최대 3개, 옷 사진은 garment_art 매크로 재사용), 피드백 버튼
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .common import current_user, go_login, render

router = APIRouter()


@router.get("/recommend", response_class=HTMLResponse)
async def recommend_page(request: Request):
    if current_user(request) is None:
        return go_login(request)
    return render(
        request,
        "placeholder.html",
        active="recommend",
        title="코디 추천",
        message="코디 추천 화면은 다음 작업에서 만들어요.",
        back="/wardrobe",
    )
