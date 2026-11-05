# 팀 합의 체크리스트 (킥오프 때 확정)

> 각 항목에 결정 내용과 날짜를 적어 두세요. 확정 전 항목은 `[ ]`, 확정되면 `[x]`.
> 굵게 표시한 것은 C(Backend·통합)의 제안 기본값입니다.

## 1. 기술 스택 — 가장 먼저
- [ ] 백엔드 프레임워크: **FastAPI** (Pydantic 스키마를 A·B와 그대로 공유, `/docs` 자동 문서로 D와 연동 쉬움)
- [ ] Python 버전: **3.12로 통일** (3.14는 torch 등 ML 라이브러리 호환이 늦을 수 있음 → B 확인 필요)
- [ ] 패키지 관리: **venv + requirements.txt** (또는 uv/poetry 중 하나로 통일)
- [ ] ORM / 마이그레이션: **SQLAlchemy 2.0 + Alembic**
- [ ] PostgreSQL 실행 방식: **docker compose (postgres:16)** / 로컬 설치 / 클라우드(Supabase 등)
- [ ] 프론트엔드: React(Vite)? 앱? — D 결정 (현재 `fitjang-web-dist`가 Vite 빌드로 보임)
- [ ] LLM 공급자·모델명 및 API Key 보관 방식 (서버 `.env` 전용)
- [ ] 날씨 데이터: 외부 API(기상청/OpenWeather) vs 사용자 직접 입력

## 2. 모듈 연결 방식 (C가 주도)
- [ ] **A·B는 순수 Python 함수로 제공, C가 import 해서 호출** (별도 서버 X)
  - A: `recommendation.recommend(context: UserContext, garments: list[GarmentAttributes]) -> RecommendationResult`
  - B: `vision.extract(image_path: str, garment_id: str) -> VisionResult`
- [ ] Vision 처리: **동기 처리(MVP)** vs 백그라운드 작업 — 1장 처리 시간 보고 결정
- [ ] 모델 가중치 위치 (Git 제외, 공유 드라이브/HuggingFace 다운로드)
- [ ] D ↔ C: REST + JSON, Base URL `http://localhost:8000/api/v1`, CORS 허용 포트

## 3. 데이터 계약 (`shared/contracts.py`)
- [ ] 필드명·타입 확정 (`GarmentAttributes`, `UserContext`, `RecommendationResult`, `FeedbackEvent`)
- [ ] **Enum 값 목록**: category / color 이름 목록 / pattern / style 태그 목록 — A·B가 같은 단어를 써야 함
- [ ] ID 형식: **UUID 문자열**
- [ ] 이미지 경로 규칙: `storage/{user_id}/{garment_id}/original.jpg`, `cutout.png`
- [ ] 기온 구간(2 / 10 / 20°C)과 `thermal_level` 1~5 의미

## 4. API 규약
- [ ] 인증: **JWT (Authorization: Bearer)** / 세션
- [ ] 에러 응답 형식: `{"detail": "...", "code": "GARMENT_NOT_FOUND"}`
- [ ] 날짜: ISO 8601 (UTC 저장, 표시 시 KST)
- [ ] 업로드 제한: jpg/png/webp, **10MB**

## 5. Git 협업 규칙
- [ ] `main`(시연용) / `develop`(통합) / `feat/*` — **develop·main 직접 push 금지** (GitHub Branch protection 설정)
- [ ] PR 리뷰어 최소 1명, 머지 방식: **Squash merge**
- [ ] 커밋 메시지: `feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`
- [ ] 코드 스타일: **ruff (format + lint)**
- [ ] 공통 파일(`shared/contracts.py`, `requirements.txt`, DB 모델) 변경은 C에게 알리고 별도 작은 PR로
- [ ] 매일 develop을 내 브랜치에 반영: `git pull origin develop` (또는 rebase)

## 6. 일정·소통
- [ ] 통합 시점(예: 매일 OO시 develop 병합 + 스모크 테스트)
- [ ] Mock 데이터 담당/위치: `mocks/`
- [ ] 시연 환경: 로컬 노트북 / 배포(Render, Railway 등)
- [ ] 소통 채널, 막힌 이슈 공유 방법 (GitHub Issues 권장)
