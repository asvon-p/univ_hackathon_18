# recommendation — 팀원 A

2026-11-08: `feat/recommendation-ranking@0ba94be`의 `rules.py`/`ranking.py`를 연결했습니다.
`recommend_top3(garments, context)`의 평면 결과를 아래 진입점에서 공통 계약으로 변환합니다.
새 추천은 상의·하의 필수, 아우터 선택이며 신발은 제외합니다. [통합 계약](../docs/TEAM_API_CONTRACT.md).

백엔드는 `recommendation/__init__.py`의 아래 함수만 호출합니다. **이름·인자·반환 타입은 바꾸지 마세요.**

```python
def recommend(context: UserContext, garments: list[GarmentAttributes]) -> RecommendationResult: ...
def warmup() -> None: ...  # 선택: 있으면 서버 시작 시 1회 호출
```

## local 추천 점수 (2026-11-11)

2026-11-12 계약 2.1: `context.required_garment_ids`를 후보 생성 **이전**에 적용합니다.
상의·하의·아우터 각 1벌까지 고정하며 필수 아우터가 있으면 아우터 없는 후보를 만들지 않습니다.
순위 상위 3개를 고른 뒤 필수 의류를 검사하는 방식은 사용하지 않습니다.
중복 슬롯·미보유·제외 충돌이면 빈 결과를 반환하고, 백엔드가 사용자에게 오류를 전달합니다.
날씨 필터가 모든 후보를 제거한 경우 백엔드의 대체 추천도 필수 의류를 유지합니다.


날씨 필터를 통과한 조합에 색상·상황·선호 스타일 점수를 적용하고 상위 3개를 반환합니다.

| 조건 | 색상 | 상황 | 선호 스타일 | 합계 |
|---|---:|---:|---:|---:|
| 지원하는 선호 태그 선택 | 최대 28 | 최대 42 | 최대 30 | 최대 100 |
| 선호 미선택 / 지원하지 않는 자유 입력만 있음 | 최대 40 | 최대 60 | 적용 안 함 | 최대 100 |

- 선호가 있으면 기존 색상·상황 점수 합계에 0.7을 곱하고 선호 일치 비율 × 30을 더합니다. 기존 색상:상황의 4:6 비율은 유지합니다.
- 선호 일치 비율은 **실제로 입는 상의·하의·아우터 중 선호 태그가 하나 이상 겹치는 의류 수 / 의류 수**입니다. 아우터가 없으면 상의·하의만 평가합니다. 태그가 없는 의류는 미일치로 봅니다.
- 복수 선호는 동등한 대안입니다. 태그 개수·중복 선호·아우터 추가 자체에 별도 가산점을 주지 않습니다. 점수는 0~100 범위, 소수 둘째 자리까지 반환합니다.
- 선호와 무관하게 기존 날씨·사용 가능 상태·제외 의류 필터를 먼저 적용합니다. 선호로 필터를 우회할 수 없습니다.
- 지원 태그는 `shared.contracts.StyleTag`의 9종입니다. 영문 태그의 공백·대소문자는 정규화합니다. 프론트는 알려진 한글 스타일 이름을 영문 태그로 변환합니다. `꾸안꾸` 같은 임의 문장을 local 엔진이 의미 추론하지는 않습니다. 알 수 없는 단어를 섞어도 알려진 선호의 점수를 희석하지 않습니다.
- 요청의 `preferred_styles`가 비어 있으면 백엔드가 프로필 선호를 사용합니다. 요청에 선택한 선호가 있으면 그 요청에만 우선 적용하고 프로필은 바꾸지 않습니다. 미선택 점수를 사용하려면 프로필 선호도 비어 있어야 합니다.
- 예: 색상·일상 상황 점수가 같은 스트리트/미니멀 옷장에서는 `minimal` 선택 시 미니멀 상·하의가 100점, 한 벌만 일치하면 85점입니다. `street`로 바꾸면 스트리트 상·하의가 1위가 됩니다.
- 면접 적합도가 모두 맞는 비선호 코디(70점)는 색상은 같지만 면접에 맞지 않는 선호 코디(58점)보다 우선합니다. 선호만으로 상황 점수를 무조건 뒤집지는 않습니다.
- 동점은 기존 후보 생성 순서를 유지합니다. 점수는 휴리스틱 적합도이며 착장 성공 확률이나 학습된 개인화 점수가 아닙니다. 피드백 기반 가중치 학습은 아직 적용하지 않습니다.
- 홈 추천 캐시는 프로필 선호·의류 ID/카테고리/색상/스타일/보온도·실제 기온이 같을 때만 재사용합니다. 프로필을 읽지 못하면 캐시를 사용하지 않습니다. 이미 DB에 저장된 추천의 점수·순위는 다시 계산하지 않습니다.

검증은 `backend/tests/test_local_ranking.py`, `test_home_recommendation_cache.py`,
`backend/tests/e2e/test_local_style_ranking.py`에 있습니다. E2E는 임시 PostgreSQL 스키마와 별도 이미지 저장소에서
실제 local 엔진·JWT·프론트 폼·저장/재조회·타 사용자 접근 차단을 검증합니다. 날씨 HTTP는 고정 응답을 사용하고
Vision은 demo/flat으로 실행해 추천 순위 검증을 외부 API·모델 추론과 분리합니다.

2026-11-11 검증: API·어댑터·계약·추천/캐시 단위 테스트·MVP/프론트 E2E **289 passed, 0 failed, 0 skipped**.
기존 `ui_preview_local`, `mentor_demo_v1`의 전체 테이블 행·저장 이미지 해시는 테스트 전후 동일했습니다.
DB 구조 변경이나 데이터 마이그레이션은 필요하지 않습니다.

선호 순위 검증 재실행 (`univ18` 활성화, PostgreSQL 실행 상태):

```powershell
$env:FITJANG_SKIP_DOTENV = "1"
$env:TEST_POSTGRES = "1"
$env:TEST_DATABASE_URL = "postgresql+psycopg://fitjang:fitjang@127.0.0.1:5432/fitjang_test"
python -m pytest backend/tests/test_local_ranking.py backend/tests/test_home_recommendation_cache.py backend/tests/e2e/test_local_style_ranking.py
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

## 하루 일정 추천

`UserContext.day_plan`이 있으면 서버가 확인한 최대 10개 일정의 길이·목적·실내외·수동 기온과
`outerwear_policy`를 평가합니다. 기존 현재 기온의 의류별 필터 대신 조합과 아우터 착용 구간을 비교합니다.
필수 슬롯을 먼저 고정하며, 대체 추천도 동일 조건을 적용합니다. 날짜/예보를 추정하지 않습니다.
API, 점수식, 한계와 검증은 [하루 일정 구현 기록](../docs/DAY_PLANS.md)을 확인하세요.
