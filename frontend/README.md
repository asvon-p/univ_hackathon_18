# Fitjang 프론트 — 팀원 D 코드 통합

`origin/feat/frontend@85ddedc`의 FastAPI + Jinja2 화면 서버를 현재 작업 트리에 통합했습니다.
프론트는 별도 Uvicorn 서버이며 `frontend/dist` 빌드는 필요하지 않습니다.
기본은 실제 백엔드 연결입니다. 기존 백엔드 코드와 Git 스테이징 상태를 유지했습니다.

## 데이터를 유지하는 멘토 HTTPS 시연

`scripts.mentor_demo`는 기존 검증 서버와 분리된 프론트 3001 / 백엔드 8003을 사용합니다.
PostgreSQL `fitjang`의 전용 `mentor_demo_v1` 스키마와 `.demo/mentor-demo/storage`에 저장하며,
종료 시 스키마나 이미지를 삭제하지 않습니다. 최초 실행 때만 시연 계정과 합성 의류 9벌
(상의·하의·아우터)을 준비하고, 이후 실행은 수정·삭제·추천·피드백을 그대로 보존합니다.

```powershell
conda activate univ18
$env:FITJANG_SKIP_DOTENV = "1"
python -m scripts.mentor_demo
```

사전 조건: 기존 PostgreSQL이 실행 중이고 Cloudflare 공식 `cloudflared-windows-amd64.exe`를
`.demo/tools/cloudflared.exe`에 설치해야 합니다. 공식 릴리스 SHA256을 확인한 파일만 사용합니다.
Cloudflare 계정 없이 프론트에 임시 HTTPS 주소를 발급하며 백엔드와 DB는 loopback에 유지합니다.
프론트 HTTPS 쿠키에는 Secure/HttpOnly를 적용하고 다른 출처의 폼 제출을 거부합니다.

다른 터미널에서 상태 확인 또는 종료:

```powershell
python -m scripts.mentor_demo --status
python -m scripts.mentor_demo --stop
```

- 공개 주소는 상태 출력과 `.demo/mentor-demo/runtime.json`에서 확인합니다. 터널 재시작 시 바뀝니다.
- 시연 로그인 정보와 임의 JWT 키·가입 초대 코드는 `.demo/mentor-demo/credentials.json`에 보관합니다.
  멘토에게는 로그인 이메일·비밀번호만 전달하고 JWT 키와 초대 코드를 전달하지 않습니다.
- `.env` 및 `.env.demo`는 읽거나 수정하지 않습니다. 기존 setup/seed 스크립트도 실행하지 않습니다.
- 계정은 `mentor-demo@example.com`이며 샘플 옷은 실제 모델 품질 평가용 사진이 아닙니다.
- 추천은 A의 local 엔진, 비전은 현재 local stub입니다. GPT와 GPU 착샷은 사용하지 않습니다.
- PC·Docker·시연 프로세스가 실행 중일 때 접속할 수 있습니다. PC 재부팅 후 자동 시작은 설정하지 않습니다.
- `--stop`은 데이터 보존 종료입니다. `.demo/mentor-demo` 또는 PostgreSQL 볼륨을 삭제하면 복구에
  필요한 이미지·키·데이터를 잃으므로 삭제하지 마세요. 이 경로의 모든 파일은 Git 제외입니다.
- 기본 `frontend_preview`의 종료 명령과 혼용하지 마세요. 두 실행 환경은 서로 독립적입니다.

## .env 없이 실행하는 지속형 로컬 서버

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
- 실제 PostgreSQL `fitjang_test` 안의 전용 `ui_preview_local` 스키마를 재사용하고 최신 마이그레이션을 적용합니다.
- 최초 실행은 빈 DB에서 시작하지만, 이후 종료·재실행은 기존 계정·옷장·추천·피드백을 유지합니다.
  기존 임시 로컬 서버의 계정과 데이터는 지속형 스키마로 복사해 보존했습니다.
- 비전과 추천은 `local`, 날씨는 `open_meteo`입니다. A의 규칙 추천을 실제로 호출하고,
  현재 B의 local 비전은 stub이므로 등록 확인 화면에서 의류 종류·속성을 수정하세요.
