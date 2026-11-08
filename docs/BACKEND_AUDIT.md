# C 백엔드 완료 점검 및 팀 연동 안내

> **프론트 통합 후 최신 결과:** 현재 `frontend/`의 실제 화면으로 가입·옷 등록·날씨·추천·피드백을 확인했고,
> PostgreSQL 전체 **281개 테스트**가 통과했습니다. [최신 실행·검증 범위](FRONTEND_INTEGRATION.md)를 참고하세요.

> **2026-11-08 갱신:** 현재 계약 **2.0**, DB **0010**. A rules/ranking·세 슬롯 추천,
> D 호환 API·GPT-6 Luna 선택형 어댑터·자동 날씨 및 수동 대체를 구현했습니다. PostgreSQL **279개 통과**,
> SQLite **260개 통과·19개 제외**. 이전 통합에서 기존 DB 값과 이미지 24개 보존 확인.
> 최신 자동 날씨 계약·검증은 [WEATHER_INTEGRATION.md](WEATHER_INTEGRATION.md)를 참고하세요.
> 아래는 0009 당시 기록이며 최신 검증·설정·잔여 항목은 [TEAM_API_CONTRACT.md](TEAM_API_CONTRACT.md)를 우선 참고하세요.

재점검일: 2026-11-07 (탈퇴 `0009` 구현 후). 기준: `BACKEND_PLAN.md` 전체, 현재 작업 트리의 코드·테스트, 실행 중인 시연 서버/OpenAPI, 개발·테스트 PostgreSQL.

> 2026-11-08 원격 브랜치 추가 점검: 실제 A ranking, D FastAPI/Jinja2 화면, tryon 서버 코드를 확인했습니다. 아래의 프론트 미제공·정적 빌드 대기 상태는 11월 7일 로컬 점검 기록입니다. 최신 산출물의 API 불일치·실행 구조·병합 순서는 [INTEGRATION_REVIEW.md](INTEGRATION_REVIEW.md)를 기준으로 합니다.

## 1. 판정

**C의 MVP API 구현은 완료됐습니다. 다만 계획서의 C 역할 전체는 아직 완료되지 않았습니다. 실제 팀 산출물 통합과 시연·배포 완료 조건이 남아 있습니다.**

0~4단계의 핵심 경로, 5단계의 백엔드 E2E·seed·실행 도구, 후속 요청인 의류 삭제 장애 보완·사용자별 추천 제한·재분석·탈퇴까지 구현됐습니다. 요청해 온 기능을 다시 미구현 항목으로 분류하지 않습니다. 단계 0의 새 PC 재현, 단계 3의 실제 추천 알고리즘 수용 검증, 단계 5의 실제 모델/화면·복원·릴리스는 별도 완료 확인이 필요합니다.

| 범위 | 판정 | 근거/남은 조건 |
|---|---|---|
| 0. 환경·DB·어댑터·오류 처리 | 코드·현재 PC 검증 완료 | conda, PostgreSQL, Alembic, 설정·로그·health, mock/local 선택. 새 PC에서 30분 내 실행 완료 기준은 미검증 |
| 1. 옷 등록·속성·옷장·이미지 | 구현 및 삭제·재분석 장애 처리 완료 | CRUD/권한/이미지 검증/수동 수정·삭제 재시도·P1 재분석 완료. 수정 필드 보존 및 기존 데이터 보호 |
| 2. 가입·로그인·프로필·탈퇴 | 구현 완료 | bcrypt/JWT/동의·철회/소유권 검증. 탈퇴 시 연관 데이터 삭제·피드백 익명화·파일 정리 재시도·기존 JWT 차단 |
| 3. 추천 연결·검증·저장·조회 | 백엔드 구현 완료 | 본인 가용 옷, 결과 재검증/대체 추천, 저장 직전 상태 확인, 조회/권한. 실제 A 알고리즘 교체 후 품질·성능 검증 대기 |
| 4. 피드백 | 구현 완료 | 수락/거절 이벤트 저장·조회, 접근 차단, 계약 1.1 최근 20건 전달. 개인화 점수 반영은 A 작업 |
| 5. MVP 통합·시연 | 백엔드 로컬 준비 완료, 팀 통합 일부 대기 | 실제 HTTP E2E, seed/스크립트/정적 제공 기반 완료. 실제 모델/GPU/프론트·3D/백업 노트북/출시 절차 남음 |

