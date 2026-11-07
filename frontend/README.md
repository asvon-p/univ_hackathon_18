# frontend

핏장 화면 (FastAPI + Jinja2).

## 실행

저장소 루트에서:

```powershell
conda activate univ18
pip install -r frontend/requirements.txt
uvicorn frontend.app:app --reload --port 3000
```

http://localhost:3000 · 데모 계정 `demo@fitjang.com` / `fitjang1234`

## 설정

`frontend/.env.example` → `frontend/.env`로 복사.

| 변수 | 설명 |
|---|---|
| `FRONTEND_USE_MOCK` | 기본 `true` (백엔드 없이 동작). `false`면 실제 백엔드 |
| `BACKEND_API_URL` | 기본 `http://localhost:8000/api/v1` |
| `TRYON_API_URL`, `TRYON_API_KEY` | 착샷 서버 (`tryon_server/`). 없으면 착샷 버튼 숨김 |

Mock 모드 데이터는 서버를 끄면 사라짐.

## 파일

| 파일 | 내용 |
|---|---|
| `app.py` | 진입점 |
| `common.py` | 공통 화면 틀·디자인 |
| `onboarding_wardrobe.py` | 로그인·회원가입·프로필·전신 사진·옷장·옷 등록 |
| `recommendation_ui.py` | 홈·코디 추천 |
| `avatar_preview.py` | 착샷 |
| `connect/` | 백엔드·착샷 서버 호출 (화면은 여기만 부름) |

## 백엔드 API

✅ 있음 · ⏳ 필요 (주소 앞 `/api/v1`)

| 기능 | 요청 | 상태 |
|---|---|---|
| 옷 목록·등록 | `GET`/`POST /garments` | ✅ |
| 코디 추천 | `POST /recommendations` | ✅ |
| 추천 피드백 | `POST /recommendations/{id}/feedback` | ✅ |
| 로그인·회원가입 | `POST /auth/login`, `/auth/signup` → `{access_token}` | ⏳ |
| 프로필 | `GET`/`PUT /users/me/profile` | ⏳ |
| 전신 사진 | `GET`/`PUT`/`DELETE /users/me/body-photo` | ⏳ |
| 옷 한 벌·수정·삭제 | `GET`/`PATCH`/`DELETE /garments/{id}` | ⏳ |
| 옷 사진 | `GET /garments/{id}/image?kind=cutout\|original` | ⏳ |
| 오늘 기온 | `GET /weather?lat=&lon=` → `{temperature_c}` | ⏳ |

요청·응답 형식은 `connect/` 각 파일 맨 위에 있음.

## 요청

- `GarmentAttributes`에 `name`, `location` 추가
- `OutfitItems.shoes`를 선택(Optional)으로
- `UserContext.user_id`를 토큰에서 꺼내기 (지금은 `"me"`)
- 추천의 `preferred_styles`에 자유 글(예: "꾸안꾸")이 들어올 수 있음