- GPT 유료 API, GPU 착샷 서버, 개인 위치·개인 사진을 사용하지 않습니다.
- 업로드용 합성 의류 파일은 출력된 `run_dir`의 `top.jpg`, `bottom.jpg`, `outerwear.jpg`입니다.
- 로그·이미지·실행 정보는 Git 제외 경로 `.demo/frontend-preview/`에 저장합니다.
- 이미지는 `.demo/frontend-preview/persistent/storage`, 로그인 서명 키는
  `.demo/frontend-preview/credentials.json`에 유지합니다. 이 폴더와 DB 볼륨을 삭제하지 마세요.
- 포트가 이미 사용 중이면 오류로 종료하며 기존 서버를 종료하지 않습니다.
- 정상 종료·재시작 또는 실행 오류 발생 시에도 DB 스키마나 업로드 이미지를 삭제하지 않습니다.
  재시작 시 JWT 서명 키도 재사용하지만 만료된 토큰은 다시 로그인해야 합니다.

다른 터미널에서 종료:

```powershell
python -m scripts.frontend_preview --stop
```

자동 날씨 실패 상황을 확인하려면 종료 후 `python -m scripts.frontend_preview --weather manual`로
재시작하세요. 기존 계정과 옷장을 그대로 사용하여 수동 기온으로 추천할 수 있습니다.
이 로컬 스크립트는 공개 개발용 테스트 DB 연결값만 사용하며 외부 공개용이 아닙니다.

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
- 로컬 실행기에서는 전신 사진 화면과 가입 후 사진 단계를 활성화합니다. 업로드 동의는 별도 확인합니다.
- 누끼가 없는 옷도 인증된 원본 이미지 경로로 표시.
- 분석 실패 category=null 검색 처리와 analysis_status 보존. 분석 완료·사용 가능 옷만 추천 가능 수에 반영.
- 프론트 HTTP 대기 시간을 35초로 조정.
- 자동 날씨·조회 실패 시 수동 기온 입력, Open-Meteo 출처 표시.
- 새 추천은 상의·하의와 선택 아우터입니다. 기존 신발 옷장 데이터는 계속 표시합니다.

## 검증과 남은 범위

실제 브라우저 및 PostgreSQL 검증 기록은 [통합 검증 보고서](../docs/FRONTEND_INTEGRATION.md)를 참고하세요.
회원가입·프로필·날씨·옷 등록·추천·피드백에 대한 현재 API는 [팀 통합 계약](../docs/TEAM_API_CONTRACT.md)에 있습니다.

전신 사진 업로드·조회·삭제 백엔드와 실제 connect 호출은 DB `0011`에서 검증했습니다.
업로드 전 프로필에서 `consent_body_info=true` 동의가 필요합니다. 자세한 계약은
[팀 통합 계약](../docs/TEAM_API_CONTRACT.md#전신-사진-api-db-0011)을 참고하세요.
로컬 실행기에서는 `FRONTEND_BODY_PHOTO_ENABLED=true`이며 `--tryon`으로 GPU를 함께 시작합니다.
멘토 공개 시연 실행기도 전신 사진·로컬 GPU 착샷을 활성화했습니다.
공개 주소·실행·검증 내용은 [멘토 착샷 시연 안내](../docs/MENTOR_TRYON.md)를 참고하세요.
RTX 5090에서 사진 업로드→3벌 순차 합성→PNG 응답·추천 HTML 연결을 검증했습니다.
브라우저 제어 연결을 동일한 세션으로 통일한 뒤, 실제 화면의 파일 선택·자르기·업로드·추천·착샷 생성·
최종 576×864 이미지 표시까지 검증했습니다. 기존 계정과 데이터는 보존하고 별도 검증 계정을 사용했습니다.
실행 방법과 테스트 자료는 [로컬 GPU 착샷 안내](../docs/LOCAL_TRYON.md)를 참고하세요.
3D 생성, 실제 GPT 계정 호출 및 실제 B 모델 품질은 이번 검증 범위에 포함하지 않습니다.

이전 프론트 README와 통합 전 코드의 복사본은 `.demo/frontend-integration/before.zip`,
파일 해시는 `before.json`, Git 인덱스 기록은 `index-before.bin`에 보관했습니다.
이번 작업은 작업 트리 소스 통합이며 Git merge 커밋·push를 수행하지 않았습니다.
