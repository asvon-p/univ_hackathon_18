# 팀 브랜치 통합 분석 — 2026-11-08

> **현재 통합 상태:** D `85ddedc`의 소스를 작업 트리에 반영하고 실제 화면·PostgreSQL 281개 테스트를 검증했습니다.
> 아래는 병합 전 분석 기록이며 최신 결과는 [FRONTEND_INTEGRATION.md](FRONTEND_INTEGRATION.md)가 기준입니다.
> Git merge 커밋·push는 수행하지 않았습니다.

> **구현 반영:** 아래는 변경 전 분석 기록입니다. 이후 A ranking·신발 없는 추천,
> D 가입 토큰·프로필 경로/소수/age·의류 메타데이터·이미지 조회·추천 본문 호환을 구현했습니다.
> 현재 계약 2.0·DB 0010·GPT-6 Luna 설정은 [TEAM_API_CONTRACT.md](TEAM_API_CONTRACT.md)가 기준입니다.
> 이후 자동 날씨 `/weather`·추천 좌표 입력·수동 대체도 연결했습니다([날씨 연동 안내](WEATHER_INTEGRATION.md)).
> PostgreSQL 279개(실제 D connect 모듈 포함), SQLite 260개 통과/19개 제외.
> 프론트/착샷 브랜치 전체 병합과 Git push는 수행하지 않았습니다.

현재 C의 로컬 스테이징 코드와 `git fetch origin`으로 받은 팀 브랜치를 비교했습니다. 기능 일부가 미완성이어도 통합을 시작할 수 있습니다. 다만 현재는 파일 병합 후 화면과 API가 바로 연결되는 상태가 아니므로, 계약을 맞춘 기능부터 `develop`에 PR로 합치는 순서를 권장합니다.

## 1. 확인한 버전과 분석 범위

| 대상 | 확인한 상태 | 의미 |
|---|---|---|
| `origin/main`, `origin/develop` | `559c2ae` | 둘 다 초기 scaffold. 완성된 C 백엔드는 아직 원격에 없음 |
| C 로컬 `codex/recommendation-integration` | HEAD `559c2ae`, 스테이징 변경 123개 | `git add`는 됐지만 커밋·푸시 전 |
| `origin/feat/recommendation-ranking` | `0ba94be`, 변경 3개 | `ranking.py`, `rules.py`, `test_local.py` |
| `origin/feat/frontend` | `85ddedc`, 변경 14개 | FastAPI + Jinja2 화면 서버, connect 계층, Mock 데이터 |
| `origin/feat/tryon-server` | `a0b57a5`, 변경 12개 | FASHN VTON 사진 합성 API·CLI·실행 가이드 |

현재 공통 조상 대비 변경 파일을 비교하고, C와 겹친 파일을 `git merge-file -p`로 검사했습니다. **텍스트 충돌은 `frontend/README.md` 한 곳에서 재현**됐습니다. A·D·tryon 브랜치끼리는 변경 경로가 겹치지 않습니다. 이는 이 커밋들에 대한 파일 비교 결과이며, 기능 호환성 검증을 대신하지 않습니다.

원본 작업 트리와 인덱스에 팀 코드를 병합하지 않고 Git 제외 경로 `.demo/integration-audit-20261108/snapshot`에 소스 복사본을 만들어 비교했습니다. 근거 파일은 `branch-comparison.json`, `contract-probes.json`, `lint-findings.json`입니다. 실제 사용자 데이터·GPU 서버·외부 이미지 전송은 사용하지 않았습니다.

## 2. 기존 계획에서 바뀐 실행 구조

프론트는 React/Vite 정적 빌드가 아닙니다. `frontend.app:app`을 Uvicorn으로 실행하는 **FastAPI + Jinja2 서버**이며, 브라우저의 쿠키에서 토큰을 꺼내 C 백엔드에 Bearer 인증으로 전달합니다. 현재 C의 `frontend/dist` 정적 제공·SPA fallback 계획은 이 브랜치의 실행 방식에 맞춰 다시 정해야 합니다.