## 2. 확인한 증거

- 최근 전체 회귀 결과를 JUnit 보고서로 재확인했습니다: PostgreSQL **210개 통과** (`.pytest_cache/account-postgres.xml`, 21:44 KST 시작), SQLite **193개 통과·PostgreSQL 전용 17개 건너뜀** (`.pytest_cache/account-sqlite.xml`). 오류·실패 0건, 기존 Starlette/httpx 경고 1건입니다. 이번 재점검에서는 애플리케이션 코드를 바꾸지 않아 전체 회귀를 반복하지 않았고, Ruff 린트·실행 상태·DB 스키마·SPA 조건을 다시 확인했습니다.
- 개발·테스트 DB 모두 `0009 (head)`이며 ORM과 실제 스키마의 차이가 없음을 재확인했습니다. 앞선 구현 검증에서 `alembic check`, 마이그레이션 왕복·기존 수동 데이터 및 seed 보존 테스트도 통과했습니다. 적용 전후 기존 DB 행과 이미지 해시가 일치했고 기존 시연 계정의 `is_demo` 표시만 추가됐습니다.
- 수정 후 두 DB의 파일 정리 대기 작업과 임시 테스트/E2E/감사 스키마는 모두 0개입니다. 기존 시연 데이터(계정 1개, 의류 12벌, 이미지 24개, 추천 6건, 피드백 3건)는 보존했습니다.
- 시연 서버 `http://127.0.0.1:8001/health`: `status=ok`, `db=ok`, `app_env=demo`, 계약 `1.1`, 두 어댑터 `local`, `gpu=null`.
- 탈퇴·재분석과 health를 포함해 **19개 HTTP operation**을 제공합니다.
- `local` E2E는 실제 `vision.extract()`/`recommendation.recommend()`를 호출했지만 B 버전은 `stub-0`이고 카테고리를 PATCH로 보정합니다. GPU/학습 모델 정확도 테스트가 아닙니다.
- `frontend/dist/index.html`은 없고 프론트 폴더에는 연동 README만 있습니다. 현재 시연 서버 `/`는 404이며 `/docs`로 API 시연합니다. 별도 임시 HTML fixture로 정적 제공 시 `/` 200·`/closet` 404를 다시 재현했습니다.
- 현재 HEAD는 `559c2ae`(초기 scaffold), 브랜치는 `codex/recommendation-integration`입니다. 기능 구현 다수가 수정/미추적 파일로 남아 있어 팀 공유 전 커밋·PR 정리가 필요합니다. 로컬 태그는 없습니다. 원격 PR·태그·브랜치 보호 설정은 이번 점검에서 조회하지 않았으므로 원격 상태의 완료 여부는 판정하지 않습니다.
- `.github`에는 PR 템플릿만 있으며 CI workflow는 없습니다. `.env`, `.env.demo`, `storage/`는 추적되지 않습니다.

## 3. C의 남은 작업

