# 팀 합의 사항 (v1 확정)

> 변경이 필요하면 C(백엔드·통합 담당)와 상의한 뒤 이 문서를 수정하는 PR을 올립니다.

## 1. 기술 스택
| 항목 | 결정 |
|---|---|
| 백엔드 프레임워크 | **FastAPI** — Pydantic 스키마를 A·B와 그대로 공유, `/docs`에서 API 문서 자동 생성 |
| Python | **3.12.14로 통일** (conda로 설치) |
| 환경 관리 | **Miniconda + `environment.yml`** (환경 이름 `univ18`) |
| 패키지 | **pip + requirements.txt** — `backend/requirements.txt` (A·B 전용은 `recommendation/requirements.txt`, `vision/requirements.txt`를 만들고 `environment.yml`에 등록) |
| ORM / 마이그레이션 | **SQLAlchemy 2.0 + Alembic** (테이블 변경은 반드시 마이그레이션 파일로) |
| DB | **PostgreSQL 16**, 로컬은 `docker compose up -d`로 실행 |
| 코드 스타일 | **ruff** (`ruff format .`, `ruff check .`) |
| 프론트엔드 | D가 결정 (React + Vite 권장), 소스는 `frontend/` |

**왜 conda인가:** `venv`는 PC에 설치된 Python을 그대로 쓰기 때문에 버전을 맞출 수 없고, python.org는 3.12.11 이후 Windows 설치 파일을 제공하지 않습니다. conda는 Python 자체를 설치하므로 전원이 정확히 3.12.14를 사용할 수 있습니다.

**환경 규칙**
- 환경 생성: `conda env create -f environment.yml` → `conda activate univ18`
- 확인: `python --version` 이 **`Python 3.12.14`** 이어야 합니다.
- 라이브러리는 **`pip install`로만** 설치하고 해당 `requirements.txt`에 추가합니다. `conda install`과 섞어 쓰지 않습니다.
- `requirements.txt`나 `environment.yml`이 바뀌면: `conda env update -f environment.yml --prune`
- 예외: B가 GPU용 PyTorch를 설치할 때는 CUDA 버전에 맞는 공식 설치 명령을 `vision/README.md`에 적어 둡니다.

## 2. 모듈 연결 방식
A·B는 **별도 서버 없이 Python 함수**로 제공하고, C(백엔드)가 import해서 호출합니다.

| 담당 | 함수 | 위치 |
|---|---|---|
| A | `recommend(context: UserContext, garments: list[GarmentAttributes]) -> RecommendationResult` | `recommendation/__init__.py` |
| B | `extract(image_path: str, garment_id: str) -> VisionResult` | `vision/__init__.py` |

- 함수 이름·인자·반환 타입은 바꾸지 않습니다. 내부 구현은 자유롭게 바꿔도 됩니다.
- 실패 시 예외를 던지지 말고 B는 `success=False`, A는 빈 `outfits`로 반환합니다.
- 모델 로딩은 모듈 import 시 1회만 합니다. 모델 가중치 파일은 Git에 올리지 않습니다.
- MVP의 Vision 처리는 업로드 요청 안에서 **동기 처리**합니다. 1장당 5초를 넘으면 백그라운드 처리로 바꿉니다.
- D ↔ C: REST + JSON, Base URL `http://localhost:8000/api/v1`

## 3. 공통 단어 목록
`shared/contracts.py`의 아래 값만 사용합니다. 모르면 `"unknown"` 또는 `null`로 둡니다.

