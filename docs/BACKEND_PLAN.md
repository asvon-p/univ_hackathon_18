# 역할 C — 백엔드·통합 시스템 설계 및 구현 계획서

> 작성일: 2026-11-07 · 갱신: 2026-11-08 (v1.4) · 담당: 팀원 C (Backend & Database, 시스템 통합)
> 관련 문서: [`DECISIONS.md`](DECISIONS.md) (팀 합의), [`CONTRIBUTING.md`](CONTRIBUTING.md) (Git 작업 방법)
> 현재 완료 범위·미구현 항목·추가 재현 문제·팀별 연동 사항: [`BACKEND_AUDIT.md`](BACKEND_AUDIT.md) (2026-11-07 점검). 단계 체크 완료는 계획서의 후속 운영 항목과 실제 모델/화면 통합까지 모두 끝났다는 뜻은 아닙니다.

**현재 구현:** 추천은 상의·하의 필수 + 선택 아우터입니다. A ranking 연결, D 호환 API, 기존 데이터 보존 `0010`, 계약 `2.0`, GPT-6 Luna 어댑터를 추가했습니다. [최신 통합 계약·설정·검증 범위](TEAM_API_CONTRACT.md)를 우선 참고하세요. 아래 단계별 테스트 수와 `(head)` 표기는 해당 단계 완료 당시의 기록입니다.

**자동 날씨 추가 완료:** JWT 기반 `GET /api/v1/weather`와 추천 POST의 `lat`·`lon` 입력을 지원합니다.
Open-Meteo 현재 기온을 추천에 적용하고 조회 실패 시 사용자가 입력한 기온으로 대체합니다.
PostgreSQL 전체 279개 통과, SQLite 260개 통과·19개 제외. [날씨 연동·설정·검증](WEATHER_INTEGRATION.md)을 참고하세요.
`.env`는 사용자 금지 해제 전까지 에이전트가 직접 또는 자동 로딩으로 읽거나 수정하지 않습니다.

**프론트 통합 완료:** D의 `85ddedc` 소스를 현재 `frontend/`에 반영했습니다.
가입·옷 등록·자동 기온·추천·수락/거절 화면과 PostgreSQL 전체 **281개 테스트**를 검증했습니다.
실행·검증 범위 및 전신 사진/착샷 등 제외 항목은 [프론트 통합 보고서](FRONTEND_INTEGRATION.md)를 참고하세요.

> 2026-11-08 통합 분석: 원격 D 산출물은 FastAPI/Jinja2 별도 화면 서버로 확인됐습니다. 이 문서의 `frontend/dist` 정적 제공은 당초 계획이며 현재 D 실행 방식과 차이가 있습니다. A ranking 진입점 연결·신발 계약·전신 사진/착샷 확장 및 최신 병합 순서는 [INTEGRATION_REVIEW.md](INTEGRATION_REVIEW.md)를 참고하세요.

---

## 0. 이 문서의 목적

1. 백엔드의 구조와 개발 환경을 **한 번에 완성**합니다.
2. A(추천)·B(비전)·D(프론트)의 기능이 **나중에 바뀌어도 백엔드 코드를 거의 고치지 않도록** 연결 지점을 설계합니다.
3. 데스크탑(RTX 5090) 한 대를 메인 서버로 하는 **시연·멘토 피드백 환경**까지 고려합니다.

### 설계 원칙

| 원칙 | 의미 |
|---|---|
| **계약 우선 (Contract-first)** | 모든 모듈은 `shared/contracts.py`의 Pydantic 모델로만 데이터를 주고받습니다. |
| **어댑터로 격리** | A·B 코드를 백엔드가 직접 부르지 않고, 중간 어댑터를 통해 부릅니다. 구현 교체는 설정 한 줄로 합니다. |
| **Mock 먼저** | 모든 연결 지점에 Mock 구현이 있어서, 다른 팀원 작업이 끝나지 않아도 전체 흐름이 동작합니다. |
| **실패해도 서비스는 동작** | 비전·LLM이 실패해도 업로드와 추천 요청은 오류로 끝나지 않고 대체 결과를 냅니다. |
| **단순함 유지** | MVP에서는 동기 SQLAlchemy, 단일 서버, 로컬 파일 저장을 씁니다. 바꿀 수 있는 구조만 만들어 둡니다. |

---

## 1. 전체 아키텍처

```
┌──────────── 노트북 / 멘토 / 휴대폰 브라우저 ────────────┐
│                     https://<터널 주소>                  │
└───────────────────────────┬──────────────────────────────┘
                            │ Cloudflare Tunnel (HTTPS)
┌───────────────────────────▼──────────── 데스크탑 (RTX 5090) ─┐
│  FastAPI (uvicorn :8000)                                      │
│   ├─ /              → 프론트엔드 정적 파일 (D의 빌드 결과)     │
│   ├─ /media/...     → 이미지 파일 (권한 확인 후 제공)          │
│   └─ /api/v1/...    → Router → Service → Repository           │
│                                    │                          │
│                     ┌──────────────┼──────────────┐           │
│                     ▼              ▼              ▼           │
│              VisionAdapter  RecommenderAdapter  WeatherAdapter│
│               (mock/local)    (mock/local)     (manual/api)   │
│                     │              │                          │
│              vision.extract  recommendation.recommend         │
│                (B, GPU)        (A, LLM API 호출 포함)          │
│                                                               │
│  PostgreSQL 16 (docker, 5432는 외부 비공개)   storage/ 폴더    │
└───────────────────────────────────────────────────────────────┘
```

### 요청 처리 계층

| 계층 | 위치 | 역할 | 하지 않는 것 |
|---|---|---|---|
| Router | `api/v1/*.py` | HTTP 입출력, 인증 확인, 스키마 검증 | 비즈니스 로직, DB 직접 접근 |
| Service | `services/*.py` | 업무 흐름 (업로드→분석→저장 등), 트랜잭션 | HTTP 관련 처리 |
| Repository | `repositories/*.py` | DB 조회·저장, **소유권 조건을 항상 포함** | 업무 판단 |
| Adapter | `adapters/*.py` | A·B·외부 API 호출, 시간 제한, 실패 처리 | DB 접근 |
| Mapper | `mappers.py` | ORM 모델 ↔ 계약 모델 변환 | — |

---

## 2. 디렉터리 구조 (목표)

```
univ_hackathon_18/
├─ environment.yml              # conda 환경 (Python 3.12.14)
├─ docker-compose.yml           # PostgreSQL 16 (+ pgvector)
├─ .env.example
├─ shared/
│  └─ contracts.py              # 팀 공통 계약 (CONTRACT_VERSION 포함)
├─ recommendation/              # A — recommend()
├─ vision/                      # B — extract()
├─ frontend/                    # D — 소스, 빌드 결과는 frontend/dist (커밋 제외)
├─ mocks/                       # Mock JSON, 샘플 이미지
├─ scripts/
│  ├─ dev.ps1                   # 자주 쓰는 명령 모음 (db, migrate, run, test, seed)
│  ├─ seed_demo.py              # 시연용 계정·옷 데이터 등록
│  └─ setup_branches.ps1
└─ backend/
   ├─ requirements.txt
   ├─ alembic.ini
   ├─ migrations/               # Alembic 마이그레이션
   ├─ app/
   │  ├─ main.py                # 앱 생성, lifespan(모델 로딩), 라우터·정적 파일 등록
   │  ├─ core/
   │  │  ├─ config.py           # Settings (pydantic-settings, .env)
   │  │  ├─ security.py         # 비밀번호 해시, JWT
   │  │  ├─ errors.py           # AppError, 에러 코드, 예외 핸들러
   │  │  └─ logging.py
   │  ├─ db/
   │  │  ├─ base.py             # DeclarativeBase, 공통 컬럼(id, created_at)
   │  │  ├─ session.py          # engine, SessionLocal, get_db
   │  │  └─ models.py           # ORM 모델 (테이블 정의)
   │  ├─ schemas/               # API 전용 요청/응답 모델 (계약 모델을 감싸거나 확장)
   │  ├─ repositories/          # users, garments, recommendations, feedback
   │  ├─ services/              # auth, profile, garment, recommendation, feedback
   │  ├─ adapters/
   │  │  ├─ base.py             # VisionPort, RecommenderPort, WeatherPort (Protocol)
   │  │  ├─ vision.py           # MockVision, LocalVision
   │  │  ├─ recommender.py      # MockRecommender, LocalRecommender, 결과 검증
   │  │  ├─ weather.py          # ManualWeather, OpenMeteoWeather (현재 기온·캐시)
   │  │  └─ registry.py         # 설정값에 따라 구현 선택 → FastAPI Depends
   │  ├─ storage/
   │  │  ├─ base.py             # StoragePort
   │  │  └─ local.py            # LocalStorage (나중에 S3Storage 추가)
   │  ├─ mappers.py
   │  ├─ deps.py                # get_current_user, get_db, 어댑터 주입
   │  └─ api/v1/                # auth, profile, garments, recommendations, media, health
   └─ tests/
      ├─ conftest.py            # 테스트 DB, 테스트 클라이언트, Mock 어댑터 고정
      ├─ contract/              # A·B 함수가 계약을 지키는지 검사
      ├─ api/                   # 엔드포인트 테스트
      └─ e2e/                   # 업로드→추천→피드백 전체 흐름
```

---

## 3. 개발 환경 세팅 (C가 완성할 부분)

### 3.1 구성 요소
| 항목 | 결정 |
|---|---|
| Python | conda 환경 `univ18` (3.12.14), `environment.yml` |
| DB | `pgvector/pgvector:pg16` 이미지 (PostgreSQL 16 + 벡터 확장, 지금은 설치만 하고 나중에 사용) |
| 라이브러리 추가분 | `pydantic-settings`, `sqlalchemy`, `psycopg[binary]`, `alembic`, `python-multipart`, `pyjwt`, `bcrypt`, `pillow`, `pgvector`, `httpx`, `pytest`, `ruff` |
| PyTorch | `requirements.txt`에 넣지 않음. 데스크탑(RTX 5090)은 **CUDA 12.8 빌드(`cu128`), PyTorch 2.7 이상** 필수. 팀원 노트북은 CPU 빌드. 설치 명령은 `vision/README.md`에 PC별로 기록 |

> 비밀번호 해시는 `passlib` 대신 `bcrypt`를 직접 사용합니다. `passlib`은 최신 `bcrypt`와 호환 경고가 있습니다.

