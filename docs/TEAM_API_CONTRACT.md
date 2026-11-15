# 상의·하의·아우터 통합 계약 (2026-11-14)

현재 계약은 `shared/contracts.py` **2.4**, DB는 **0024**입니다. A의
`feat/recommendation-ranking@0ba94be`, D의 `feat/frontend@85ddedc`를 기준으로 맞췄습니다.
기존 단계별 완료 기록보다 이 문서가 최신 계약입니다.

## 옷장 페이지 조회와 무한 스크롤

- `GET /api/v1/garments/page`: JWT 소유자 기준. `limit` 기본 24, 최대 60, `cursor`, `category`, `status`, `q`(최대 50자)를 받습니다.
- 응답은 `items`, `next_cursor`, `has_more`, `total`(본인 전체), `all_count`(검색·상태 적용), `matched_count`(종류까지 적용), `counts`(검색·상태 기준 종류별 개수)입니다.
- 등록 시각과 ID 내림차순 커서로 조회합니다. 필터 또는 사용자가 바뀐 커서는 422입니다. 새 옷은 새로고침 후 첫 페이지에서 보이며, 이미 읽은 옷의 삭제로 뒤 페이지가 밀리지 않습니다. 조회 중 속성이 바뀐 경우는 새로고침해 최신 필터 결과를 확인합니다.
- 검색은 이름·보관 위치·역할·형태·색·무늬·스타일의 한국어/영문과 일상 색 이름을 DB에서 비교합니다. 여러 검색어는 모두 일치해야 합니다. 공통 용어는 `shared/garment_terms.py`를 사용합니다.
- `/wardrobe`는 24벌만 먼저 렌더링하고 스크롤 끝에서 다음 묶음을 요청합니다. `더 보기`와 JavaScript 없는 다음 페이지 링크도 제공합니다. 로딩 실패 시 자동 재시도를 멈추고 수동 재시도를 안내합니다. 동시 요청 잠금과 의류 ID 중복 제거를 적용했습니다.
- 기존 `GET /api/v1/garments`와 추천의 전체 옷장 조회는 변경하지 않습니다. 첫 화면에 없는 필수 의류도 추천 대상에 포함됩니다. 페이지 API를 추천 엔진의 입력으로 사용하지 마세요.
- `0023`은 `(user_id, created_at, id)` 조회 인덱스만 추가합니다. 기존 행과 파일은 수정하지 않습니다.

2026-11-14 검증: 실제 PostgreSQL을 활성화한 API·E2E·추천·마이그레이션 테스트 **54개 통과**, 실패/건너뜀 없음. 공개 HTTPS와 로컬 모바일 크기(390px) 화면에서 70벌을 24→48→70벌로 읽고 중복 없는 표시·썸네일 로딩·전체 검색을 확인했습니다. 70벌의 목록 JSON은 기존 전체 49,573바이트, 첫 페이지 17,499바이트였습니다(이미지와 HTML 제외, 지연 시간 보장 수치가 아님).

임시 검증 계정 정리 후 기존 계정 6개·의류 39벌·추천 68건·이미지 파일 112개를 보존했습니다. Git 제외 경로 `.demo/wardrobe-pages/`에 검증 기록과 화면을 보관합니다. `.env`는 읽거나 변경하지 않았습니다.

이 변경은 최초 전송·렌더링량을 제한합니다. 수만 벌에서의 검색 집계, 끝까지 스크롤해 누적되는 DOM, 전체 옷장 추천 비용까지 일정하게 만드는 것은 아니며 그 규모에서는 추가 부하 검증이 필요합니다.

## 착용 역할과 형태

`category`는 착용 역할, `subcategory`는 옷 형태이며 서로 독립적입니다.
예: `category=outerwear, subcategory=shirt`는 상의 위에 걸치는 셔츠입니다.
옷장 조회에는 같은 값을 나타내는 `wearing_role`, `garment_type`과 `role_source`도 제공합니다.
PATCH는 새 이름 또는 기존 이름을 받으며, 업로드 multipart의 `wearing_role` 선택값은
첫 분석 전에 저장하고 재분석에서도 보호합니다. 추천·착샷 슬롯은 역할을 따릅니다.
기존 DB 컬럼과 값을 보존하며 별도 데이터 마이그레이션은 없습니다.
상세 계약·검증은 [착용 역할 분리](WEARING_ROLES.md)를 참고하세요.