현재 코드에 맞는 1차 구성은 다음과 같습니다.

```text
브라우저
  → 프론트 화면 서버 :3000 (Jinja2, 쿠키, 이미지 중계)
      → C API :8001 /api/v1 및 /media (JWT, PostgreSQL, 옷장·추천)
      → 착샷 GPU API :8002 또는 별도 GPU PC (X-API-Key, job polling)
```

8001은 현재 C 시연 서버 포트입니다. tryon은 기본 8000이므로 같은 PC에서 실행할 때 C 개발 서버의 기본 8000과 겹치지 않게 별도 포트를 정해야 합니다. 공개 시에는 프론트 진입점과 내부 서버 간 연결을 기준으로 터널 구성을 정합니다. 프론트가 서버에서 C를 호출하므로 모든 요청을 브라우저의 CORS/proxy 문제로 취급하면 안 됩니다.

`tryon_server`는 사람 사진과 옷 사진을 받아 **PNG 착용 이미지**를 만드는 기능입니다. 3D 메시·GLB 생성이나 회전 가능한 3D 아바타 구현은 이 브랜치에 없습니다. 또한 옷의 카테고리·색상·cutout을 추출하는 B의 `vision.extract()`와는 별도 기능입니다. 현재 확인한 브랜치에는 실제 비전 추출 구현이 없습니다.

## 3. 연결을 막는 차이와 처리 방향

아래 검증 오류는 실제 프론트 모델/요청 데이터를 C의 Pydantic 스키마에 넣어 재현했습니다. HTTP 422 표기는 해당 API 검증에서 예상되는 응답이며, 실제 사용자 계정을 생성한 HTTP 통합 테스트 결과는 아닙니다.

| 항목 | 현재 차이·결과 | 필요한 수정 |
|---|---|---|
| 회원가입 | D는 email/password만 보내고 토큰을 기대. C는 nickname 필수이며 사용자 정보를 반환. nickname 누락 검증 실패 | D 가입 화면에 닉네임·필요 시 초대 코드 추가. 가입 성공 후 로그인 API를 호출해 토큰 저장 |
| 로그인 | 요청·토큰 응답은 대체로 일치 | 실제 DB 계정으로 검증. D의 Mock 데모 계정은 C의 `.env.demo` 계정과 별개 |
| 프로필 | D: `GET/PUT /users/me/profile`, age·실수 키/몸무게. C: `GET/PATCH /profile`, birth_year·정수 키/몸무게·신체정보 동의 | 경로/메서드 수정, 출생연도 입력 또는 명확한 변환 정책, 정수/소수 지원 범위 합의, 동의 UI 추가. 동의를 임의로 true로 보내지 않음 |
| 의류 수정 | D의 name/location은 C의 허용 필드가 아니어서 422 | 1차에는 해당 입력을 숨기거나 전송 제외. 실제 저장하려면 C API·DB 마이그레이션 추가. 화면에서 저장됐다고 표시만 하면 안 됨 |
| 분석 실패 응답 | C는 category=null 가능. D의 `Garment`는 필수 category라 역직렬화 실패 | API용 응답 모델을 도입해 nullable category, analysis_status, error를 보존하고 수동 수정·재분석 UI 연결 |
| 이미지 | D는 `/garments/{id}/image?kind=...` 호출. C는 응답의 `/media/...` URL 제공. D 모델이 image_url/cutout_url을 버림 | 응답 URL 필드 보존 후 프론트 이미지 중계에서 JWT를 붙여 가져오기. API prefix `/api/v1`과 별개인 `/media` 경로를 올바르게 구성 |
| 추천 요청 | D는 `UserContext.model_dump()` 전체를 전송. user_id 및 C 계약 1.1의 recent_feedback이 포함돼 422 | HTTP 요청에는 temperature_c, occasion, preferred_styles, excluded_items만 전송. 사용자 ID·피드백 이력은 C가 구성 |
| 자유 스타일 문구 | D는 모르는 문구를 그대로 preferred_styles에 넣음. `꾸안꾸` 등은 공통 Literal 검증 실패. 화면에서는 ApiError만 잡으므로 ValidationError 처리도 부족 | 1차에는 허용 코드 선택/명시적 매핑. 자유 문장을 지원하려면 별도 입력 필드와 처리 계약 추가 |
| 신발 | A의 신규 ranking·D의 화면은 상의/하의 중심. C는 신발 필수 | 아래 계약 결정부터 수행. 임의 신발 ID를 채워 통과시키지 않음 |
| 오늘 기온 | D는 `/weather` 호출. C는 API 없고 추천 요청의 temperature_c 필수 | 1차에는 직접 입력 유지·자동 날씨 미제공 표시. 홈의 자동 추천은 weather가 없으면 실행하지 않는 현재 분기도 수정 필요 |
| 전신 사진 | D는 `/users/me/body-photo` GET/PUT/DELETE와 갱신 시각 요구. C에는 없음 | 1차 가입 흐름에서 전신 사진 단계를 건너뛰게 하고 착샷 비활성화. 착샷까지 연결하려면 동의·소유권·교체/삭제·탈퇴 정리를 포함한 새 API 구현 |
| 요청 제한·대기 | D 일반 API timeout 10초, C 분석/추천 timeout 기본 20/30초. D ApiError에 Retry-After 보존 없음 | 용도별 timeout 정렬, 진행/중복 제출 처리, 429의 Retry-After 전달. 프론트 timeout 후 백엔드가 저장을 끝낼 수 있어 재조회 필요 |
| 재분석·탈퇴 | C API는 완료됐지만 D connect/UI에는 대응 경로 없음 | 재분석 200의 done/failed·409 처리, 탈퇴 204 후 토큰·화면 캐시 제거를 연결 |