### 3.2 설정값 (`.env` → `core/config.py`)
| 키 | 기본값 | 설명 |
|---|---|---|
| `APP_ENV` | `dev` | `dev` / `test` / `demo` |
| `DATABASE_URL` | `postgresql+psycopg://fitjang:fitjang@127.0.0.1:5432/fitjang` | Docker의 IPv4 loopback 공개 주소와 일치 |
| `TEST_DATABASE_URL` | `...:5432/fitjang_test` | 테스트 전용 DB |
| `JWT_SECRET`, `JWT_EXPIRE_MINUTES` | –, `1440` | |
| `STORAGE_DIR` | `./storage` | 이미지 저장 위치 |
| `STORAGE_DELETE_RETRY_SECONDS` | `30` | 파일 정리 worker 주기(초), 0이면 자동 재시도 중지 |
| `MAX_UPLOAD_MB` | `10` | |
| `VISION_PROVIDER` | `mock` | `mock` / `local` |
| `RECOMMENDER_PROVIDER` | `mock` | `mock` / `local` |
| `WEATHER_PROVIDER` | `open_meteo` | `open_meteo` 자동 조회 / `manual` 수동 입력 |
| `WEATHER_TIMEOUT_SEC` | `3` | 날씨 조회 대기 제한(초), 실패 시 수동 입력 |
| `WEATHER_CACHE_TTL_SEC` | `300` | 성공한 기온의 메모리 캐시(초), 0이면 비활성화 |
| `WEATHER_CACHE_MAX_ENTRIES` | `256` | 날씨 캐시 최대 항목 수 |
| `VISION_TIMEOUT_SEC` | `20` | 시간 초과 시 분석 실패 처리 |
| `RECOMMEND_TIMEOUT_SEC` | `30` | 시간 초과 시 대체 추천 |
| `LLM_API_KEY`, `WEATHER_API_KEY` | – | 서버 설정. Open-Meteo는 WEATHER_API_KEY를 사용하지 않음 |
| `CORS_ORIGINS` | `http://localhost:5173` | 개발 중 프론트 서버 |
| `SERVE_FRONTEND` | `false` | `true`면 `frontend/dist`를 `/`에서 제공 |
| `SIGNUP_INVITE_CODE` | 비어 있음 | 값이 있으면 이 코드가 있어야 가입 가능 (시연 공개 시) |

### 3.3 명령 모음 (`scripts/dev.ps1`)
```powershell
.\scripts\dev.ps1 db        # docker compose up -d
.\scripts\dev.ps1 migrate   # alembic upgrade head
.\scripts\dev.ps1 run       # uvicorn backend.app.main:app --reload
.\scripts\dev.ps1 test      # pytest backend/tests
.\scripts\start_demo.ps1 -PrepareOnly  # 시연 DB·seed 준비 (dev.ps1에 seed 명령은 없음)
.\scripts\dev.ps1 lint      # ruff format . ; ruff check .
```

---

## 4. 연결 지점 설계 (나중에 수정 가능하게 만드는 핵심)

### 4.1 포트(Port) — 백엔드가 기대하는 모양

```python
# backend/app/adapters/base.py
class VisionPort(Protocol):
    def extract(self, image_path: str, garment_id: str) -> VisionResult: ...


class RecommenderPort(Protocol):
    def recommend(self, context: UserContext, garments: list[GarmentAttributes]) -> RecommendationResult: ...


class WeatherPort(Protocol):
    def current(self, lat: float, lon: float) -> WeatherResponse | None: ...
```

### 4.2 구현체와 선택 방식

| 포트 | `mock` | `local` | 나중에 추가 가능 |
|---|---|---|---|
| Vision | 파일 이름 규칙 또는 고정값으로 속성 반환 | `vision.extract()` 호출 | `remote`: B의 모델을 별도 GPU 서버로 분리 시 HTTP 호출 |
| Recommender | 규칙 기반 간단 조합 (현재 stub) | `recommendation.recommend()` 호출 | `remote`: 추천 서버 분리 |
| Weather | `manual`: 요청에 담긴 기온 사용 | `open_meteo`: 현재 기온 조회·검증·캐시 | 다른 공급자는 WeatherPort 구현으로 추가 |

```python
# backend/app/adapters/registry.py (개념)
@lru_cache
def get_vision() -> VisionPort:
    return {"mock": MockVision, "local": LocalVision}[settings.VISION_PROVIDER]()
```

- 라우터·서비스는 `Depends(get_vision)`으로만 받습니다. **구현 교체 = `.env` 값 변경**.
- 일반 API/어댑터 테스트는 Mock이나 dependency override를 사용합니다. 단계 5의 실제 HTTP E2E는 `mock`/`local`을 각각 선택하고 함수·인증·서비스를 대체하지 않습니다.

### 4.3 어댑터가 책임지는 것 (A·B는 신경 쓰지 않아도 되는 부분)

| 책임 | Vision | Recommender |
|---|---|---|
| 시간 제한 | `VISION_TIMEOUT_SEC` 넘으면 실패 처리 | `RECOMMEND_TIMEOUT_SEC` 넘으면 대체 추천 |
| 예외 처리 | 예외 발생 시 `VisionResult(success=False, error=...)`로 변환 | 예외 발생 시 Mock(규칙) 결과로 대체 + 로그 |
| 결과 검증 | category가 비어 있으면 실패 처리 | **반환된 garment_id가 모두 요청 목록 안에 있고 `available`인지 재검증**, 위반 outfit 제거, 최대 3개 |
| 실행 방식 | 블로킹 함수이므로 스레드에서 실행 | 동일 |
| GPU 자원 | 서버 시작 시 1회 로딩(`warmup()`이 있으면 호출), 동시 실행 1개로 제한(lock) | — |
| 기록 | `model_version`, 처리 시간, 실패 사유 저장 | 사용한 provider, 처리 시간, 대체 여부 저장 |

### 4.4 A·B에게 요구하는 최소 규칙
1. 함수 이름·인자·반환 타입 유지 (`DECISIONS.md` 2절).
2. 선택 함수 `warmup() -> None`을 모듈에 두면 서버 시작 시 호출합니다(모델 미리 로딩). 없어도 됩니다.
3. DB·인증·HTTP 응답·최종 저장 경로는 C가 담당합니다. B는 전달된 임시 이미지 옆에 `cutout.png`를 쓰고 약속한 상대 경로를 반환합니다. 실제 storage 경로를 조립해 덮어쓰지 않습니다.
4. A의 LLM 호출 키는 `os.environ["LLM_API_KEY"]`로 읽습니다(서버 `.env`에서 로딩됨).

### 4.5 계약 버전 관리
- `shared/contracts.py`의 현재 버전은 `CONTRACT_VERSION = "2.0"`입니다. 신발 선택화(새 추천은 제외), 미분류 의류 category nullable, 요청 선호 자유 문자열을 반영했습니다. 1.1의 recent_feedback은 유지합니다.
- 필드 **추가**(기본값 있음) → 마이너 버전 증가(1.1), 기존 코드 영향 없음.
- 필드 **이름 변경·삭제·타입 변경** → 메이저 버전 증가(2.0), 전원 합의 + C가 DB·매퍼 동시 수정.
- `tests/contract/`에서 Mock JSON과 A·B 함수 결과가 계약 모델로 검증되는지 매 PR마다 확인합니다.

---

## 5. 데이터베이스 설계

### 5.1 테이블

**users**
| 컬럼 | 타입 | 비고 |
|---|---|---|
| id | UUID PK | |
| email | varchar(255) UNIQUE | 소문자 저장 |
| password_hash | varchar(255) | bcrypt |
| nickname | varchar(50) | |
| is_active | bool | 비활성 계정 인증 차단. 탈퇴 API는 사용자 행을 직접 삭제 |
| is_demo | bool | 서버 seed 계정 표시, API 입력/출력 제외. 재생성 시 새 UUID 발급 (`0009`) |
| created_at, updated_at | timestamptz | UTC |

**user_profiles** (민감 정보 분리, 모두 선택)
| 컬럼 | 타입 | 비고 |
|---|---|---|
| user_id | UUID PK/FK → users | 1:1 |
| gender | varchar(10) NULL | |
| height_cm, weight_kg | double precision NULL | 소수 허용, 기존 정수 보존 (`0010`) |
| birth_year | smallint NULL | 기존 출생연도 보존 |
| age | smallint NULL | D 입력 나이 0~130. 출생연도와 상호 추정하지 않음 (`0010`) |
| preferred_styles | varchar[] | `StyleTag` 값 |
| disliked_colors | varchar[] | `Color` 값 |
| consent_body_info_at | timestamptz NULL | 신체정보 수집 동의 시각 |
| avatar_config | JSONB NULL | D의 `AvatarConfig` |

**garments**
| 컬럼 | 타입 | 비고 |
|---|---|---|
| id | UUID PK | 계약의 `garment_id` |
| user_id | UUID FK, INDEX | 모든 조회 조건에 포함 |
| status | varchar(20) | `available` / `laundry` / `inactive` |
| analysis_status | varchar(20) | `pending` / `done` / `failed` |
| original_path | varchar(500) | storage 상대경로 |
| name, location | varchar(100) NULL | 사용자 이름·보관 위치, 재분석 시 보존 (`0010`) |
| cutout_path | varchar(500) NULL | 투명 PNG |
| created_at, updated_at | timestamptz | |

**garment_attributes** (분석 결과, 1:1)
| 컬럼 | 타입 | 비고 |
|---|---|---|
| garment_id | UUID PK/FK | |
| category | varchar(20) NULL, INDEX | 필터용 일반 컬럼. 분석 전·실패 시 NULL, 수동 입력 시 완료 처리 |
| subcategory | varchar(50) NULL | |
| color | varchar(20) | |
| pattern | varchar(20) | |
| style_candidates | varchar[] | |
| thermal_level | smallint NULL | 1~5 |
| source | varchar(10) | `model` / `user` (사용자가 수정했으면 user) |
| user_edited_fields | JSONB | PATCH한 분석 필드 이름 목록, 명시적 null/빈 목록도 보호. `0008` 추가 |
| model_version | varchar(50) NULL | |
| raw_output | JSONB NULL | B의 원본 예측값 (확률 등) |
| error | text NULL | 분석 실패 사유 |
| analyzed_at | timestamptz | |

**garment_embeddings** (P1, pgvector)
| garment_id UUID PK/FK | model_name varchar | embedding vector(512) | — 유사 옷 검색·개인화용, B가 임베딩을 반환하면 저장