참고 사진의 영역 분석·확인·내 옷장 재현과 필수 의류·일정 통합은
[저장한 스타일 재현 계약](STYLE_REFERENCES.md)을 참고하세요.
자동 날씨 조회와 수동 입력 대체도 연결했습니다. 요청 예시·설정·최신 검증 결과는
[날씨 연동 안내](WEATHER_INTEGRATION.md)를 참고하세요.
팀원 프론트 소스를 현재 프로젝트에 통합하고 실제 화면과 PostgreSQL 전체 281개 테스트를 검증했습니다.
현재 실행 방법·화면 검증 범위는 [프론트 통합 보고서](FRONTEND_INTEGRATION.md)를 우선 참고하세요.

## 하루 일정 추가

본인 일정 저장·조회·버전 수정·삭제 API `/api/v1/day-plans`를 추가했습니다.
추천 요청에 `schedule_id`와 `schedule_version`을 함께 보내면 해당 사용자의 확인된 일정만 불러옵니다.
`outerwear_policy`는 `always_worn`(기본) / `removable`입니다. 결과의 `wearing_plan`과
`limitations`는 서버에서 검증하며, GPT는 후보 순서와 판단 이유만 반환합니다.
일정별 수동 기온·목적·실내외를 반영하며, 현재 기온은 미래 예보나 실내 온도로 사용하지 않습니다.
자세한 입력, 오류, 저장·삭제 정책과 검증은 [하루 일정 구현 기록](DAY_PLANS.md)을 따릅니다.
내부 일정 사본을 프론트에서 주입할 수 없습니다. 공통 UserContext 직렬화의 `day_plan: null`만
호환용으로 허용하고, 실제 내용이 있는 `day_plan` 요청은 422로 거부합니다.

## 필수 의류 추가

추천 POST에 선택 필드 `required_garment_ids: UUID[]`를 추가했습니다. 기본 `[]`, 최대 3개이며
상의·하의·아우터에서 각각 한 벌까지 지정합니다. 후보 생성·GPT 판단·대체 추천·저장 직전 검증에서
동일하게 적용합니다. GPT6-Luna는 제공된 후보의 순위와 판단 이유만 반환합니다.

필수 의류 누락 결과는 폐기합니다. 다른 사용자/없는 의류는 404, 중복 카테고리·세탁 중·분석 미완료·제외 충돌은
422, 처리 중 필수 의류 상태·카테고리 변경은 409입니다. 새 필드가 없는 과거 추천은 빈 배열로 조회합니다.
기존 JSON 문맥을 사용하므로 이번 기능에 새 DB migration은 필요하지 않습니다.

프론트는 `/recommend?required=<id>`로 옷을 미리 선택할 수 있습니다. 결과 화면의 조건 바꾸기와 입력 오류에도
선택을 유지합니다. [동작·검증·운영 범위](REQUIRED_GARMENTS.md)를 참고하세요.

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
| `GET /garments/{id}/image?kind=cutout` | cutout/original/thumbnail 지원. thumbnail은 최대 384px WebP, 누끼 우선·원본 대체. 본인 이미지, private/no-store. 기존 `/media/...`도 유지 |
| `GET /weather?lat=...&lon=...` | 현재 기온·날씨 상태. 실패 시 503 WEATHER_UNAVAILABLE로 수동 입력 안내. 선택 temperature_c가 있으면 수동 대체 |
| `POST /recommendations` | temperature_c 또는 lat/lon 쌍 필요. 좌표 자동 조회 실패 시 temperature_c로 대체. occasion/preferred_styles/excluded_items 선택. `user_id:"me"`, `recent_feedback:[]`도 호환 |
| `GET /recommendations`, `GET /recommendations/{id}` | 본인 이력/결과. 새·과거 추천 함께 조회 가능 |
| `GET /recommendations/requests/{request_key}` | 본인 생성 요청 상태 조회. GPT 호출·추천 저장 없음 |
| `POST /recommendations/{id}/feedback` | 기존 FeedbackEvent 그대로. 본인 추천/코디만 허용 |

### 추천 생성과 재조회 (2026-11-13)

- 새 추천마다 클라이언트가 UUID를 만들고 `POST /api/v1/recommendations`의
  `Idempotency-Key` 헤더에 넣습니다. 허용 값은 8~128자의 영문·숫자·`._:-`입니다.
  더블 클릭, 응답 유실 후 재전송은 **같은 키와 같은 본문**을 유지합니다.
- 같은 로그인 사용자·키·조건이면 저장된 추천 ID와 결과를 재사용합니다(201).
  필수 의류·일정/버전·SNS 키워드/버전·스타일·기온도 요청 조건에 포함됩니다.
  같은 키에 다른 조건은 `409 RECOMMENDATION_REQUEST_CONFLICT`입니다.