일치하는 부분도 있습니다. 의류 목록/단건/삭제의 기본 경로, 로그인 토큰, 피드백 본문의 핵심 필드는 맞습니다. D의 공통 request 함수는 201·204를 처리하므로 README의 과거 200 표기만 보고 이 부분을 장애로 판단하지 않았습니다.

## 4. A 추천 엔진 연결

`recommendation/ranking.py`에는 `recommend_top3(garments, context)`가 있지만, `recommendation/__init__.py`의 `recommend(context, garments)`는 여전히 초기 임시 구현입니다. **브랜치를 합치고 local로 설정하는 것만으로 새 랭킹이 사용되지 않습니다.** 격리 복사본에서 진입점을 호출해 `(stub)` 추천 사유가 반환되는 것을 확인했습니다.

연결할 때 필요한 작업:

1. 공개 진입점 `recommend(context, garments)`에서 실제 `recommend_top3(garments, context)`를 호출합니다.
2. flat dict 목록을 공통 `RecommendationResult`와 `Outfit(items=...)`로 변환합니다. 추천 사유·스타일 태그·점수도 정의합니다. 저장용 최종 ID는 계속 C가 발급합니다.
3. 신발 계약을 맞춥니다. 현재 ranking 결과를 `OutfitItems`로 검증하면 shoes 누락으로 실패합니다.
4. `preferred_styles`·`recent_feedback`의 반영 범위를 명시합니다. 현재 실제 점수 경로는 색상·occasion을 사용하며, 별도 style scoring 함수는 현재 합산 경로에서 호출되지 않습니다. 개인화가 연결됐다고 표시하면 안 됩니다.
5. 추운 날에도 아우터 없는 후보를 만드는 현재 규칙, 옷이 많을 때 후보 조합 수를 A와 점검합니다. 1차 연결 이후 품질/성능 검증 대상으로 둡니다.

