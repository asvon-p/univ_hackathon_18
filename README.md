# Fitjang — AI 코디 추천 MVP

## 폴더 구조
| 경로 | 담당 | 내용 |
|---|---|---|
| `shared/contracts.py` | 전원 (관리: C) | 공통 데이터 계약 (Pydantic) |
| `backend/` | C | FastAPI, PostgreSQL, 인증·옷장·추천·피드백 API |
| `recommendation/` | A | `recommend()` — 추천 엔진 |
| `vision/` | B | `extract()` — 의류 속성 추출·분리 |
| `frontend/` | D | 사용자 화면, 2D 아바타 |
| `mocks/` | 전원 | Mock JSON 데이터 |
| `docs/DECISIONS.md` | 전원 | 킥오프 합의 체크리스트 |

## 로컬 실행 (Windows PowerShell 기준)
```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
copy .env.example .env
docker compose up -d          # PostgreSQL
uvicorn backend.app.main:app --reload
```
- API 문서: http://localhost:8000/docs
- 테스트: `pytest backend/tests`

## 브랜치 전략
`main`(시연) ← `develop`(통합) ← `feat/*` (항상 최신 develop에서 생성, PR + 리뷰 1명)

```bash
git switch develop && git pull
git switch feat/recommendation-rules
git merge develop        # 작업 전 최신화
# 작업 후
git push && GitHub에서 develop 대상으로 PR
```
