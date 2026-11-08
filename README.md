# Fitjang — AI 코디 추천 MVP

현재 추천은 **상의·하의 필수 + 선택 아우터**입니다. 신발을 등록하지 않아도 됩니다.
팀원 A의 실제 ranking 연결, D의 API 호환 경로, GPT-6 Luna 설정과 남은 화면 연동 사항은
[최신 통합 계약](docs/TEAM_API_CONTRACT.md)을 참고하세요. 계약 `2.0`, 마이그레이션 `0011`입니다.
전신 사진 업로드·조회·삭제 API도 추가했습니다. 기존 신체정보 동의가 필요하며, 교체·삭제·탈퇴 시
이미지를 안전하게 정리합니다. 전신 사진 화면과 로컬 GPU 착샷 서버도 연결했습니다.
RTX 5090 실행 방법·테스트 이미지·검증 범위는 [로컬 GPU 착샷 안내](docs/LOCAL_TRYON.md)를 참고하세요.
현재 공개 HTTPS 시연에서도 착샷을 사용할 수 있습니다([멘토 시연 안내](docs/MENTOR_TRYON.md)).
팀원 B의 실제 분류·누끼와 아이폰 HEIC 자동 변환은 [Vision·HEIC 통합 가이드](docs/VISION_HEIC.md)를 참고하세요.
위치 기반 자동 기온 조회와 실패 시 수동 입력 대체를 지원합니다([날씨 연동 안내](docs/WEATHER_INTEGRATION.md)).
팀원 D의 FastAPI/Jinja2 프론트도 현재 `frontend/`에 통합했습니다.
`.env`를 열지 않는 독립 검증 서버와 실제 화면 검증 범위는 [프론트 실행 안내](frontend/README.md)를 참고하세요.

## 폴더 구조
| 경로 | 담당 | 내용 |
|---|---|---|
| `shared/contracts.py` | 전원 (관리: C) | 공통 데이터 계약 (Pydantic, `CONTRACT_VERSION`) |
| `backend/` | C | FastAPI, PostgreSQL, 어댑터(A·B 연결), API |
| `recommendation/` | A | `recommend()` — 추천 엔진 ([가이드](recommendation/README.md)) |
| `vision/` | B | `extract()` — 의류 속성 추출·분리 ([가이드](vision/README.md)) |
| `frontend/` | D | 사용자 화면, 2D 아바타 ([가이드](frontend/README.md)) |
| `tryon_server/` | D·C 연동 | FASHN VTON 1.5 로컬 GPU 착샷 API |
| `mocks/` | 전원 | Mock JSON 데이터 |
| `docs/` | 전원 | [합의 사항](docs/DECISIONS.md), [작업 방법](docs/CONTRIBUTING.md), [백엔드 설계](docs/BACKEND_PLAN.md) |

## 처음 실행 (Windows PowerShell)
필요: Miniconda, Docker Desktop

```powershell
conda env create -f environment.yml      # 최초 1회 (Python 3.12.14)
conda activate univ18
.\scripts\dev.ps1 setup                  # .env·JWT 키 생성 + DB 실행 + 마이그레이션 + 테스트
.\scripts\dev.ps1 run                    # http://localhost:8000/docs
```
`.\scripts\dev.ps1` 만 입력하면 전체 명령 목록이 나옵니다.
PowerShell 실행 정책 오류가 나면 한 번만: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

## A·B 코드 연결 방식
백엔드는 A·B 코드를 직접 부르지 않고 어댑터(`backend/app/adapters/`)를 거칩니다.
`.env`의 값만 바꾸면 Mock ↔ 실제 구현이 바뀝니다.

```
VISION_PROVIDER=mock        # mock: 파일 이름으로 추정 / local: vision.extract() 호출
RECOMMENDER_PROVIDER=local  # mock: 기본 조합 / local: A의 규칙·ranking / openai: GPT 후보 정렬·설명
```
어댑터가 시간 제한, 예외 처리, 결과 검증, 대체 추천을 맡으므로 A·B 코드가 실패해도 서버는 멈추지 않습니다.
현재 상태는 `GET /health`에서 확인할 수 있습니다.

## 단계 1 — 실제 옷장 API
`users`, `garments`, `garment_attributes` 테이블이 추가되었습니다. 서버를 실행하기 전
`.\scripts\dev.ps1 migrate`로 최신 마이그레이션을 적용하세요(현재 `0010`).