| 항목 | 값 |
|---|---|
| category | `top`, `bottom`, `outerwear`, `shoes` |
| color | `black` `white` `gray` `ivory` `beige` `brown` `camel` `khaki` `navy` `blue` `sky_blue` `denim` `green` `olive` `red` `burgundy` `pink` `purple` `yellow` `orange` `multi` `unknown` |
| pattern | `solid` `stripe` `check` `dot` `floral` `graphic` `other` `unknown` |
| style | `casual` `minimal` `street` `formal` `dandy` `sporty` `vintage` `feminine` `amekaji` |
| occasion | `daily` `work` `date` `interview` `exercise` `travel` |
| status | `available` `laundry` `inactive` |
| thermal_level | 1(매우 얇음) ~ 5(매우 두꺼움), 모르면 `null` |

- 기온 구간: **≤2°C / 2~10°C / 10~20°C / ≥20°C**
- 세부 카테고리는 `SUBCATEGORIES`의 값을 우선 사용합니다.
- 단어는 모두 영어 소문자, 띄어쓰기는 `_`로 씁니다. 한글 표시는 D가 화면에서 변환합니다.

## 4. API 규칙
| 항목 | 결정 |
|---|---|
| 인증 | **JWT**, `Authorization: Bearer <token>`, HS256, 만료 24시간 |
| ID | **UUID v4 문자열** |
| 에러 응답 | `{"detail": "사람이 읽는 메시지", "code": "GARMENT_NOT_FOUND"}` |
| 상태 코드 | 400 잘못된 요청 / 401 미인증 / 403 권한 없음 / 404 없음 / 413 파일 초과 / 415 형식 오류 / 422 검증 실패 |
| 업로드 | **jpg, png**만 허용, **최대 10MB** |
| 날짜·시간 | ISO 8601, DB에는 UTC로 저장하고 화면에서 KST로 표시 |
| JSON 키 | snake_case |
| 이미지 경로 | `storage/{user_id}/{garment_id}/original.jpg`, `storage/{user_id}/{garment_id}/cutout.png` |

- DB에는 이미지 파일 자체가 아니라 **storage 기준 상대경로**만 저장합니다.
- 다른 사용자의 데이터에 접근하면 404를 반환합니다(존재 여부를 노출하지 않음).

## 5. DB와 운영
- **SQLAlchemy + Alembic 사용.** DB 모델 파일과 마이그레이션은 C가 관리하고, 다른 팀원은 필요한 컬럼을 C에게 요청합니다.
- **PostgreSQL은 docker compose로 실행**합니다. Docker를 쓸 수 없는 팀원만 로컬 설치(PostgreSQL 16)를 사용하고 `.env`의 `DATABASE_URL`만 바꿉니다.
- **비밀 정보는 `.env`에만 보관**합니다(LLM API 키, JWT 비밀키, 날씨 API 키). `.env`는 커밋하지 않고, 새 변수가 생기면 `.env.example`에 이름만 추가합니다.
- LLM API는 **백엔드에서만** 호출합니다. 프론트엔드 코드에 키를 넣지 않습니다.
- 사용자 사진, 모델 가중치, `storage/` 폴더는 Git에 올리지 않습니다.
- 키·몸무게·나이·성별은 선택 입력이며, 분리된 테이블에 저장합니다.

## 6. Git 규칙
- `main`과 `develop`은 **직접 push 금지** (GitHub Ruleset으로 설정 완료).
- 모든 변경은 **기능 브랜치 → `develop` PR**로 합칩니다.
- PR은 **작성자 외 1명 이상 승인** 후 **Squash and merge**로 병합합니다.
- 커밋 메시지는 `종류: 내용` 형식을 사용합니다.
  - `feat:` 기능 / `fix:` 버그 수정 / `refactor:` 구조 개선 / `test:` 테스트 / `docs:` 문서 / `chore:` 설정·환경
- `shared/contracts.py`, `backend/requirements.txt`, DB 모델 변경은 **별도의 작은 PR**로 올리고 팀 채팅에 알립니다.
- `main` 병합은 시연 가능한 상태를 확인한 뒤 C가 `develop → main` PR로 진행합니다.
- 자세한 방법은 [`docs/CONTRIBUTING.md`](CONTRIBUTING.md)를 참고하세요.
