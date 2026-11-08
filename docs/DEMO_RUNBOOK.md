# MVP 시연 실행·리허설

현재 백엔드의 가입 → 옷 등록 → 추천 → 수락/거절 → 재로그인 후 이력 조회를 시연할 수 있습니다.
`local`은 저장소의 A·B Python 모듈을 실제로 호출합니다.
B는 아직 `stub-0` 상의 분석 결과, A는 연결된 실제 날씨·색상·상황 ranking을 반환합니다.
신발 없는 추천과 GPT 설정은 [통합 계약](TEAM_API_CONTRACT.md)을 따릅니다.
위치 기반 기온 조회와 실패 시 수동 대체는 [날씨 연동 안내](WEATHER_INTEGRATION.md)를 따릅니다.
사용자가 만든 `.env`는 금지 해제 전까지 에이전트의 접근·수정 대상이 아닙니다.
아래 일반 실행 명령은 사용자가 직접 실행하는 절차이며, 에이전트 검증은 dotenv를 생략한 별도 테스트 설정으로 수행합니다.
학습 모델의 품질·GPU 성능·3D 렌더링은 해당 구현이 준비된 뒤 검증합니다.

## 1. 서버 실행

Docker Desktop을 켜고 저장소 루트에서 실행합니다.

```powershell
conda activate univ18
.\scripts\start_demo.ps1 -Provider local -Port 8001
# .env에 OpenAI 키를 설정한 경우 (A 로컬 후보 + GPT-6 Luna 정렬·설명)
.\scripts\start_demo.ps1 -Provider local -RecommendationProvider openai -Port 8001 -SkipSeed
```

스크립트는 DB 시작 → 최신 마이그레이션/스키마 검사 → seed → Uvicorn 시작 → `/health` 확인 순서로 실행합니다.
Python 3.12를 확인하며 `APP_ENV=demo`, 두 어댑터의 `local` 설정은 자식 서버 프로세스에 적용합니다.
기존 `.env` 값과 호출한 터미널 환경변수는 유지합니다. 실행 중인 포트를 발견하면 다른 서버를 중지하지 않고 오류를 반환합니다.
이 작업에서는 기존 8000번 서버를 유지하고 **8001번**을 사용했습니다.

- API 시연: <http://127.0.0.1:8001/docs>
- 상태: <http://127.0.0.1:8001/health>
- 계정: 최초 실행이 생성한 **`.env.demo`의 `DEMO_EMAIL`, `DEMO_PASSWORD`**. 비밀번호는 무작위 생성되며 콘솔·보고서에 출력하지 않습니다.
- 로그: `.demo/server-8001.log`, `.demo/server-8001.err.log`
- 실행 프로세스 정보: `.demo/server-8001.json`
- 프론트 빌드가 `frontend/dist/index.html`에 있으면 `/`에서 제공합니다. 현재 빌드가 없어 `/docs`로 시연합니다.

```powershell
# 서버 실행 없이 DB·seed 준비만
.\scripts\start_demo.ps1 -PrepareOnly

# D의 실제 빌드가 반드시 있어야 하는 리허설
.\scripts\start_demo.ps1 -Provider local -Port 8001 -RequireFrontend

# 이 스크립트가 시작한 서버만 중지 (DB와 시연 데이터 유지)
.\scripts\stop_demo.ps1 -Port 8001

# 대체 모드: 같은 DB/계정/옷으로 규칙 기반 추천
.\scripts\start_demo.ps1 -Provider mock -Port 8001
```

`-Python 'C:\...\envs\univ18\python.exe'`로 Python 실행 파일을 지정할 수도 있습니다.
서버는 loopback 주소에서 워커 1개, reload 없이 백그라운드로 실행됩니다.
종료 스크립트는 PID와 정확한 시작 시각을 함께 검사해 PID 재사용 시 다른 프로세스를 중지하지 않습니다.
외부 터널·자동 시작 등록은 별도 운영 단계입니다.

## 2. Seed 데이터

`python -m scripts.seed_demo`는 `APP_ENV=demo`에서 실행합니다. 시작 스크립트가 이 설정과 호출을 처리합니다.