| 순서/시점 | 작업 | 현재 상태와 완료 조건 |
|---|---|---|
| **팀 공유 전** | 구현 코드 커밋·PR | 작업 트리의 기능을 정리해 `develop` 대상 PR, 계약 1.1과 마이그레이션 공유. 시연용 최종 검증 후 main/태그 절차 진행 |
| **지금 C가 진행 가능** | 임시 모듈에 의존하는 테스트 정리 | 아래 두 테스트가 `(stub)` 문구·`stub-0` 버전을 고정 기대. 계약을 지킨 실제 A·B 구현으로 바꿔도 실패할 수 있으므로 계약·소유권·저장 동작 검증으로 정리 |
| **지금 C가 진행 가능** | PR CI 구성 | `.github/workflows` 없음. Mock 기반 lint/API·계약 검증과 PostgreSQL 통합 검증 실행 경로 마련. 실제 모델·LLM 네트워크/가중치가 필요한 검증은 별도 실행 조건 명시 |
| **외부 공개 시** | 초대 코드·터널 운영 | 초대 코드 기능은 구현됐지만 점검 당시 설정은 비어 있음. Cloudflare Tunnel/자동 시작 등록은 스크립트에 없음. 공개 범위를 정하고 설정·리허설 |
| **실제 모델 연결 후** | 동기 처리·timeout·부하 검증 | 현재 스레드 timeout은 응답 대기만 종료하며 실제 모델 작업을 취소하지 않음. 계획서의 5초 기준과 다중 업로드/GPU 점유·대기열을 실측해 비동기 작업 여부 결정 |
| **D 연동 시** | SPA 직접 경로 접속 | `/`는 제공되지만 `/closet` 직접 접속은 404. D의 라우팅 방식에 맞춰 index fallback 또는 hash 경로를 결정하고 API/media 404가 가려지지 않게 검증 |
| **A·D와 계약 합의 시** | 개인화 입력·아바타 계약 확정 | `recent_feedback`에는 과거 코디의 옷/속성 snapshot 없음. `avatar_config`는 16KB JSON 보관만 가능. 필요한 추가 필드·3D 에셋/생성 API 여부를 먼저 확정 |
| **통합/릴리스 전** | 최종 복원·시연·배포 | 새 PC 환경 재현, 실제 프론트·모델 통합, 백업 노트북 DB/storage 복원, 최종 리허설, `develop → main` PR·`v1.0-demo`·별도 시연 checkout |
| **후순위** | 자동 날씨·임베딩·외부 저장소 | 날씨는 manual만, pgvector는 확장 설치만. `garment_embeddings` 테이블/검색, S3/remote 어댑터는 확장 계획이며 MVP 완료 조건과 구분 |

### 이번 재점검에서 추가로 확인한 연동 준비 작업

1. **실제 A·B 코드로 바꾸기 전에 테스트의 임시값 의존을 제거해야 합니다.** [`test_recommendations.py`](../backend/tests/api/test_recommendations.py)의 `test_local_provider_connects_actual_recommendation_package`는 추천 사유가 `(stub)`로 시작해야 통과합니다. [`test_reanalysis.py`](../backend/tests/e2e/test_reanalysis.py)는 local 모델 버전을 `stub-0`으로 고정하고 합성 이미지를 상의로 분류한다고 가정합니다. 서비스의 함수 연결 구조는 준비됐지만 전체 테스트가 임의의 실제 모델 교체를 이미 수용하는 상태는 아닙니다.
2. **모델 품질 검증용 입력을 따로 준비해야 합니다.** 현재 HTTP E2E는 합성 도형과 PATCH 보정을 사용합니다. 비전 계약 테스트는 이미지가 아닌 바이트를 주고 결과 타입/실패 형식을 검사합니다. 실제 옷 사진·정답 분류·투명 cutout 샘플, 추천 수락/거절 전후 비교 시나리오를 A·B와 정한 뒤 별도 통합 검증을 추가해야 합니다. 현재 테스트 수는 품질 정확도나 GPU 성능을 뜻하지 않습니다.
3. **확장 기능이 설정 한 줄로 완성되는 것은 아닙니다.** 현재 추천 요청은 `temperature_c`가 필수이고 서비스가 날씨 어댑터를 호출하지 않습니다. 자동 날씨에는 요청 스키마·서비스 연결이 추가로 필요합니다. 미디어 응답도 `local_path`와 `FileResponse`를 사용하므로 S3 전환에는 인증된 스트리밍/서명 URL 설계와 의존성 주입 변경이 필요합니다. 새 비전 속성은 계약뿐 아니라 DB·API 스키마·매퍼·수동 수정 보존을 함께 점검해야 합니다. `BACKEND_PLAN.md` 13절을 현재 코드 기준으로 수정했습니다.

