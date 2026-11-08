# 상의·하의·아우터 통합 계약 (2026-11-08)

현재 계약은 `shared/contracts.py` **2.0**, DB는 **0010**입니다. A의
`feat/recommendation-ranking@0ba94be`, D의 `feat/frontend@85ddedc`를 기준으로 맞췄습니다.
기존 단계별 완료 기록보다 이 문서가 최신 계약입니다.
자동 날씨 조회와 수동 입력 대체도 연결했습니다. 요청 예시·설정·최신 검증 결과는
[날씨 연동 안내](WEATHER_INTEGRATION.md)를 참고하세요.
팀원 프론트 소스를 현재 프로젝트에 통합하고 실제 화면과 PostgreSQL 전체 281개 테스트를 검증했습니다.
현재 실행 방법·화면 검증 범위는 [프론트 통합 보고서](FRONTEND_INTEGRATION.md)를 우선 참고하세요.

## 추천 범위와 이전 데이터

- 새 추천은 **상의(top) + 하의(bottom) 필수, 아우터(outerwear) 선택**, 최대 3개입니다.
- 기존 신발 의류와 신발이 포함된 과거 추천·피드백은 삭제하지 않습니다.
  `items.shoes`는 과거 조회 호환용 nullable 필드입니다. 새 추천에서는 항상 `null`이며 엔진 입력에도 신발을 보내지 않습니다.
- A의 `recommend_top3(garments, context)`가 반환하는 평면 딕셔너리를
  `recommendation.recommend(context, garments)`에서 `RecommendationResult`로 변환합니다.
- A의 기온 필터·색상(40)·상황(60) 점수는 유지합니다. `score`는 **규칙 점수(0~100)**이며 확률이 아닙니다.
  GPT 사용 시 최종 순위는 GPT가 바꿀 수 있으므로 점수가 내림차순이 아닐 수 있습니다.
- 후보가 없거나 엔진이 실패하면 현재 보유 상의·하의로 기본 추천합니다. 이 결과는
  `provider=fallback`, `is_fallback=true`로 표시하며 날씨 적합성을 보장하지 않습니다.
- 사용자별 분당 10회 제한, 제외 의류·세탁 중·분석 미완료 제외, 저장 직전 DB 재검증과 접근 차단은 유지합니다.

## D 프론트 요청과 응답

모든 보호 API는 `Authorization: Bearer <access_token>`이 필요합니다.
프론트 서버 환경은 `BACKEND_API_URL=http://127.0.0.1:8001/api/v1`, `FRONTEND_USE_MOCK=false`입니다.

| 기능 | 계약 |
|---|---|
| `POST /auth/signup` | email/password 필수. nickname 선택(기본 `사용자`). 201에 기존 사용자 정보와 access_token/token_type/expires_in을 함께 반환. Cache-Control: no-store |
| `GET /users/me/profile` | 기존 `/profile`과 같은 본인 프로필. 키·몸무게는 소수 허용, age는 선택 정수 0~130 |
| `PUT /users/me/profile` | `/profile` PATCH와 같은 부분 갱신. age와 기존 birth_year는 독립적으로 보존하며 상호 추정하지 않음 |
| `PATCH /garments/{id}` | 기존 속성 외 name/location 각 100자 이하 또는 null. 재분석 후에도 유지 |
| `GET /garments/{id}/image?kind=cutout` | cutout/original 지원. cutout 미생성이면 원본 반환. 본인 이미지, private/no-store. 기존 `/media/...`도 유지 |
| `GET /weather?lat=...&lon=...` | 현재 기온·날씨 상태. 실패 시 503 WEATHER_UNAVAILABLE로 수동 입력 안내. 선택 temperature_c가 있으면 수동 대체 |
| `POST /recommendations` | temperature_c 또는 lat/lon 쌍 필요. 좌표 자동 조회 실패 시 temperature_c로 대체. occasion/preferred_styles/excluded_items 선택. `user_id:"me"`, `recent_feedback:[]`도 호환 |
| `GET /recommendations`, `GET /recommendations/{id}` | 본인 이력/결과. 새·과거 추천 함께 조회 가능 |
| `POST /recommendations/{id}/feedback` | 기존 FeedbackEvent 그대로. 본인 추천/코디만 허용 |

