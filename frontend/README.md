# Frontend (팀원 D) — FastAPI + Jinja2

## 파일 구성 (브랜치별)
```
frontend/
  app.py                    진입점 (uvicorn frontend.app:app), 브랜치별 화면을 묶음
  common.py                 공통 화면 틀: 메뉴·디자인·한글 표시(LABELS)·render()
  onboarding_wardrobe.py    feat/onboarding-wardrobe-ui  로그인·회원가입·프로필·옷장·옷 등록   ✅
  recommendation_ui.py      feat/recommendation-ui       홈(오늘 날씨·오늘의 코디)·코디 추천     ✅
  avatar_preview.py         feat/avatar-2d-preview       코디 착샷(FASHN)                      ✅
  connect/                  ⚠ 다른 팀원 작업과 연결되는 부분 (아직 미완성, Mock으로 동작)
    __init__.py               공통: 백엔드 주소, 요청·에러 처리
    auth_profile.py           ↔ C  feat/auth-profile
    garment_storage.py        ↔ C  feat/garment-storage  (+ B vision-extraction·garment-segmentation)
    recommendation.py         ↔ A  feat/recommendation-rules·ranking, A+C feat/recommendation-feedback
    weather.py                ↔ C  오늘 기온 (백엔드가 날씨 API 호출, 키는 서버 .env)
    tryon.py                  ↔ 착샷 서버 (FASHN VTON v1.5, 시아의 GPU 서버)  ✅ 실제 연결
  .env.example              환경 변수 예시 → frontend/.env 로 복사 (커밋 금지)
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

## 환경 변수 (`frontend/.env`)
`frontend/.env.example`을 `frontend/.env`로 복사해서 값을 채우면 서버를 켤 때 자동으로 읽습니다. (이미 설정된 환경 변수가 우선)
- `TRYON_API_URL`, `TRYON_API_KEY`: 착샷 서버. **키는 커밋 금지** (`.env`는 .gitignore에 있음). 둘 중 하나라도 없으면 착샷 버튼이 안 보임
- `FRONTEND_USE_MOCK`, `BACKEND_API_URL`: 아래 Mock 모드 참고

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
| 전신 사진 저장 | `PUT /api/v1/users/me/body-photo` multipart `file` (세로 2:3, 1080×1620 JPEG) | `{body_photo_updated_at}` / 400·413·415 (선택: 422 `BODY_NOT_FULL`) | ⏳ 프론트 제안 |
| 전신 사진 | `GET /api/v1/users/me/body-photo` | 이미지 파일 / 404 `BODY_PHOTO_NOT_FOUND` | ⏳ 프론트 제안 |
| 전신 사진 삭제 | `DELETE /api/v1/users/me/body-photo` | 204 | ⏳ 프론트 제안 |
| 옷 한 벌 | `GET /api/v1/garments/{id}` | `GarmentAttributes` / 404 `GARMENT_NOT_FOUND` | ⏳ 프론트 제안 |
| 옷 정보 수정·상태 변경 | `PATCH /api/v1/garments/{id}` JSON `{name, location, category, subcategory, color, pattern, style_candidates, thermal_level, status}` 중 일부 (상세 화면의 상태 버튼은 `{"status": ...}`만 보냄) | `GarmentAttributes` | ⏳ 프론트 제안 |
| 옷 삭제 | `DELETE /api/v1/garments/{id}` | 204 | ⏳ 프론트 제안 |
| 옷 사진 | `GET /api/v1/garments/{id}/image?kind=cutout\|original` | 이미지 파일 / 404 | ⏳ 프론트 제안 |
| 오늘 기온 | `GET /api/v1/weather?lat=..&lon=..` | `{temperature_c, condition?, location_name?}` | ⏳ 프론트 제안 (C가 날씨 API 호출) |

- 로그인 토큰은 프론트 서버 쿠키(HttpOnly)에 저장하고, 백엔드 호출 때 `Authorization: Bearer <token>`으로 붙입니다.
- **옷 사진:** 브라우저는 프론트의 `/garment-images/{id}`로 요청하고, 프론트 서버가 토큰을 붙여 백엔드에서 받아 넘겨줍니다. 누끼(cutout, B) → 없으면 원본(original) → 둘 다 없으면 옷 그림으로 대체.
- 에러는 팀 형식 `{detail, code}`와 FastAPI 기본 422 형식을 모두 처리합니다.
- 프로필 조회 응답에 `body_photo_updated_at` (전신 사진 올린 시각, 없으면 `null`)도 넣어 주세요. 프론트는 이걸로 사진 유무를 알고 캐시를 구분합니다.
- `gender` 값: `female` / `male` / `other` / `null` (제안 값, contracts.py에 아직 없음)
- 비밀번호 규칙(프론트 검사): 영문+숫자 8자 이상
- 세부 종류 선택지는 `onboarding_wardrobe.py`의 `SUBCATEGORIES` (contracts.py에 생기면 교체).
- 옷 데이터는 `shared/contracts.py`의 `GarmentAttributes`를 씁니다.
- ⚠ **C에게 요청: `GarmentAttributes`(옷 테이블)에 `name: str | None`, `location: str | None` 추가.** 지금은 프론트가 `connect/garment_storage.Garment`로 두 필드를 붙여 쓰고 있어서, 백엔드가 저장·반환하기 전까지 실제 모드에서는 이름·위치가 저장되지 않습니다. (각 30자 이하)

## 전신 사진 (착샷용, `/profile/body-photo`)
FASHN VTON v1.5로 착샷을 만들 때 쓰는 **사람 사진**. 머리끝부터 발끝(신발)까지 다 나와야 합니다.
- 프로필 화면 위쪽 "전신 사진" 칸에서 올리기·다시 올리기·삭제
- 가입 직후 흐름: 1/2 신체 정보 → 2/2 전신 사진 (둘 다 건너뛸 수 있음)
- 사진은 항상 **세로 2:3, 1080×1620 JPEG** (착샷 결과 576×864와 같은 비율. FASHN 권장대로 결과 크기에 맞춰 줄이지 않음)
- 카메라: 2:3 화면에 **사람 실루엣 + 머리끝·발끝 노란 선** 가이드, **타이머(끔/3초/10초)** — 혼자 찍을 때 휴대폰을 세워 두고 사용. 세는 중에 셔터를 다시 누르면 취소
- 앨범: 2:3 자르기 화면(같은 가이드). 짧은 변이 512px보다 작은 사진은 거름
- 저장 전 "머리끝부터 발끝(신발)까지 다 나왔나요?" 확인
- 서버(프론트)도 jpg/png·10MB·2:3인지 다시 검사. 사진은 프론트 `/body-photo?v=올린시각`으로 토큰을 붙여 받아 옴
- 몸이 다 나왔는지 자동 검사는 아직 없음 → 백엔드(착샷 서버의 DWPose)에서 검사해 422로 돌려주면 화면에 그 메시지를 그대로 보여줌
- 카메라·자르기 코드는 옷 등록과 같이 씀: `onboarding_wardrobe.py`의 `photo_picker.html` (`picker_css`, `picker_js(가로, 세로, 파일이름, 최소크기)`)

## 홈 (`/`, 로그인하면 처음 보는 화면)
- 오늘 날짜(한국 시간)와 지금 기온 → **오늘의 코디** 1개 (추천 결과의 첫 번째, 착샷 보기 가능) → **내 옷장** 요약(상의·하의·아우터·세탁 중 개수, 누르면 옷장 필터) + 옷 등록
- 위치: 처음 열 때 브라우저 위치를 물어 쿠키 `fitjang_loc`(소수 둘째 자리, 7일)에 저장하고 새로고침 → 서버가 그 위치 기온으로 그림. 거부하면 서울 기준 + "내 위치로" 버튼
- 오늘의 코디는 같은 사용자·기온(반올림)·입을 수 있는 옷 목록이면 1시간 동안 같은 결과 (프론트 메모리). "다른 코디" → `/recommend/result?temp=지금기온`
- 상의·하의가 없으면 옷 등록 안내, 추천이 실패하면 조건 골라서 추천받기 링크
- 메뉴: 홈 · 옷장 · 코디 추천 · 프로필 (로고도 홈으로)

## 화면 원칙 (2026-11-07~08 "AI 티 빼기")
- 제목 아래 설명 문장·팁 상자·고맙다는 인사 같은 문장은 빼고, 꼭 필요한 안내만 짧게 (예: "한 장에 한 벌 · 단순한 배경")
- 화면에 "AI"라는 말을 쓰지 않음. 반짝이(✨) 아이콘 쓰지 않음 (코디 추천 탭은 티셔츠 아이콘)
- 버튼은 한 줄 (설명 줄 없음)
- 색은 무채색 (검정·회색·흰색). 베이지·카멜 같은 따뜻한 강조색은 쓰지 않음 (옷 색 표시는 예외)
- 모서리는 덜 둥글게 (기본 8px, 카드 10px, 칩·태그 6px). 번호 동그라미·"착샷용" 같은 배지 없음
- 사진 없는 옷은 그림 대신 **옷 색 동그라미** (큰 칸에서는 "사진 없음" 글자도)
- "필수·선택·(선택)" 같은 작은 표시 없음. 로고 밑 슬로건 없음

## 옷 이름·위치·검색
- 이름(선택): 예 "유니클로 네이비 니트". 비워 두면 세부 종류(니트) → 종류(상의)로 보여줌
- 보관 위치(선택): 예 "안방 옷장 2칸", "본가". 전에 쓴 위치는 입력할 때 추천
- 옷장 검색(`/wardrobe?q=`): 이름·위치·세부 종류·종류·색·패턴·스타일·상태를 한글·영어로 찾음. 여러 단어는 모두 포함("네이비 니트"), 일상 색 이름도 됨(빨강→레드, 남색→네이비). 종류·상태 필터와 함께 동작

## 코디 추천 (`/recommend` → `/recommend/result`)
- **오늘 기온은 숫자 하나** (2026-11-07: 한파·추움·선선·더움 버튼은 없앰). −/+ 버튼으로 1도씩, 직접 입력도 됨 (-30~45°C)
- **자동 입력**: 화면을 열면 브라우저 위치(허용 시) → 프론트 `/weather` → `connect/weather.py` → C의 `GET /weather`. 위치를 거부하거나 8초 안에 답이 없으면 서울 기준. "지금 기온 불러오기"로 다시
- 조건: 오늘 기온(필수), 상황(선택), 원하는 스타일(선택)
- **원하는 스타일**: 대표 버튼 4개(캐주얼·미니멀·스트릿·포멀, `QUICK_STYLES`) + 직접 입력칸(쉼표로 여러 개, 40자). 입력한 말이 아는 스타일이면 코드로 바꿔 보냄(빈티지→vintage, 스트리트→street), 모르는 말("꾸안꾸")은 그대로 `UserContext.preferred_styles`에 넣음 (A가 쓸지 결정)
- "오늘 입기 싫은 옷 빼기"는 화면에서 없앰 (2026-11-07). 예전 주소의 `exclude`는 그대로 받아 줌
- 예전 주소 `?band=mild` 같은 것도 대표 기온(한파 0 / 추움 6 / 선선 15 / 더움 25°C)으로 받아 줌
- 결과: 코디 최대 3개, 각 아우터·상의·하의 사진과 이름(누르면 옷 상세, 아우터가 없으면 그 줄은 안 보임), 착샷 보기, 추천 이유, 좋아요/별로예요 → `FeedbackEvent`
- 결과 화면은 GET 주소라 새로고침·뒤로 가기가 됨
- 입을 수 있는 상의·하의가 없으면 미리 안내하고 버튼을 막음 (세탁 중·추천 제외는 빠짐)
- ⚠ `UserContext.user_id`는 지금 `"me"`로 보냄 → C가 토큰에서 사용자를 꺼내도록 바꿔야 함
- **신발은 추천에서 뺌** (옷장 등록은 그대로). ⚠ `shared/contracts.py`의 `OutfitItems.shoes`는 아직 필수 → C에게 선택(Optional)으로 바꾸거나 지워 달라고 요청. 그 전까지 프론트는 `connect/recommendation.py`의 느슨한 모델로 shoes 없이도 읽음

## 착샷 (추천 결과 안에, FASHN VTON v1.5)
- 코디 카드의 **"착샷 만들기"** → 내 전신 사진에 **하의 → 상의 → 아우터** 순서로 한 벌씩 입힘 (앞 결과를 다음 사람 사진으로). 아우터는 `tops`로 한 번 더 입힘
- 옷 한 벌에 **약 40초** (RTX 3060 Ti) → 코디 하나 1~2분. 착샷 서버는 한 번에 하나씩 처리하고 나머지는 줄을 섬
- 진행 창: 단계(하의 · 청바지 ✓ / 상의 · 니트 …)·남은 시간·진행 막대. **창을 닫거나 새로고침해도 계속 만들고**, 다 되면 코디 카드 왼쪽에 착샷이 생김 (누르면 크게 보기·사진 저장). 그 전에는 옷 목록만 (그림 마네킹은 어색해서 없앰)
- 같은 코디·같은 전신 사진이면 다시 만들지 않고 만든 것을 보여줌. 실패하면 "착샷 다시 만들기"
- 전신 사진이 없으면 버튼 대신 "전신 사진 올리고 착샷 보기" → `/profile/body-photo`
- 사진 없이 등록된 옷(데모 옷장 등)은 빼고 입힘. 옷 사진은 원본 → 없으면 누끼 순서 (`garment_storage.fetch_image(kinds=...)`)
- 결과(576×864 PNG)와 진행 상황은 **프론트 서버 메모리**에만 (서버를 끄면 사라짐, 최근 40개)
- 흐름: 브라우저 → 프론트 `POST /tryon` → 뒤에서 `connect/tryon.py`가 착샷 서버 `POST /tryon`·`GET /jobs/{id}`·`GET /results/{id}.png` → 브라우저는 `GET /tryon/{id}`를 2초마다, 결과는 `/tryon/{id}/image`
- 나중에 C의 백엔드가 착샷 서버를 대신 부르게 되면 `connect/tryon.py`만 바꾸면 됨

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
