# 멘토·고객 검증: 무료 고정 HTTPS와 로컬 데이터 공유

2026-11-11 기준입니다. 기존 Quick Tunnel 주소 대신 **Tailscale Funnel**을 사용합니다.

- 현재 주소: <https://sgy-host.taile39b25.ts.net:8443/login>
- 도메인 구매가 필요 없습니다. 방문자는 Tailscale 설치나 가입 없이 접속합니다.
- 이 PC의 Tailscale 기기명·tailnet DNS 이름을 유지하면 서버 재시작 후에도 주소를 재사용합니다.
- 기존 443 포트의 비공개 Serve(`127.0.0.1:8000`)는 유지하고, **8443만** 멘토 프론트로 공개합니다.
- PC·인터넷·Docker DB·멘토 서버·Tailscale이 켜져 있어야 합니다. GPU 착샷에는 GPU 서비스도 필요합니다.
- Funnel은 대역폭 제한이 있는 시연용 경로이며, 무료 주소가 상시 가동 서버를 제공하는 것은 아닙니다.

공식 안내: [Funnel](https://tailscale.com/docs/features/tailscale-funnel),
[로컬 서버 공개](https://tailscale.com/docs/use-cases/application-testing/share-local-dev-server-with-internet).

## 데이터와 계정

로컬(`localhost:3000`)과 멘토(`localhost:3001` 및 위 HTTPS)는 이제 같은 영구 DB·이미지를 사용합니다.
서버 사이에 데이터 복사나 동기화 작업을 반복하지 않습니다. 같은 계정으로 로그인하면 같은 옷장·프로필·추천·피드백을 봅니다.

- DB: `fitjang_test`, 스키마 `ui_preview_local`.
- 이미지: `.demo/frontend-preview/persistent/storage`.
- JWT 서명 키: `.demo/frontend-preview/credentials.json`의 기존 키.
- 공개 회원가입은 초대 코드 없이 이메일·비밀번호로 진행합니다. 새 가입자의 사진은 session 정책입니다.
  [공개 시연 보안 안내](PUBLIC_DEMO_SECURITY.md)에 소스 접근 차단과 가입 운영 방법을 정리했습니다.
- 멘토의 이전 DB `fitjang.mentor_demo_v1`와 이미지 저장소는 복구용으로 남겼습니다. 이후 변경은 공유 DB에만 저장됩니다.
- 이전 멘토 계정 1개·의류 9벌·추천 32건·코디 27건·피드백 4건·이미지 10개를 ID와 비밀번호 해시까지 보존해 합쳤습니다.
- 전환 전 DB 행 스냅샷과 이미지 백업: `.demo/shared-mentor/`. 계정 정보가 있으므로 Git/공개 첨부 금지입니다.
- `scripts.shared_demo_data.merge_data`는 웹 쓰기를 중지한 상태에서만 사용하는 일회성 병합 도구입니다. 시작할 때 자동 병합하지 않습니다.

| 용도 | 계정 | 준비된 데이터 |
| --- | --- | --- |
| 기존 멘토 | `mentor-demo@example.com` | 기존 데이터·전신 사진·비밀번호 유지 |
| 기존 로컬 | `local-test@example.com` | 기존 데이터·전신 사진·비밀번호 유지 |
| 신규 고객 검증 | `customer-validation@example.com` | 상의·하의·아우터 각 3벌, 전신 사진은 사용자가 등록 |
| 신규 기능 테스트 | `qa-test@example.com` | 각 3벌과 AI 생성 성인 전신 사진 1장 |

새 계정의 무작위 비밀번호는 **`.demo/shared-mentor/test-accounts.json`**에서 확인합니다.
기존 멘토 비밀번호는 `.demo/mentor-demo/credentials.json`에 있습니다. 문서·콘솔·Git에는 기록하지 않습니다.
계정은 일반 사용자이며 서로의 옷장·사진·추천·피드백에 접근할 수 없습니다.
새 계정 두 개의 의류도 각각 독립된 ID와 파일을 사용하므로 한쪽 수정이 다른 계정에 반영되지 않습니다.
고객 여러 명을 각각 구분해 기록하려면 사람마다 별도 계정을 발급해야 합니다.

## 다시 실행하기

이 PC는 **Windows 로그인 후 자동 실행**이 등록되어 있습니다. 상태 확인·중지·재개는
[자동 실행 안내](DEMO_AUTOSTART.md)를 따릅니다. 아래는 자동 실행을 사용하지 않을 때의 수동 실행 방법입니다.
감독기가 실행 중이면 멘토 서버만 종료해도 다시 시작하므로, 수동 종료 전 감독기를 먼저 중지하세요.

Docker Desktop이 실행 중인 상태에서 프로젝트 루트 PowerShell에서 실행합니다.

```powershell
conda activate univ18
$env:FITJANG_SKIP_DOTENV = '1'
docker compose --env-file .env.example -f docker-compose.yml up -d --wait db
.\scripts\start_shared_mentor.ps1
```

이 스크립트는 현재 Tailscale 호스트명을 사용해 같은 Funnel을 연결하고, 공유 데이터·GPU를 사용하는 멘토 서버를 실행합니다.
이미 실행 중이면 중복 실행하지 말고 아래 상태 명령을 사용합니다.
최초 기기에서만 Tailscale 로그인 및 Funnel 활성화가 필요합니다. 현재 PC는 승인됐습니다.
기존 다른 서비스가 8443 포트를 사용하면 덮어쓰지 않고 중단합니다.

로컬 화면도 별도 터미널에서 사용하려면:

```powershell
conda activate univ18
$env:FITJANG_SKIP_DOTENV = '1'
python -m scripts.frontend_preview --tryon
```

로컬 프론트가 꺼져 있어도 멘토 서버는 같은 데이터를 계속 사용합니다. GPU는 독립 서비스로 공유합니다.
학습 중 착샷 중지/재개는 [GPU 학습 안내](GPU_TRAINING.md)를 따릅니다.

```powershell
# 상태
python -m scripts.mentor_demo --status
& "$env:ProgramFiles\Tailscale\tailscale.exe" funnel status

# 멘토 앱 종료: 계정·DB·사진과 Funnel 주소 설정은 유지
python -m scripts.mentor_demo --stop

# 필요할 때 공개 접속만 중지: 기존 443 Serve는 유지
& "$env:ProgramFiles\Tailscale\tailscale.exe" funnel --https=8443 off
```

`python -m scripts.mentor_demo --tryon`만 실행하는 이전 명령은 Cloudflare 임시 주소 모드입니다.
고정 주소 운영에는 위 `start_shared_mentor.ps1`을 사용합니다.
`--no-shared-local`은 이전 독립 멘토 저장소를 쓰는 명시적 복구 옵션입니다. 평소에는 사용하지 않습니다.
DB/볼륨/`.demo` 삭제 또는 테스트 DB 초기화는 공유 시연 데이터를 없앨 수 있으므로 실행하지 않습니다.

## 착샷 시연

1. 기능 테스트 계정 또는 기존 멘토 계정으로 로그인합니다.
2. 필요하면 **프로필 → 전신 사진**에서 업로드·활용 동의를 완료합니다.
3. **코디 추천**에서 예를 들어 `15°C`, `일상`, `캐주얼`을 선택합니다.
4. 코디의 **착샷 보기**를 누릅니다. 하의 → 상의 → 선택 아우터 순서로 로컬 GPU가 처리합니다.
5. 결과를 확인하고 **사진 저장**으로 내려받을 수 있습니다.

의류·추천·피드백과 기존 멘토·QA 계정의 전신 사진·완성 착샷은 재시작 후 유지됩니다.
완성 착샷은 **내 착샷**에서 조회·삭제합니다. 고객 검증 계정과 새 가입 계정의 전신 사진·착샷은
메모리에서만 처리하며 로그아웃 또는 30분 미사용 시 삭제합니다. 고객 사진은 재시작 시에도 사라집니다.
정책과 참여자별 계정 운영은 [사진 보관 안내](PHOTO_RETENTION.md)를 참고하세요.
기존 멘토 로그인 세션은 공통 JWT 키 전환으로 재로그인이 필요할 수 있으며, 비밀번호는 바뀌지 않았습니다.

## 연결과 검증

`외부 HTTPS:8443 → Funnel → 프론트 3001 → JWT 백엔드 8003 → 공유 PostgreSQL/이미지`

프론트는 API 키로 GPU 서비스 8010에도 연결합니다. 백엔드·GPU 포트를 외부에 공개하지 않습니다.
로그인 쿠키는 `Secure`, `HttpOnly`, `SameSite=Lax`이며 공개 폼의 다른 출처 요청은 403으로 차단합니다.
사진·착샷은 계정 인증을 확인하고 `private, no-store`로 응답합니다.
`.env`를 직접 또는 dotenv/Settings/Docker 자동 로딩으로 읽지 않습니다. 실행기와 서버 감사 훅으로 열기를 차단합니다.

- 실제 PostgreSQL 임시 스키마의 회원·프로필·의류·추천·피드백·전신 사진·MVP E2E 및 병합 검증: 148개 통과.
- 병합 충돌 시 기존 행과 이미지를 덮어쓰지 않고 롤백하는 회귀 검증 포함.
- 기존 두 계정·신규 두 계정의 로컬/멘토 API 조회 결과 일치, 다른 계정 의류 404 확인.
- 공개 DNS의 Funnel 릴레이 IP로 연결해 TLS 인증서를 검증하고 새 두 계정 로그인·옷장·쿠키·접근 차단 확인.
- 실제 공개 브라우저에서 로그인 → 추천 → 3벌 GPU 착샷 → 576×864 결과 표시 → 좋아요 저장 확인.
- 로컬 앱 재시작 후 기존·신규 계정 4개 로그인과 동일 옷장 재확인. 공개 피드백이 로컬에도 조회되고 다른 계정은 404.
- 상세 검증 기록과 화면 캡처는 Git에서 제외한 `.demo/shared-mentor/`에 보관합니다.
