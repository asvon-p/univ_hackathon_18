# Vision 브랜치 연결과 HEIC 자동 표준화

2026-11-08, `origin/feat/vision-extraction`의 `925e3a3`까지 확인해 현재 작업 브랜치에 반영했습니다.
팀원의 `vision.extract(image_path, garment_id)` 계약을 유지하며, 백엔드 설정을 실제 파이프라인
인스턴스에 전달하도록 연결했습니다. 기존 전신 사진·GPU 착샷·시연 데이터 변경은 보존합니다.

## 사진 처리

- 입력: JPG/JPEG, PNG, HEIC, HEIF. 확장자나 MIME 대신 실제 바이트로 검사합니다.
- 최대 10MB / 원본 최대 6,400만 픽셀. 아이폰 48MP 사진 포함.
- HEIF 컨테이너의 주 사진을 사용하고 회전 방향을 보정합니다.
- 긴 변 최대 2048px, RGB JPEG로 표준화합니다. ICC 프로필이 있으면 sRGB로 변환합니다.
- 투명 영역은 흰색으로 합성하고 EXIF·GPS·XMP 메타데이터는 저장하지 않습니다.
- 의류 원본과 전신 사진은 JPEG, 모델이 만든 누끼는 투명 PNG로 저장합니다.
- 손상·미지원 형식·해상도 초과는 415, 파일 크기 초과는 413입니다.

로그인 후 `POST /api/v1/images/normalize`에 multipart `file`을 보내면 JPEG 바이트를 받습니다.
이 API는 DB나 저장소에 사진을 남기지 않으며, 탈퇴·무효 JWT도 거부합니다.
프론트 `/photos/normalize`가 인증 쿠키를 백엔드 JWT로 전달합니다.
앨범 선택·드래그 → 자동 변환 → 자르기 → 저장 순서가 의류와 전신 사진에 공통 적용됩니다.
HEIC를 지원하지 않는 데스크톱 브라우저도 같은 흐름을 사용합니다.
전신 사진의 최종 저장은 기존 동의 확인 절차를 유지합니다.

## 실제 Vision 실행

`VISION_PROVIDER=local`의 백엔드 기본값은 CLIP 분류 + U2Net 누끼입니다.
`VISION_DEVICE=cpu`를 기본으로 사용해 기존 착샷 GPU와 자원을 분리합니다.
CLIP은 카테고리·세부 종류·패턴·스타일·시각적 두께/소재를 추정합니다.
보온도는 세부 종류 기본값에 시각적 두께를 반영한 1~5 추정값이며 실측 단열 성능이 아닙니다.
의류 영역의 색 분포와 데님 소재 단서로 대표 색상을 추정합니다.
불확실한 패턴·스타일·보온도는 unknown / 빈 목록 / null을 유지합니다.
사용자가 수정한 속성은 재분석에서도 유지합니다. 모델 장애를 정상 분류로 위장하지 않습니다.
분석 실패 시 의류는 등록되고 `analysis_status=failed`로 반환되며 재분석할 수 있습니다.
모델 누끼만 실패한 경우 원본으로 표시될 수 있으므로 `cutout_path`도 확인하세요.

설치 (PowerShell, 저장소 루트):

```powershell
conda activate univ18
$env:FITJANG_SKIP_DOTENV = "1"
python -m pip install -r backend/requirements.txt
python -m pip install -r frontend/requirements.txt
# univ18에 torch가 없는 경우 CPU 환경 설치. 기존 CUDA torch는 교체하지 마세요.
python -m pip install "torch>=2.6,<3" --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r vision/requirements-models.txt
$env:VISION_CLASSIFIER = "clip"
$env:VISION_SEGMENTATION = "rembg"
$env:VISION_DEVICE = "cpu"
python -m vision --warmup
```

기본 캐시는 `.demo/vision-runtime/huggingface`와 `.demo/vision-runtime/rembg`이며 Git에서 제외됩니다.
`HF_HOME`, `U2NET_HOME` 환경변수로 다른 캐시 경로를 지정할 수 있습니다.
이 작업에서는 실제 `.env`를 읽거나 수정하지 않았습니다. `.env.example`만 옵션 설명을 추가했습니다.

기존 서버가 종료된 상태에서만 실행하세요:

```powershell
python -m scripts.frontend_preview --tryon
# 별도 터미널, 공개 멘토 시연이 필요한 경우
python -m scripts.mentor_demo --tryon
```

로컬 실행기도 이미 실행 중인 GPU의 인증 키를 검증해 공유합니다. 공유 GPU는 종료하지 않습니다.
본인이 시작한 GPU만 관리 프로세스 종료 때 함께 종료합니다. 프론트 재시작 시 착샷 메모리 캐시는
초기화되지만 계정·옷장·전신 사진은 PostgreSQL과 저장소에 그대로 남습니다.

모델 없는 빠른 파이프라인 점검은 `VISION_CLASSIFIER=demo`, `VISION_SEGMENTATION=flat`을
명시하세요. demo는 실제 분류가 아니며 기본 상의 값을 사용합니다.

## 테스트

```powershell
$env:FITJANG_SKIP_DOTENV = "1"
$env:TEST_POSTGRES = "1"
$env:TEST_DATABASE_URL = "postgresql+psycopg://fitjang:fitjang@127.0.0.1:5432/fitjang_test"
python -m pytest backend/tests vision/tests
```

실제 모델 검증은 무지 블루 데님 청바지 사진의 절대 경로를 `VISION_SMOKE_IMAGE`에 지정하고 실행합니다:

```powershell
python -m pytest backend/tests/e2e/test_vision_heic.py::test_real_clip_u2net_heic_postgresql
```

이 테스트는 실제 HEIC 인코딩 → API 업로드 → CLIP 속성 추정 → U2Net 투명 누끼 → PostgreSQL 저장을
검증합니다. 색상·패턴·스타일·보온도의 저장, 재분석 시 수동 수정값 보존, 타인 접근 차단도 확인합니다.
일반 테스트는 모델 다운로드 없이 demo/flat을 명시하고 테스트 전용 스키마를 사용합니다.

