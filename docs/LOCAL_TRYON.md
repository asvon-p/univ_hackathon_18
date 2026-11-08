# 로컬 GPU 착샷 실행

`feat/tryon-server`의 FASHN VTON 1.5 코드를 `tryon_server/`에 통합했습니다.
전신 사진 → 추천 카드의 **착샷 보기** → 하의·상의·아우터 순서로 GPU 추론 → 결과 표시 흐름입니다.

## 이 PC에서 실행

프로젝트 루트의 PowerShell:

```powershell
conda activate univ18
$env:FITJANG_SKIP_DOTENV = "1"
python -m scripts.frontend_preview --tryon
```

- 화면: http://127.0.0.1:3000
- 백엔드 문서: http://127.0.0.1:8002/docs
- GPU 상태: http://127.0.0.1:8010/health
- 기존 계정·DB(`fitjang_test.ui_preview_local`)·이미지·JWT 서명 키를 유지합니다. seed/reset을 실행하지 않습니다.
- 첫 실행 후에는 `python -m scripts.frontend_preview`도 GPU 연결을 기억합니다.
- GPU 없이 실행하려면 `python -m scripts.frontend_preview --no-tryon`을 사용합니다.
- 이미 서버가 실행 중이면 다른 터미널에서 `python -m scripts.frontend_preview --stop`으로 종료한 뒤 다시 시작합니다.
- 실행 중인 터미널의 Ctrl+C로도 종료할 수 있습니다. 종료 시 데이터는 삭제하지 않습니다.
- 멘토 공개 시연 서버(3001/8003)와 별개입니다. GPU는 `127.0.0.1`에만 바인딩됩니다.
- 멘토 HTTPS 서버도 같은 GPU를 공유할 수 있습니다. 현재 공개 연결은 [멘토 착샷 시연 안내](MENTOR_TRYON.md)를 참고하세요.

프로필 → **전신 사진**에서 사진을 선택하고 자른 뒤 활용 동의에 체크하고 저장합니다.
옷장에 사진이 있는 상의·하의를 등록하고 추천을 받으면 **착샷 보기** 버튼이 표시됩니다.
아우터가 추천되면 마지막 단계로 입힙니다. 아우터는 GPU 서버의 `tops` 카테고리에 매핑됩니다.

## 파일 및 비밀값

- `.env`는 읽거나 수정하지 않습니다. 실행기와 자식 서버는 `.env` 열기를 차단하는 감사 훅을 사용합니다.
- GPU 환경: `.demo/tryon-runtime/venv` (기존 `univ18`과 분리)
- 모델 가중치: `.demo/tryon-runtime/weights`, `huggingface`
- GPU 인증 키: `.demo/tryon-runtime/credentials.json` (자동 생성·재사용, 브라우저에는 전달하지 않음)
- 서버 로그: `.demo/frontend-preview/persistent/tryon.log`
- 생성한 테스트 자료: `.demo/tryon-assets/person-01.png` ~ `person-03.png`, `garment-top.png`, `garment-bottom.png`, `garment-outerwear.png`
- 이미지 생성 프롬프트: `.demo/tryon-assets/prompts.json`
- 위 `.demo` 파일은 Git에서 제외됩니다. 실제 사람 사진 대신 생성한 가상 성인 이미지입니다.

## 다른 PC에 GPU 환경 준비

Python 3.12와 NVIDIA CUDA 지원 GPU가 필요합니다. 이 PC의 검증 조합은 RTX 5090, PyTorch 2.10.0+cu128,
torchvision 0.25.0+cu128, ONNX Runtime GPU 1.23.2입니다.
공식 FASHN 소스 기준 커밋: `7c0f10af3f91ad4048fe9729c470a13ef905d25a`.

```powershell
$env:FITJANG_SKIP_DOTENV = "1"
python -m venv .demo/tryon-runtime/venv
git clone https://github.com/fashn-AI/fashn-vton-1.5.git .demo/tryon-runtime/fashn-vton-1.5
git -C .demo/tryon-runtime/fashn-vton-1.5 checkout 7c0f10af3f91ad4048fe9729c470a13ef905d25a
& .demo/tryon-runtime/venv/Scripts/python.exe -m pip install torch==2.10.0 torchvision==0.25.0 --index-url https://download.pytorch.org/whl/cu128
& .demo/tryon-runtime/venv/Scripts/python.exe -m pip install -e .demo/tryon-runtime/fashn-vton-1.5 -r tryon_server/requirements.txt
$env:HF_HOME = Join-Path (Get-Location).Path '.demo/tryon-runtime/huggingface'
$env:HF_HUB_DISABLE_IMPLICIT_TOKEN = "1"
& .demo/tryon-runtime/venv/Scripts/python.exe .demo/tryon-runtime/fashn-vton-1.5/scripts/download_weights.py --weights-dir .demo/tryon-runtime/weights
python -m scripts.frontend_preview --tryon
```