**recommendations**
| 컬럼 | 타입 | 비고 |
|---|---|---|
| id | UUID PK | 계약의 `recommendation_id` |
| user_id | UUID FK, INDEX | |
| context | JSONB | 요청 당시 `UserContext` 전체 (재현용) |
| provider | varchar(20) | `mock` / `local` / `openai` / `fallback` |
| latency_ms | int | |
| created_at | timestamptz INDEX | |

**recommendation_outfits**
| 컬럼 | 타입 | 비고 |
|---|---|---|
| id | UUID PK | 계약의 `outfit_id` |
| recommendation_id | UUID FK | |
| rank | smallint | 1~3, 추천 내 순위 UNIQUE |
| top_id, bottom_id | UUID FK → garments | 필수. ON DELETE CASCADE |
| shoes_id | UUID FK NULL | 과거 추천 보존. 새 추천은 NULL (`0010`) |
| outerwear_id | UUID FK NULL | ON DELETE CASCADE |
| style_tags | varchar[] | |
| reason | text | |
| score | double precision NULL | 유한한 숫자만 저장 |

> 새 추천은 세 슬롯(top/bottom/outerwear)이며, shoes 컬럼은 과거 데이터 보존용으로 남깁니다.

**feedback_events**
| 컬럼 | 타입 | 비고 |
|---|---|---|
| id | UUID PK | |
| user_id | UUID FK NULL, INDEX | 사용자 삭제 시 SET NULL |
| recommendation_id, outfit_id | UUID FK NULL, INDEX | 대상 삭제 시 SET NULL, 로그 보존 |
| accepted | bool | |
| reason | text NULL | 최대 1,000자, 공백 제거·빈 값 NULL |
| created_at | timestamptz INDEX | 로그성 데이터, 삭제하지 않음 |

**recommendation_rate_limits** (마이그레이션 `0007`)

| 컬럼 | 타입 | 비고 |
|---|---|---|
| user_id | UUID PK, FK → users | 사용자별 1행, 사용자 삭제 시 CASCADE |
| request_times | jsonb | 최근 허용 요청 시각(UTC epoch 초) 최대 10개 |

추천 생성 시작 시 사용자 행을 잠그고 DB 현재 시각 기준 60초가 지난 기록을 제외합니다. 10회 미만이면 시각을 추가해 먼저 커밋하며, 초과면 429로 차단합니다. 엔진 실행 중에는 사용자 잠금을 유지하지 않습니다. 사용량은 추천 결과 저장과 별도로 확정해 후속 실패에도 남습니다. 새 서버 프로세스도 같은 DB 기록을 사용합니다.

**storage_deletion_jobs** (마이그레이션 `0006`)

| 컬럼 | 타입 | 비고 |
|---|---|---|
| garment_id | UUID PK | 삭제된 의류 ID, FK 없음 |
| user_id | UUID | 파일 경로 구성용, 사용자 삭제 후에도 정리 작업 보존 |
| attempts | int | 파일 삭제 실패 횟수, 0 이상 |
| last_error | varchar(100) NULL | 최근 파일 오류 종류만 저장 |
| available_at | timestamptz INDEX | 다음 재시도 가능 시각 |
| created_at | timestamptz | 작업 생성 시각 |

의류 삭제와 같은 트랜잭션으로 기록하고 커밋 이후에만 파일을 삭제합니다. 정리 성공 시 작업을 삭제하며 실패 시 재시도합니다.

**account_storage_deletion_jobs** (마이그레이션 `0009`)

| 컬럼 | 타입 | 비고 |
|---|---|---|
| user_id | UUID PK | 삭제된 사용자 폴더 식별자, FK 없음 |
| attempts | int | 파일 삭제 실패 횟수, 0 이상 |
| last_error | varchar(100) NULL | 최근 파일 오류 종류만 저장 |
| available_at | timestamptz INDEX | 다음 재시도 가능 시각 |
| created_at | timestamptz | 작업 생성 시각 |

탈퇴와 함께 커밋하며 기존 의류별 대기 작업을 대체합니다. 원본·cutout·이전 버전·미등록 파일까지 사용자 폴더 전체를 정리한 뒤 작업 행을 삭제합니다. 실패하면 서버 worker와 `scripts.cleanup_storage`가 재시도합니다.

### 5.2 규칙
- 모든 테이블 변경은 Alembic 마이그레이션 파일로만 합니다 (`alembic revision --autogenerate -m "..."`).
- 마이그레이션 파일은 C만 생성합니다. 동시에 두 개가 생기면 충돌하기 때문입니다. 다른 팀원은 필요한 컬럼을 Issue로 요청합니다.
- 사용자 삭제 시: 옷·이미지·추천 기록 삭제, `feedback_events`는 사용자·추천·코디 참조와 자유 입력 사유를 비우고 수락 여부·시각만 익명 통계로 보존합니다. 단계 4의 DB 트리거가 SQL 삭제에도 이 정책을 적용합니다(사업화 대비, 동의 문구에 명시).

### 5.3 ORM ↔ 계약 변환 (`mappers.py`)
| 계약 모델 | ORM |
|---|---|
| `GarmentAttributes` | `garments` + `garment_attributes` 조인 |
| `UserContext` | 요청 본문 + `user_profiles.preferred_styles` 기본값 + 세탁/비활성 옷 자동 제외 |
| `RecommendationResult` | `recommendations` + `recommendation_outfits` |
| `FeedbackEvent` | `feedback_events` |

ORM과 계약 사이 변환은 이 파일에 모읍니다. 계약에 속성을 추가하면 DB 모델·마이그레이션뿐 아니라 API 요청/응답 스키마와 재분석의 수동 수정 보존도 함께 점검합니다(13절).

---

## 6. API 설계

### 6.1 엔드포인트
| Method | Path | 인증 | 설명 | 우선순위 |
|---|---|---|---|---|
| GET | `/health` | – | 서버·DB·어댑터 상태 | P0 |
| POST | `/api/v1/auth/signup` | – | 회원가입 (초대 코드 선택) | P0 |
| POST | `/api/v1/auth/login` | – | JWT 발급 | P0 |
| GET | `/api/v1/auth/me` | ✔ | 내 정보 | P0 |
| DELETE | `/api/v1/auth/me` | ✔ | 본인 탈퇴·데이터 삭제·피드백 익명화·이미지 정리. 성공 204 | 구현 완료 |
| GET / PATCH | `/api/v1/profile` | ✔ | 프로필·선호 조회/수정 | P0 (단계 2) |
| GET / PUT | `/api/v1/users/me/profile` | ✔ | D 호환 경로, 동의 및 본인 범위 유지 | 구현 완료 |
| POST | `/api/v1/garments` | ✔ | 사진 업로드 → 분석 → 저장 (multipart) | P0 |
| GET | `/api/v1/garments` | ✔ | 옷장 목록 (`?category=&status=`) | P0 |
| GET | `/api/v1/garments/{id}` | ✔ | 단건 | P0 |
| GET | `/api/v1/garments/{id}/image` | ✔ | kind=cutout/original, 미생성 cutout은 원본 대체 | 구현 완료 |
| PATCH | `/api/v1/garments/{id}` | ✔ | 속성 수정, 세탁 중 표시 | P0 |
| DELETE | `/api/v1/garments/{id}` | ✔ | 삭제 (이미지 포함) | P0 |
| POST | `/api/v1/garments/{id}/reanalyze` | ✔ | 원본 재분석, 수동 수정 필드 보존. 성공/분석 실패 모두 200 + 상태 | P1 |
| POST | `/api/v1/recommendations` | ✔ | 추천 요청 | P0 |
| GET | `/api/v1/recommendations` | ✔ | 추천 이력 (`limit`, `offset`) | 단계 3에서 구현 |
| GET | `/api/v1/recommendations/{id}` | ✔ | 추천 결과 | P0 |
| POST | `/api/v1/recommendations/{id}/feedback` | ✔ | 수락·거절 | P1 |
| GET | `/api/v1/recommendations/{id}/feedback` | ✔ | 본인 추천의 피드백 이력 (`limit`, `offset`) | 단계 4에서 구현 |
| GET | `/media/{path}` | ✔ | 이미지 (본인 것만) | P0 |

### 6.2 주요 요청·응답
```jsonc
// POST /api/v1/recommendations  요청
{ "temperature_c": 8.5, "occasion": "daily", "preferred_styles": ["minimal"], "excluded_items": [] }
// user_id는 토큰에서, preferred_styles를 비우면 프로필 값 사용

// 응답 = RecommendationResult + 화면 표시용 확장 정보
{
  "recommendation_id": "uuid",
  "outfits": [{
    "outfit_id": "uuid",
    "items": { "top": "uuid", "bottom": "uuid", "shoes": null, "outerwear": null },
    "style_tags": ["minimal"], "reason": "...", "score": 0.82
  }],
  "garments": { "uuid": { /* GarmentAttributes + image_url, cutout_url */ } },
  "provider": "local", "is_fallback": false
}
```
`garments` 맵을 함께 보내서 D가 옷 정보를 다시 조회하지 않아도 화면과 2D 아바타를 그릴 수 있게 합니다.

### 6.3 에러
- 형식: `{"detail": "사람이 읽는 메시지", "code": "ERROR_CODE"}`
- `core/errors.py`에 `AppError(status, code, detail)` 하나만 두고 전역 핸들러로 변환합니다. Pydantic 검증 오류(422)도 같은 형식으로 바꿉니다.

| code | status | 상황 |
|---|---|---|
| `AUTH_INVALID_CREDENTIALS` | 401 | 로그인 실패 |
| `AUTH_TOKEN_EXPIRED` | 401 | 토큰 만료 |
| `AUTH_REQUIRED` | 401 | Bearer 인증 누락 |
| `AUTH_INVALID_TOKEN` | 401 | 위조·잘못된 토큰, 없는 사용자·비활성 계정 |
| `BODY_INFO_CONSENT_REQUIRED` | 422 | 신체정보·아바타 저장 전 동의 누락 |
| `EMAIL_ALREADY_EXISTS` | 409 | 중복 가입 |
| `INVITE_CODE_INVALID` | 403 | 초대 코드 오류 |
| `GARMENT_NOT_FOUND` | 404 | 없거나 남의 옷 |
| `GARMENT_IMAGE_NOT_FOUND` | 404 | 재분석할 원본 파일이 없거나 읽을 수 없음 |
| `GARMENT_BUSY` | 409 | 같은 의류가 재분석/수정/삭제 처리 중. 잠시 후 재시도 |
| `UPLOAD_TOO_LARGE` | 413 | 10MB 초과 |
| `UPLOAD_UNSUPPORTED_TYPE` | 415 | jpg/png 아님 (실제 파일 내용으로 판별) |
| `NOT_ENOUGH_GARMENTS` | 422 | 상의·하의 중 사용 가능한 옷이 없음 |
| `RECOMMENDATION_NOT_FOUND` | 404 | |
| `RECOMMENDATION_RATE_LIMITED` | 429 | 같은 로그인 사용자의 최근 60초 추천 생성 요청 10회 초과. `Retry-After`: 재시도까지 정수 초 |
| `VALIDATION_ERROR` | 422 | 요청 형식 오류 |
| `INTERNAL_ERROR` | 500 | 그 외 |

