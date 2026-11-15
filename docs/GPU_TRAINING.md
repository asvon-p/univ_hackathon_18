# ResNet 학습과 착샷 GPU 전환 (Windows)

## deep_learning_team 연결

인접한 `deep_learning_team` 프로젝트의 두 ResNet50 baseline 진입점은 GPU 전환 실행기에
자동 연결되어 있습니다. 기존 baseline 명령을 그대로 실행하면 `univ18` Python으로 이
프로젝트의 실행기를 시작하고, 실제 학습은 호출한 `deep_team` Python으로 수행합니다.
새 실험 파일은 학습 프로젝트에서 `python train_with_gpu.py --script <학습.py> -- <인자>`로
실행합니다. 경로와 타임아웃은 해당 프로젝트의 `gpu_training.json`에서 관리합니다.

실행기는 자식에게 `FITJANG_GPU_TRAINING_RUN_ID`와 `FITJANG_GPU_TRAINING_ROOT`를 전달합니다.
학습 프로젝트는 상태 파일과 실제 제어 잠금을 확인한 뒤 중복 실행기 없이 학습합니다.
이 연결은 임의 GPU 프로세스를 전역 감시하는 기능이 아닙니다. 상세 사용법은 학습 프로젝트의
`GPU_TRAINING.md`에 있습니다. `.env` 접근 제한과 기존 프로세스 정리/복구 동작은 유지됩니다.

웹·백엔드·PostgreSQL·HTTPS 터널을 실행한 채 GPU 착샷 프로세스만 중지/재시작합니다.
계정, JWT 키, 옷장, 전신 사진, 추천, 피드백, GPU 출력 파일을 삭제하거나 초기화하지 않습니다.
`.env`는 읽거나 수정하지 않습니다. dotenv 차단 감사 훅과 `FITJANG_SKIP_DOTENV=1`을 사용합니다.

## 학습과 자동 전환 (권장)

이 변경 사항이 반영된 **실제 서버 프로젝트 폴더**에서 실행합니다.
작업용 Git worktree와 시연 서버의 프로젝트 폴더가 다르면 코드를 먼저 반영해야 합니다.
GPU 설치 경로는 해당 프로젝트의 `.demo/tryon-runtime/`입니다.

```powershell
cd 'C:\Users\user\Desktop\vscode setting\univ_hackathon_18'
conda activate univ18
$env:FITJANG_SKIP_DOTENV = '1'

# 본인의 학습 환경과 학습 파일 경로로 변경합니다.
python -m scripts.train_with_gpu `
  --python 'C:\path\to\resnet\python.exe' `
  --script 'C:\path\to\resnet\train.py' `
  -- --epochs 20 --batch-size 32