**권장 계약 방향:** A·D가 현재 사용하는 상의+하의 중심 코디에 맞춰 `shoes`를 선택으로 만드는 방안을 팀과 확정합니다. 최종 결정 전에는 기존 계약을 유지합니다. 선택으로 바꾸면 `shared/contracts.py`만 수정해서는 안 됩니다. DB `recommendation_outfits.shoes_id`의 NULL 허용, 서비스 필수 슬롯, 대체 추천, 결과 검증, 매퍼의 UUID 변환, 삭제 연계, seed 및 테스트를 함께 변경해야 합니다. `0009` 다음의 새 마이그레이션으로 기존 추천 데이터를 보존합니다. 타입 변경이므로 문서의 계약 버전 규칙도 적용합니다.

당장 기존 계약을 유지하기로 한다면 A의 후보/결과에 실제 보유 신발을 포함하고 D에도 신발 필요 조건을 표시해야 합니다. 사용자가 보유하지 않은 신발이나 가짜 ID를 넣는 방식은 사용할 수 없습니다.

## 5. 착샷 기능을 붙일 때 추가할 범위

`frontend/connect/tryon.py`와 tryon API의 요청·응답 형식은 대체로 맞습니다. D는 하의→상의→아우터 순서로 결과 사진을 다음 단계의 사람 사진으로 넣으며, 서버의 202/job polling을 사용합니다. 그러나 이 경로는 전신 사진 API와 실제 옷 이미지 중계가 먼저 완성돼야 동작합니다.

실제 사용자 데이터를 넣기 전 해결할 사항:

- **캐시 결과 인증:** D의 `_mine()`은 토큰 문자열의 해시가 저장된 owner와 같은지만 검사합니다. JWT 유효성·계정 탈퇴 여부를 재확인하지 않습니다. 기존 함수만 분리 실행한 검증에서, 캐시에 결과를 준비하고 그 owner에 대응하는 유효하지 않은 쿠키를 전달하면 이미지 응답 200을 확인했습니다. 이는 다른 사용자의 이미지를 임의 조회했다는 의미는 아니며, 만료/탈퇴 후에도 옛 토큰으로 자신의 캐시 결과가 제공될 수 있다는 문제입니다. C의 JWT 확인과 결과 조회 권한을 연결해야 합니다.
- **삭제 범위:** C의 탈퇴는 현재 C 저장소의 사용자 폴더만 정리합니다. D 메모리의 합성 이미지와 GPU 서버 `outputs/api` 결과는 포함되지 않습니다. 전신 사진 삭제·동의 철회·탈퇴 시 캐시 무효화, 진행 작업 취소/결과 폐기, 원격 결과 삭제를 연결해야 합니다.
- **원격 결과 보관:** GPU 서버는 공용 API 키와 메모리 JOBS를 사용하고 사용자 ID 기반 소유권이 없습니다. 사용자 권한은 앞단 C 또는 D에서 보장해야 합니다. 현재 TTL 정리는 다음 작업 완료 시 실행되며 재시작 후 기존 디스크 결과를 스캔하는 구조도 아니므로, '6시간 후 항상 삭제'로 해석하면 안 됩니다.
- **키/포트/실행 경로:** 키는 서버 환경변수로 관리합니다. `run_server.ps1`에 지정된 Python 절대 경로는 팀원 PC 전용이라 다른 PC에서 그대로 실행할 수 없습니다. 인터프리터 인자와 포트를 환경에 맞게 바꿔야 합니다.
- **환경:** C 환경과 GPU 환경을 분리해 검증하는 편이 현재 구조에 맞습니다. 모델/가중치·CUDA 구성은 tryon 담당자의 검증 환경을 기준으로 이 PC에서 확인합니다. 이번 분석은 GPU 추론·원격 서버 가동 여부·품질을 검증하지 않았습니다.

첫 통합에서는 기존 D→tryon 호출 구조를 활용할 수 있지만, 사용자별 작업/권한/탈퇴 정리를 일관되게 관리하려면 C의 tryon 중계 서비스와 사용자별 job 기록을 추가하는 방향을 권장합니다. 이것은 현재 구현된 기능이 아니라 다음 구현 범위입니다.

## 6. 의존성과 검증 준비

