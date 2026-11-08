# Fitjang 프론트 — 팀원 D 코드 통합

`origin/feat/frontend@85ddedc`의 FastAPI + Jinja2 화면 서버를 현재 작업 트리에 통합했습니다.
프론트는 별도 Uvicorn 서버이며 `frontend/dist` 빌드는 필요하지 않습니다.
기본은 실제 백엔드 연결입니다. 기존 백엔드 코드와 Git 스테이징 상태를 유지했습니다.

## .env 없이 실행하는 검증 서버

사용자가 금지를 해제하기 전까지 에이전트는 `.env`를 읽거나 수정하지 않습니다.
아래 전용 스크립트는 두 서버의 dotenv 로딩을 생략하고, `.env` 파일을 열면 오류가 나는 검사도 설치합니다.
기존 Docker PostgreSQL이 실행 중이어야 하며 Docker Compose는 호출하지 않습니다.

```powershell
conda activate univ18
pip install -r frontend/requirements.txt
python -m scripts.frontend_preview
```

- 화면: http://127.0.0.1:3000
- 백엔드: http://127.0.0.1:8002/docs
- 실제 PostgreSQL `fitjang_test` 안의 독립 `ui_preview_*` 스키마를 생성하고 `0010`까지 마이그레이션합니다.
- 새 빈 옷장에서 시작합니다. 화면에서 테스트 계정을 가입하세요. 기존 개발 계정·옷장은 사용하지 않습니다.
- 비전과 추천은 `local`, 날씨는 `open_meteo`입니다. A의 규칙 추천을 실제로 호출하고,
  현재 B의 local 비전은 stub이므로 등록 확인 화면에서 의류 종류·속성을 수정하세요.
- GPT 유료 API, GPU 착샷 서버, 개인 위치·개인 사진을 사용하지 않습니다.
- 업로드용 합성 의류 파일은 출력된 `run_dir`의 `top.jpg`, `bottom.jpg`, `outerwear.jpg`입니다.
- 로그·이미지·실행 정보는 Git 제외 경로 `.demo/frontend-preview/`에 저장합니다.
- 포트가 이미 사용 중이면 오류로 종료하며 기존 서버를 종료하지 않습니다.
- 종료하면 이 실행이 만든 DB 스키마만 삭제합니다. 로그와 합성 이미지 파일은 검증 증거로 남습니다.

다른 터미널에서 종료:

```powershell
python -m scripts.frontend_preview --stop
```

자동 날씨 실패 상황을 확인하려면 종료 후 `python -m scripts.frontend_preview --weather manual`로
새 검증 환경을 시작하세요. 새 계정으로 가입한 뒤 수동 기온으로 추천할 수 있습니다.
이 검증용 스크립트는 공개 개발용 테스트 DB 연결값만 사용하며 실제 서비스 실행용이 아닙니다.

## 별도로 실행 중인 백엔드와 연결

사용자가 백엔드를 실행한 뒤, 프론트 터미널에 다음 값을 지정할 수 있습니다.
백엔드 포트는 실제 사용 중인 포트로 맞춥니다. 프론트에는 OpenAI/JWT 비밀키를 넣지 않습니다.

```powershell
$env:FITJANG_SKIP_DOTENV = "1"
$env:FRONTEND_USE_MOCK = "false"
$env:BACKEND_API_URL = "http://127.0.0.1:8001/api/v1"
python -m uvicorn frontend.app:app --host 127.0.0.1 --port 3000
```

`FRONTEND_USE_MOCK=true`는 화면 개발 전용입니다. 실제 연동 검증에서는 false를 사용합니다.

## 통합하며 수정한 부분

- 회원가입 초대 코드 입력과 가입 JWT 쿠키 연결.
- 최초 신체정보 저장에 명시적 동의 체크박스. 미동의 요청은 백엔드가 거부합니다.
- 아직 없는 전신 사진 API 단계는 기본 가입 흐름에서 제외하고 옷장으로 이동.
- 누끼가 없는 옷도 인증된 원본 이미지 경로로 표시.
- 분석 실패 category=null 검색 처리와 analysis_status 보존. 분석 완료·사용 가능 옷만 추천 가능 수에 반영.
- 프론트 HTTP 대기 시간을 35초로 조정.
- 자동 날씨·조회 실패 시 수동 기온 입력, Open-Meteo 출처 표시.
- 새 추천은 상의·하의와 선택 아우터입니다. 기존 신발 옷장 데이터는 계속 표시합니다.

## 검증과 남은 범위

실제 브라우저 및 PostgreSQL 검증 기록은 [통합 검증 보고서](../docs/FRONTEND_INTEGRATION.md)를 참고하세요.
회원가입·프로필·날씨·옷 등록·추천·피드백에 대한 현재 API는 [팀 통합 계약](../docs/TEAM_API_CONTRACT.md)에 있습니다.

전신 사진 업로드·GPU 착샷·3D 생성, 실제 GPT 계정 호출 및 실제 B 모델 품질은 이번 검증 범위에 포함하지 않습니다.
`FRONTEND_BODY_PHOTO_ENABLED`는 백엔드 전신 사진 API가 구현된 뒤에만 켜세요.

이전 프론트 README와 통합 전 코드의 복사본은 `.demo/frontend-integration/before.zip`,
파일 해시는 `before.json`, Git 인덱스 기록은 `index-before.bin`에 보관했습니다.
이번 작업은 작업 트리 소스 통합이며 Git merge 커밋·push를 수행하지 않았습니다.