---

## 7. 핵심 흐름

### 7.1 옷 업로드
```
POST /garments (file)
 1. 크기 검사(스트리밍 중 10MB 초과 시 중단) → Pillow로 열어 실제 형식 확인(jpg/png)
 2. EXIF 회전 보정, 긴 변 2048px로 축소, jpg로 저장  → storage/{user}/{garment}/original.jpg
 3. garments 행 생성 (analysis_status=pending)  ← 여기까지는 비전과 무관하게 항상 성공
 4. 원본 복사본으로 VisionAdapter.extract(path, garment_id) 실행 (시간 제한, GPU lock)
 5-a. 성공 → garment_attributes 저장, cutout 있으면 cutout_path 저장, analysis_status=done
 5-b. 실패 → error 저장, analysis_status=failed  (사용자가 PATCH로 직접 입력 가능)
 6. 응답: 옷 정보 + analysis_status
```
- MVP는 요청 안에서 동기 처리합니다. 1장당 5초를 넘으면 4~5번을 `BackgroundTasks`로 옮기고 프론트가 `analysis_status`를 다시 조회하도록 바꿉니다. **응답 형식은 처음부터 `analysis_status`를 포함**해서 이 변경이 D에게 영향을 주지 않게 합니다.

### 7.2 추천
```
POST /recommendations
 0. JWT 인증·본문 검증 → 사용자별 최근 60초 10회 검사·사용량 커밋 (초과 시 429, 엔진 호출 없음)
 1. 좌표가 있으면 현재 기온 조회 → 실패 시 요청 temperature_c 사용, 둘 다 없으면 503 WEATHER_UNAVAILABLE
    UserContext 구성: 토큰 user_id + 확정한 기온 + 요청값 + 프로필 기본값 (좌표는 엔진·DB에 전달하지 않음)
 2. 사용 가능한 옷만 조회: status=available AND analysis_status=done AND id NOT IN excluded
 3. 상의·하의가 하나라도 없으면 NOT_ENOUGH_GARMENTS (아우터 선택, 신발 제외)
 4. RecommenderAdapter.recommend(context, garments)  (시간 제한)
 5. 결과 검증: ID 소속·사용 가능 여부 재확인, 위반 outfit 제거, 최대 3개
 6. 결과가 비었거나 실패 → 규칙 기반 대체 추천 (is_fallback=true)
 7. recommendations + recommendation_outfits 저장 (A가 준 ID 대신 서버가 UUID 발급, 원래 ID는 무시)
 8. 응답 (garments 맵 포함)
```

### 7.3 피드백
```
POST /recommendations/{id}/feedback
 1. JWT 인증, UUID·bool·사유 길이 검증
 2. 경로/본문 추천 ID 일치 및 코디가 본인 추천에 속하는지 확인 (삭제와 겹치지 않게 행 잠금)
 3. feedback_events에 새 이벤트 저장·커밋, 실패 시 롤백
 4. 서버 UUID·UTC 시각이 포함된 저장 결과를 201로 반환
 5. 다음 추천 요청에서 본인의 유효한 최근 피드백 20건을 UserContext.recent_feedback으로 전달
```
A의 개인화는 직접 DB를 읽지 않고 계약 1.1의 `recent_feedback: list[FeedbackEvent]`를 사용합니다. 기본값은 `[]`이며 기존 입력을 유지합니다. 별도 `on_feedback` 콜백 대신 다음 추천의 context로 전달하므로 피드백 저장이 엔진 상태에 의존하지 않습니다. 점수 반영 알고리즘과 추가 개인화 속성은 A의 후속 작업입니다.

---

### 7.4 의류 재분석

`POST /garments/{id}/reanalyze`는 본문 없이 JWT로 요청합니다. 소유권 확인 후 해당 의류 행을 NOWAIT로 잠그며, 동일 의류 처리 중이면 409입니다. 원본 복사본으로 `SafeVision`을 실행하고 수동 수정하지 않은 필드만 적용한 뒤 결과를 커밋합니다. PATCH/DELETE도 같은 의류 잠금을 사용하며 대기 후 새 쿼리로 속성을 읽으므로 오래된 값으로 수동 수정 기록을 덮어쓰지 않습니다.

- 분석 성공/실패는 모두 200 `GarmentView`. 성공은 `done`·`error=null`, 재실패/timeout/잘못된 모델 속성은 `failed`·오류 메시지이며 기존 속성과 이미지 경로는 유지합니다. 실패 상태는 새 추천 대상에서 제외하고 재시도/수동 수정으로 복구할 수 있습니다.
- `user_edited_fields`에는 PATCH로 명시한 분석 필드를 누적합니다. 명시적 null/빈 배열도 보호하고 `status`는 별도로 항상 보존합니다. 보호 필드가 있으면 `source=user`이며 원래 모델 출력은 `raw_output`에 별도 기록합니다.
- `0008` 이전 `source=user`의 수정 필드는 알 수 없어 모든 분석 필드를 보호합니다. 단 `category=null`은 수동 지정 불가능했으므로 모델이 채울 수 있게 남깁니다. 기존 모델 속성은 보호 목록이 비어 있고 새 PATCH부터 필드 단위로 추적합니다.
- 동기 분석 시간 동안 의류 행 잠금을 유지합니다. 응답 timeout 후에는 잠금을 해제하고 늦은 모델 작업은 임시 폴더에만 쓰며, 작업 종료 시 해당 폴더를 정리합니다. 장시간 모델 작업의 비동기 전환은 기존 성능 점검 항목입니다.

### 7.5 사용자 탈퇴

`DELETE /api/v1/auth/me`는 JWT 사용자 본인만 삭제합니다. 본문은 생략하거나 `{}`이며 사용자 ID·이메일 등 추가 필드는 422입니다. 성공은 본문 없는 204입니다.

1. 활성 사용자 행을 배타 잠금하고 계정·프로필·옷장/속성·추천/코디·추천 요청 사용량을 삭제합니다. DB 트리거가 피드백의 `user_id`, `recommendation_id`, `outfit_id`, `reason`을 NULL로 지우고 이벤트 ID·수락 여부·생성 시각만 보존합니다.
2. 사용자 폴더 정리 작업을 같은 트랜잭션으로 기록하고 기존 의류별 정리 작업을 대체합니다. 이 커밋이 실패하면 데이터·피드백·파일을 보존합니다.
3. 커밋 후 전체 사용자 폴더를 정리합니다. 파일 삭제 실패/부분 실패 시에도 204이며 영속 작업으로 재시도합니다. 정리 완료 기록의 커밋 실패나 최초 커밋 응답 유실도 남은 작업으로 복구합니다.
4. 탈퇴 후 기존 JWT는 만료 전이라도 보호 API·미디어에서 401입니다. 같은 이메일로 재가입하거나 seed를 다시 만들면 새 사용자 UUID를 발급하므로 과거 JWT가 되살아나지 않습니다. 기존 seed는 `0009`에서 ID·데이터를 보존하고 `is_demo`만 표시합니다.

쓰기 작업은 사용자 행부터 잠가 탈퇴와 자식 행 삭제를 직렬화합니다. 업로드 최초 저장 이후 분석은 복사본에서 수행하고 게시 직전에 사용자를 다시 확인합니다. 분석 중 탈퇴가 먼저 끝나면 게시를 401로 차단합니다. 재분석은 분석 결과 커밋까지 사용자 잠금을 유지하므로 탈퇴가 완료를 기다립니다. timeout 후 늦은 출력은 두 경우 모두 임시 폴더에만 남고 모델 스레드 종료 시 정리됩니다.

## 8. 저장소(이미지) 설계

```python
class StoragePort(Protocol):
    def save(self, key: str, data: bytes) -> str: ...  # key 예: "{user}/{garment}/original.jpg"
    def open(self, key: str) -> BinaryIO: ...
    def local_path(self, key: str) -> str: ...  # 인증된 미디어 응답용 실제 경로
    def delete_prefix(self, prefix: str) -> None: ...
```
- MVP: `LocalStorage` (`STORAGE_DIR`), 이미지 제공은 `/media/{key}`에서 **소유자 확인 후** `FileResponse`.
- 사업화: `S3Storage`(S3·R2), 저장소 선택 설정·의존성 주입과 인증된 이미지 전달 방식을 추가합니다. 현재 `/media`는 로컬 경로의 `FileResponse`이므로 스트리밍/서명 URL 전환에는 서비스·라우터 연결 변경이 필요합니다.
- 경로에 사용자 입력을 직접 넣지 않습니다(UUID만 사용, 경로 조작 방지).
- 재분석의 새 cutout은 `cutout-{UUID}.png`에 저장하고 DB 경로만 바꿉니다. 기존 이미지와 원본은 덮어쓰지 않으며 cutout이 없거나 잘못되면 기존 것을 유지합니다. 커밋 실패/응답 유실에 대비해 이전 버전·미등록 버전도 의류 삭제 시 폴더 정리까지 보관합니다. `/media`는 현재 DB에 등록된 파일만 제공합니다.

---

## 9. 보안·개인정보

| 항목 | 방법 |
|---|---|
| 비밀번호 | bcrypt 해시, 최소 8자·UTF-8 최대 72바이트 (잘라서 저장하지 않음) |
| 인증 | JWT(HS256, 24시간), `Authorization: Bearer` |
| 소유권 | Repository의 모든 조회에 `user_id` 조건, 남의 데이터는 404 |
| 업로드 | 크기 제한, 실제 이미지 판별, 재인코딩 저장(악성 파일·메타데이터 제거, EXIF 위치 정보 삭제) |
| 비밀 정보 | `.env`에만, 로그에 키·토큰·비밀번호 출력 금지 |
| 외부 공개(터널) | 초대 코드·DB loopback 바인딩·로그인 사용자별 추천 최근 60초 10회 제한 구현. 초과 시 429 + Retry-After |
| CORS | 개발 중 프론트 주소만 허용, 시연 시에는 같은 주소라 불필요 |
| 민감 정보 | 신체 정보 동의/철회, 탈퇴 시 계정·프로필·옷장·추천 삭제 및 피드백 참조·사유 익명화. 사용자 폴더 정리는 커밋 후 실행·실패 시 영속 재시도 |