추천 요청의 `user_id`는 `"me"` 외 실제 UUID를 받지 않습니다. 값이 없거나 `"me"`여도 실제 ID는
항상 JWT에서 가져옵니다. 클라이언트의 비어 있지 않은 `recent_feedback`은 422로 거부하고
서버가 본인 피드백을 조회합니다. `preferred_styles`는 `"minimal"` 등의 코드와 `"꾸안꾸"` 같은 자유 입력을
허용합니다(최대 20개, 항목 1~100자). 의류 `style_candidates`와 결과 `style_tags`는 기존 표준 코드입니다.

```json
{
  "user_id": "me",
  "temperature_c": 18,
  "occasion": "daily",
  "preferred_styles": ["꾸안꾸"],
  "excluded_items": [],
  "recent_feedback": []
}
```

분석 실패 의류의 `category`는 공통 모델에서도 `null`을 허용합니다. 추천에는 넣지 않으며
사용자 수동 수정 또는 재분석이 필요합니다. 백엔드가 임의로 상의라고 분류하지 않습니다.

### 현재 통합 프론트의 반영 사항

1. **신체정보 동의 UI**: 체크한 경우에만 `consent_body_info:true`를 전송합니다.
   미동의 최초 저장은 422 `BODY_INFO_CONSENT_REQUIRED`이며 화면에 안내합니다.
2. 회원가입에 선택 초대 코드 입력을 추가했습니다. `SIGNUP_INVITE_CODE`를 설정한 서버와도 연결할 수 있습니다.
3. 누끼가 없는 옷도 인증된 원본 이미지로 표시하고 category=null 검색을 처리합니다.
   analysis_status를 보존해 분석 미완료 옷은 추천 가능 수에서 제외합니다.
4. 프론트 timeout을 35초로 조정했습니다. 429 오류 메시지는 표시하며 Retry-After 기반 카운트다운 UI는 후속 항목입니다.
5. `/weather` GET→기온 POST 흐름과 수동 기온 요청을 검증하고 날씨 UI에 Open-Meteo 출처 링크를 표시했습니다.
6. **현재 범위 밖**: 전신 사진 `/users/me/body-photo`, GPU 착샷 결과의
   탈퇴 후 캐시 무효화는 아직 연결되지 않았습니다. `body_photo_updated_at`은 현재 null입니다.
   현재 가입 흐름에서는 전신 사진 단계를 기본으로 숨깁니다. 착샷은 별도 2D 서비스이며 3D 모델 생성은 아닙니다.

## GPT-6 Luna 구성

백엔드 루트 `.env`에 설정합니다. 키는 브라우저/프론트 환경 또는 Git에 넣지 않습니다.

```dotenv
VISION_PROVIDER=local
RECOMMENDER_PROVIDER=openai
OPENAI_API_KEY=
OPENAI_MODEL=gpt-6-luna
OPENAI_REASONING_EFFORT=low
OPENAI_TIMEOUT_SEC=6
OPENAI_MAX_OUTPUT_TOKENS=1200
```

`OPENAI_API_KEY`가 비어 있으면 기존 `LLM_API_KEY`를 사용합니다. 둘 다 없으면 로컬 대체 추천입니다.
OpenAI Responses API + strict JSON Schema를 사용하며 추가 SDK 설치 없이 기존 httpx로 호출합니다.
A의 최대 3개 후보를 먼저 생성하고 GPT에는 후보 번호·의류 속성·기온·상황·선호만 전달합니다.
사용자 UUID, 옷 UUID, 이메일, 신체정보, 이미지, 저장 경로, 피드백 자유 문장은 전송하지 않습니다.
`store=false`, 자동 재시도 없음, 출력 토큰 제한을 적용합니다.