- 처리 중 재전송은 `409 RECOMMENDATION_IN_PROGRESS`, `Retry-After: 2`입니다.
  이후에는 상태 GET만 반복합니다. `completed`이면 `recommendation_id`로 결과 GET을 호출합니다.
  `failed`는 실패한 요청이며 자동으로 생성하지 않습니다. `deleted`는 결과 삭제 상태입니다.
  `interrupted`는 2분 이상 완료를 확인하지 못한 상태로, 실제 중단을 확정하는 의미는 아닙니다.
  새로고침으로 상태를 재확인할 수 있고, 새 생성은 사용자가 새 요청 키로 명시적으로 시작합니다.
- 키가 없는 이전 클라이언트는 같은 사용자·정규화된 조건에 대해 60초 동안 결과를 재사용합니다.
  미완료 요청은 60초가 지나도 자동 재실행하지 않습니다. 안정적인 재시도에는 명시적 키가 필요합니다.
- 새 접수만 기존 분당 10회 한도를 사용합니다. 결과·상태 GET과 중복 재전송은 한도를 추가로 사용하지 않습니다.
- PostgreSQL `recommendation_requests`가 서버 간 접수를 직렬화합니다. GPT 호출 전에 접수를 확정하고,
  추천·코디와 완료 상태를 한 트랜잭션으로 저장합니다. API 장애 시 local 대체 추천도 같은 기록을 사용합니다.
  외부 GPT 응답 이후 프로세스가 죽으면 결과를 복구할 수 없는 경우가 있으므로, 이 경우 자동 재호출하지 않습니다.
  이 구조는 외부 API와 DB 사이의 분산 트랜잭션을 보장하는 것은 아닙니다.
- 키는 해시만 보관하며 추가 원본 이미지나 요청 본문 사본을 저장하지 않습니다.
  사용자 탈퇴 시 요청 기록도 삭제됩니다. 결과 삭제 후 같은 키를 보내도 새로 생성하지 않습니다.
- 프론트는 `GET /recommend` → `POST /recommend` → `303 /recommend/result/{id}` 순서입니다.
  결과 새로고침·뒤로 가기·재로그인은 조회만 수행합니다. 이전 `/recommend/result?...` 주소는 조건 화면으로 이동합니다.
  홈의 **최근 추천 코디**도 기존 결과만 조회합니다. 프로필·날씨가 바뀌면 조건 화면에서 새로 추천받습니다.
- 마이그레이션 `0020`은 요청 기록 테이블만 추가합니다. 기존 계정·옷·추천 ID·이미지는 수정하지 않습니다.

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
2. 회원가입은 이메일·비밀번호로 진행합니다. 2026-11-11 초대 코드 기능을 제거했습니다.
   `/auth/signup`에 `invite_code`를 보내지 않습니다(제거된 필드는 422). `nickname`은 선택입니다.
3. 누끼가 없는 옷도 인증된 원본 이미지로 표시하고 category=null 검색을 처리합니다.
   analysis_status를 보존해 분석 미완료 옷은 추천 가능 수에서 제외합니다.
4. 프론트 timeout을 35초로 조정했습니다. 429 오류 메시지는 표시하며 Retry-After 기반 카운트다운 UI는 후속 항목입니다.
5. `/weather` GET→기온 POST 흐름과 수동 기온 요청을 검증하고 날씨 UI에 Open-Meteo 출처 링크를 표시했습니다.
6. 전신 사진 `/users/me/body-photo` API와 프로필의 `body_photo_updated_at`을 구현했습니다.
   아래 계약을 참고하세요. 현재 시연 실행 설정은 전신 사진 UI와 GPU 착샷을 기본으로 숨깁니다.
   GPU 착샷 서버 연결·결과 영속 저장·탈퇴 후 착샷 캐시 무효화는 후속 범위입니다.
   착샷은 별도 2D 서비스이며 3D 모델 생성은 아닙니다.

## 전신 사진 API (DB 0011)

세 경로 모두 JWT에서 본인 ID를 결정합니다. 사용자 ID나 저장 경로를 입력받지 않습니다.
사진 저장 전 `PATCH /api/v1/profile` 또는 `PUT /api/v1/users/me/profile`에
`{"consent_body_info": true}`를 보내 명시적으로 동의해야 합니다. 기존 동의를 재사용합니다.

| 요청 | 성공 응답 |
|---|---|
| `PUT /api/v1/users/me/body-photo` | multipart `file`, JPG/PNG/HEIC/HEIF 최대 기본 10MB → 200 `{body_photo_updated_at}` |
| `GET /api/v1/users/me/body-photo` | 본인 사진 JPEG 바이트, `Cache-Control: private, no-store` |
| `DELETE /api/v1/users/me/body-photo` | 204, 없는 사진의 반복 삭제도 204 |