- 계정 1개와 의류 **12벌**: 상의 4, 하의 3, 신발 3, 아우터 2.
- 사용 가능 10벌, 세탁 중 1벌, 비활성 1벌로 추천 필터를 보여 줄 수 있습니다.
- 원본 JPEG 12개, alpha 채널이 있는 cutout PNG 12개를 `storage/{user}/{garment}/`에 생성합니다.
- 이미지는 외부 사진이 아닌 합성 도형입니다. 속성은 미리 지정하며 `source=user`, `model_version=demo-seed-v1`로 구분합니다.
- 기존 시연 계정·의류 ID를 재사용해 재실행 시 중복을 만들지 않습니다. 사용자가 바꾼 속성·상태·프로필·비밀번호를 초기화하지 않습니다. 계정이 없으면 새 UUID로 생성하므로 탈퇴 전 JWT는 재사용할 수 없습니다. `0009`는 기존 시연 계정의 ID를 유지하고 내부 `is_demo` 표시만 추가합니다.
- 이미지 파일이 빠졌다면 해당 파일만 복구합니다. 같은 이메일의 일반 계정이 있으면 오류로 종료합니다.
- 계정 이메일을 바꾸려면 실행 전에 `.env.demo`를 설정합니다. 기존 시연 계정의 비밀번호를 바꿨다면 `.env.demo`도 일치시켜야 합니다.
- `.env.demo`, `.demo/`, `storage/`는 Git에서 제외됩니다.

## 3. 5분 시연 순서 (`/docs`)

1. `POST /api/v1/auth/login`에 `.env.demo`의 이메일·비밀번호를 입력합니다. 반환 토큰을 **Authorize**에 넣습니다.
2. `GET /api/v1/garments`로 12벌과 `available/laundry/inactive` 상태를 확인합니다. 이미지 요청에도 인증 헤더가 필요합니다.
3. 새 사용자의 흐름을 보이려면 `auth/signup` → `auth/login` → 사진 3장 이상 업로드 순서로 진행합니다. `SIGNUP_INVITE_CODE`가 설정돼 있다면 가입 본문에 입력합니다.
4. 현재 B의 임시 모듈은 모든 사진을 상의로 분류하므로 `PATCH /garments/{id}`로 상의·하의·신발을 각각 설정합니다. 실제 모델이 연결돼도 사용자의 속성 수정 기능으로 쓸 수 있습니다.
5. `POST /api/v1/recommendations`에 `{"temperature_c": 8, "occasion": "daily"}`를 보냅니다. 201과 `provider`, `is_fallback`, 코디·이미지 맵을 확인합니다.
6. 반환된 추천·코디 UUID로 `POST /recommendations/{id}/feedback`에 `{"recommendation_id": "...", "outfit_id": "...", "accepted": true, "reason": "마음에 들어요"}`를 보냅니다. 거절은 `accepted: false`입니다.
7. 재로그인한 토큰으로 추천 단건·이력·피드백 이력을 조회합니다. 다음 추천의 `context.recent_feedback`에도 의견이 포함됩니다.
8. 다른 계정의 토큰으로 첫 계정의 옷·이미지·추천·피드백을 요청해 404를 확인합니다.

## 4. 자동 검증과 리허설

```powershell
conda activate univ18
# 린트 + 실제 PostgreSQL 전체 회귀 테스트 + mock/local HTTP E2E
.\scripts\verify_demo.ps1

# 실제 HTTP E2E 두 모드만 빠르게 재검증
.\scripts\verify_demo.ps1 -E2EOnly

# 실행 중인 시연 계정으로 실제 추천 2건·수락 피드백 1건을 추가하며 리허설
python -m scripts.rehearse_demo --base-url http://127.0.0.1:8001
```

E2E는 실제 Uvicorn 자식 프로세스와 TCP HTTP 요청을 사용하며 인증·서비스·어댑터를 대체하지 않습니다.
`fitjang_test` 안에 실행별 스키마를 만들고 같은 Alembic 마이그레이션을 적용합니다.
별도 저장소와 두 계정으로 전체 사용자 흐름을 수행한 후 자식 서버와 해당 테스트 스키마를 정리합니다.
기존 개발/테스트 공개 테이블을 초기화하지 않습니다.