---

## 10. 테스트 전략

| 종류 | 위치 | 내용 | 실행 시점 |
|---|---|---|---|
| 계약 테스트 | `tests/contract/` | Mock JSON, `vision.extract`, `recommendation.recommend` 결과가 계약 모델로 검증되는지 | 모든 PR |
| API 테스트 | `tests/api/` | 엔드포인트별 정상·오류, **다른 사용자 데이터 접근 차단** | 모든 PR |
| 어댑터 테스트 | `tests/adapters/` | 시간 초과·예외·잘못된 ID 반환 시 대체 동작 | 모든 PR |
| E2E | `tests/e2e/` | 가입→업로드 3벌→추천→피드백→재로그인 후 이력 확인 | develop 병합 전, 시연 전 |
| GPU 실측 | 수동 | 데스크탑에서 `VISION_PROVIDER=local`로 처리 시간 측정 | B 기능 병합 시 |

- PostgreSQL API 테스트는 `fitjang_test`의 격리 스키마·트랜잭션을 롤백합니다. 실제 HTTP E2E는 별도 스키마에 커밋한 뒤 자식 서버 종료·스키마 삭제로 정리합니다.
- 나중에 GitHub Actions로 `ruff` + `pytest`(Mock 모드)를 PR마다 자동 실행하고, 브랜치 규칙의 필수 체크로 등록합니다.

---

## 11. 시연·운영 (데스크탑 서버)

| 항목 | 방법 |
|---|---|
| 실행 코드 | 시연 서버 폴더는 **`main` 브랜치(태그 `v1.0-demo`)만** 체크아웃. 개발 폴더와 분리 |
| 설정 | `APP_ENV=demo`, `VISION_PROVIDER=local`, `RECOMMENDER_PROVIDER=local`, `SERVE_FRONTEND=true` |
| 프론트 | D가 `frontend/dist` 빌드 → FastAPI가 `/`에서 제공, API 주소는 상대 경로 `/api/v1` |
| 외부 접속 | Cloudflare Tunnel → `localhost:8000` 하나만 연결 |
| 시작 | `scripts/start_demo.ps1`: DB → 마이그레이션·스키마 검사 → seed → 단일 서버 → health 확인. 외부 터널·자동 시작 등록은 후속 운영 항목 |
| 시연 데이터 | `scripts/seed_demo.py`: 시연 계정 + 의류 12벌(미리 지정한 속성·합성 원본·투명 cutout), 반복 실행 시 기존 수정 내용 보존 |
| 백업 | 노트북에 같은 환경 + DB 덤프(`pg_dump`) + storage 복사, `VISION_PROVIDER=mock`으로 실행 |
| 점검 | `/health`에서 DB 연결, 어댑터 종류, GPU 사용 여부(`torch.cuda.is_available`) 표시 |
| 서버 프로세스 | **uvicorn 워커 1개**로 실행 (`--workers` 사용 안 함). 워커마다 비전 모델이 GPU에 따로 올라가 VRAM을 낭비하기 때문. 동시 요청은 스레드로 처리하고 GPU 호출은 lock으로 1개씩 |
| 시연 중 `--reload` | 사용하지 않음 (파일 변경 시 모델을 다시 로딩함) |

---

## 12. 브랜치별 구현 계획

### 단계 0 — `chore/project-setup` (즉시)
- [x] `environment.yml`, `requirements.txt` 정리, `docker-compose.yml`을 pgvector 이미지로 변경
- [x] `core/config.py`, `core/errors.py`, `core/logging.py`
- [x] `db/base.py`, `db/session.py`, Alembic 초기화
- [x] `adapters/` 포트·Mock·Local 구현·registry, `storage/local.py`
- [x] `/health`, 계약 테스트, `scripts/dev.ps1`
- [x] `CONTRACT_VERSION` 추가
- **완료 기준:** 새 PC에서 문서대로 따라 하면 30분 안에 서버 실행 + 테스트 통과, Mock 모드로 `/docs`에서 추천 API 호출 가능

### 단계 1 — `feat/garment-storage` (P0)
- [x] `users`(임시 고정 사용자 허용), `garments`, `garment_attributes` 모델 + 마이그레이션 `0002`
- [x] 업로드·목록·단건·수정·삭제 API, `/media`
- [x] 업로드 검증, 이미지 재인코딩, VisionAdapter 연동
- **완료 기준:** Mock 비전으로 사진 업로드 → DB 저장 → 목록 조회, 분석 실패해도 업로드 성공

**단계 1 구현 사항 / 사용 방법**
- `POST /api/v1/garments`: multipart `file`, 성공 시 **201**. 실제 JPG/PNG 바이트와 용량을 검사하고, EXIF 회전 보정·긴 변 2048px 축소·메타데이터 제거 후 `original.jpg`로 저장합니다. 과도한 디코딩 메모리를 막기 위해 입력 해상도는 3,200만 픽셀 이하입니다.
- `GET /api/v1/garments?category=top&status=available`: DB에 저장된 내 옷 목록. 단건 조회·수정·삭제는 `/api/v1/garments/{UUID}`이며 삭제 성공 시 **204**입니다.
- 분석 실패도 **201**이며 `analysis_status=failed`, `category=null`, `error`를 반환합니다. `PATCH`로 `category`를 입력하면 `done`, `source=user`로 전환합니다. 세탁 상태만 바꾸면 분석 상태는 유지합니다.
- `PATCH`는 `category`, `subcategory`, `color`, `pattern`, `style_candidates`, `thermal_level`, `status`만 허용합니다. 두께는 1~5 또는 null, 세부 카테고리는 최대 50자 또는 null입니다.
- `image_url`과 `cutout_url`은 `/media/...` 상대 URL입니다. DB에 등록된 본인 의류의 경로만 제공합니다. 삭제 시 의류·속성과 파일 정리 작업을 함께 DB에 커밋한 뒤 원본·cutout을 제거합니다. 커밋 실패 시 파일은 보존합니다. 파일 삭제 실패는 204 응답을 바꾸지 않으며, 옷·이미지 접근 차단 후 DB 작업 기록으로 자동 재시도합니다(`0006`).
- 단계 1에서 사용했던 임시 사용자 자동 생성은 **단계 2에서 제거**했습니다. 현재 모든 환경에서 JWT의 사용자 ID를 사용하며 기존 임시 계정은 마이그레이션 `0003`에서 비활성화합니다. 기존 옷·이미지는 보존하며 다른 신규 사용자에게 자동 이전하지 않습니다.
- 비전에는 저장 원본의 임시 복사본 절대 경로를 전달합니다. 파일명은 `original.jpg`이므로 현재 파일명 기반 Mock은 기본값(상의/색상 미상)을 반환합니다. 다른 분류는 `PATCH`로 설정하거나 B의 실제 모델을 연결합니다.
- **단계 3부터 추천 생성·조회는 실제 옷장과 DB를 사용합니다.** 단계 4에서 피드백 저장·조회도 구현했습니다.
- `.gitignore`의 `storage/`를 `/storage/`로 좁혀 `backend/app/storage/` 소스가 Git에서 누락되지 않도록 수정했습니다.

**검증 완료 (2026-11-07):** Docker 엔진 정상 응답과 PostgreSQL 16 컨테이너의 `healthy` 상태를 확인했습니다. 개발 DB `fitjang`과 테스트 DB `fitjang_test` 모두 마이그레이션 `0002 (head)` 적용 완료. 실제 PostgreSQL 모드에서 전체 **56개 테스트 통과, 건너뜀 없음**을 확인했습니다. 옷 업로드·조회·수정·삭제·파일 검증·분석 실패 복구·다른 사용자 접근 차단, 마이그레이션 되돌리기/재적용, ORM과 DB 스키마 일치를 검증했습니다. 테스트 DB의 임시 스키마는 트랜잭션 롤백으로 정리하며 기존 테이블을 삭제하지 않습니다.

**연결 설정:** 재확인 시 이전 Docker 소켓 오류는 재현되지 않았습니다. 별도로 `localhost`를 사용한 DB 연결이 지연되어, Docker가 실제 공개하는 `127.0.0.1:5432`로 로컬 `.env`와 기본 설정을 맞췄습니다. 마이그레이션에도 DB 연결 제한 시간 3초를 적용했습니다.

```powershell
conda activate univ18
.\scripts\dev.ps1 db
python scripts/init_auth_secret.py # 단계 2부터 JWT 키 설정 필요
.\scripts\dev.ps1 migrate         # 최신 마이그레이션 적용
.\scripts\dev.ps1 run             # /docs에서 garments API 사용
# 기본 테스트는 실행 중인 DB가 없어도 동작
python -m pytest
# 실제 PostgreSQL에서 동일 API + 마이그레이션 왕복 검증
$env:TEST_POSTGRES = "1"
python -m pytest backend/tests/api/test_garments.py backend/tests/test_migrations.py
Remove-Item Env:TEST_POSTGRES
```

### 단계 2 — `feat/auth-profile` (P0로 앞당김 권장)
- [x] 가입·로그인·`/me`, JWT, `get_current_user`
- [x] `user_profiles`, 프로필 API, 초대 코드
- [x] 모든 보호 대상 API에 인증·소유권 적용 + 차단 테스트
- **완료 기준:** 사용자 A가 사용자 B의 옷·이미지·추천에 접근 불가 (테스트로 증명)