### 해결한 삭제 문제 (후속 수정, 2026-11-07)

기존 `GarmentService.delete()`는 파일 삭제 후 DB를 커밋해 커밋 실패 시 이미지가 유실됐습니다. 현재는 다음 순서입니다.

1. 소유권 확인 및 의류 행 잠금 → 의류 삭제와 `storage_deletion_jobs` 기록을 한 트랜잭션으로 커밋.
2. 커밋 실패 시 롤백. 원본·cutout과 의류·속성·관련 코디·피드백 참조를 보존.
3. 커밋 성공 후 파일 정리. 실패/부분 실패면 작업을 DB에 남기고 자동 재시도. 정리 완료 기록의 커밋 실패도 재시도로 복구.

**204는 논리적 삭제 완료**이며, 남은 파일의 물리적 삭제는 지연될 수 있습니다. 해당 의류·미디어 접근은 즉시 404입니다. DB와 파일시스템을 하나의 원자적 트랜잭션으로 묶는 방식은 아니며, 영속 작업으로 최종 정합성을 유지합니다. 지속적인 권한 오류는 권한을 복구해야 실제 파일 정리가 끝납니다.

`backend/tests/api/test_garment_deletion.py`에서 커밋 실패, 파일 전체/부분 삭제 실패, 정리 완료 커밋 실패, 커밋 성공 후 응답 유실을 검증합니다. `backend/tests/e2e/test_cleanup_worker.py`는 별도 서버와 실제 PostgreSQL에서, 커밋 후 남은 작업을 추가 DELETE 요청 없이 worker가 정리하는지 확인합니다. seed의 동일 UUID 재생성 및 저장소 루트 삭제 방지도 검증했습니다.