보고서:

- `.demo/reports/e2e-local.json`, `.demo/reports/e2e-mock.json`: 모듈 버전, 업로드·추천·전체 요청 시간, 검증 범위.
- `.demo/reports/rehearsal.json`: 실제 시연 서버에서 확인한 이미지 수, 저장된 추천·피드백 ID.
- `.demo/reports/rehearsal-mock.json`: 대체 모드 리허설 결과.

**2026-11-07 탈퇴 추가 후 검증:** PostgreSQL 전체 **210개 통과**, SQLite **193개 통과·PostgreSQL 전용 17개 건너뜀**.
기존 Starlette/httpx 사용 중단 예정 경고 1건만 있습니다. 개발·테스트 DB 모두 **0009** 적용 및 스키마 일치를 확인했습니다. 기존 DB 행과 이미지 24개의 내용은 그대로 보존했습니다.
DB 커밋 실패·파일 전체/부분 삭제 실패·정리 완료 커밋 실패·미완료 작업의 실제 서버 자동 정리도 검증했습니다.
추천 생성 20개 동시 HTTP 요청은 10개 성공·10개 429였고, 사용자 분리·최근 60초 경계·별도 프로세스의 사용량 공유·후속 실패 시 사용량 보존을 검증했습니다.
재분석의 실제 mock/local HTTP 연결, 수동 필드·원본·기존 cutout 보존, timeout 후 늦은 출력 격리, 동시 재분석 409 및 PATCH 직렬화, 기존 수동 데이터 마이그레이션도 검증했습니다.
탈퇴의 연관 데이터 삭제·피드백 익명화·기존 JWT 차단, DB/파일 장애 복구, mock/local HTTP·worker, 분석과 탈퇴의 동시 실행, 같은 이메일/seed 재생성 후 과거 JWT 차단도 검증했습니다.
단계 5 최초 `local` E2E 한 번의 측정은 업로드 4회 **15.25~37.62ms**, 추천 HTTP **13.41ms**, 전체 흐름 **1.14초**였습니다.
이는 합성 이미지·임시 모듈의 연결 측정값이며 모델 품질 또는 GPU 성능 수치가 아닙니다(`gpu=null`).
실제 시연 DB의 12벌·24개 이미지와 두 모드의 추천/피드백 저장도 리허설했습니다.

## 5. 현장 점검과 백업 노트북

- 시작 전 `/health`: `db=ok`, `app_env=demo`, 어댑터가 의도한 모드인지 확인합니다.
- 모델 장애 시 종료 후 `-Provider mock`으로 다시 시작합니다. 이미 저장된 옷과 이력을 그대로 사용합니다.
- `NOT_ENOUGH_GARMENTS`: 분석 완료·사용 가능 상태의 상의/하의/신발이 각각 있는지 확인합니다.
- 모델 준비 시간이 60초를 넘으면 로그에서 warmup 상태를 확인하고 시작 스크립트의 readiness 시간을 조정한 뒤 재검증합니다.
- 시연 당일은 `--reload`를 사용하지 않습니다. 실제 프론트 빌드는 `-RequireFrontend`로 빠짐없이 확인합니다.

백업 노트북에는 같은 코드·conda 환경, PostgreSQL 덤프, `storage/`, 로컬 `.env`/`.env.demo`가 필요합니다.
기본 compose DB의 덤프는 텍스트 리디렉션 대신 컨테이너 파일로 생성해 Windows에서 바이너리 손상을 피합니다.

```powershell
$backupPath = Join-Path (Get-Location).Path ('.demo/backups/' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $backupPath | Out-Null
docker compose exec -T db pg_dump -U fitjang -d fitjang -Fc -f /tmp/fitjang-demo.dump
docker compose cp db:/tmp/fitjang-demo.dump (Join-Path $backupPath 'fitjang.dump')
Copy-Item -LiteralPath ./storage -Destination (Join-Path $backupPath 'storage') -Recurse
```