공식 자료: [FASHN VTON](https://github.com/fashn-AI/fashn-vton-1.5),
[PyTorch 설치](https://pytorch.org/get-started/locally/).

## 결과 보관 범위

2026-11-08 후속 작업으로 `local-test@example.com`의 옷장을 실사 스타일 상의·하의·아우터
각 3벌로 교체했습니다. 공개 멘토 계정에도 같은 9종을 등록했습니다.
구성·백업·GPU 화면 검증은 [시연 옷장 교체 기록](DEMO_WARDROBE.md)을 참고하세요.

전신 사진과 옷장 데이터는 재시작 후에도 유지됩니다. 현재 팀원 프론트의 착샷 작업 목록은 메모리 저장이므로
프론트 재시작 후에는 다시 생성해야 합니다. 생성한 GPU 중간 결과는 로컬 outputs에 저장됩니다.
착샷 화면의 다운로드 버튼으로 최종 PNG를 별도 저장할 수 있습니다.
기존 사진을 삭제·교체하거나 탈퇴하면 이전 세션의 착샷 조회를 차단합니다.

## 2026-11-08 검증 기록

- 실제 RTX 5090: 전신 사진 1장에 청바지 → 니트 → 재킷 합성 완료, 첫 실행 전체 약 62.6초.
- 결과: `.demo/tryon-assets/tryon-result.png` (576×864 PNG).
- 실제 프론트 HTTP 업로드·동의·추천·작업 폴링·결과 응답, 재로그인 후 조회, 익명 조회 차단 확인.
- 추천 HTML의 착샷 버튼 상태와 최종 이미지 경로 연결 확인.
- 기존 사용자 2명, 기존 의류 3벌과 이미지 3개, 추천 2건·코디 4건·피드백 2건 보존 확인.
- PostgreSQL 전체 312개 통과 후, 추가한 재로그인 소유자 검증 등을 포함한 관련 9개 재검증 통과.
- 실제 브라우저에서도 **전신 사진 선택·자르기·동의·업로드 → 추천 → GPU 착샷 생성 → 결과 표시**를 확인했습니다.
  아래 후속 화면 검증 기록을 참고하세요.
- 결과와 보존 검사 기록: `.demo/tryon-assets/verification.json`, `preservation.json`.

### 실제 화면 후속 검증 완료

브라우저 탭 생성과 파일 선택기를 동일한 in-app browser 세션의 Playwright API로 제어하여
이전의 탭 세션 불일치 없이 업로드했습니다. Windows Chrome URL 확인 제한을 우회하지 않았습니다.

기존 테스트 계정과 전신 사진을 변경하지 않도록 별도 로컬 검증 계정을 사용했습니다.
생성한 `person-02.png`와 니트·청바지·재킷 이미지로 다음 순서를 실제 화면에서 수행했습니다.

1. 로그인 → 전신 사진 화면 → **앨범에서 고르기**로 파일 선택.
2. 세로 2:3 자르기 → 활용 동의 체크 → 저장.
3. **전신 사진을 저장했어요** 알림과 프로필 사진 표시 확인.
4. 10°C·일상 조건으로 추천 요청 → 아우터 포함 코디 2의 **착샷 보기** 클릭.
5. 하의·상의·아우터 순서로 실제 RTX 5090 생성 진행 표시 확인.
6. 추천 카드의 착샷과 확대 창 확인. 최종 이미지 `complete=true`, 576×864로 로딩됐으며
   브라우저 오류 로그는 없었습니다. 세 GPU 작업의 추론 시간은 각각 약 8.5초였습니다.

기존 사용자 2명, 의류 6벌, 추천 7건·코디 14건·피드백 2건 등 **검증 시작 전 모든 DB 행이 동일**하며,
기존 이미지 7개의 파일 해시도 일치합니다. 새 검증 계정과 그 계정의 데이터만 추가했습니다.
`.env`는 열거나 수정하지 않았으며 서버 재시작·DB 초기화는 수행하지 않았습니다.

검증 산출물은 Git에서 제외되는 `.demo/tryon-ui/`에 보관합니다.

- `01-photo-selected.jpg`: 파일 선택 후 사진 자르기 화면
- `02-photo-uploaded.jpg`: 업로드 완료·프로필 사진
- `03-generating.jpg`: GPU 생성 진행 창
- `04-result-visible.jpg`: 최종 착샷 표시
- `browser-verification.json`: UI 단계·실제 이미지 로딩·브라우저 오류 기록
- `preservation.json`: 기존 DB 행·이미지 보존 검증
- `credentials.json`: 별도 로컬 검증 계정 (Git 포함 금지)
