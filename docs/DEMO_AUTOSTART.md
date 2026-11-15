# Windows 로그인 후 멘토·GPU 자동 실행

2026-11-11, 이 PC에 `Fitjang-Mentor-Autostart` 예약 작업을 등록했습니다.
**재부팅 후 현재 Windows 사용자로 로그인하면 약 20초 뒤 기동을 시작합니다.**
Windows 로그인 전 실행이나 자동 로그인은 설정하지 않았습니다.
비밀번호 저장이나 관리자 권한 실행 없이 현재 사용자 권한으로 작동합니다.

고정 주소: <https://sgy-host.taile39b25.ts.net:8443/login>

## 자동 실행 순서

1. 기존 Docker Desktop이 준비됐는지 확인하고 필요하면 공식 CLI로 시작합니다.
2. 기존 `univ_hackathon_18_pgdata` 볼륨이 존재하는지 확인합니다. 없으면 새 DB를 만들지 않고 중단합니다.
3. 동일 프로젝트의 PostgreSQL 컨테이너를 시작하고 상태가 healthy가 될 때까지 기다립니다.
4. 현재 Tailscale 로그인·고정 호스트명·8443 Funnel을 확인합니다. 기존 비공개 443 Serve는 바꾸지 않습니다.
5. 로컬 GPU 모델을 로드하고 멘토 프론트·백엔드를 시작합니다.
6. 30초마다 상태를 확인합니다. 준비 지연·종료 등은 재시도하며, 이미 실행 중인 서버는 재사용합니다.

로그인 후 준비 시간은 Docker와 GPU 모델 로딩 시간에 따라 달라집니다.
이 PC의 중지 상태 기동 검증에서는 서버 시작까지 약 30초, 감독 상태의 최종 ready 확인까지 약 1분이 걸렸습니다.
작업 실행 시간이 3일로 제한되지 않도록 설정했고, 중복 실행을 차단했습니다.
감독 프로세스 자체가 실패하면 Windows 작업 스케줄러가 1분 간격으로 최대 10회 다시 실행합니다.
Docker Desktop은 기존 로그인 시작 항목을 유지하며 Tailscale도 기존 공용 시작프로그램을 사용합니다.
Tailscale의 무인 모드·자동 로그인·Windows 절전 정책은 변경하지 않았습니다.

## 보존되는 내용

- 공유 DB: `fitjang_test.ui_preview_local`.
- 이미지: `.demo/frontend-preview/persistent/storage`.
- 기존 로컬·멘토·고객 검증·기능 테스트 계정과 비밀번호·JWT 키·GPU API 키.
- `.env` 직접 접근과 dotenv 자동 로딩을 차단합니다. Docker Compose는 명시적인 `.env.example`만 지정합니다.
- 자동 실행은 seed·계정 생성·DB 초기화·볼륨 삭제를 수행하지 않습니다.
- 기존 볼륨·키 파일·이미지 저장소가 사라지면 새것으로 대체하지 않고 오류를 기록합니다.
- 원래 DB 컨테이너의 `unless-stopped` 재시작 정책을 적용했습니다. 로그인 감독기는 중지된 컨테이너도 기존 볼륨으로 다시 시작합니다.

착샷 결과 캐시는 프론트 메모리에 있으므로 재부팅 후 다시 생성합니다. 전신 사진·의류는 유지됩니다.
`localhost:3000`의 별도 로컬 프론트는 이번 자동 실행 대상이 아닙니다.
PC에서는 `http://localhost:3001`로 동일 멘토 서버를 이용할 수 있습니다.

## 상태 확인

프로젝트 루트 PowerShell에서:

```powershell
conda activate univ18
$env:FITJANG_SKIP_DOTENV = '1'
Get-ScheduledTask -TaskName 'Fitjang-Mentor-Autostart'
python -m scripts.demo_autostart --status
python -m scripts.mentor_demo --status
python -m scripts.tryon_control status
```

