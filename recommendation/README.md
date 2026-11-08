# recommendation — 팀원 A

2026-11-08: `feat/recommendation-ranking@0ba94be`의 `rules.py`/`ranking.py`를 연결했습니다.
`recommend_top3(garments, context)`의 평면 결과를 아래 진입점에서 공통 계약으로 변환합니다.
새 추천은 상의·하의 필수, 아우터 선택이며 신발은 제외합니다. [통합 계약](../docs/TEAM_API_CONTRACT.md).

백엔드는 `recommendation/__init__.py`의 아래 함수만 호출합니다. **이름·인자·반환 타입은 바꾸지 마세요.**

```python
def recommend(context: UserContext, garments: list[GarmentAttributes]) -> RecommendationResult: ...
def warmup() -> None: ...  # 선택: 있으면 서버 시작 시 1회 호출
```

## 백엔드가 해 주는 것
- `garments`에는 로그인한 사용자의 **실제 DB 옷장 중 분석 완료·사용 가능·제외되지 않은 옷**만 들어옵니다. 방어적으로 `status == "available"`이고 `excluded_items`에 없는 옷만 써 주세요.
- 결과를 다시 검증합니다: 미보유·세탁 중·제외 옷, 슬롯과 category 불일치, 중복 조합은 제거, 최대 3개.
- 시간 초과(`RECOMMEND_TIMEOUT_SEC`, 기본 30초)·예외·유효한 결과 0개 → 규칙 기반 **대체 추천**으로 응답 (`is_fallback=true`).
- `recommendation_id`, `outfit_id`는 서버가 DB 저장 시 새 UUID로 다시 발급합니다. 반환 ID가 이전 요청과 같아도 DB 충돌을 일으키지 않습니다.
- `context.user_id`는 검증된 토큰에서 가져오며 `preferred_styles`를 요청에서 비우면 프로필 선호를 사용합니다.
- 계약 **1.1**은 `UserContext.recent_feedback: list[FeedbackEvent]`를 기본값 `[]`로 추가합니다. 본인의 최근 20건을 생성 시각/ID 내림차순으로 전달하며 각 항목은 `{recommendation_id, outfit_id, accepted, reason}`입니다. 삭제된 추천/코디, 타인, 익명화된 로그는 제외합니다.
- 같은 코디에 여러 이벤트가 있을 수 있으므로 선호 해석은 최신 이벤트부터 판단하세요. 현재 A의 색상·상황 점수는 피드백을 반영하지 않습니다. GPT는 후보와 현재 선호만 사용하며 피드백 자유 문장은 외부에 보내지 않습니다. A는 DB에 직접 접근하지 않고 context로 개인화를 확장할 수 있습니다.
- 피드백은 먼저 DB에 저장되고 다음 추천 요청에서 전달됩니다. 별도 `on_feedback` 콜백을 호출하지 않으므로 엔진 오류가 피드백 저장을 방해하지 않습니다.
- 저장 직전에 DB에서 가용 상태를 다시 읽어 검증하므로, 모델 처리 중 세탁 상태·속성이 바뀌면 결과 일부를 제거하거나 기본 추천으로 대체할 수 있습니다.
- 입력 모델은 복사해서 전달합니다. 입력을 변경해도 서버의 검증 기준과 저장할 요청 조건은 바뀌지 않습니다. `score`에는 NaN/Infinity를 반환하지 마세요.

## 지켜 주세요
- `outfits`는 **최대 3개** (계약에서 4개 이상은 거부됩니다).
- 각 슬롯에는 해당 category 의 옷만: `top`←top, `bottom`←bottom, `outerwear`←outerwear 또는 `None`.
- `shoes`는 과거 조회 호환 필드이며 새 추천에서는 None입니다. 기존 신발 데이터는 보존합니다.
- LLM 호출은 백엔드 `openai` 어댑터에서 처리합니다. `OPENAI_API_KEY` 우선, 기존 `LLM_API_KEY` 호환.
- OpenAI HTTP 제한은 기본 6초, 어댑터 응답 제한은 최대 7초이며 실패 시 A의 로컬 규칙으로 대체합니다.
- `reason`은 사용자에게 그대로 보이는 한국어 문장입니다.

## 백엔드와 연결해 보기
```powershell
# .env 에서 RECOMMENDER_PROVIDER=local 로 바꾼 뒤
.\scripts\dev.ps1 run
# http://localhost:8000/docs → POST /api/v1/recommendations
#   먼저 가입·로그인 → Authorize에 토큰 입력 → 옷 등록 후 상의·하의 카테고리를 설정
#   생성 성공은 201, GET /api/v1/recommendations/{id}로 저장된 결과 확인
#   응답의 provider 가 "local" 이면 내 코드 결과, "fallback" 이면 실패한 것 (서버 로그에 이유 출력)
pytest backend/tests/contract   # PR 전에 반드시 통과
```

## 전달받은 의존성과 univ18 비교 (2026-11-07)

팀원이 전달한 `requirements_recommend.txt`에는 아래 한 항목만 있습니다.
비교 대상은 conda `univ18` 환경의 Python 3.12.14입니다.

| 라이브러리 | 전달받은 조건 | univ18 설치 버전 | 추가 조치 |
|---|---|---|---|
| pydantic | `>=2,<3` | `2.14.3` | 조건 충족, 설치·변경 불필요 |

추가로 설치할 라이브러리는 **0개**입니다. 같은 인터프리터에서 `python -m pip check`도 `No broken requirements found.`로 통과했습니다.
이 비교에서는 패키지를 설치하거나 버전을 바꾸지 않았습니다. 새 알고리즘 코드의 import 목록은 전달받지 않았으므로, 판정 범위는 이 requirements 파일에 기재된 의존성입니다.