백업 노트북의 빈 DB에 복원한 뒤 `start_demo.ps1 -Provider mock`과 자동 리허설을 실행합니다.
실제 노트북 복원, D의 실제 화면/3D 연결, A·B 학습 모델/GPU 실측, 배포 PR·태그는 별도 팀 통합 점검 항목입니다.

## 6. 의류 삭제와 파일 정리

서버를 업데이트할 때 먼저 `alembic -c backend/alembic.ini upgrade head`로 최신 마이그레이션(현재 `0009`, 의류 파일 정리 `0006`·탈퇴 파일 정리 `0009`)을 적용하고 재시작합니다. `start_demo.ps1`은 이 순서를 처리합니다.

- 의류 삭제와 `storage_deletion_jobs` 기록은 한 DB 트랜잭션입니다. 커밋 전에는 파일을 건드리지 않으므로 DB 삭제 실패 시 원본·cutout과 기존 데이터가 보존됩니다.
- **DELETE의 204는 DB 삭제 완료**입니다. 원본·cutout 정리가 실패/부분 실패해도 의류·미디어 API는 404이며 물리적 파일 정리는 재시도합니다. DB와 파일시스템의 동시 원자적 삭제를 의미하지 않습니다.
- 앱 시작 시와 `STORAGE_DELETE_RETRY_SECONDS` 간격(기본 30초)으로 worker가 실행됩니다. 파일 오류 시 30초부터 지수적으로 지연하며 최대 1시간 간격으로 계속 재시도합니다. 0이면 자동 worker만 중지됩니다.
- 요청 처리 중 프로세스가 종료되거나 파일 정리 완료 기록의 커밋이 실패해도 작업은 DB에 남습니다. 서버 재시작 후 처리되며 이미 없는 파일도 성공으로 처리합니다.
- seed가 같은 의류 UUID를 재생성할 때는 잠금으로 기존 정리 작업을 취소해 새 파일을 보호합니다.

재시도 시각이 지난 작업을 수동으로 한 번 처리하려면 서버와 같은 DB·`STORAGE_DIR` 설정으로 실행합니다(계정·의류 각각 최대 100건, 합계 최대 200건).

```powershell
conda activate univ18
python -m scripts.cleanup_storage
# {"selected": 0, "completed": 0, "deferred": 0}이면 현재 처리할 작업 없음

# DB에 남은 모든 작업과 다음 재시도 시각 확인 (기본 Docker 개발 DB)
docker compose exec -T db psql -U fitjang -d fitjang -c "SELECT garment_id, attempts, last_error, available_at FROM storage_deletion_jobs ORDER BY available_at;"
docker compose exec -T db psql -U fitjang -d fitjang -c "SELECT user_id, attempts, last_error, available_at FROM account_storage_deletion_jobs ORDER BY available_at;"
```

`deferred`에는 이번 처리에서 실패하거나 다른 worker가 처리 중인 작업이 포함됩니다. 미래 시각으로 예약된 작업은 `selected`에 포함되지 않으므로, 대기 작업 전체는 위 SQL로 확인합니다. `PermissionError`가 반복되면 파일 잠금/권한과 저장소 경로를 확인하고 복구하세요. 작업 행을 먼저 지우면 자동 재시도가 사라집니다. 물리적 파일이 정리되기 전까지 DB 작업 기록과 storage를 함께 백업합니다.

## 7. 추천 요청 제한과 429 대응

`POST /api/v1/recommendations`는 로그인 사용자별 최근 **60초 동안 최대 10회**입니다. 시연 리허설도 같은 한도를 사용합니다. 10회를 사용한 뒤에는 429 `RECOMMENDATION_RATE_LIMITED`를 반환하며, 응답의 `Retry-After` 초만큼 기다린 뒤 다시 요청합니다. 반복해서 차단된 요청이 대기 시간을 연장하지는 않습니다.

사용량은 마이그레이션 `0007`의 `recommendation_rate_limits`에 기록됩니다. 같은 계정의 다른 JWT·브라우저·IP·서버 프로세스는 한도를 공유하고 서버를 재시작해도 초기화되지 않습니다. 사용자마다 별도 한도이므로 같은 와이파이에 접속한 다른 계정에는 영향을 주지 않습니다. 최근 시각은 최대 10개만 보관하고 오래된 시각은 다음 허용 요청 때 제외합니다.