- 현재 `univ18`에서 프론트 앱 import를 시도했을 때 **Jinja2 미설치 오류**가 발생했습니다. D의 `frontend/requirements.txt` 설치·환경 문서 반영이 필요합니다. `environment.yml`은 현재 백엔드 requirements만 포함합니다.
- A 브랜치 최신 버전에는 별도 requirements 파일이 없고 신규 추천 코드는 Pydantic 계약을 사용합니다. 이전에 전달받은 Pydantic 조건은 기존 환경으로 충족하지만 함수 연결은 별도 작업입니다.
- GPU 서버는 별도 패키지/가중치와 `python-dotenv`가 필요합니다(README의 설치 명령에 포함). GPU 패키지를 `univ18`에 일괄 설치하는 식으로 통합하지 않습니다.
- C의 Ruff 설정으로 신규 팀 파일들을 검사한 결과 **215건의 lint 항목**이 나왔습니다. 긴 줄·import 정리 등도 포함하므로 215개의 실행 장애라는 뜻은 아닙니다. 현재 `verify_demo.ps1`은 lint부터 실행하므로 이 상태로는 검증 단계가 통과하지 않습니다. 코드·HTML 문자열을 확인하면서 수정해야 합니다.
- C의 최근 210개 PostgreSQL 테스트 통과는 **C 단독 구현 기준**입니다. 이번 분석에서 합친 앱의 전체 E2E나 실제 GPU 검증을 통과했다고 판단하지 않았습니다. 기존 `(stub)`·`stub-0` 고정 기대 테스트도 실제 모듈 연결에 맞춰 정리해야 합니다.

## 7. 권장 병합 순서

`CONTRIBUTING.md`의 **기능 브랜치 → develop PR → 작성자 외 1명 승인 → Squash and merge** 흐름을 유지합니다. main은 마지막 시연 검증 후 대상으로 둡니다. 기능이 일부 미완성인 것은 통합 시작을 막지 않지만, 병합할 각 PR의 기본 실행 경로는 검증해야 합니다.

### 1차 — C 백엔드를 공통 기준으로 공유

현재 스테이징된 C 코드를 검토·커밋하고 `codex/recommendation-integration`에 푸시합니다. 원격 develop에는 아직 이 코드가 없으므로 팀원은 새 인증/API 규칙을 사용할 수 없습니다. 현재 브랜치에 작업을 보관하는 커밋·푸시와, 팀 규칙에 따른 PR 분리는 구분합니다. 공통 계약/의존성과 DB 변경은 작은 PR로 먼저 공유하고, API·테스트·실행 도구는 의존 관계에 맞춰 후속 PR로 합칩니다.

### 2차 — 계약 결정과 A의 실제 엔진 연결

신발 필수 여부를 합의합니다. 변경이 필요하면 C가 계약/DB/서비스/테스트 PR을 먼저 올립니다. 이후 A가 최신 develop을 반영해 ranking 진입점을 연결하고, 추천 브랜치 PR을 develop으로 올립니다. 코드를 단순 병합한 뒤에도 stub가 호출되는 상태를 완료로 처리하지 않습니다.

### 3차 — D의 기본 서비스 흐름 연결

D가 최신 develop을 반영해 `connect/`의 가입·프로필·옷장·미디어·추천 요청을 수정합니다. 분석 실패 모델과 429·timeout·탈퇴 처리를 연결합니다. 1차 화면 범위는 **가입/로그인 → 옷 등록/수정 → 수동 기온 추천 → 피드백 → 재분석/탈퇴**로 잡습니다.

자동 날씨·전신 사진·착샷·미지원 이름/보관 위치는 미완성 상태를 사용자에게 명확히 표시하거나 입력/진입점을 숨깁니다. `FRONTEND_USE_MOCK=false`는 전체 C 연동을 실제 모드로 전환하므로, 기능 하나만 미완성이라고 전체 Mock을 켜 둔 상태를 통합 완료로 보지 않습니다. 가입 후 전신 사진 단계로 가는 흐름도 실제 지원 여부에 맞춰 수정해야 합니다.