**단계 2 구현 사항 / 사용 방법**
- `POST /api/v1/auth/signup`: JSON `{email, password, nickname, invite_code?}` → 사용자 정보 (201). 이메일은 공백 제거·소문자로 저장하고 중복 가입은 409입니다. `SIGNUP_INVITE_CODE` 설정 시 코드가 일치해야 가입할 수 있습니다(불일치 403).
- `POST /api/v1/auth/login`: JSON `{email, password}` → `{access_token, token_type: "bearer", expires_in: 86400}`. 비밀번호는 bcrypt cost 12로 저장하며 JWT는 HS256, 기본 유효기간 24시간입니다.
- `GET /api/v1/auth/me`: 현재 사용자 정보. `Authorization: Bearer <token>`이 필요하며 매 요청마다 서명·만료·토큰 종류·사용자 존재·활성 상태를 확인합니다. 로그인 토큰에는 사용자 ID와 발급/만료 시각·토큰 종류만 담습니다.
- 옷장·이미지·프로필·추천·피드백 요청 모두 인증이 필요합니다. 토큰 누락·위조·만료·비활성 계정은 401, 다른 사용자의 옷·이미지 조회/수정/삭제는 404입니다. 프로필은 `/profile`에서 토큰 사용자 본인만 조회·수정합니다.
- **단계 3에서 추천 저장·소유권 조회, 단계 4에서 피드백 저장·조회가 연결됐습니다.** 피드백 성공은 201, 타인/잘못된 ID는 404입니다.
- `GET/PATCH /api/v1/profile`: 닉네임, 선호 스타일, 기피 색상, 선택적 성별·키·몸무게·출생연도·아바타 설정. 필드를 생략하면 유지하고, nullable 필드에는 null, 목록 초기화에는 `[]`를 사용합니다.
- `consent_body_info=true`로 동의하면 서버가 `consent_body_info_at`을 기록합니다. 신체정보와 아바타 저장은 현재 동의가 있어야 합니다. `false`로 철회하면 성별·키·몸무게·출생연도·아바타·동의 시각을 삭제하고 스타일 선호는 유지합니다. 클라이언트가 동의 시각이나 사용자 ID를 직접 수정할 수 없습니다.
- 입력 범위: 성별 `male`/`female`/`other`/null, 키 50~250cm, 몸무게 20~400kg(정수), 출생연도 1900~현재 연도. 아바타 설정은 D의 스키마 확정 전까지 16KB 이하 JSON 객체 또는 null입니다.
- `0003`은 `user_profiles`를 추가하고 기존 사용자별 빈 프로필을 채웁니다. 단계 1의 비밀번호 해시가 `!disabled-stage1`인 임시 계정은 비활성화하되 기존 사용자·옷을 삭제하지 않습니다.
- `scripts/init_auth_secret.py`는 `.env`의 비어 있거나 예제인 JWT 키만 임의 값으로 바꾸고 출력하지 않습니다. `scripts/dev.ps1 setup`에도 포함했습니다. 유효한 기존 키는 보존하며, 32바이트 미만/예제 키로는 앱이 시작되지 않습니다.

**검증 완료 (2026-11-07):** 실제 PostgreSQL 모드에서 전체 **101개 테스트 통과, 건너뜀 없음**. 가입·중복/초대 코드·로그인·JWT 만료/위조·비활성 사용자·프로필 부분 수정·동의/철회·두 실제 계정 간 소유권 차단·기존 단계 1 기능을 검증했습니다. 마이그레이션 왕복 및 기존 사용자/옷 보존 테스트를 통과했고 개발 DB `fitjang`과 테스트 DB `fitjang_test` 모두 **`0003 (head)`** 적용 및 `alembic check` 일치를 확인했습니다.

### 단계 3 — 추천 연결 (`feat/garment-storage` 후속 또는 `test/mvp-integration`)
- [x] `recommendations`, `recommendation_outfits` 모델 + 마이그레이션 `0004`
- [x] 추천 API, 결과 재검증, 대체 추천, `garments` 맵 응답
- [x] 추천 단건·이력 조회와 소유권 차단
- **완료 기준:** A의 실제 `recommend()`로 교체해도 백엔드 코드 수정 없음 (`.env` 변경만)

**단계 3 구현 사항 / 사용 방법**
- `POST /api/v1/recommendations`: `{temperature_c?, lat?, lon?, occasion?, preferred_styles?, excluded_items?}` → **201**. 기온 또는 좌표 쌍이 필요하며 `excluded_items`는 의류 UUID 목록입니다. 좌표 조회 실패 시 입력 기온을 사용하고, 없으면 503으로 수동 입력을 안내합니다.
- `GET /api/v1/weather?lat=...&lon=...`: D의 날씨 요청에 맞춘 JWT 보호 API. 추천의 적용 기온·출처·시각은 기존 context JSONB에 저장하고 생성/조회 응답의 `weather`로 제공합니다. 기존 이력은 weather=null로 보존합니다.
- 토큰의 사용자 ID와 프로필 기본 선호를 조합한 `UserContext`를 만듭니다. `preferred_styles` 생략/빈 배열은 프로필을 사용하고 명시한 스타일은 우선합니다. 단계 4에서 계약 1.1의 `recent_feedback`을 추가했으며 기피 색상은 아직 엔진 입력에 포함하지 않습니다.
- DB에서 **본인 소유 AND available AND done AND 제외 목록에 없음**인 옷만 골라 A에 전달합니다. 필수 슬롯(상의·하의)이 부족하면 엔진을 호출하지 않고 422 `NOT_ENOUGH_GARMENTS`를 반환하며 추천 기록을 저장하지 않습니다. 아우터는 선택이며 신발은 새 추천에서 제외합니다.
- 어댑터는 입력을 복사하고, 결과의 스키마·ID 소속·가용 상태·슬롯 카테고리·중복·유한한 점수 여부를 검증해 최대 3개를 남깁니다. 예외·시간 초과·유효한 코디 없음은 규칙 기반 대체 추천입니다.
- 엔진 처리 후 DB에서 최신 옷/속성을 다시 읽어 잠근 상태로 재검증하고 저장합니다. 변경으로 결과가 무효화되면 현재 옷장의 기본 추천을 만들고, 필수 옷이 사라졌다면 422로 반환합니다.
- 추천·코디 ID는 서버 UUID이며 엔진이 준 ID는 사용하지 않습니다. 요청 당시 `context`, 실제 결과의 `provider`, `latency_ms`, 생성 시각과 코디 순위·이유·점수를 한 트랜잭션으로 저장합니다. DB 실패 시 추천/코디 전체를 롤백합니다.
- `GET /api/v1/recommendations/{id}`: 본인 결과만 조회합니다. 타인/없는 ID는 동일한 404 `RECOMMENDATION_NOT_FOUND`입니다.
- `GET /api/v1/recommendations?limit=20&offset=0`: 본인 결과 배열, 최신 생성 시각/ID 순. `limit` 1~100, `offset` 0 이상입니다. 당초 단계 4의 이력 조회를 이번 결과 조회 작업에 포함했습니다.
- 생성·조회 응답은 `recommendation_id`, `outfits`, `garments`, `provider`, `is_fallback`, `context`, `latency_ms`, `created_at`입니다. `garments` 맵은 해당 코디가 사용하는 본인 옷의 현재 정보·이미지 URL만 담습니다.
- **옷 삭제와 이력:** 옷을 삭제하면 이를 포함한 코디 행만 함께 삭제됩니다. 추천 요청 기록과 다른 코디는 유지하며, 남은 코디가 없으면 `outfits=[]`, `garments={}`입니다. 단순 세탁/비활성 상태 변경은 과거 코디를 지우지 않으며 조회 시 현재 옷 상태를 보여 줍니다.
- 기존 샘플 JSON 기반 `api/v1/mock.py`는 제거했습니다. `RECOMMENDER_PROVIDER=mock`은 실제 옷장을 쓰는 규칙 기반, `local`은 A의 `recommendation.recommend()` 호출입니다. 현재 A 모듈 자체의 추천 알고리즘은 임시 구현이며 설정 전환 연결을 테스트합니다.
- 피드백 저장·조회는 아래 단계 4에서 구현했습니다. 본인 유효 요청은 201, 타인·ID 불일치는 404입니다.

**검증 완료 (2026-11-07):** 실제 PostgreSQL 모드에서 전체 **123개 테스트 통과, 건너뜀 없음**. SQLite 모드는 **121개 통과, PostgreSQL 전용 2개 건너뜀**입니다. 실제 업로드·로그인·추천 저장·재로그인 후 조회, 프로필 기본 선호, 타인/세탁/비활성/분석 실패/제외 옷 필터링, 잘못된 엔진 결과·예외·시간 초과의 대체 추천, 저장 전 상태 변경 재검증, 소유권 차단, 이력 페이지, 의류 삭제 연계, DB 실패 시 전체 롤백, `local` 설정의 A 모듈 호출을 검증했습니다. 마이그레이션 왕복·기존 데이터 보존 테스트도 통과했습니다. 개발 DB `fitjang`과 테스트 DB `fitjang_test` 모두 **`0004 (head)`** 적용 및 `alembic check` 일치를 확인했으며 임시 테스트 스키마는 남아 있지 않습니다. 개발 설정 `/health`는 DB 정상, 미인증 추천 생성·조회는 401입니다. 테스트에는 기존 Starlette/httpx 사용 중단 예정 경고 1건이 있으며 실패는 없습니다.

### 단계 4 — `feat/recommendation-feedback` (A와 협업, P1)
- [x] `feedback_events` 모델 + 마이그레이션 `0005`, 수락·거절 저장 API
- [x] 피드백 조회·사용자별 접근 차단·입력 검증·실패 시 롤백
- [x] 추천 이력 API (단계 3에서 구현)
- [x] 계약 1.1: `UserContext.recent_feedback` 기본값 `[]`, 본인 최근 20건 전달