GPT는 후보의 순서와 한국어 이유만 반환합니다. 중복/누락/존재하지 않는 후보, 형식 오류,
refusal/incomplete, HTTP 401/429/5xx, timeout은 로컬 추천으로 대체합니다. 모델명은
[공식 GPT-6 Luna 문서](https://developers.openai.com/api/docs/models/gpt-6-luna), 형식은
[Structured Outputs 문서](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)를 기준으로 했습니다.
실제 계정의 모델 접근권한·요금·응답 지연은 아직 검증하지 않았습니다. 사용자가 만든 `.env`는
접근 금지 대상이므로 현재 키 설정 여부를 확인하지 않습니다.

```powershell
conda activate univ18
python -m alembic -c backend/alembic.ini upgrade head
.\scripts\start_demo.ps1 -Provider local -RecommendationProvider openai -Port 8001 -SkipSeed
```

이미 관리 중인 8001 서버가 있으면 `scripts/stop_demo.ps1 -Port 8001`로 먼저 종료하세요.
D는 별도 FastAPI/Jinja2 서버입니다. D 브랜치를 통합한 뒤 `frontend/requirements.txt`를 설치하고
`python -m uvicorn frontend.app:app --host 127.0.0.1 --port 3000`으로 실행합니다.
`frontend/dist` 빌드가 필요하지 않습니다.

## 마이그레이션과 검증

`0010`은 shoes_id를 nullable로 바꾸고 name/location/age를 nullable 컬럼으로 추가합니다.
키·몸무게는 smallint → double precision으로 확장합니다. 기존 행·ID·이미지·피드백을 삭제하거나 재작성하지 않습니다.
새 신발 없는 추천 또는 새 메타데이터가 존재하면 downgrade를 거부합니다. 옷을 임의 지정하거나
새 데이터를 삭제해 이전 NOT NULL 제약에 맞추지 않습니다.

```powershell
$env:FITJANG_SKIP_DOTENV = "1"
$env:TEST_POSTGRES = "1"
python -m pytest --tb=short
# D 소스가 있는 체크아웃의 frontend 폴더를 지정하면 실제 connect 모듈 E2E도 실행
$env:TEAM_FRONTEND_ROOT = "C:\path\to\team-checkout\frontend"
python -m pytest backend/tests/e2e/test_frontend_connector.py --tb=short
```

테스트는 전용 PostgreSQL 임시 스키마와 임시 이미지 저장소를 사용합니다.
GPT 테스트는 모의 HTTP 응답을 사용하므로 유료 호출을 하지 않습니다.

### 2026-11-08 자동 날씨 추가 후 검증

- 전체 PostgreSQL **279개 통과**, SQLite **260개 통과·19개 제외**.
- D 실제 connect의 날씨→추천→수동 대체 E2E와 Open-Meteo 실제 호출을 확인했습니다.
- DB head `0010` 유지, `.env` 접근 없음. 기존 시연 서버는 이번 작업에서 재시작하지 않았습니다.
- 세부 검증 범위·보고서는 [날씨 연동 안내](WEATHER_INTEGRATION.md)를 참고하세요.

### 2026-11-08 자동 날씨 추가 이전 검증 기록

- 전체 PostgreSQL **234개 통과**, 제외 없음. 실제 D connect 모듈의 TCP HTTP E2E 포함.
  프로필 동의가 없는 기존 D 요청의 거부와 명시적 동의 후 저장을 함께 검증했습니다.
- SQLite **215개 통과·19개 제외**(PostgreSQL 및 외부 D 소스 전용). 기존 Starlette/httpx 경고 1건.
- Ruff lint/format, `pip check` 통과. 신규 패키지 설치 없음.
- 개발 `fitjang`과 테스트 `fitjang_test` 모두 **0010**, ORM과 마이그레이션 일치.
- 개발 DB 계정 1·프로필 1·옷/속성 각 12·추천 6·코디 10·피드백 3의 모든 기존 값을
  적용 전후 비교해 보존했습니다. 원본/cutout **24개**의 SHA-256도 동일합니다.
- 보고서: `.pytest_cache/team-pg-final.xml`, `.pytest_cache/team-sqlite-final.xml`,
  `.demo/reports/team-contract-migration.json` (Git 제외).
- 8001 시연 서버를 seed 재생성 없이 `local`로 재시작했습니다. `/health`의 계약 2.0·DB 정상,
  기존 추천 6건/코디 10개 및 새 프로필·이미지 경로 조회를 확인했습니다. 테스트 임시 스키마 0개입니다.
- OpenAI 실제 키가 설정되지 않아 유료 API 호출·실제 모델의 추천 품질은 검증하지 않았습니다.
  테스트의 GPT 성공/실패 응답은 모의 HTTP 전송 계층을 사용합니다.