참고: [pillow-heif 공식 Pillow 연동 문서](https://pillow-heif.readthedocs.io/en/stable/pillow-plugin.html),
[Hugging Face CLIP 문서](https://huggingface.co/docs/transformers/model_doc/clip).

## 이전 Vision·HEIC 통합 검증

- 실제 PostgreSQL 전체 회귀: **336 passed, 2 skipped**. 선택형 GPU/실제 비전 모델 테스트는 일반 실행에서 제외됩니다.
- 제외된 실제 비전 모델 테스트는 별도로 실행해 **1 passed**를 확인했습니다.
- 상의 셔츠·하의 청바지·아우터 재킷 3종의 실제 CLIP 분류와 U2Net 투명 누끼 검증 완료.
- 48,771,072픽셀 입력 → 2048×1536 JPEG 변환 확인.
- 로컬 실제 화면: HEIC 선택 → 자동 변환 → 자르기 → 하의/청바지 자동 분석 → 옷장 저장 완료.
- 공개 HTTPS 실제 화면: 같은 HEIC 선택·변환·자르기·분석 결과와 누끼 표시 확인.
- 기존 HTTPS 주소와 RTX 5090 착샷 프로세스 유지. 백엔드·프론트 프로세스만 갱신했습니다.
- 검증용 의류 2개를 제거한 뒤 두 스키마의 기존 모든 DB 행과 저장 이미지 해시 일치 확인.
- `pip check`, 변경 파일 Ruff 검사 통과. 커밋·push는 수행하지 않았습니다.

0.1 색상 추정에서는 화이트→gray, 데님→navy, 올리브→green 오분류를 확인했습니다.
아래 0.2 개선에서 해당 사례를 보정했습니다. 사용자는 정보 확인 화면에서 수정할 수 있습니다.
원래 시연 의류 9종의 수동 입력 속성은 재분석하거나 바꾸지 않았습니다.

검증 기록은 Git 제외 경로 `.demo/vision-integration/`에 있습니다:
`postgres-results.xml`, `real-model-test.log`, `model-verification.json`,
`browser-verification.json`, `preservation.json`, `public-heic-analysis.jpg`.

## 0.2: 패턴·스타일·보온도와 색상 개선

`vision/attributes.py`의 고정 영어 라벨을 기존 CLIP에 함께 전달합니다. 이미지·텍스트 유사도에서
각 항목별 후보를 비교하며, 상대 점수와 후보 간 차이가 작으면 선택을 보류합니다. 이 점수는
검증된 확률이나 정확도 백분율이 아닙니다. 별도 API 키, GPT 호출, 신규 라이브러리는 필요 없습니다.
카테고리는 원본을 보고, 무늬·스타일 등은 누끼를 흰 배경에 합성한 의류 영역을 봅니다.

| 항목 | 반환값 / 처리 |
| --- | --- |
| 패턴 | solid, stripe, check, dot, floral, graphic, other, unknown |
| 스타일 | 기존 9개 태그 중 점수 기준을 통과한 최대 3개, 불확실하면 빈 목록 |
| 보온도 | 세부 종류별 기본값 + 충분히 구분되는 얇음/두꺼움 단서로 ±1, 1~5 제한 |
| 색상 | 불투명 의류 픽셀만 사용, 누끼 경계 제외, 색조별 분포 투표, 그림자 영향 완화 |
| 데님 / 멀티 | 데님 소재가 뚜렷한 파란 의류는 denim, 서로 다른 색이 충분한 면적을 차지하면 multi |

보온도는 사진에서 보이지 않는 안감·소재 구성·충전량을 알 수 없으므로 참고값입니다.
등록 화면에 이 한계를 안내하며 사용자가 직접 수정할 수 있습니다. 분석 실패 시 전체 실패 처리는
유지하고, demo 모드는 패턴 unknown / 스타일 [] / 보온도 null을 그대로 반환합니다.
누끼 실패 시 색상은 중앙 영역을 사용하므로 배경 영향을 받을 수 있습니다.

DB/API 필드는 기존 계약 그대로이며 새 마이그레이션은 필요하지 않습니다. 새 업로드 또는 명시적
`POST /api/v1/garments/{id}/reanalyze`에 적용되고 기존 의류는 일괄 재분석하지 않습니다.
`user_edited_fields`에 기록된 수동 값(빈 목록과 null 포함)은 재분석에서 덮어쓰지 않습니다.

검증 결과:

- 실제 PostgreSQL 전체 회귀 **379 passed, 2 skipped**. 선택형 실제 Vision 테스트는 별도로 실행해 통과했습니다.
- 새 속성·색상·DB 회귀와 실제 CLIP/U2Net 통합을 묶은 별도 실행은 **51 passed**입니다.
- 기존 시연용 생성 이미지 9장: 색상 라벨 일치 **3/9 → 9/9**. 동일한 누끼에 구·신 색상 함수를 비교했습니다.
- 같은 9장의 패턴: 무지 8개, 불확실한 데님 재킷 1개는 unknown. 스타일과 보온도 반환도 확인했습니다.
- 제어된 절차형 테스트 이미지 6장: 무지·줄무늬·체크·도트·꽃무늬·그래픽 구분 확인.
- 이 샘플들은 개발 검증용이며 독립 정확도 평가가 아닙니다. 실사진 전반의 정확도를 보장하지 않습니다.
- 세부 종류 오분류(니트→가디건, 치노→조거)가 남아 보온도 추정에도 영향을 줍니다. 확인 화면에서 수정하세요.
- 로컬 실제 화면: 셔츠 선택 → 자르기 → 화이트/무지/포멀/얇음 자동 입력 → 보통으로 수정 → 저장 확인.
- 공개 HTTPS 실제 화면: HEIC 변환 → 자르기 → 데님/무지/아메카지/보통 자동 입력 → 저장 확인.
- 두 검증 의류만 삭제한 뒤 기존 두 스키마의 모든 계정·프로필·옷장·추천·피드백 행 및 이미지 해시 일치.
- 기존 공개 HTTPS 터널과 로컬 GPU 프로세스 유지. 실제 `.env`는 직접·간접 접근하지 않았습니다.

이번 증빙은 Git 제외 경로 `.demo/vision-attributes/`의 `benchmark.json`,
`pattern-fixtures/results.json`, `real-tests.log`, `postgres-results.xml`,
`browser-verification.json`, `data-before.json`, `data-after.json`, `ui-upload-records.json`에 있습니다.

모델의 세부 분류 한계는 [CLIP 공식 모델 카드](https://huggingface.co/openai/clip-vit-base-patch32)도 참고하세요.