$LASTEXITCODE
```

`--` 뒤의 옵션은 학습 파일에 그대로 전달하므로 **본인 학습 코드가 받는 인자명**을 사용합니다.
학습 작업 폴더는 기본적으로 `train.py`가 있는 폴더입니다. 필요하면 `--workdir 'D:\resnet'`을
`--` 앞에 추가합니다. `--python`을 생략하면 실행기를 실행한 Python 환경을 사용합니다.
현재 `univ18`의 PyTorch는 CPU 빌드이므로 GPU 학습에는 CUDA PyTorch가 설치된 학습 환경을 지정하세요.
기존 착샷 전용 가상환경의 라이브러리를 학습용으로 변경할 필요는 없습니다.

동작 순서는 다음과 같습니다.

1. 새 착샷 생성을 차단하고 진행 중인 코디가 완성될 때까지 기다립니다.
2. 확인된 착샷 GPU 프로세스만 종료하고 GPU 종료를 확인합니다.
3. 지정한 Python으로 학습 파일을 실행합니다. 로그는 터미널과 Git 제외 폴더에 저장됩니다.
4. 정상 종료·예외·`Ctrl+C`·`--stop` 모두 학습과 자식 프로세스를 정리합니다.
5. 학습 프로세스가 모두 종료된 뒤 착샷 모델 로드·상태·API 인증을 확인하고 서비스를 재개합니다.

Windows Job Object로 학습에서 생성된 DataLoader/자식 프로세스까지 관리합니다.
주 학습 프로세스가 먼저 끝나도 남은 자식이 GPU를 붙잡은 채 착샷을 재개하지 않습니다.
별도로 이미 실행한 노트북 커널이나 다른 학습 프로세스는 관리 대상이 아닙니다.
학습 중에는 잠금을 유지하므로 다른 실행기나 수동 `resume`이 GPU를 재시작할 수 없습니다.

중단하려면 실행 중인 터미널에서 **Ctrl+C를 누르고 복구 완료까지 기다리세요.**
다른 터미널에서도 다음 명령을 사용할 수 있습니다.

```powershell
conda activate univ18
$env:FITJANG_SKIP_DOTENV = '1'
python -m scripts.train_with_gpu --status
python -m scripts.train_with_gpu --stop
```

`--stop`은 중단 요청만 전달하고 반환합니다. 원래 터미널의 `Try-on is ready again.` 또는
`--status`의 `phase=finished`와 `tryon_control status`의 `gpu_ready=true`로 복구 완료를 확인합니다.
중단은 학습 코드에 `KeyboardInterrupt`를 전달하며, 기본 15초 후에도 끝나지 않으면 해당 학습
프로세스 트리만 강제 종료합니다. 체크포인트 저장 시간이 길면 `--grace 60`처럼 늘리세요.
체크포인트 저장 자체는 학습 코드의 `except KeyboardInterrupt`/`finally`에서 구현해야 합니다.

| 종료 코드 | 의미 |
|---|---|
| `0` | 학습 정상 종료 및 착샷 복구 완료 |
| 학습 코드의 비정상 종료 값 | 착샷 복구 후 학습 오류 코드를 그대로 반환 |
| `130` | Ctrl+C/중단 요청 후 정리 및 착샷 복구 완료 |
| `2` | 학습 실행 준비/실행기 오류. 상세 상태 확인 |
| `70` | 착샷 복구 실패. 원래 학습 종료 코드는 JSON에 별도 보관 |
| `71` | 학습 프로세스 종료 확인 실패. GPU를 다시 시작하지 않음 |

학습 코드가 2/70/71/130을 직접 반환할 수도 있으므로 정확한 구분은 상태 JSON의
`phase`, `training_exit_code`, 오류 종류를 확인합니다. 실행기 상태는
`.demo/tryon-runtime/training.json`, 실행별 결과·로그는
`.demo/training-runs/<실행ID>/result.json`, `training.log`에 남습니다. CLI 인자는 기록하지 않습니다.
학습 코드가 출력한 내용은 로그에 남으므로 비밀값을 출력하지 마세요.

학습 Python과 multiprocessing 자식은 패키지를 불러오기 전에 `.env` 파일 읽기/쓰기를
차단합니다. dotenv에 의존하는 학습 코드는 실패하며, 착샷은 복구됩니다. 별도로 실행하는
다른 프로그램도 `.env`를 읽지 않도록 구성해야 합니다. 노트북 셀이 아닌 `.py` 진입점이 필요합니다.

터미널 창 강제 종료·작업 관리자에서 실행기 강제 종료·PC 종료에는 자동 복구를 보장할 수 없습니다.
이 경우 학습 자식은 Windows Job Object에 의해 종료되지만 착샷은 중지 상태로 남을 수 있습니다.
학습이 종료된 것을 확인한 뒤 아래 수동 `status`/`resume`으로 복구하세요. 공개 HTTPS 터널이나
웹 실행기를 종료할 필요는 없습니다.

## 수동 전환

```powershell
# 상태만 조회
python -m scripts.tryon_control status

# 학습 전: 새 착샷 차단 → 진행 중 코디 완성 대기 → GPU만 중지
python -m scripts.tryon_control pause
if ($LASTEXITCODE -ne 0) { throw 'GPU 중지를 확인하지 못했습니다. 학습을 시작하지 마세요.' }

# 여기서 별도의 ResNet 가상환경으로 학습 실행
# conda activate resnet
# python train.py

