# Windows 자동 분석·누끼 복구

## 원인과 변경

Windows 응용 프로그램 제어가 `numba._helperlib` DLL 로딩을 차단했습니다.
기존 `rembg`의 import가 사용하지 않는 고급 matting 라이브러리까지 불러오면서 문제가 전파됐습니다.
CLIP, PyTorch, ONNX Runtime은 별도로 정상 로딩되는 것을 확인했습니다.

- 실제 CLIP 분류·색상·패턴·스타일·보온도 추정은 유지합니다.
- 기존 `VISION_SEGMENTATION=rembg` 설정은 동일한 U²-Net 가중치를 ONNX Runtime으로 직접 실행합니다.
- 입력 320×320 정규화 → 실제 모델 추론 → 원본 크기 마스크 복원 → RGBA PNG 저장 순서입니다.
- 부드러운 경계의 원본 RGB를 유지해 알파가 두 번 적용되어 어두워지는 것을 방지합니다.
- 기존 착샷의 경계 정제·아우터 안쪽 영역 처리는 그대로 사용합니다.
- CPU 추론 스레드를 최대 4개로 제한합니다. GPU 착샷 서버는 별도로 유지하며 재시작하지 않습니다.
- 필수 누끼가 실패했는데 분석 성공으로 표시하던 동작을 막았습니다. 실패 기록은 재분석할 수 있고
  기존 원본·누끼·사용자 수정 값은 덮어쓰지 않습니다.

Windows 보안 정책·예외·레지스트리·DLL 파일은 변경하지 않습니다. 차단된 라이브러리를 실행하지 않습니다.
DB는 이전에 검증한 `pg8000` 연결을 유지하며 이번 작업에는 DB 마이그레이션이 없습니다.
`.env`는 읽거나 변경하지 않습니다.

## 모델과 개인정보

- 기존 모델 SHA-256: `8d10d2f3bb75ae3b6d527c77944fc5e7dcd94b29809d47a739a7a728a912b491`.
- 신규 설치에서만 고정된 공식 GitHub 릴리스 주소로 모델을 내려받습니다.
  다운로드 크기 제한과 해시 검증 뒤 원자적으로 저장합니다. 손상된 기존 파일은 보존하고 오류로 알립니다.
- 사용자 사진을 외부 모델 API로 전송하지 않습니다. 기존 합성 의류 이미지만 검증에 사용합니다.
- 실제 배포 검증은 별도 임시 계정으로 진행하고 정리합니다. 기존 계정은 재분석하거나 변경하지 않습니다.

## 검증 명령

```powershell
$env:FITJANG_SKIP_DOTENV = '1'
$env:TEST_POSTGRES = '1'
$env:TEST_DATABASE_URL = 'postgresql+psycopg://fitjang:fitjang@127.0.0.1:5432/fitjang_test'
$env:VISION_SMOKE_IMAGE = (Join-Path (Get-Location) 'backend/assets/starter_wardrobe/bottom-blue-jeans.jpg')
python -m pytest vision/tests backend/tests/e2e/test_vision_heic.py backend/tests/e2e/test_vision_restoration.py backend/tests/api/test_vision_attributes.py backend/tests/adapters/test_vision.py backend/tests/api/test_reanalysis.py backend/tests/api/test_garments.py backend/tests/api/test_wearing_roles.py -v
```

테스트는 새로운 PostgreSQL 임시 스키마를 사용하며 기존 시연 스키마를 초기화하지 않습니다.
핵심 검증은 실제 모델의 JPEG·HEIC 분석, 세 종류 의류 등록, 투명 PNG, DB 속성 저장,
재분석의 수동 값 보존, 타 사용자 접근 차단, 누끼 실패 회귀, 모델 체크섬·다운로드 실패 정리입니다.

## 적용 및 검증 결과 (2026-11-14)

- 위 테스트 묶음 **117개 통과**. 실제 PostgreSQL 임시 스키마와 CLIP/U²-Net을 사용했습니다.
- 로컬 API(8002)·멘토 API(8003)를 기존 실행기로 재시작한 뒤 모두
  `status=ok`, `db=ok`, `vision_error=null`을 확인했습니다.
- 공개 HTTPS 화면에서 합성 청바지를 앨범 선택 → 자르기 → 자동 분석 → 옷장 저장했습니다.
  하의·청바지·데님·무지·아메카지·두께 보통이 자동 입력되고 투명 배경 이미지가 표시됐습니다.
- 로컬 화면에서는 합성 흰 셔츠의 착용 역할을 아우터로 지정했습니다. 분석 후에도 역할은 아우터,
  형태는 셔츠로 유지됐고 화이트·무지·포멀·얇음이 추출됐습니다.
- 두 신규 이미지 모두 `vision-baseline-0.3/clip/rembg` 결과와 실제 RGBA 알파 0~255,
  미인증 이미지 요청 401을 확인했습니다.
- 화면 검증용으로 이번에 만든 임시 계정만 삭제했습니다. 기존 6개 계정, 의류 39벌 및
  11개 테이블의 기존 행 해시, 이미지 파일 112개의 크기·수정 시각이 작업 전후 동일합니다.
- 기존 사용자 사진·의류에는 일괄 재분석을 수행하지 않았습니다. 과거 실패한 항목은 사용자가
  재분석 API로 재시도할 수 있습니다.