**단계 4 구현 사항 / 사용 방법**
- `POST /api/v1/recommendations/{id}/feedback`: `{recommendation_id, outfit_id, accepted, reason?}` → **201**. 본문 ID는 UUID이며 추천 ID는 경로와 같아야 합니다. `accepted`는 실제 JSON bool이고 `reason`은 생략/null 또는 최대 1,000자입니다. 공백 제거 후 빈 사유는 null, null 문자나 추가 필드(`user_id` 등)는 422입니다.
- 사용자 ID는 JWT에서 가져옵니다. 본인 추천 안의 코디만 저장하고, 타인/없는 추천·코디/본문 불일치는 동일한 404 `RECOMMENDATION_NOT_FOUND`입니다. 인증 누락·위조·비활성 계정은 401입니다.
- 응답: `{feedback_id, recommendation_id, outfit_id, accepted, reason, created_at}`. UUID와 UTC 시각은 서버가 발급합니다. 검증·저장 동안 사용자/추천/코디 행을 잠그고, 실패하면 전체 피드백 저장을 롤백합니다.
- 매 POST는 새 이벤트입니다. 같은 코디의 재전송·수락 후 거절도 별도 기록으로 보존하며 덮어쓰지 않습니다. D는 전송 중 중복 클릭을 막고 저장 응답을 확인합니다.
- `GET /api/v1/recommendations/{id}/feedback?limit=20&offset=0`: 본인 추천의 피드백 배열, 생성 시각/ID 최신순. `limit` 1~100, `offset` 0 이상이며 기록이 없으면 `[]`입니다. 조회에도 추천 소유권과 이벤트 사용자 필터를 모두 적용합니다.
- 세탁·비활성 상태로 바뀐 옷을 포함한 과거 코디에도 피드백을 저장할 수 있습니다. 옷 삭제로 코디가 사라지면 기존 로그는 `outfit_id=null`로 보존하고 신규 피드백은 404입니다. 추천 삭제 시 추천 참조도 null이 됩니다. 사용자 삭제 시 DB 트리거가 모든 참조와 자유 입력 사유를 지워 익명 통계만 남깁니다.
- 다음 추천의 `recent_feedback`에는 본인의 살아 있는 추천·코디에 대한 최근 20건을 `{recommendation_id, outfit_id, accepted, reason}` 형태로 전달합니다. 타인/삭제/익명 로그는 제외하며 요청 본문에서 이 이력을 주입할 수 없습니다. 엔진 입력은 복사하고 추천 context에도 당시 이력을 저장합니다.
- 계약 1.1은 기본값 필드 추가이므로 기존 1.0 입력·저장된 context를 그대로 읽습니다. A의 `recommend()` 인자는 유지됩니다. 현재 임시 알고리즘의 피드백 점수 반영과 추가 속성은 A의 후속 작업이며, `local` 모듈까지 데이터 전달을 테스트합니다.

**검증 완료 (2026-11-07):** 실제 PostgreSQL 전체 **148개 테스트 통과, 건너뜀 없음**. SQLite는 **145개 통과, PostgreSQL 전용 3개 건너뜀**입니다. 수락/거절 저장·재로그인 후 조회, 반복 이벤트·페이지 조회, 두 사용자 사이의 저장/조회 차단, 추천/코디 ID 교차 요청, 입력 검증, 비활성 계정, DB 실패 시 롤백, 삭제 시 로그 보존·사용자 익명화, 최근 20건의 본인 피드백을 `local` 추천 모듈에 전달하는 경로를 검증했습니다. 계약 1.0 호환성과 기존 추천 데이터 보존·마이그레이션 왕복도 통과했습니다. 개발 DB `fitjang`과 테스트 DB `fitjang_test` 모두 **`0005 (head)`** 적용 및 `alembic check` 일치를 확인했고, 익명화 트리거와 테스트 임시 스키마 정리 상태도 확인했습니다. 개발 `/health`는 DB 정상·계약 1.1, 미인증 피드백 생성/조회는 401입니다. 기존 Starlette/httpx 사용 중단 예정 경고 1건만 남아 있으며 테스트 실패는 없습니다.

### 단계 5 — `test/mvp-integration` (P0)
- [x] 실제 HTTP E2E: 가입→업로드→추천→수락·거절→재로그인·이력·소유권 검증
- [x] `mock` 및 저장소 B·A `local` 모듈 연동과 요청 시간 측정
- [x] 백엔드 프론트 정적 제공 검증, 시연 시작/종료/검증/리허설 스크립트
- [x] 시연 계정·의류 12벌·원본 JPEG/투명 PNG seed, 반복 실행·기존 데이터 보존 검증
- [x] 실제 PostgreSQL 전체 회귀 테스트와 실행·백업 가이드
- [ ] 실제 모듈 교체를 위한 local 테스트의 `(stub)` 문구·`stub-0` 버전 고정 기대 정리
- [ ] PR CI 구성 및 실제 모델/외부 호출이 필요한 검증의 실행 조건 분리
- [ ] A·B 학습 모델/GPU 실측, D의 실제 프론트·3D 빌드 통합 (현재 임시 모듈/프론트 미제공)
- [ ] 새 PC에서 설치·실행 재현 (단계 0의 30분 내 준비 기준 포함)
- [ ] 백업 노트북에서 실제 복원·리허설 (절차는 문서화)
- [ ] `develop → main` PR, `v1.0-demo` 태그

**단계 5 구현 사항 / 사용 방법**
- 상세 명령과 5분 시연 순서는 [DEMO_RUNBOOK.md](DEMO_RUNBOOK.md)에 정리했습니다.
- `scripts/verify_demo.ps1`: DB 시작·마이그레이션 후 린트와 PostgreSQL 전체 테스트를 실행합니다. `-E2EOnly`로 실제 HTTP 테스트 두 모드만 실행할 수 있습니다.
- `backend/tests/e2e/`: 서비스/인증/어댑터 대체 없이 실제 Uvicorn 자식 프로세스와 HTTP 클라이언트를 사용합니다. 실행별 PostgreSQL 스키마와 임시 이미지 저장소를 만들며 완료 후 프로세스·스키마를 정리합니다. 두 계정 사이의 옷·이미지·추천·피드백 접근 차단도 포함합니다.
- `scripts/start_demo.ps1 -Provider local -Port 8001`: DB → 마이그레이션/스키마 검사 → seed → 서버 → health 확인. `mock` 대체 모드, `-PrepareOnly`, `-SkipSeed`, `-RequireFrontend`, `-Python` 옵션을 제공합니다. 기존 `.env`는 유지하며 시작한 자식 프로세스에만 시연 설정을 적용합니다.
- `scripts/stop_demo.ps1`: PID와 시작 시각을 확인해 생성한 서버만 종료합니다. 기존 서버/DB/데이터를 유지하며 시연 서버는 reload 없이 loopback 주소에 실행됩니다.
- `scripts/seed_demo.py`: 시연 계정·의류 12벌(사용 가능 10, 세탁 1, 비활성 1)과 이미지 24개를 만듭니다. 기존 시연 계정의 ID·속성·프로필·파일은 유지하고 누락 파일만 복구합니다. 계정이 없으면 새 UUID로 생성하므로 탈퇴 전 JWT는 재사용할 수 없습니다. 실패 시 이번 실행의 DB 변경과 새 파일을 정리하며 무작위 비밀번호는 Git 제외된 `.env.demo`에만 저장합니다.
- `scripts/rehearse_demo.py`: 실행 중인 시연 계정으로 이미지 로딩·추천·수락 피드백·재로그인 조회·다음 추천의 피드백 입력을 확인합니다. 매 실행마다 실제 추천 2건과 피드백 1건을 추가하고 인증 정보를 제외한 보고서를 저장합니다.
- `frontend/dist/index.html`이 있으면 정적 제공을 켜며, 현재 빌드가 없으면 `/docs`로 진행합니다. 임시 빌드 fixture로 `/`, 정적 자산, 인증 API/404/문서 경로가 서로 가리지 않는지 검증했습니다.
- 현재 B는 `stub-0`이고 A도 기본 조합입니다. E2E는 실제 local 함수를 거친 후 사용자 PATCH로 옷 카테고리를 확정합니다. 합성 이미지·임시 모듈의 통합 테스트 결과를 모델 품질/GPU 실측으로 해석하지 않습니다.

**검증 완료 (2026-11-07):** 실제 PostgreSQL 전체 **157개 통과**, SQLite **152개 통과·PostgreSQL 전용 5개 건너뜀**. 신규 통합/seed/정적 제공 9개를 포함하며 기존 Starlette/httpx 사용 중단 예정 경고 1건 외 실패가 없습니다. 새 스키마 변경 없이 `0005`를 유지합니다. 시연 DB에도 12벌/24개 이미지를 생성해 두 모드의 시작·종료·리허설을 실행했고, 재시작 seed가 새 의류/파일 0개임을 확인했습니다. `.demo/reports/e2e-local.json`, `e2e-mock.json`, `rehearsal.json`, `rehearsal-mock.json`에 결과를 저장했습니다. local HTTP 측정은 업로드 15.25~37.62ms, 추천 13.41ms, 전체 흐름 1.14초이며 GPU는 로드되지 않았습니다.

### 삭제 장애 후속 보완 (`0006`, 2026-11-07)

- [x] 의류 삭제 + 파일 정리 작업을 같은 DB 트랜잭션으로 기록하고 커밋 후 파일 삭제
- [x] 최초 커밋 실패 시 원본·cutout·연관 데이터 보존
- [x] 파일 전체/부분 삭제 실패 및 정리 완료 커밋 실패 시 영속 작업으로 재시도
- [x] 서버 시작/주기적 worker, `python -m scripts.cleanup_storage` 수동 실행
- [x] seed 동일 UUID 재생성 보호, 저장소 루트 삭제 방지, 실제 PostgreSQL worker E2E