# 학습 프로세스/노트북 커널의 GPU 메모리를 반환한 뒤 착샷 재시작
conda activate univ18
python -m scripts.tryon_control resume
```

PowerShell 실행기를 사용해도 같습니다.

```powershell
.\scripts\gpu_switch.ps1 -Mode training
.\scripts\gpu_switch.ps1 -Mode tryon
.\scripts\gpu_switch.ps1 -Mode status
```

`status`에서 `phase=paused`, `gpu_online=false`를 확인하면 학습을 시작할 수 있습니다.
`resume`은 모델 로드와 API 인증 검증까지 끝난 뒤 `phase=ready`, `gpu_ready=true`를 반환합니다.
GPU 작업/모델 로드를 기다리는 기본 제한은 600초입니다. 필요하면 `--timeout 900`을 사용합니다.
오류 시 종료 코드는 1이며 완료되지 않은 전환을 성공으로 보고하지 않습니다.

자동 복구가 필요한 학습에는 위의 `train_with_gpu`를 사용하세요. 수동 전환은 학습 프로세스를
감시하지 않습니다. 노트북은 셀 실행 종료만으로 모델 메모리가 반환되지 않으므로 학습 커널을
종료한 뒤 수동으로 재개합니다.

## 실행 중 화면

- 준비 중: 새 착샷 요청은 503 `TRYON_PAUSED`, 이미 진행 중인 여러 벌 착샷은 완료까지 허용합니다.
- 학습 중: “모델 학습으로 착샷 생성이 일시 중지되었어요…” 안내와 새 생성 버튼 비활성화.
- 재시작 중: “착샷 서비스를 다시 준비하고 있어요…” 안내. 모델 준비 후 버튼 자동 복구.
- 기존 페이지는 5초 간격으로 상태를 반영합니다. 로그인·옷장·추천·피드백은 계속 사용할 수 있습니다.
- 이미 프론트가 보관한 완성 착샷의 조회/다운로드도 유지합니다. GPU의 완료 작업 목록은 메모리
  저장이므로 GPU 프로세스 재시작 후 초기화되지만 저장된 PNG와 프론트의 결과는 삭제하지 않습니다.
- 조회 전용 `GET /tryon/service-status`는 비밀값/사용자 데이터 없이 상태와 진행 건수만 반환합니다.
  공개 웹에서 GPU 중지/재개를 실행하는 관리 API는 제공하지 않습니다.

## 처음 반영할 때와 장애 처리

이전 버전의 프론트는 실행 중인 Python 메모리에 코드를 보관합니다. 파일만 교체해서는 안내가
추가되지 않습니다. 최초 반영 시 해당 프론트 실행기를 새 코드로 다시 시작해야 합니다.
그 이후 전환에는 웹·백엔드·DB 재시작이 필요 없습니다.

구버전 실행기가 GPU를 자식 프로세스로 감시하는 경우 GPU를 종료하면 전체 서비스가 종료될 수
있으므로, 전환 스크립트가 이를 감지해 **중지를 거부**합니다. 실행 중 프론트가 상태 조회 API를
제공하지 않거나 서로 다른 상태 파일을 읽는 경우도 GPU를 중지하지 않습니다.
이 PC의 로컬·공개 시연 서버는 아래 후속 배포에서 최초 재시작을 완료했습니다. 다른 PC나
아직 반영하지 않은 체크아웃에는 위 최초 반영 절차가 필요합니다. GPU 전환 명령 자체는
다른 서버를 종료하거나 재시작하지 않습니다.

새 실행기는 GPU를 독립 프로세스로 시작합니다. 웹 종료만으로 공유 GPU를 종료하지 않습니다.
학습 중 웹을 재시작해도 `paused` 상태를 유지하고 GPU를 자동 기동하지 않습니다.

- 상태: `.demo/tryon-runtime/maintenance.json` (비밀값 없음, 원자적으로 교체)
- 잠금: `.demo/tryon-runtime/control.lock` (동시 전환 방지, 프로세스 종료 시 OS에서 해제)
- GPU 로그: `.demo/tryon-runtime/worker.log` (Git 제외)
- 기존 GPU 키: `.demo/tryon-runtime/credentials.json` 재사용

대기 시간이 초과되면 GPU를 종료하지 않고 이전 상태로 돌립니다. 중지/시작 실패 시에는 안내를
유지하며 `error` 상태를 반환합니다. 모델 오류를 수정한 후 `resume`하거나 `pause`로 다시 정리합니다.
`pause`/`resume`은 해당 가상환경의 실행 경로·모듈·부모/자식 관계로 확인한 GPU 프로세스만
다루며, 종료 직전에 PID의 생성 시각도 검증합니다. 임의의 `python.exe` 전체를 종료하지 않습니다.

`--project-root`는 다른 체크아웃의 명시적인 GPU 런타임 위치를 지정하는 고급 옵션입니다.
모든 프론트와 GPU가 같은 `TRYON_MAINTENANCE_FILE`을 읽어야 하며, 상대 프로젝트 자동 탐색은 하지 않습니다.

## 2026-11-10 검증

- 관련 회귀 테스트와 실제 PostgreSQL E2E: 26개 통과. 새 임시 스키마만 사용하고 정리했습니다.
- 로컬·멘토 프론트 모두에서 중지/재시작 안내, 503 응답, 기존 로그인 토큰·프로필·옷장 유지 확인.
- 별도 GPU 런타임/포트에서 실제 RTX 5090 모델 준비 → 중지 → 재시작 → 중지 확인.
  중지 전후 전체 VRAM은 약 12.4GiB → 7.5GiB로 감소했습니다. 원래 시연 GPU는 유지한 측정입니다.
- 검증 브라우저에서 새로고침 없이 안내 표시·버튼 비활성화와 재시작 후 활성화 확인. 콘솔 오류 없음.
- 기존 로컬·멘토 관리자/웹/백엔드/HTTPS 터널/착샷 GPU 프로세스는 같은 PID로 유지했습니다.
- 검증용 GPU·웹 프로세스는 종료했습니다. 화면 증빙은 Git 제외 경로
  `.demo/gpu-switch-validation/paused.png`, `resumed.png`에 있습니다.
- 위 항목은 격리 환경에서 진행한 최초 구현 검증입니다. 실제 서버 배포 결과는 아래와 같습니다.

## 2026-11-10 로컬·공개 시연 배포 완료

- 실제 실행 프로젝트 `C:\Users\user\Desktop\vscode setting\univ_hackathon_18`에 코드 반영 및
  로컬(3000/8002)·공개 시연(3001/8003) 실행기 최초 재시작 완료.
- 기존 Cloudflare 터널 프로세스를 유지하고 `--reuse-tunnel`로 인계하여 공개 HTTPS 주소 유지.
- 배포 전 모든 기존 DB 행과 저장 이미지 27개, 인증 키 파일의 해시 일치 확인.
  배포 전에 받은 JWT로 재시작 후에도 프로필 접근 성공. 기존 계정은 그대로 사용합니다.
- 실제 두 화면에서 착샷 생성 중 `pause` 실행: 진행 중 코디 완성 후 GPU만 종료됨.
  중지 중에는 새 요청 503, 화면 안내·새 생성 버튼 비활성화, 이미 완성한 결과 조회 유지.
- 전체 GPU 메모리 사용량 약 7.5GiB → 1.1GiB로 감소. 웹·백엔드·DB·HTTPS는 계속 이용 가능.
- `resume` 후 화면 자동 복구 및 로컬·공개 각각 새 착샷 생성·결과 표시 확인.
  GPU 전환 전후 웹·백엔드·관리자·터널 PID는 동일합니다.
- 배포된 원본 프로젝트의 PostgreSQL 포함 관련 테스트 26개 통과.
- 화면 검증으로 추천 2건과 해당 코디가 추가됐으며, 기존 계정·의류·프로필·추천·피드백은 보존됐습니다.
- 최종 운영 상태는 GPU `ready`입니다. `.env` 접근 없이 작업했습니다.

검증 기록과 배포 전 코드 백업은 작업 트리의 Git 제외 폴더 `.demo/gpu-deployment/`에 있습니다.
`verification.json`, `pause-verification.json`, `final-health.json`, `local-paused.png`,
`mentor-paused.png`를 참고하세요. 해당 폴더의 로그인 검증용 토큰 파일은 공유·커밋하지 않습니다.

## 2026-11-10 학습 통합 실행기 검증

- Windows 프로세스 회귀 테스트와 실제 PostgreSQL HTTP E2E를 포함해 40개 통과.
  한글·공백 경로, 인자 전달, 정상/오류 종료, Ctrl+C 처리, 중단 요청, 중단을 무시하는 프로세스의
  강제 정리, 고아 자식 정리, 무관한 프로세스 보존, 동시 실행 차단을 확인했습니다.
- 학습 부모와 multiprocessing 자식 모두 `.env` 열기가 차단됨을 검증했습니다.
  상태 파일 저장 실패 시에도 GPU 복구를 진행하고, 자식 종료 확인 실패 시 재시작을 차단합니다.
- 실제 운영 프로젝트에 실행기·문서를 반영했습니다. 웹·백엔드·터널의 추가 재시작은 없었습니다.
- 실제 RTX 5090에서 합성 데이터로 ResNet50 224×224, 배치 2, BF16, DataLoader 작업자 2개의
  순전파·역전파·optimizer step을 수행했습니다. Kaggle 데이터셋 본 학습을 실행한 것은 아닙니다.
- 정상 종료(0), 의도한 학습 오류(1), 다른 터미널의 `--stop`(130) 각각 착샷 GPU만 중지되고,
  학습과 작업자 정리 뒤 GPU가 `ready`로 자동 복구됐습니다. 학습 코드의 `finally`도 실행됐습니다.
- 세 경우 모두 학습 중 로컬·공개 웹/백엔드/DB 정상 응답, 기존 계정·옷장 조회 성공,
  새 착샷 요청 503 및 공개 HTTPS의 일시 중지 상태를 확인했습니다.
- 검증 전후 기존 DB 데이터·저장 이미지·인증 키 파일 해시가 일치했습니다.
  웹·백엔드·관리자·Cloudflare PID와 생성 시각, 공개 HTTPS 주소도 동일합니다.
- 최종 상태는 착샷 GPU `ready`입니다. `.env`는 읽거나 수정하지 않았습니다.

검증 기록은 작업 트리의 Git 제외 폴더 `.demo/training-wrapper-validation/` 안의
`results.json`, `verification.json`에 있습니다. 실행별 학습 로그도 Git에 포함하지 않습니다.