- `/profile`과 `/users/me/profile` 조회에 최신 `body_photo_updated_at`을 반환합니다.
  등록 전·삭제 후·동의 철회 후에는 null입니다. 실제 저장 경로나 사진 ID는 공개하지 않습니다.
- 미인증·탈퇴한 JWT는 401, 사진 없음은 404 `BODY_PHOTO_NOT_FOUND`, 동의 없음은
  422 `BODY_INFO_CONSENT_REQUIRED`, 용량 초과는 413, 손상/지원하지 않는 이미지는 415입니다.
- 실제 파일 내용을 검사하고 EXIF 메타데이터를 제거하며 JPEG로 재인코딩합니다.
- 의류와 전신 사진 모두 HEIC/HEIF를 자동 표준화합니다. 원본 최대 6,400만 픽셀, 결과 긴 변 최대 2048px입니다.
- 자르기 미리보기용 `POST /api/v1/images/normalize`는 동일한 입력을 받아 JPEG 바이트를 반환합니다.
  로그인 필요, DB/파일 저장 없음, `private, no-store`. 프론트의 연결 경로는 `/photos/normalize`입니다.
  프론트의 1080×1620 이미지는 그대로 처리할 수 있고 긴 변이 2048px보다 크면 축소합니다.
- 옷 등록 화면은 기본 `photo_mode=square`(1080×1080 JPEG)이며, 롱패딩·긴 코트 등에는
  `photo_mode=long`(1080×1620 JPEG)을 선택할 수 있습니다. 이 필드는 프론트 등록 폼 전용으로,
  백엔드 의류 종류/속성을 강제하거나 DB에 새 종류를 추가하지 않습니다. 세로 모드는 전체 사진을
  먼저 맞추고 흰 여백을 허용하며, 등록 시 선택한 모드와 실제 이미지 비율을 함께 검사합니다.
- 교체는 새 UUID 파일에 저장한 뒤 DB 참조를 변경합니다. DB 커밋 실패 시 이전 사진이 유지됩니다.
  새 파일을 쓰기 전 정리 작업을 먼저 DB에 기록해 업로드 실패·프로세스 중단 시에도 회수할 수 있습니다.
- 삭제/교체/동의 철회는 DB 커밋 후 파일을 정리합니다. 파일 삭제가 실패해도 이전 사진 API 접근은
  차단되고 작업 기록을 worker가 재시도합니다. 사용자 탈퇴는 모든 사진 버전·임시 파일을 함께 정리합니다.
- 공개 `/media` 경로로 전신 사진을 제공하지 않습니다. 전신 사진 GET은 잠금 상태에서 바이트를 읽어
  교체/삭제 중 파일 응답 경쟁을 피합니다. 원본 사진은 비전·추천·외부 GPU 서버에 자동 전송하지 않습니다.
- `0011`은 기존 프로필에 nullable 사진 ID/시각을 추가하고 사진 정리 작업 테이블을 만듭니다.
  기존 데이터는 보존하며 사진 또는 미처리 정리 작업이 있으면 손실 방지를 위해 downgrade를 거부합니다.
- 팀원 `frontend/connect/auth_profile.py`의 upload/fetch/delete 호출을 실제 PostgreSQL HTTP E2E로 검증합니다.

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

2026-11-11 아우터 상의 보존: `0015`는 저장 착샷에 `render_version`과 `layering_status`만 추가합니다.
기존 이미지는 유지하며 새 생성에 구버전 결과를 재사용하지 않습니다. GPU 보존 모드, 결과 저장 multipart,
실패 시 안내 및 검증 범위는 [아우터 상의 보존 계약](TRYON_LAYERING.md)을 참고하세요.

2026-11-11 착샷 접수 제어: `0014`부터 사용자당 진행 중 코디 1개,
로컬·멘토 서버 합계 8개를 PostgreSQL에서 제한합니다. `TRYON_USER_BUSY`와
`TRYON_QUEUE_FULL`은 429이며 `Retry-After`를 제공합니다. 생성 완료/취소 시 접수를
반환하고, 프론트는 생성 중 heartbeat를 보냅니다. GPU 요청에는 인증으로 확인한
사용자 ID와 단계별 요청 ID를 전달합니다. 상세 API·실패 처리·공정성 범위는
[착샷 대기열 계약](TRYON_QUEUE.md)을 참고하세요. 기존 마이그레이션 기록은 아래에 유지합니다.

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
