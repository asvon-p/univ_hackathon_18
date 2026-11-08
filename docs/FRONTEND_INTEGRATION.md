# D 프론트 통합과 실제 화면 검증

2026-11-08. `origin/feat/frontend@85ddedcf4a3b7d889bf6eea09c27d39eaf118a94`의 소스를
현재 `frontend/`에 반영했습니다. 별도 검토 폴더의 복사본이 아니라 실제 프로젝트 코드를 실행했습니다.
Git merge 커밋·push는 하지 않았고 기존 스테이징 상태를 유지했습니다.

## 보존과 실행 환경

- 통합 전 소스 151개를 `.demo/frontend-integration/before.zip`에 보관했습니다.
- 기존 `backend/app/`, `backend/migrations/`, `shared/`, `recommendation/` 소스 78개의
  SHA-256이 통합 전 기록과 동일합니다. Git 인덱스도 `index-before.bin`과 같습니다.
- `.env`를 읽거나 수정하지 않았습니다. 프론트에도 `FITJANG_SKIP_DOTENV=1`을 적용하고,
  검증 서버 프로세스에는 `.env` 파일 열기를 차단하는 Python audit hook을 설치했습니다.
- `scripts/frontend_preview.py`가 별도 PostgreSQL 테스트 스키마와 이미지 저장소를 만듭니다.
  개발 DB와 기존 시연 서버는 사용하지 않습니다. 현재 DB 마이그레이션은 `0010`입니다.
- 화면 `http://127.0.0.1:3000`, 백엔드 `http://127.0.0.1:8002`.
- 필요한 Jinja2 3.1.6을 `univ18`에 설치했습니다. `pip check`가 통과했습니다.

## 통합하며 수정한 동작

| 항목 | 반영한 내용 |
|---|---|
| 실제 API 연결 | 기본 `FRONTEND_USE_MOCK=false`, 35초 HTTP timeout |
| 회원가입 | 선택 초대 코드 입력과 JWT 쿠키 연결 |
| 프로필 | 신체정보 저장 동의 체크박스. 동의 없이 최초 저장하면 안내 후 거부 |
| 가입 이후 | 미구현 전신 사진 단계를 기본으로 숨기고 옷장으로 이동 |
| 옷 이미지 | cutout이 없더라도 인증된 원본 이미지 경로로 표시 |
| 분석 상태 | 응답의 analysis_status를 프론트 모델에 보존. 분석 미완료 옷은 추천 가능 수에서 제외 |
| 분석 실패 검색 | category=null인 옷도 검색 문자열 처리 가능 |
| 날씨 | /weather 연결, 자동 기온 입력, 실패 시 수동 입력 안내, Open-Meteo 출처 링크 |

첫 PostgreSQL 화면 테스트에서 옷을 등록한 뒤 `/recommend`가 500을 반환했습니다.
프론트 `Garment` 모델에서 analysis_status를 버리는데 추천 화면은 해당 필드를 참조한 것이 원인이었습니다.
프론트 모델에 상태 필드를 추가했고 같은 테스트와 전체 회귀 테스트가 통과했습니다.

## 실제 브라우저에서 확인한 내용

Windows Chrome의 URL 식별 문제를 우회해 보안 검사를 끈 것이 아니라, URL을 확인할 수 있는
앱 내 브라우저의 정식 API로 검증했습니다. 검증 탭을 유지하도록 설정해 세션 소실을 피했습니다.
파일 전달 도구에서 긴 대기가 있었지만, 이후 업로드 결과를 실제 화면에서 확인했습니다.

1. 화면에서 테스트 계정 회원가입. 이전 실행에서 동의 없는 프로필 저장 거부와 동의 후 저장을 확인했고,
   최종 실행에서는 신체정보를 입력하지 않고 건너뛰는 흐름도 확인했습니다.
2. 상의 합성 JPEG를 앨범 선택 → 사진 자르기 → 업로드 → 이름·색상·패턴·스타일·보관 위치 수정 → 옷장 저장.
3. 추천 조합용 하의·아우터 합성 이미지는 동일 테스트 계정에 API로 준비했습니다.
   세 종류의 업로드·확인 폼은 별도 HTTP 통합 테스트에서도 모두 실행했습니다.
4. 옷장에 상의·하의·아우터 3벌과 실제 저장 이미지가 표시됐습니다.
5. 추천 화면에서 Open-Meteo 자동 기온 **5.3℃**, `서울 기준 · 이슬비`가 표시됐습니다.
   사용자 개인 위치 대신 프론트의 서울 기본 좌표를 사용했습니다. 실제 위치 권한 허용 경로는 검증하지 않았습니다.
6. 일상·캐주얼을 선택해 A의 local 추천 2개를 표시했습니다. 상의·하의 조합과 아우터 포함 조합이며 신발은 없습니다.
7. 코디 1의 `좋아요`, 코디 2의 `별로예요`를 클릭했고 선택 상태가 화면에 반영됐습니다.
8. 최종 브라우저 콘솔 오류는 0건이었습니다.

검증 캡처(Git 제외):

- `.demo/reports/frontend-ui/wardrobe.jpg`
- `.demo/reports/frontend-ui/weather-form.jpg`
- `.demo/reports/frontend-ui/recommendation-feedback.jpg`

## 실제 PostgreSQL 검증

전체 **281개 통과**, 실패·제외 없음. 기존 Starlette/httpx deprecation 경고 1건입니다.
Ruff lint/format과 `pip check`도 통과했습니다.

새 `backend/tests/e2e/test_frontend_pages.py`는 실제 프론트 서버 → 백엔드 서버 → PostgreSQL을 거칩니다.
자동 날씨 성공과 날씨 조회 불가 두 설정에서 초대 코드, 동의 검증, 의류 3종 업로드·수정·이미지,
추천, 수락/거절, 미인증 접근 차단, 로그아웃·재로그인을 검증했습니다.
날씨 HTTP는 재현 가능한 응답으로 대체하고, 실제 Open-Meteo 호출은 위 브라우저 검증에서 확인했습니다.
기존 D 연결 함수 테스트도 이제 현재 프로젝트의 `frontend/`를 대상으로 실행했습니다.

화면 검증 스키마의 최종 데이터는 계정 1·의류 3·추천 1·코디 2·피드백 2개입니다.
추천의 `context.temperature_c=5.3`, `provider=local`, 피드백 `accepted=true/false`가 각각 저장됐고
두 피드백이 화면에서 본 동일 추천을 참조하는 것을 SQL로 확인했습니다.
추천 요청에는 D가 조회한 기온을 전달하므로 날씨 출처 메타데이터는 클라이언트 입력을 뜻하는 manual입니다.

보고서(Git 제외): `.pytest_cache/frontend-pg-full.xml`, `.pytest_cache/frontend-pages-fixed.xml`,
`.demo/reports/frontend-ui/database-proof.json`, `.demo/reports/frontend-ui/verification.json`.

## 실행과 한계

[프론트 README](../frontend/README.md)에 실행·종료 명령과 설정을 정리했습니다.
검증용 실행은 종료 시 자체 DB 스키마를 삭제합니다. 화면 상태를 확인할 수 있도록 현재 실행은 유지했습니다.

현재 B의 local 비전은 stub이므로 속성을 화면에서 확인·수정했습니다. 합성 이미지는 모델 품질 평가용이 아닙니다.
GPT 유료 API·실제 개인 위치 권한·전신 사진·GPU 착샷·3D 생성은 이번 검증 범위에 포함되지 않습니다.
외부 서비스 운영 배포, HTTPS·공개 도메인 설정, Git 커밋·push도 별도 작업입니다.