- `POST /api/v1/garments`: 실제 JPG/PNG 사진 업로드 → 이미지 저장·비전 분석·DB 저장 (201).
- `GET /api/v1/garments`: 옷장 목록. `category`, `status` 필터 지원.
- `GET / PATCH / DELETE /api/v1/garments/{id}`: 조회, 속성·세탁 상태 수정, 이미지 포함 삭제.
- 삭제는 의류 제거와 파일 정리 작업을 함께 DB에 커밋한 뒤 이미지를 지웁니다. DB 커밋 실패 시 원본·cutout이 보존됩니다. 파일 삭제 실패 시에도 논리적 삭제는 **204**로 완료되며 옷·이미지 조회는 404, 남은 파일은 DB 작업 기록으로 자동 재시도합니다.
- `/media/...`: 응답의 `image_url`, `cutout_url`로 본인 옷 이미지 조회.
- 분석 실패도 사진은 저장됩니다. `analysis_status=failed`인 옷에 `PATCH`로 카테고리를 입력할 수 있습니다.
- `POST /api/v1/garments/{id}/reanalyze`: 본인 옷의 저장된 원본으로 분석 재시도(본문 없음). **200**의 `analysis_status`/`error`로 성공·실패를 확인합니다. PATCH로 수정한 필드는 `null`·빈 목록까지 보존하고 수정하지 않은 속성만 갱신합니다. 세탁 상태는 유지합니다. 다른 사용자/없는 옷은 404, 동일 의류 처리 중에는 409입니다.
- 재분석은 원본 복사본에서 수행하고 유효한 새 cutout을 별도 파일로 저장합니다. 모델 실패·timeout·DB 커밋 실패가 기존 이미지를 덮어쓰지 않습니다. 기존 `source=user` 데이터는 수정 필드 이력이 없어 `0008`에서 속성을 보수적으로 보호합니다([재분석 안내](docs/DEMO_RUNBOOK.md#8-의류-재분석)).

단계 2부터 모든 환경에서 로그인한 사용자의 JWT가 필요합니다. 임시 고정 사용자 자동 생성은 제거했습니다.
현재 Mock 비전은 저장 파일 `original.jpg`에 대해 기본 상의 속성을 반환하므로 필요한 속성은 수정 API로 입력하세요.
단계 3부터 추천은 로그인한 사용자의 실제 옷장으로 생성·저장하며, 단계 4에서 수락·거절 피드백 저장·조회도 연결했습니다.
상세 동작과 PostgreSQL 검증 명령은 [백엔드 계획서](docs/BACKEND_PLAN.md#단계-1--featgarment-storage-p0)에 있습니다.

## 단계 2 — 회원가입·로그인·프로필
기존 환경은 의존성 갱신 후 아래 순서로 실행합니다.

```powershell
conda activate univ18
pip install -r backend/requirements.txt
python scripts/init_auth_secret.py       # 미설정/예제 JWT 키만 생성, 기존 유효 키는 보존
.\scripts\dev.ps1 migrate                # 현재 0010까지 적용
.\scripts\dev.ps1 run
```

- `POST /api/v1/auth/signup`: JSON `email`, `password`, 선택 `nickname`/`invite_code` → 내 정보 + JWT (201).
- `POST /api/v1/auth/login`: JSON `email`, `password` → `access_token`, `token_type=bearer`, `expires_in` (기본 86400초).
- `GET /api/v1/auth/me`: 로그인한 내 정보.
- `DELETE /api/v1/auth/me`: JWT로 본인 탈퇴(본문 없음) → **204**. 계정·프로필·옷장·추천을 지우고 피드백 참조·사유를 익명화합니다. 이후 모든 기존 JWT는 401이며 같은 이메일 재가입도 새 사용자 ID입니다. 이미지 정리 실패는 DB 작업으로 자동 재시도합니다([탈퇴 안내](docs/DEMO_RUNBOOK.md#9-사용자-탈퇴)).
- `GET / PATCH /api/v1/profile`: 내 닉네임·선호 스타일·기피 색상·선택적 신체정보·아바타 설정.
- D 호환 `GET / PUT /api/v1/users/me/profile`: 동일한 본인 프로필. 키·몸무게 소수, age 지원. 신체정보 동의 필수.
- 인증 요청에는 `Authorization: Bearer <access_token>` 헤더를 보냅니다. `/docs`에서는 로그인 응답의 토큰을 **Authorize**에 입력하세요.
- 비밀번호는 최소 8자, UTF-8 기준 최대 72바이트이며 bcrypt로 저장합니다. 이메일은 앞뒤 공백 제거·소문자 변환 후 중복을 검사합니다.
- `SIGNUP_INVITE_CODE`를 설정하면 가입 시 같은 코드가 필요합니다. `JWT_SECRET`은 32바이트 이상 임의 값이어야 하며 미설정/예제 키로는 앱이 시작되지 않습니다.
- 신체정보·아바타 저장에는 `consent_body_info=true`가 필요합니다. `false`로 철회하면 저장된 신체정보·아바타와 동의 시각을 지웁니다.
- 다른 사용자의 옷·이미지는 404, 인증 누락·만료·위조 또는 비활성 계정은 401입니다. `/health`와 가입·로그인은 인증 없이 사용할 수 있습니다.

## 단계 3 — 실제 옷장 추천·조회
- `POST /api/v1/recommendations`: `{temperature_c, occasion?, preferred_styles?, excluded_items?}` → 추천 저장 (201). `excluded_items`는 실제 의류 UUID 목록입니다.
- 로그인 사용자별 **최근 60초 동안 10회**까지 생성 요청을 허용합니다. 초과 시 **429 `RECOMMENDATION_RATE_LIMITED`**, `Retry-After` 헤더에 대기할 초를 반환합니다. PostgreSQL에 사용량을 저장해 같은 사용자의 동시 요청·다른 토큰·서버 프로세스도 한도를 공유합니다.
- 인증·본문 검증 통과 후 요청을 계산하므로 옷 부족/추천 저장 실패도 1회입니다. 인증·본문 형식 오류와 429는 계산하지 않으며 조회·피드백에는 이 제한을 적용하지 않습니다.
- 분석 완료(`done`)·사용 가능(`available`)한 본인 상의·하의·아우터만 엔진에 전달합니다. 상의·하의 중 하나라도 부족하면 422 `NOT_ENOUGH_GARMENTS`입니다. Mock 비전으로 등록한 옷도 `PATCH`로 올바른 카테고리를 설정해야 합니다.
- 선호 스타일을 생략하거나 `[]`로 보내면 프로필 값을 사용합니다. 기온 대신 `lat`·`lon`을 함께 보내 자동 조회할 수 있습니다. 조회 실패 시 함께 보낸 `temperature_c`를 사용하며, 없으면 503 `WEATHER_UNAVAILABLE`로 수동 입력을 안내합니다.
- `GET /api/v1/weather?lat=...&lon=...`: JWT로 현재 기온·날씨 상태 조회. D의 `/weather` 요청과 연결됩니다. 추천 생성·조회 응답의 `weather`에는 적용한 기온의 출처와 시각이 포함됩니다. 자세한 설정·실패 처리·검증은 [날씨 연동 안내](docs/WEATHER_INTEGRATION.md)를 참고하세요.
- 엔진의 잘못된 ID·다른 사용자 옷·세탁 중/제외 옷·슬롯 불일치·중복·잘못된 점수를 검증하고 최대 3개 코디를 저장합니다. 실패·시간 초과·유효 결과 없음은 규칙 기반 대체 추천으로 처리합니다.
- 저장 직전 DB 상태를 다시 확인합니다. 추천 중 옷이 변경됐다면 현재 옷장으로 재검증·대체하고, 필수 옷이 부족해졌다면 422로 반환합니다.
- `GET /api/v1/recommendations/{id}`: 본인의 저장된 추천 조회. 다른 사용자 또는 없는 ID는 404입니다.
- `GET /api/v1/recommendations?limit=20&offset=0`: 본인 이력 배열, 최신순. `limit`은 1~100입니다.
- 응답에는 `outfits`, `garments` 맵, `provider`, `is_fallback`, 요청 당시 `context`, `latency_ms`, `created_at`이 포함됩니다. 추천·코디 ID는 서버가 새 UUID로 발급합니다.
- 옷을 삭제하면 해당 옷을 포함한 코디만 이력에서 제거합니다. 모든 코디가 제거된 추천은 `outfits=[]`로 남습니다. 이력의 `garments`는 현재 속성·상태이며, 요청 당시 `context`와 남아 있는 코디의 이유·점수는 유지합니다.
- `RECOMMENDER_PROVIDER=mock`도 실제 옷장을 사용합니다. `local`은 A의 날씨·색상·상황 ranking을 호출하며 `openai`는 로컬 후보를 GPT-6 Luna로 정렬하고 설명합니다. 자세한 설정은 [통합 계약](docs/TEAM_API_CONTRACT.md)을 참고하세요.

## 단계 4 — 추천 수락·거절 피드백
- `POST /api/v1/recommendations/{id}/feedback`: `{recommendation_id, outfit_id, accepted, reason?}` → 저장된 피드백 (201). 두 ID는 UUID, 본문 `recommendation_id`는 경로와 같아야 하며 `accepted`는 JSON `true`/`false`입니다.
- 사유는 최대 1,000자입니다. 앞뒤 공백은 제거하고 비어 있으면 null로 저장합니다. 소유자는 JWT에서 가져오며 `user_id` 등 추가 필드는 422로 거부합니다.
- 추천·코디·사용자를 함께 확인해 타인/없는 대상/ID 불일치는 동일한 404, 미인증·비활성 계정은 401입니다. DB 저장 실패 시 롤백합니다.
- `GET /api/v1/recommendations/{id}/feedback?limit=20&offset=0`: 본인 추천의 피드백 배열, 생성 시각/ID 최신순. `limit` 1~100, `offset` 0 이상입니다.
- 응답은 `{feedback_id, recommendation_id, outfit_id, accepted, reason, created_at}`입니다. 매 POST마다 새 이벤트가 생기며 재전송·선택 변경도 기존 기록을 덮어쓰지 않습니다.
- 옷 삭제로 코디가 사라져도 피드백은 `outfit_id=null`로 보존합니다. 사용자 삭제 시 식별자와 사유를 비우고 수락 여부·시각만 통계용으로 남깁니다.
- 1.1에서 추가한 `UserContext.recent_feedback`은 2.0에서도 유지합니다. 본인의 유효한 최근 피드백 최대 20건을 로컬 엔진에 전달합니다. A의 현재 점수에는 반영되지 않으며 GPT에도 피드백 자유 문장을 전송하지 않습니다.

## 테스트
```powershell
.\scripts\dev.ps1 test                       # 전체 (DB 없이도 실행됨)
.\scripts\dev.ps1 test backend/tests/contract  # 계약 테스트 — A·B는 PR 전에 꼭 실행
```
인증·프로필·옷장·추천·피드백 API 테스트는 기본적으로 임시 SQLite DB를 사용합니다. `TEST_POSTGRES=1`을 설정하면
`TEST_DATABASE_URL`의 PostgreSQL에 격리된 임시 스키마를 만들어 실제 마이그레이션으로 검증합니다.

```powershell
$env:TEST_POSTGRES = "1"
python -m pytest
Remove-Item Env:TEST_POSTGRES
```

## 단계 5 — MVP 통합 테스트·시연 준비
```powershell
conda activate univ18
.\scripts\verify_demo.ps1                         # 실제 PostgreSQL 전체 + mock/local HTTP E2E
.\scripts\start_demo.ps1 -Provider local -Port 8001 # DB·마이그레이션·12벌 seed·서버·health 확인
python -m scripts.rehearse_demo --base-url http://127.0.0.1:8001
.\scripts\stop_demo.ps1 -Port 8001                 # 생성한 서버만 종료, DB·데이터 유지
```
- 로그인 정보는 최초 실행이 생성한 `.env.demo`에 보관하며 로그에 출력하지 않습니다. seed 재실행은 기존 데이터와 수정 내용을 보존합니다.
- `frontend/dist/index.html`이 있으면 `/`에서 제공하고, 현재는 프론트 빌드가 없어 `/docs`에서 시연합니다.
- A의 `local` ranking은 연결됐고 B 비전은 임시 구현입니다. 연결·저장·접근 차단 검증과 실제 이미지 분석 품질/GPU 평가는 구분합니다.
- 자세한 실행 옵션, 시연 순서, 테스트 보고서, 백업 절차는 [시연 가이드](docs/DEMO_RUNBOOK.md)를 참고하세요.
- 파일 정리 worker는 시작 시와 기본 30초 간격으로 실행됩니다(`STORAGE_DELETE_RETRY_SECONDS`, 0이면 중지). `python -m scripts.cleanup_storage`로 재시도 시각이 지난 작업을 한 번 처리할 수 있습니다. DB와 같은 `STORAGE_DIR`을 사용하세요. 상세 장애 처리: [시연 가이드](docs/DEMO_RUNBOOK.md#6-의류-삭제와-파일-정리).

## 브랜치 전략
`main`(시연) ← `develop`(통합) ← `feat/*` — 자세한 방법은 [CONTRIBUTING.md](docs/CONTRIBUTING.md)