실행/장애 대응은 [`DEMO_RUNBOOK.md`](DEMO_RUNBOOK.md#6-의류-삭제와-파일-정리)를 참고하세요.

### 완료한 사용자별 추천 요청 제한 (`0007`)

`POST /api/v1/recommendations`는 JWT의 사용자 ID 기준 최근 60초 동안 10회로 제한합니다. 초과 시 429 `RECOMMENDATION_RATE_LIMITED`와 `Retry-After`를 반환하고 엔진 호출·추천 결과 저장을 생략합니다. PostgreSQL에 사용자별 사용량을 저장하고 사용자 행 잠금으로 최초/동시 요청을 직렬화하므로 여러 토큰·프로세스가 같은 한도를 사용합니다. 잠금은 사용량 커밋 직후 해제합니다.

사용자별 한도는 독립적이며 조회·피드백은 제한하지 않습니다. 인증·본문 검증 후 허용된 요청은 옷 부족이나 후속 실패에도 계산하고, 인증/본문 오류·차단된 429는 계산하지 않습니다. 테스트는 최근 60초 경계·부분 만료, 사용자 분리·재로그인, DB 저장 실패, 실제 HTTP 동시 요청과 별도 프로세스의 사용량 공유를 포함합니다.

### 완료한 의류 재분석 (`0008`)

`POST /api/v1/garments/{id}/reanalyze`는 저장 원본으로 다시 분석하며 POST 본문은 필요 없습니다. 200 응답의 `analysis_status`와 `error`로 성공·실패를 표시합니다. 새 `user_edited_fields`로 PATCH한 필드만 보존하고, 기존 수동 데이터는 마이그레이션에서 보수적으로 보호합니다. 세탁 상태는 유지하며 실패 시에도 기존 값·이미지를 잃지 않습니다.

동시 재분석은 409로 차단하고 PATCH/삭제는 완료 후 최신 상태에서 처리합니다. 모델은 복사본에만 쓰며 새 cutout을 고유한 파일명으로 게시하므로 timeout 후 출력·DB 커밋 실패가 기존 파일을 덮어쓰지 않습니다. 파일 이전 버전은 의류 삭제 시 함께 정리합니다. 실제 mock/local HTTP와 PostgreSQL 소유권·동시 요청·마이그레이션을 검증했습니다. 상세는 [`DEMO_RUNBOOK.md`](DEMO_RUNBOOK.md#8-의류-재분석)를 참고하세요.

### 완료한 사용자 탈퇴 (`0009`)

`DELETE /api/v1/auth/me`는 JWT의 본인 계정만 삭제하고 204를 반환합니다. 프로필·옷장/속성·추천/코디·요청 사용량을 함께 삭제하며 피드백의 사용자·추천·코디 참조와 자유 입력 사유는 NULL로 익명화합니다. 계정 삭제와 사용자 폴더 정리 작업을 같은 트랜잭션으로 확정한 후 이미지를 정리하므로 DB 커밋 실패는 기존 데이터·파일을 보존합니다. 파일 전체/부분 삭제 실패나 정리 완료 커밋 실패는 영속 작업과 worker로 재시도합니다.

탈퇴 후 과거 JWT는 보호 API·미디어에서 401입니다. 같은 이메일로 재가입하거나 seed를 재생성해도 새 사용자 ID를 쓰므로 과거 JWT가 되살아나지 않습니다. 기존 seed 계정은 `is_demo` 표시만 추가해 ID·데이터를 보존했습니다. 업로드·재분석 모두 임시 복사본을 모델에 제공하며 사용자 행 잠금과 게시 직전 인증 재확인으로 늦은 결과가 삭제된 계정의 파일을 다시 만들지 않도록 했습니다.

실제 PostgreSQL의 정상/장애 회귀 테스트, mock/local HTTP, 서버 worker 복구, 분석과 탈퇴의 동시 실행, seed 재생성/기존 계정 마이그레이션을 검증했습니다. D의 204·401 처리와 운영 안내는 [`DEMO_RUNBOOK.md`](DEMO_RUNBOOK.md#9-사용자-탈퇴)에 있습니다.

### 재현한 SPA 조건

임시 `index.html`을 두고 현재 `StaticFiles(html=True)` 구성으로 확인한 결과:

```json
{"probe": "SPA deep link", "root_status": 200, "closet_status": 404, "docs_status": 200}
```

프론트가 URL 경로 기반 라우팅을 선택할 경우 새로고침/직접 링크에 필요한 추가 통합 작업입니다.

## 4. 공통 연동 기준

- Python 환경: 저장소의 `environment.yml`과 `backend/requirements.txt`. A/B 추가 의존성은 각 requirements와 환경 구성에 명시합니다. 전달받은 `requirements_recommend.txt`의 유일한 항목 `pydantic>=2,<3`은 현재 `univ18`의 `2.14.3`으로 충족하며 추가 설치는 없습니다([비교 결과](../recommendation/README.md#전달받은-의존성과-univ18-비교-2026-11-07)).
- 공통 계약: [`shared/contracts.py`](../shared/contracts.py), **버전 1.1**. 이름/필수 필드/타입을 임의로 변경하지 않습니다. 계약 추가와 DB 스키마 변경은 C와 함께 맞춥니다.
- API 전용 응답은 `backend/app/schemas/`와 실행 서버 `/docs`, `/openapi.json`을 기준으로 합니다. 예를 들어 분석 실패 시 API의 `category`는 null일 수 있습니다.
- ID는 서버 UUID입니다. A가 반환한 추천/코디 ID는 저장 시 새 UUID로 바뀝니다. 피드백에는 API 응답에서 받은 최종 ID를 사용합니다.
- A/B는 별도 서버 대신 Python 함수로 연결하며 DB 조회·JWT·파일 접근 권한은 C가 담당합니다.
- 전체 검증: `conda activate univ18` → `.\scripts\verify_demo.ps1`. 실제 HTTP 두 모드만 확인하려면 `-E2EOnly`를 붙입니다.
- 시연 실행: `.\scripts\start_demo.ps1 -Provider local -Port 8001`. 계정은 `.env.demo`, 실행 안내는 [`DEMO_RUNBOOK.md`](DEMO_RUNBOOK.md).
- `.env` 변경 시 서버를 재시작합니다. 설정·어댑터는 프로세스 내 캐시를 사용합니다.

팀원이 넘겨줄 산출물과 함께 확인할 완료 조건은 다음과 같습니다.

| 담당 | C에게 전달할 것 | 공동으로 확인할 완료 조건 |
|---|---|---|
| A | 실제 `recommend()` 구현, 의존성 목록, LLM 환경변수 이름·호출 제한, 개인화에 필요한 추가 입력 | 현재 옷장 ID만 사용, 최대 3개 유효 코디, timeout/실패 대체, 피드백 반영 효과와 실제 지연 시간 |
| B | 실제 `extract()`/선택 `warmup()`, 의존성·모델 이름/버전·가중치 준비 방법, GPU/CPU 실행 조건, 샘플 사진·cutout | PATCH 보정 없이 실제 사진 분류, 임시 경로 옆 투명 PNG, 재분석 수동값 보존, timeout·탈퇴 중 늦은 출력 안전성, GPU 처리 시간 |
| D | 프론트 소스·빌드 명령과 `frontend/dist`, 라우팅 방식, `avatar_config` 구조와 3D 에셋 제공 방식 | 가입→옷 등록→추천→피드백→재분석→탈퇴의 실제 화면, JWT 이미지, 401/404/409/429 처리, 직접 URL·새로고침, 동의 철회·아바타 상태 정리 |

## 5. A — 추천 엔진

유지할 진입점:

```python
def recommend(context: UserContext, garments: list[GarmentAttributes]) -> RecommendationResult: ...
def warmup() -> None: ...  # 선택
```

- 위치: `recommendation/__init__.py`, 선택: `RECOMMENDER_PROVIDER=local`.
- 입력은 로그인한 사용자의 분석 완료·사용 가능·제외되지 않은 실제 옷입니다. 프로필 선호, 기온, 상황과 본인의 최근 피드백 최대 20건이 context에 포함됩니다.
- 결과는 최대 3개 코디입니다. 상의·하의·신발은 필수, 아우터는 선택이며 각 슬롯의 카테고리가 맞아야 합니다. 입력 밖 ID, 세탁/제외 옷, 중복 조합, NaN/Infinity 점수는 제거됩니다.
- `reason`은 사용자에게 표시되는 문장입니다. 정상 결과 없음·예외·기본 30초 초과 시 C가 규칙 기반 대체 추천을 저장합니다.
- LLM 키는 `os.environ["LLM_API_KEY"]`로 읽고 외부 호출에 자체 timeout을 둡니다. 어댑터 timeout은 실행 자체를 중지시키지 않습니다.
- 피드백은 즉시 `on_feedback()`으로 호출되지 않고 다음 `recommend()`의 `recent_feedback`으로 전달됩니다. 같은 코디에 여러 이벤트가 있을 수 있습니다.
- **개인화 계약 협의 필요:** 현재 피드백에는 추천/코디 UUID·수락 여부·사유만 있고 과거 코디의 옷 ID·색상·스타일 snapshot은 없습니다. A가 의류/스타일 단위로 학습하려면 C와 필드 확장·매핑을 정해야 합니다. A가 생성했던 임시 코디 ID로 서버 UUID를 역추적할 수 있다고 가정하면 안 됩니다.
- 현재 `disliked_colors`, 신체정보, 임베딩은 `UserContext`에 전달하지 않습니다.
- 전달받은 라이브러리 목록의 조건 충족과 실제 알고리즘 연결 완료는 별도입니다. 현재 저장소 `recommendation/__init__.py`는 첫 번째 가용 옷을 조합하는 임시 구현이며 새 알고리즘 소스가 아직 반영되지 않았습니다.
- A 완료 확인: 실제 알고리즘 교체 → 계약/어댑터 테스트 → local E2E → 실제 옷 사진·선호·수락/거절에 따른 품질 검증.

상세: [`recommendation/README.md`](../recommendation/README.md).

## 6. B — 비전 분석

유지할 진입점:

```python
def extract(image_path: str, garment_id: str) -> VisionResult: ...
def warmup() -> None: ...  # 선택, 모델 1회 로딩
```

- 위치: `vision/__init__.py`, 선택: `VISION_PROVIDER=local`.
- 입력은 업로드 원래 파일명이 아닌 검증·EXIF 회전 보정·축소 후 저장된 `original.jpg`의 **절대 경로**입니다.
- `garment_id`는 입력을 유지하고 category/color/pattern/style 값은 공통 계약 목록을 사용합니다. 모르는 값은 허용된 `unknown`/null, 실패는 `success=False`와 정리된 오류 사유로 반환합니다.
- `model_version`을 채우고 모델은 import/warmup 때 1회 로드합니다. 실패 사유에 비밀키나 불필요한 내부 정보를 넣지 않습니다.
- cutout은 원본 옆 `cutout.png`에 저장하고 `cutout_path`는 `{user_id}/{garment_id}/cutout.png`라는 **storage 상대 경로**로 반환합니다. user ID는 `Path(image_path).parent.parent.name`으로 구할 수 있습니다. 임의 절대 경로나 다른 사용자 경로는 제공되지 않습니다.
- 업로드·재분석 모두 임시 복사본 `image_path`를 받습니다. 전달된 파일 옆에 cutout을 쓰며 실제 storage 경로를 조립해 덮어쓰지 않습니다. C가 검증 후 별도 버전 파일로 게시하며 탈퇴 후에는 게시를 차단합니다.
- 현재 timeout은 기본 20초, GPU 호출 lock은 1개입니다. timeout 후 모델이 계속 실행될 수 있으므로 실제 모델의 실행 제한/실패/늦은 파일 생성도 함께 점검합니다.
- 실제 모델에서 수동 PATCH 없이 상의·하의·신발·아우터가 분류되는지, alpha가 있는 실제 cutout이 제공되는지 검증해야 합니다. 현재 `stub-0`/합성 seed 테스트는 이를 대신하지 않습니다.
- 5초를 넘는 처리와 동시 요청은 C와 비동기 작업 상태/재시도 방식을 결정합니다. GPU/CPU 환경별 설치·모델 다운로드·버전을 문서화합니다.

상세: [`vision/README.md`](../vision/README.md).

## 7. D — 프론트·아바타

- API 주소는 `/api/v1`, 개발 proxy는 `/api`와 `/media` 모두 백엔드로 연결합니다. 별도 origin 직접 호출 시 CORS 설정도 맞춥니다.
- 가입/로그인 외 보호 요청에는 `Authorization: Bearer <token>`을 보냅니다. 미인증·만료는 401, 타인/없는 리소스는 404이며 오류 분기는 JSON `code`로 처리합니다.
- 탈퇴는 `DELETE /api/v1/auth/me`에 JWT만 보내고 본문 없는 204를 처리합니다. 성공 후 토큰·사용자 데이터 캐시·이미지 object URL을 정리합니다. 기존 JWT는 모든 보호 요청에서 401이며 같은 이메일 재가입은 새 계정입니다.
- 옷 업로드는 multipart `file`, 성공 201입니다. `analysis_status=failed`도 등록은 성공하므로 수동 분류 입력 화면이 필요합니다. `pending`/`done`/`failed`를 구분합니다.
- 이미지는 인증이 필요합니다. `fetch`로 Blob을 받아 object URL로 표시하고 사용 후 해제합니다. `<img src>`에 상대 URL만 넣으면 Bearer 헤더가 붙지 않습니다.
- 추천 POST 성공은 201이며 `temperature_c`는 필수입니다. `NOT_ENOUGH_GARMENTS`와 `is_fallback`을 각각 처리합니다. 이미지는 응답의 `garments` 맵에서 찾습니다.
- 추천은 계정별 최근 60초 10회입니다. 429 `RECOMMENDATION_RATE_LIMITED`의 `Retry-After`를 읽어 대기 시간을 표시합니다. 토큰을 다시 발급받아도 같은 계정의 한도는 공유합니다.
- 재분석은 `POST /api/v1/garments/{id}/reanalyze`에 본문 없이 요청합니다. 200이어도 `analysis_status=failed`일 수 있으므로 `error`와 상태로 분기합니다. PATCH한 필드는 보존하며 동일 의류 처리 중에는 409 `GARMENT_BUSY`입니다. 성공 시 응답의 새 `cutout_url`을 사용합니다.
- 의류 삭제·탈퇴의 204에는 본문이 없습니다. DB 삭제가 완료됐으며 물리적 이미지 정리는 실패 시 지연될 수 있습니다. 의류 삭제 후 그 리소스는 404, 탈퇴 후 기존 JWT는 401입니다.
- 피드백 POST도 201입니다. 본문 추천 ID=경로 ID, 코디 ID는 응답에서 받은 값, `accepted`는 JSON bool이어야 합니다. 매 POST가 새 이벤트이므로 전송 중 중복 클릭을 막습니다.
- 옷 삭제 후 추천의 `outfits=[]`, 피드백의 `outfit_id=null`을 처리합니다. 이력의 의류 정보는 현재 속성/상태입니다.
- `avatar_config`는 동의가 필요한 최대 16KB JSON 저장 기능입니다. 3D 모델 생성/렌더링/GLB 업로드 API는 없습니다. 아바타 설정 스키마·에셋 제공·이미지 인증 방식을 D와 C가 확정해야 합니다.
- 빌드는 `frontend/dist/index.html`에 두고 `start_demo.ps1 -RequireFrontend`로 확인합니다. URL 경로 라우팅을 쓰면 위 SPA 직접 접속 처리가 필요합니다.
- D 완료 확인: 실제 화면에서 가입·등록·실패 수정·추천·피드백·재로그인 → 이미지/아바타 → 새로고침·직접 URL → 다른 사용자 접근 차단 리허설.

상세: [`frontend/README.md`](../frontend/README.md).

## 8. 권장 진행 순서

1. C: 임시값에 의존하는 local 테스트를 정리하고 PR CI를 구성한 뒤, 완료한 기능을 커밋·PR로 공유합니다. A·B 모델 제공을 기다리지 않고 진행할 수 있는 작업입니다.
2. A/B/D와 C: 실제 알고리즘·모델·프론트 빌드 제공, 개인화 입력·아바타 스키마·3D 에셋·라우팅 계약을 확정합니다.
3. C 주도 통합: 실제 사진/모델·프론트 E2E, 5초 기준 처리 시간·동시 요청, 이미지/3D 표시·직접 URL, 재분석·탈퇴 화면을 검증합니다. 자동 날씨·임베딩·S3는 별도 우선순위로 결정합니다.
4. 팀: 새 PC 설치·백업 노트북 복원, 필요한 외부 접속/초대 코드 설정, 최종 시연 리허설 후 배포 PR·태그·분리된 시연 checkout을 완료합니다.