감독 상태의 `ready`가 정상, `starting_mentor`는 기동 중, `retrying`은 준비 지연이나 오류입니다.
`ready_gpu_paused`는 모델 학습 등을 위해 GPU를 의도적으로 중지한 상태입니다.
`LastTaskResult=267009`는 Windows의 ‘작업 실행 중’ 코드이며 오류가 아닙니다.

- 비밀값 없는 상태: `.demo/autostart/status.json`
- 회전 로그: `.demo/autostart/supervisor.log`
- 멘토 기동 로그: `.demo/autostart/mentor-launch.log`
- GPU 로그: `.demo/tryon-runtime/worker.log`
- 설정: `.demo/autostart/config.json` (실행 경로·주소만 저장, 비밀번호 없음)

GPU 학습 때문에 `pause`한 상태는 자동으로 해제하지 않습니다. 다시 사용할 때:

```powershell
python -m scripts.tryon_control resume
```

## 자동 실행 중지·재개

수동으로 서버를 종료하려면 먼저 감독기를 중지해야 합니다. 그렇지 않으면 정상 복구 동작으로 서버가 다시 켜집니다.

```powershell
# 다음 로그인 자동 실행 비활성화 + 현재 감독기만 정상 종료
Disable-ScheduledTask -TaskName 'Fitjang-Mentor-Autostart'
python -m scripts.demo_autostart --stop

# --status가 stopped로 바뀐 뒤, 필요하면 실제 서버도 종료
python -m scripts.demo_autostart --status
python -m scripts.mentor_demo --stop
python -m scripts.tryon_control pause

# 다시 활성화하고 지금 시작
Enable-ScheduledTask -TaskName 'Fitjang-Mentor-Autostart'
Start-ScheduledTask -TaskName 'Fitjang-Mentor-Autostart'
```

작업 등록만 없애려면 감독기를 정상 중지한 뒤:

```powershell
Unregister-ScheduledTask -TaskName 'Fitjang-Mentor-Autostart' -Confirm:$false
```

이 명령들은 DB·이미지·계정을 삭제하지 않습니다. 등록을 복구하려면:

```powershell
.\scripts\install_demo_autostart.ps1 -Python 'C:\Users\user\miniconda3\envs\univ18\python.exe'
Start-ScheduledTask -TaskName 'Fitjang-Mentor-Autostart'
```

프로젝트 폴더나 Python 설치 경로를 이동하면 등록도 갱신해야 합니다.
PC가 꺼져 있거나 절전 중이면 외부 접속·GPU 처리는 불가능합니다.

## 검증 범위

실제 PC 재부팅은 수행하지 않았습니다. 현재 작업을 보존한 상태에서 **동일 예약 작업을 실제 실행**했습니다.
프로젝트 DB 컨테이너·멘토 서버·GPU를 중지한 후 작업이 모두 복구하는 것을 확인했습니다.
공개 DNS의 Funnel 릴레이 경로로 로그인·옷장 조회·3벌 GPU 착샷 생성(576×864)까지 재검증했습니다.
계정 6개·의류 42벌·추천 57건·코디 70건·피드백 7건을 포함한 DB 전체 행,
이미지 64개·키/계정 파일 4개의 전후 해시가 일치했습니다. 기존 멘토·로컬·신규 테스트 계정 로그인도 확인했습니다.
자동 시작·GPU 제어 회귀 테스트 27개와 코드 검사에 통과했습니다.
실제 Windows 로그인 트리거 등록·수동 작업 실행·중복 실행 차단·정상 종료 후 재실행을 검증했습니다.
결과 파일은 Git에서 제외한 `.demo/autostart/`에 남겼습니다.

관련 공식 문서: [Docker Desktop 로그인 시 시작](https://docs.docker.com/desktop/settings-and-maintenance/settings/),
[Windows 작업 주체](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtaskprincipal).
