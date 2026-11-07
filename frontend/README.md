# Frontend (팀원 D) — FastAPI + Jinja2

## 파일 구성 (브랜치별)
```
frontend/
  app.py                    진입점 (uvicorn frontend.app:app), 브랜치별 화면을 묶음
  common.py                 공통 화면 틀: 메뉴·디자인·한글 표시(LABELS)·render()
  onboarding_wardrobe.py    feat/onboarding-wardrobe-ui  로그인·회원가입·프로필·옷장·옷 등록   ✅
  recommendation_ui.py      feat/recommendation-ui       코디 추천 화면                        ✅
  avatar_preview.py         feat/avatar-2d-preview       2D 아바타 미리보기                    ⏳ 자리만
  connect/                  ⚠ 다른 팀원 작업과 연결되는 부분 (아직 미완성, Mock으로 동작)
    __init__.py               공통: 백엔드 주소, 요청·에러 처리
    auth_profile.py           ↔ C  feat/auth-profile
    garment_storage.py        ↔ C  feat/garment-storage  (+ B vision-extraction·garment-segmentation)
    recommendation.py         ↔ A  feat/recommendation-rules·ranking, A+C feat/recommendation-feedback
    weather.py                ↔ C  오늘 기온 (백엔드가 날씨 API 호출, 키는 서버 .env)
  requirements.txt
```
- 화면 파일은 백엔드를 직접 부르지 않고 `connect/`의 함수만 호출합니다. 상대 브랜치가 완성되면 `connect/`의 해당 파일만 고치면 됩니다.
- 각 브랜치는 자기 화면 파일만 고치므로 PR끼리 충돌이 적습니다. `common.py`를 바꿀 때는 다른 화면도 확인하세요.

## 실행 (저장소 루트, conda 환경 `univ18`)
팀 규칙: Python **3.12.14**, Miniconda 환경 이름 **`univ18`** (docs/DECISIONS.md, docs/CONTRIBUTING.md)
```powershell
conda activate univ18
python --version                       # Python 3.12.14
pip install -r frontend/requirements.txt   # environment.yml에 등록되면: conda env update -f environment.yml --prune
uvicorn frontend.app:app --reload --port 3000
```
브라우저에서 http://localhost:3000 (백엔드는 8000번 포트를 그대로 사용). **반드시 저장소 루트에서 실행**하세요.
- `frontend/requirements.txt`는 `environment.yml`의 pip 목록에 등록해야 팀원 모두 같은 환경이 됩니다 (C에게 요청).

## Mock 모드
기본값은 Mock 모드라 백엔드 없이 화면을 볼 수 있습니다.
- 데모 계정: `demo@fitjang.com` / `fitjang1234`
- 데모 계정의 옷장은 `mocks/garments.json`, 새로 가입한 계정은 빈 옷장으로 시작합니다.
- Mock 모드에서 가입한 계정·프로필·올린 사진은 메모리에만 저장돼 서버를 끄면 사라집니다.
- 실제 백엔드 연결: 환경 변수 `FRONTEND_USE_MOCK=false` (백엔드 주소는 `BACKEND_API_URL`, 기본 `http://localhost:8000/api/v1`)
  - 백엔드에 아직 없는 API를 부르면 화면에 "서버에 아직 이 기능이 없어요. (백엔드 연결 전)"이 나옵니다.

## 연결 현황 (2026-11-07, 실제 모드로 C의 백엔드 Mock에 붙여 확인)
| 기능 | 요청 | 응답 | 상태 |
|---|---|---|---|
| 옷 목록 | `GET /api/v1/garments` | `[GarmentAttributes, ...]` (shared/contracts.py) | ✅ 백엔드에 있음 |
| 옷 등록 | `POST /api/v1/garments` multipart `file` | 201 `GarmentAttributes` (Vision이 채운 값) / 413 / 415 | ✅ 백엔드에 있음 |
| 코디 추천 | `POST /api/v1/recommendations` JSON `UserContext` | `RecommendationResult` | ✅ 백엔드에 있음 · 화면 ✅ |
| 추천 피드백 | `POST /api/v1/recommendations/{id}/feedback` JSON `FeedbackEvent` | 200 | ✅ 백엔드에 있음 · 화면 ✅ |
| 로그인 | `POST /api/v1/auth/login` JSON `{email, password}` | `{access_token, token_type: "bearer", expires_in}` / 401 `INVALID_CREDENTIALS` | ⏳ 프론트 제안 |
| 회원가입 | `POST /api/v1/auth/signup` JSON `{email, password}` | 201 로그인과 같은 토큰 형식 / 400 `EMAIL_ALREADY_EXISTS` | ⏳ 프론트 제안 |
| 프로필 조회 | `GET /api/v1/users/me/profile` | `{height_cm, weight_kg, age, gender}` (없으면 `null`) | ⏳ 프론트 제안 |
| 프로필 저장 | `PUT /api/v1/users/me/profile` JSON 위와 같음 | 저장된 프로필 | ⏳ 프론트 제안 |
| 옷 한 벌 | `GET /api/v1/garments/{id}` | `GarmentAttributes` / 404 `GARMENT_NOT_FOUND` | ⏳ 프론트 제안 |
| 옷 정보 수정·상태 변경 | `PATCH /api/v1/garments/{id}` JSON `{name, location, category, subcategory, color, pattern, style_candidates, thermal_level, status}` 중 일부 (상세 화면의 상태 버튼은 `{"status": ...}`만 보냄) | `GarmentAttributes` | ⏳ 프론트 제안 |
| 옷 삭제 | `DELETE /api/v1/garments/{id}` | 204 | ⏳ 프론트 제안 |
| 옷 사진 | `GET /api/v1/garments/{id}/image?kind=cutout\|original` | 이미지 파일 / 404 | ⏳ 프론트 제안 |
| 오늘 기온 | `GET /api/v1/weather?lat=..&lon=..` | `{temperature_c, condition?, location_name?}` | ⏳ 프론트 제안 (C가 날씨 API 호출) |