파일 정리가 지연돼도 **204는 의류 DB 삭제 완료**를 의미하며 해당 의류·미디어는 즉시 404입니다. DB와 파일시스템의 동시 원자적 삭제가 아니라, 실패 작업을 보존해 재시도하는 방식입니다. 운영 상세는 [`DEMO_RUNBOOK.md`](DEMO_RUNBOOK.md#6-의류-삭제와-파일-정리)에 있습니다.

**삭제 보완 당시 검증 결과:** 실제 PostgreSQL **169개 통과**, SQLite **163개 통과·전용 6개 건너뜀**, 기존 Starlette/httpx 경고 1건. 개발·테스트 DB 모두 **0006 (head)** 및 `alembic check` 일치. 8001번 시연 서버를 수정 코드로 재시작하고 local 어댑터·DB 정상 상태를 확인했습니다. 위 단계별 수치는 각 단계 완료 당시의 기록입니다.

### 사용자별 추천 요청 제한 (`0007`, 2026-11-07)

- [x] `POST /api/v1/recommendations`에 JWT 사용자 ID 기준 최근 60초 10회 제한
- [x] 429 `RECOMMENDATION_RATE_LIMITED` + `Retry-After` 정수 초, 브라우저 CORS 헤더 노출
- [x] PostgreSQL 사용량 저장·사용자 행 잠금으로 동시 요청 및 프로세스 간 한도 공유
- [x] 다른 사용자 한도 분리, 재로그인/IP/요청 쿼리로 한도 우회 방지
- [x] 경계 시각 만료, DB 커밋 실패, 엔진 미호출, 실제 동시 HTTP 요청 검증

인증과 요청 본문 형식 검증을 통과해 생성 처리를 시작한 요청은 1회로 계산합니다. 옷 부족·대체 추천·후속 저장 실패에도 사용량은 유지합니다. 인증/본문 오류 및 차단된 429는 계산하지 않으며 이력 조회·피드백에는 적용하지 않습니다. 60초가 지난 요청부터 차례로 만료되므로 정각마다 한도가 일괄 초기화되는 방식이 아닙니다. 사용자별 행에는 최대 10개의 시각만 보관하고 계정 DB 삭제 시 함께 제거합니다.

**요청 제한 구현 당시 검증 결과:** 실제 PostgreSQL 전체 **176개 통과**, SQLite **169개 통과·전용 7개 건너뜀**, 기존 Starlette/httpx 경고 1건. 실제 HTTP 20개 동시 요청에서 **201 10개·429 10개**를 확인했고 별도 사용자와 프로세스의 한도 분리/공유도 검증했습니다. 개발·테스트 DB 모두 **0007 (head)** 적용 및 `alembic check` 일치, 8001번 시연 서버 local 모드 재시작을 완료했습니다.

### 의류 재분석 (`0008`, 2026-11-07)

- [x] `POST /api/v1/garments/{id}/reanalyze`, 원본 파일 재사용·실패 재시도·최신 분석 메타데이터 저장
- [x] 사용자 수정 필드별 보존, null/빈 목록/세탁 상태 유지, 기존 수동 데이터 보호 마이그레이션
- [x] 소유권·미인증 차단, 중복 재분석 409, PATCH/삭제와 행 잠금 직렬화
- [x] 원본 복사본 분석·고유 cutout 게시, timeout 후 늦은 출력 및 DB 커밋 실패로부터 기존 이미지 보호
- [x] 실제 mock/local HTTP 재분석, PostgreSQL 동시 재분석·PATCH 및 기존 수동 데이터 보존 검증

**재분석 구현 당시 검증:** 실제 PostgreSQL 전체 **195개 통과**, SQLite **184개 통과·전용 11개 건너뜀**, 기존 Starlette/httpx 경고 1건. 재분석 실패/timeout/무효 출력, 원본·이전 cutout 보존, 커밋 실패/응답 유실, 사용자별 차단, 수동 null/빈 목록 보존, 동시 재분석 409 및 PATCH 직렬화를 검증했습니다. 당시 개발·테스트 DB 모두 **0008** 및 `alembic check` 일치. 개발 DB의 기존 시연 의류 12벌은 속성값을 그대로 유지하면서 수동 보호 목록만 채웠습니다.

### 사용자 탈퇴 (`0009`, 2026-11-07)

- [x] `DELETE /api/v1/auth/me` → 204, JWT 본인 계정만 대상으로 처리·추가 본문 필드 차단
- [x] 계정·프로필/신체정보/아바타·옷장/속성·추천/코디·요청 사용량 삭제, 피드백 참조·사유 익명화
- [x] DB 커밋 후 사용자 폴더 전체 정리, 파일 전체/부분 실패·정리 완료 커밋 실패·커밋 응답 유실 시 영속 재시도
- [x] 탈퇴 후 기존 JWT의 API·미디어 접근 차단, 같은 이메일 재가입·시연 seed 재생성에도 새 사용자 UUID 발급
- [x] 부모 사용자 잠금 순서 통일 및 업로드/재분석 복사본 격리로 탈퇴와 동시 실행 시 늦은 이미지 생성 방지
- [x] 실제 PostgreSQL mock/local HTTP E2E·worker 복구·분석/탈퇴 동시 실행·기존 seed 데이터 보존 마이그레이션 검증

**최신 검증 결과:** 실제 PostgreSQL 전체 **210개 통과**, SQLite **193개 통과·전용 17개 건너뜀**, 기존 Starlette/httpx 경고 1건. Ruff 린트·변경 코드 포맷 검사 통과. 개발·테스트 DB 모두 **0009 (head)** 및 `alembic check` 일치. 기존 DB 행·이미지 내용 보존을 확인했고 기존 시연 계정에는 `is_demo` 표시만 추가했습니다. 시연 데이터는 계정 1개·의류 12벌·이미지 24개·추천 6건·피드백 3건이며 파일 정리 대기 작업과 임시 테스트 스키마는 없습니다. 8001 시연 서버는 최신 코드의 local 모드로 재시작했습니다.

### 일정 의존성
| C 작업 | 다른 팀원에게 주는 것 |
|---|---|
| 단계 0 완료 | 전원: 실행 가능한 서버, Mock API, 계약 테스트 |
| 단계 1 완료 | D: 옷 등록·옷장 화면 실제 연결 / B: 실제 업로드 이미지로 테스트 가능 |
| 단계 2 완료 | D: 로그인 화면 연결 |
| 단계 3 완료 | A: 실제 DB 데이터로 추천 품질 확인 / D: 추천 화면·2D 아바타 연결 |

---

## 13. 다른 팀원이 기능을 바꿀 때의 영향 범위

| 변경 | 수정할 곳 | 백엔드 서비스·라우터 수정 |
|---|---|---|
| A가 추천 알고리즘·LLM 모델 변경 | `recommendation/`, 의존성/키 설정, 실제 입력으로 검증 | 기존 함수·계약 유지 시 서비스 수정 불필요. 임시값 의존 테스트는 정리 필요 |
| B가 비전 모델 교체 | `vision/`, 의존성/가중치 준비, 실제 사진·임시 cutout 검증 | 기존 함수·계약 유지 시 서비스 수정 불필요. 임시 모델 버전 고정 테스트는 정리 필요 |
| B가 새 속성 추가 (예: 소재) | 공통 계약 → DB 모델/마이그레이션 → API 스키마·매퍼 | 분석 적용/PATCH 허용·수동 수정 보호 필드를 함께 점검 |
| 비전을 별도 GPU 서버로 분리 | Remote 어댑터, Settings 허용값·registry·원격 이미지/cutout 전달 방식 | 현재 임시 파일 경로 계약을 원격 전송에 맞춰 연결 |
| 날씨 공급자 변경 | WeatherPort 구현·설정/registry | Open-Meteo 자동 조회·수동 대체·D /weather 연결 완료. 다른 공급자 추가 시 구현 필요 |
| 로컬 저장 → S3 | S3 저장소, Settings/의존성 주입, 파일 정리·이미지 전달 계약 | 필요. 현재 `/media`는 `local_path` + `FileResponse`에 의존 |
| D가 화면 구조 변경 | `frontend/` | 없음 (API 유지 시) |
| D가 URL 경로 라우팅 채택 | 프론트 라우터·백엔드 정적 파일 fallback | 필요할 수 있음. 현재 `/closet` 직접 접속은 404 |
| 3D 생성/에셋 저장 API 추가 | D와 아바타 계약·에셋 관리 범위부터 합의 | 현재는 동의 기반 `avatar_config` JSON 저장만 제공. 새 API는 추가 구현 |
| 슬롯 추가 (가방·모자) | 계약 메이저 변경 → 테이블·매퍼·스키마·결과 검증 | 일부 |

---

## 14. 미결정 사항 (팀과 확인 필요)

1. **D의 프론트엔드 프레임워크**와 빌드 결과 위치(`frontend/dist`) — 정적 제공 방식에 영향
2. **B의 모델 출력에 임베딩 포함 여부** — 포함하면 `VisionResult`에 `embedding` 필드 추가(다음 계약 버전)
3. **A의 개인화 알고리즘** — 계약 1.1의 `recent_feedback`은 전달 완료, 점수 반영과 추가 속성 협의
4. **기온 입력 방식** — 자동 조회·수동 대체 구현 완료. D 위치 권한/수동 입력 화면과 Open-Meteo 출처 표시 확인
5. **비전 동기 처리 유지 여부** — 데스크탑 실측 후 결정 (5초 기준)
6. **시연 시 공개 범위** — 초대 코드 사용 여부, 멘토 계정 수
7. **D의 아바타/3D 계약** — `avatar_config` 구조·버전, 에셋 식별자와 제공 방식, 별도 생성/업로드 API 필요 여부. 현재 16KB 이하 JSON 저장만 제공
8. **D의 라우팅 방식** — hash 또는 URL 경로 라우팅. 후자는 SPA 새로고침/직접 링크 처리 필요
9. **실제 모델 테스트 입력과 실행 조건** — 실제 사진·정답/추천 품질 사례, 모델·외부 호출이 필요한 테스트를 일반 PR 검증과 어떻게 구분할지 합의

---

## 15. 팀원별 연결 체크리스트

| 담당 | 해야 할 것 | C가 해 주는 것 |
|---|---|---|
| **A (추천)** | `recommendation/__init__.py`의 `recommend()` 시그니처 유지, 반환 ID는 입력받은 `garments` 안에서만 선택, LLM 키는 `os.environ["LLM_API_KEY"]` | 사용 가능한 옷만 골라서 전달, 결과 재검증, 시간 초과·실패 시 대체 추천, DB 저장, ID 발급 |
| **B (비전)** | `vision/__init__.py`의 `extract()` 시그니처 유지, 실패 시 `success=False`, 공통 단어 목록만 사용, (선택) `warmup()` 제공, `cuda`/`cpu` 자동 선택 | 이미지 검증·회전 보정·저장 경로 결정, GPU lock, 시간 제한, 결과 저장, cutout 파일 제공 |
| **D (프론트)** | API 주소를 상대 경로 `/api/v1`로, 토큰은 `Authorization: Bearer`, 에러는 `code`로 분기, 업로드 후 `analysis_status` 확인 | `/docs` API 문서, Mock 모드 서버, 추천 응답에 `garments` 맵 포함, 이미지 URL 제공, 빌드 결과 정적 제공 |

**연결 순서:** 각자 Mock으로 개발 → A·B는 `tests/contract/` 통과 확인 후 PR → C가 `.env`에서 `*_PROVIDER=local`로 바꿔 데스크탑에서 실측 → 문제 없으면 `develop` 병합.

---

## 변경 이력

| 버전 | 날짜 | 내용 |
|---|---|---|
| v1.0 | 2026-11-07 | 최초 작성 |
| v1.1 | 2026-11-07 | RTX 5090 PyTorch 요구사항, 단일 워커 운영 원칙, 팀원별 연결 체크리스트 추가 |
| v1.2 | 2026-11-07 | `0009` 탈퇴까지 재점검. MVP API 완료/팀 통합·운영 잔여 조건 구분, local 임시값 의존 테스트와 CI 추가, 확장 기능 영향 범위 및 3D 계약 미결정 사항 보정 |
| v1.3 | 2026-11-08 | 세 슬롯 추천, A ranking 연결, D API 호환, 계약 2.0·보존 마이그레이션 0010, GPT-6 Luna 선택형 어댑터 |