`frontend/README.md` 충돌은 D 실행 방식과 C API 계약·운영 안내를 함께 보존해 해결합니다. 한쪽 파일 전체를 선택하면 필요한 문서가 사라집니다.

### 4차 — 전신 사진·착샷 통합

C의 전신 사진 저장/권한/동의/삭제 API와 원격 job 정리를 구현한 뒤 tryon 브랜치의 소스·실행 환경을 통합합니다. 실제 GPU의 단일 의류 → 여러 의류 순차 합성, 재로그인·타인 접근 차단·탈퇴 중 작업·실패/timeout·재시작을 검증합니다. tryon 소스는 그 전에 비활성 기능으로 병합할 수 있으나 사용자에게 착샷 기능을 제공하는 완료 조건은 별도입니다.

### 5차 — 전체 develop 검증 후 시연 배포

Mock을 끈 프론트와 실제 PostgreSQL로 HTTP/화면 E2E, 모델/GPU 시간, 새 PC/백업 복원, 초대 코드/외부 접속을 확인합니다. 이후 develop→main PR 및 시연 태그/별도 checkout을 진행합니다.

## 8. 실행 명령의 기준

아래는 **통합 수정이 끝난 뒤의 실행 구성 예시**입니다. 지금 바로 설정만 바꿔 실행하면 3절의 불일치가 발생합니다.

```powershell
# C 서버: 프로젝트 루트, univ18 환경
.\scripts\start_demo.ps1 -Provider local -Port 8001

# D 서버: 별도 터미널, 프로젝트 루트
conda activate univ18
python -m pip install -r frontend/requirements.txt
$env:BACKEND_API_URL = 'http://127.0.0.1:8001/api/v1'
$env:FRONTEND_USE_MOCK = 'false'
python -m uvicorn frontend.app:app --host 127.0.0.1 --port 3000

# GPU 서버를 같은 PC에서 띄울 때: 별도 검증된 GPU 환경/터미널
Set-Location tryon_server
$env:TRYON_PORT = '8002'
python server.py
```

1차 통합에서 착샷을 끄려면 프론트 프로세스의 `TRYON_API_URL`·`TRYON_API_KEY`를 비워 `enabled()`가 false가 되게 합니다. 전신 사진 온보딩 단계까지 자동으로 없어지는 것은 아니므로 화면 흐름 수정도 필요합니다. 착샷을 켤 때는 실제 GPU 서버 주소·키를 프론트 서버 설정에 넣고 재시작합니다.

팀원별 최신 develop 반영은 자신의 변경을 먼저 커밋한 뒤 자신의 기능 브랜치에서 수행합니다.

```powershell
git fetch origin
git merge origin/develop
# 충돌 해결 및 담당 기능 검증 후
git push
```

현재 C 브랜치 첫 푸시는 `git push -u origin codex/recommendation-integration`입니다. 이후 PR의 base는 develop입니다. 사용자 인덱스를 보존하기 위해 이번 분석에서는 커밋·푸시·실제 병합을 수행하지 않았습니다.

## 9. 코드 근거

- [D 연결 계층과 실행 방식](https://github.com/asvon-p/univ_hackathon_18/tree/85ddedcf4a3b7d889bf6eea09c27d39eaf118a94/frontend)
- [A ranking 구현](https://github.com/asvon-p/univ_hackathon_18/blob/0ba94bef7285b75dbc7d0f8e1bb7468eebb9b16b/recommendation/ranking.py), [교체되지 않은 진입점](https://github.com/asvon-p/univ_hackathon_18/blob/0ba94bef7285b75dbc7d0f8e1bb7468eebb9b16b/recommendation/__init__.py)
- [tryon 서버](https://github.com/asvon-p/univ_hackathon_18/blob/a0b57a5949add3f7027cf278c25637ee9e57996b/tryon_server/server.py)
- C: `backend/app/schemas/`, `backend/app/services/recommendation.py`, `backend/app/services/account.py`, `backend/app/api/v1/media.py`, `shared/contracts.py`의 현재 스테이징 코드