- 로그인 토큰은 프론트 서버 쿠키(HttpOnly)에 저장하고, 백엔드 호출 때 `Authorization: Bearer <token>`으로 붙입니다.
- **옷 사진:** 브라우저는 프론트의 `/garment-images/{id}`로 요청하고, 프론트 서버가 토큰을 붙여 백엔드에서 받아 넘겨줍니다. 누끼(cutout, B) → 없으면 원본(original) → 둘 다 없으면 옷 그림으로 대체.
- 에러는 팀 형식 `{detail, code}`와 FastAPI 기본 422 형식을 모두 처리합니다.
- `gender` 값: `female` / `male` / `other` / `null` (제안 값, contracts.py에 아직 없음)
- 비밀번호 규칙(프론트 검사): 영문+숫자 8자 이상
- 세부 종류 선택지는 `onboarding_wardrobe.py`의 `SUBCATEGORIES` (contracts.py에 생기면 교체).
- 옷 데이터는 `shared/contracts.py`의 `GarmentAttributes`를 씁니다.
- ⚠ **C에게 요청: `GarmentAttributes`(옷 테이블)에 `name: str | None`, `location: str | None` 추가.** 지금은 프론트가 `connect/garment_storage.Garment`로 두 필드를 붙여 쓰고 있어서, 백엔드가 저장·반환하기 전까지 실제 모드에서는 이름·위치가 저장되지 않습니다. (각 30자 이하)

## 옷 이름·위치·검색
- 이름(선택): 예 "유니클로 네이비 니트". 비워 두면 세부 종류(니트) → 종류(상의)로 보여줌
- 보관 위치(선택): 예 "안방 옷장 2칸", "본가". 전에 쓴 위치는 입력할 때 추천
- 옷장 검색(`/wardrobe?q=`): 이름·위치·세부 종류·종류·색·패턴·스타일·상태를 한글·영어로 찾음. 여러 단어는 모두 포함("네이비 니트"), 일상 색 이름도 됨(빨강→레드, 남색→네이비). 종류·상태 필터와 함께 동작

## 코디 추천 (`/recommend` → `/recommend/result`)
- **오늘 기온 자동 입력**: 화면을 열면 브라우저 위치(허용 시) → 프론트 `/weather` → `connect/weather.py` → C의 `GET /weather`. 위치를 거부하거나 8초 안에 답이 없으면 서울 기준. 맞는 구간도 표시되고, 직접 바꿀 수 있음
- 조건: 오늘 기온(필수, DECISIONS 구간 4개 중 고르거나 직접 입력), 상황(선택), 원하는 스타일(선택, 최대 3개), 오늘 입기 싫은 옷 빼기
  - 구간을 고르면 대표 기온으로 보냄: 한파 0°C / 추움 6°C / 선선 15°C / 더움 25°C
- 결과: 코디 최대 3개, 각 아우터·상의·하의 사진과 이름(누르면 옷 상세), 추천 이유, 좋아요/별로예요 → `FeedbackEvent`
- 결과 화면은 GET 주소라 새로고침·뒤로 가기가 됨
- 입을 수 있는 상의·하의가 없으면 미리 안내하고 버튼을 막음 (세탁 중·추천 제외는 빠짐)
- ⚠ `UserContext.user_id`는 지금 `"me"`로 보냄 → C가 토큰에서 사용자를 꺼내도록 바꿔야 함
- **신발은 추천에서 뺌** (옷장 등록은 그대로). ⚠ `shared/contracts.py`의 `OutfitItems.shoes`는 아직 필수 → C에게 선택(Optional)으로 바꾸거나 지워 달라고 요청. 그 전까지 프론트는 `connect/recommendation.py`의 느슨한 모델로 shoes 없이도 읽음

## 옷 상세 (`/garments/{id}`)
옷장 카드를 누르면 열립니다. 사진·종류·색·패턴·스타일·두께를 보여주고,
- 상태 변경: 입을 수 있음 / 세탁 중 / 추천 제외 (`inactive`, 뜻이 문서에 없어 실제 동작대로 표시. 세탁 중·추천 제외는 코디 추천에서 빠짐)
- 정보 수정: 등록 때 쓴 확인 폼을 그대로 사용 (`/garments/{id}/edit`)
- 삭제: 한 번 더 확인한 뒤 DELETE

## 옷 사진은 항상 1:1
옷 등록은 2단계: 사진 올리기 → 자동으로 채운 값 확인·수정 후 저장 ("등록 취소"는 DELETE).
- 올라가는 사진은 항상 **1080×1080 JPEG**입니다. (B의 Vision 입력)
- 카메라로 찍기: 화면 안 정사각형 카메라로 찍고 가운데 정사각형만 사용 (https 또는 localhost에서만 동작. 휴대폰에서 `http://IP:3000`으로 접속하면 휴대폰 기본 카메라로 찍은 뒤 자르기 화면으로 넘어감)
- 앨범에서 고르기: 정사각형 자르기 화면에서 끌기·확대(막대, 마우스 휠, 두 손가락) 후 자르기
- 서버도 jpg/png·10MB·정사각형인지 다시 검사합니다.