- 인증과 본문 형식 검증을 통과해 생성 처리를 시작한 요청은 1회입니다. 옷 부족 422, 대체 추천, 후속 저장 실패도 사용량이 남습니다.
- 인증/본문 형식 오류와 차단된 429는 사용량에 포함하지 않습니다.
- 추천 결과 조회, 피드백, 옷장·프로필에는 이 제한을 적용하지 않습니다.
- 429 시 추천 엔진은 호출하지 않습니다. 사용량 DB 커밋이 실패해도 엔진을 호출하지 않고 서버 오류로 처리합니다.
- D는 `Retry-After` 헤더를 읽어 버튼 대기 시간을 표시할 수 있습니다(CORS 노출 완료).

```json
{"detail": "추천 요청은 사용자별 60초 동안 최대 10회 가능합니다. 잠시 후 다시 시도해 주세요", "code": "RECOMMENDATION_RATE_LIMITED"}
```

## 8. 의류 재분석

1. `/docs`에서 로그인 토큰을 설정하고 `GET /api/v1/garments`로 본인 의류 UUID를 확인합니다.
2. `POST /api/v1/garments/{id}/reanalyze`를 본문/파일 없이 요청합니다. 기존 원본 사진을 재사용하며 같은 의류 ID를 유지합니다. 완료된 의류도 다시 분석할 수 있습니다.
3. **200 응답의 `analysis_status`**를 확인합니다. `done`이면 성공·`error=null`, `failed`이면 모델 실패/timeout/무효 결과이며 오류를 표시하고 재시도하거나 PATCH로 보정합니다. 최근 분석의 모델 버전·시각·오류가 DB에 남습니다.

PATCH로 명시한 카테고리·세부 분류·색상·패턴·스타일·보온도는 이후 분석에서도 보존합니다. `null`, 빈 스타일 목록, 원래 값과 같은 수정도 포함합니다. 수정하지 않은 필드만 새 결과로 갱신하며 세탁 상태는 항상 유지합니다. `0008` 이전 `source=user`는 수정 필드를 알 수 없어 모든 속성을 보호하되 `category=null`은 새 모델이 채웁니다. 새 시연 seed도 모든 지정 속성을 보호합니다.

| 응답 | 의미/대응 |
|---|---|
| 200 `done` | 최신 결과로 화면과 이미지 URL 갱신 |
| 200 `failed` | 기존 속성·이미지는 보존. `error` 안내 후 재시도/수동 수정 |
| 401 | 로그인 필요/토큰 오류/비활성 계정 |
| 404 `GARMENT_NOT_FOUND` | 없는 의류 또는 다른 사용자 소유 |
| 404 `GARMENT_IMAGE_NOT_FOUND` | 원본 파일 누락/읽기 실패. storage 복원 확인 |
| 409 `GARMENT_BUSY` | 동일 의류 처리 중. 중복 버튼 비활성화 및 잠시 후 재시도 |

재분석은 동기 방식입니다. 분석 중 해당 의류 행을 잠그며, 동일 의류의 PATCH/삭제는 완료를 기다린 뒤 최신 값을 읽어 처리합니다. 이때 `analysis_status=pending`을 별도로 커밋하지 않으므로 UI는 요청의 진행 상태를 표시합니다. 추천 생성용 분당 10회 제한은 재분석에는 적용하지 않습니다.

모델에는 임시 복사본을 전달합니다. timeout 이후 늦게 끝나는 모델도 실제 원본/cutout에 쓰지 않으며 작업 스레드가 종료될 때 임시 폴더를 정리합니다. 유효한 새 PNG만 고유한 `cutout-{버전}.png`로 저장하고 DB 경로를 바꿉니다. 새 cutout이 없거나 무효이면 기존 경로를 유지합니다. 기존 파일을 덮어쓰지 않으므로 DB 커밋 실패·커밋 응답 유실에도 참조 이미지가 유실되지 않습니다.

