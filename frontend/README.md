# Frontend (팀원 D) — FastAPI + Jinja2

| 파일 | 내용 |
|---|---|
| `app.py` | 화면 전부 (라우트, HTML 템플릿, CSS, 작은 JS) |
| `api_client.py` | 백엔드(C) API 호출 — **다른 팀원과 연결되는 부분은 여기만** |
| `requirements.txt` | 프론트 서버에 필요한 패키지 |

## 실행 (저장소 루트, .venv 활성화 상태)
```powershell
pip install -r frontend/requirements.txt
uvicorn frontend.app:app --reload --port 3000
```
브라우저에서 http://localhost:3000 (백엔드는 8000번 포트를 그대로 사용)

## Mock 모드
기본값은 Mock 모드라 백엔드 없이 화면을 볼 수 있습니다.
- 데모 계정: `demo@fitjang.com` / `fitjang1234`
- Mock 모드에서 가입한 계정·프로필은 메모리에만 저장돼 서버를 끄면 사라집니다.
- 실제 백엔드 연결: 환경 변수 `FRONTEND_USE_MOCK=false` (백엔드 주소는 `BACKEND_API_URL`, 기본 `http://localhost:8000/api/v1`)

## 백엔드와 맞출 API (프론트 제안, C 확인 필요)
| 기능 | 요청 | 응답 |
|---|---|---|
| 로그인 | `POST /api/v1/auth/login` JSON `{email, password}` | `{access_token, token_type: "bearer", expires_in}` / 401 `INVALID_CREDENTIALS` |
| 회원가입 | `POST /api/v1/auth/signup` JSON `{email, password}` | 201 로그인과 같은 토큰 형식 / 400 `EMAIL_ALREADY_EXISTS` |
| 프로필 조회 | `GET /api/v1/users/me/profile` | `{height_cm, weight_kg, age, gender}` (없으면 `null`) |
| 프로필 저장 | `PUT /api/v1/users/me/profile` JSON 위와 같음 | 저장된 프로필 |

- `gender` 값: `female` / `male` / `other` / `null` (제안 값, contracts.py에 아직 없음)
- 비밀번호 규칙(프론트 검사): 영문+숫자 8자 이상

- 로그인 토큰은 브라우저 쿠키(HttpOnly)에 저장하고, 백엔드 호출 때 `Authorization: Bearer <token>`으로 붙입니다.
- 에러는 팀 형식 `{detail, code}`와 FastAPI 기본 422 형식을 모두 처리합니다.