이전 버전과 커밋 실패 시의 미등록 파일은 같은 의류 폴더에 보관하며, 의류 삭제 시 영속 정리 작업이 함께 제거합니다. 현재 DB 경로 이외의 파일은 `/media`에서 제공하지 않습니다. 따라서 재분석을 반복하면 의류 삭제 전까지 저장 공간 사용량이 늘 수 있습니다. DB 직접 삭제 대신 의류 DELETE API를 사용합니다.

## 9. 사용자 탈퇴

`DELETE /api/v1/auth/me`에 `Authorization: Bearer <token>`을 보냅니다. 본문은 생략하거나 `{}`이며 계정 ID·이메일 등 추가 필드는 422입니다. JWT의 본인 계정만 삭제합니다.

- **204(본문 없음):** 계정, 신체정보·아바타를 포함한 프로필, 옷장/속성, 추천/코디, 추천 요청 사용량 삭제 완료. 피드백의 사용자·추천·코디 ID 및 자유 입력 사유는 NULL이며 이벤트 ID·수락 여부·생성 시각만 익명 통계로 남습니다.
- 기존 JWT는 남은 유효기간과 관계없이 보호 API·이미지 요청에서 **401 `AUTH_INVALID_TOKEN`**입니다. 같은 토큰의 두 번째 DELETE도 401입니다. D는 성공 후 토큰·캐시·이미지 object URL을 정리합니다. 응답을 못 받았다면 `/auth/me`로 세션 상태를 확인합니다.
- 계정 삭제와 `account_storage_deletion_jobs` 기록은 한 트랜잭션입니다. 커밋 전에는 파일을 삭제하지 않습니다. 커밋 실패 시 계정·연관 데이터·피드백·이미지가 유지됩니다. 커밋은 성공했지만 응답만 유실된 경우에는 탈퇴 상태와 정리 작업이 남습니다.
- 커밋 후 `{user_id}/` 전체를 정리합니다. 등록 원본·cutout뿐 아니라 이전 버전·미등록 이미지와 기존 의류별 미완료 작업도 처리합니다. 파일 삭제 실패/부분 실패는 204를 바꾸지 않으며 worker가 시작 시 및 기본 30초 간격으로 재시도합니다. 오류 재시도 간격은 30초에서 최대 1시간까지 증가합니다. 수동 실행·오류 확인은 6절과 같습니다.
- **204는 DB 삭제 완료이며 물리적 파일 정리는 지연될 수 있습니다.** 남은 파일은 인증된 미디어 API로 접근할 수 없습니다. 권한 오류가 지속되면 파일 잠금·권한을 복구해야 정리가 끝납니다. DB에서 사용자 행만 직접 삭제하면 파일 정리 작업은 생성되지 않으므로 탈퇴 API를 사용합니다.
- 업로드 분석 중 먼저 탈퇴하면 결과 게시를 차단합니다. 재분석 중에는 해당 요청의 커밋/롤백까지 탈퇴가 기다립니다. 두 분석 모두 임시 복사본을 사용하므로 timeout 후 늦은 출력이 삭제한 사용자 폴더를 다시 만들지 않습니다. 임시 폴더는 실제 모델 스레드가 끝날 때 정리됩니다.
- 같은 이메일로 재가입하거나 탈퇴한 시연 계정을 seed로 다시 생성하면 **새 사용자 UUID**를 발급합니다. 과거 JWT는 계속 차단되고 기존 정리 작업은 옛 폴더만 지웁니다. 현재 시연 데이터를 유지하려면 탈퇴 시연은 새 가입 계정으로 진행하세요.

`backend/tests/api/test_account_deletion.py`는 사용자 분리, 전체 삭제·익명화, JWT 차단, 커밋/파일 장애·재시도를 검증합니다. `backend/tests/e2e/test_account_deletion.py`는 실제 PostgreSQL과 mock/local HTTP, 서버 worker 복구 및 업로드·재분석과 탈퇴의 동시 실행을 검증합니다. 기존 seed 보존 마이그레이션과 탈퇴 후 재생성도 테스트합니다.
