# 아우터 안쪽 상의 보존 — 2026-11-11

## 원인과 비교 실험

순서는 원래부터 하의 → 상의 → 아우터였습니다. 마지막 아우터도 FASHN의 `tops`로
전달되어 상체를 다시 생성하기 때문에 앞 단계의 니트가 유지된다는 보장이 없었습니다.
이번 경로는 3D 오브젝트를 사용하지 않고 의류 사진을 입력으로 사용합니다.

기존 올리브 봄버 제품 사진은 **지퍼가 열려 있으나 중앙에 어두운 안감이 보이는 형태**였습니다.
이전 화면만 보고 닫힌 재킷으로 추정한 설명을 이번 직접 비교로 정정합니다.
앞섶 사이에 흰 배경이 넓게 보이는 합성 제품 사진을 추가해 동일 합성 인물·상의와 비교했습니다.

| 조건 | 관찰 |
|---|---|
| 합성 인물 + 아이보리 케이블 니트 | 니트 색·케이블 무늬 확인 |
| 기존 안감 있는 봄버, 기존 처리 | 중앙도 올리브색으로 바뀌어 니트가 사라짐 |
| 새 열린 봄버, 기존 처리 | 개방부는 생겼으나 니트가 흰 이너로 바뀜 |
| 새 열린 봄버, 상의 보존 처리 | 개방부에 이전 니트의 색·케이블 무늬 유지 |
| 두 번째 합성 인물 + 남색 후드 + 열린 봄버 | 이전 후드의 남색·끈·주머니 일부를 개방부에 유지 |
| 기존 안감 있는 봄버, 상의 보존 처리 | 신뢰할 개방부가 없어 아우터 적용을 건너뛰고 이전 상의 결과 유지 |

사용자 첨부 사진은 열거나 복사하거나 GPU 테스트에 사용하지 않았습니다.
실험 인물은 기존 생성 자산 `person-01.png`, `person-02.png`뿐입니다.
새 제품 사진은 built-in image generation 도구로 인물 참조 없이 생성했습니다.
원래 옷장 45벌의 제품 사진은 교체하지 않았습니다.

## 구현

1. 프론트가 하의 → 상의를 실제로 완료한 뒤 아우터 단계에만 `preserve_inner=true`를 전달합니다.
   상의 사진이 없거나 적용되지 않은 경우에는 이 모드를 요청하지 않습니다.
2. FASHN이 아우터 후보를 생성합니다. 기존 human parser로 이전 상의·생성 결과·제품의 의류 영역을
   구분합니다. 얼굴 인식·신원 임베딩·학습 데이터 저장은 하지 않습니다.
3. 제품의 주된 원단 색 계열을 Lab 공간에서 구하고, 생성된 재킷 중앙의 원단과 다른 연결 영역을
   개방부 후보로 찾습니다. 흰 배경·지퍼 하이라이트를 원단 색으로 혼동하지 않도록 합니다.
4. 후보를 이전 상의 영역과 교차하고, 중심·면적·높이·너비 조건을 확인합니다. 흰 이너 밑단처럼
   옆으로 퍼지는 부분은 중앙 몸통의 대표 폭으로 제한합니다. 작은 내부 구멍은 채웁니다.
5. 지퍼 경계를 남기고 개방부 안쪽에 직전 상의 결과를 복원합니다. 경계는 부드럽게 연결하고,
   의류 편집 범위 밖과 분할된 얼굴·머리·손 영역은 이전 단계 픽셀을 유지합니다.
6. 확실한 개방부를 찾지 못하면 아우터 단계 이전 이미지를 그대로 반환합니다.
   `layering_status=skipped`와 설명을 완료 팝업·내 착샷에 표시합니다. 아우터를 입혔다고 표시하지 않습니다.

`tryon_server/layering.py`에는 디스크 I/O가 없습니다. 분할 마스크와 중간 이미지는 RAM에서만
사용합니다. 기존 고객 계정은 결과까지 세션 메모리 전용이며 로그아웃·30분 미사용 정책을 유지합니다.
영구 보관 계정의 완성 결과는 기존 DB+파일 경로로 저장합니다. 최종 JPEG 저장으로 미세한 압축 차이는
생길 수 있으므로 화면상의 무손실·픽셀 단위 동일성을 보장하지 않습니다.

## 계약과 기존 결과

- GPU `POST /tryon`: `preserve_inner`는 `tops`에서만 허용합니다. 다른 유형이면 422입니다.
  상태 응답의 `layering`은 적용 여부·실패 이유·보존 픽셀 수를 제공합니다. 이미지나 신원 정보는 넣지 않습니다.
- GPU의 `remaining_categories`에 `outerwear`를 넣으면 보존 처리 비용을 포함한 미래 단계로 예측합니다.
  실제 추론 category는 여전히 `tops`입니다. 보존 모드의 시간 표본은 일반 `tops`와 따로 측정합니다.
  기존 4개 설정 키 시간 기록도 읽어 일반 모드 기록으로 유지합니다.
- `PUT /api/v1/tryon-results/{id}/image`: multipart `render_version=inner-preserve-v1`,
  `layering_status=not_requested|applied|skipped`. 기존 클라이언트 기본값은 `legacy`/`not_requested`입니다.
  `applied`/`skipped`는 요청 결과에 상의와 아우터가 모두 있을 때만 저장 가능합니다.
- 조회 응답은 버전·보존 상태·고정 안내 문구 `warnings`를 제공합니다. 타 사용자 결과는 계속 404입니다.
- **마이그레이션 0015**는 위 두 메타데이터 컬럼만 추가합니다. 기존 3개 저장 결과는 `legacy`로 남고
  이미지·ID·과거 기록은 유지됩니다. 이전 결과는 내 착샷에서 조회할 수 있지만 새 코디 생성에는 재사용하지 않습니다.
- 세션 전용 고객 결과는 새 메타데이터도 RAM에만 보관합니다. DB에 이미지나 착샷 행을 추가하지 않습니다.

## 검증

- 실제 PostgreSQL 전체 회귀: **509 passed, 2 skipped**. 기존 선택 테스트 2개는 외부 checkout/실사 입력 필요.
- 별도 GPU venv의 이미지 마스크 회귀: **7 passed**. 상의 무늬 보존·얼굴/외곽 보호·닫힌 입력·해상도 불일치·
  상의/제품 분할 없음·전체 패널 오검출·넓게 퍼진 이너 밑단을 검사했습니다.
- 새 저장 메타데이터, 고객 RAM 정책, 타 사용자 저장 거부, 이전 결과 보존 마이그레이션, 캐시 버전 구분,
  상의 누락 시 플래그 억제, 불확실한 아우터 안내, 모드별 ETA 회귀를 포함합니다.
- 실제 GPU에서 두 합성 인물의 열린 아우터 보존 적용을 확인했습니다. 보존 처리 포함 아우터 단계는
  해당 실험에서 약 **8.2~8.8초**였습니다. 단일 사례 측정이며 처리시간 보장이 아닙니다.
  같은 `auto` 사진 유형·seed 42·30 step 조건에서 중앙 상의 ROI(576×864 출력의
  x=280..301, y=200..319, 2,640픽셀)를 비교했습니다. 이전 상의와의 RGB 평균 절대 차이는
  아이보리 **13.356 → 0**, 남색 **208.050 → 0**이었습니다. 보존 내부 픽셀의 확인이며
  재킷 경계·그림자·전체 이미지의 품질 점수를 뜻하지 않습니다.
- 로컬 3000과 멘토 3001에 걸쳐 임시 사용자 3명이 각각 하의·상의·아우터를 동시 생성했습니다.
  GPU 순서는 **A,C,B,A,C,B,A,C,B**, 완료는 약 **57.6/66.3/74.6초**였습니다.
  모두 `applied`, 결과 576×864, 초기 예측 완료 시각 오차는 **+2.5~2.9초**였습니다.
- 사용자 중복 접수와 다른 백엔드에서 같은 사용자 접수는 429, 타 사용자 조회·취소·이미지는 404였습니다.
  공개 DNS를 통해 Funnel로 연결한 HTTPS 결과는 200, 인증 이미지 캐시는 `private, no-store`였습니다.
- 실제 브라우저에서 합성 계정으로 로컬 생성 → 완료 이미지 → 멘토 HTTPS에서 같은 저장 결과 조회를 확인했습니다.
  프론트·백엔드를 재시작한 뒤에도 새 메타데이터와 이미지가 유지됐고, 불확실한 안감 아우터의
  생략 안내가 내 착샷에 표시됐습니다. 완료 뒤 남아 있던 취소 버튼도 숨기도록 수정했습니다.
  테스트 산출물은 Git 제외 경로 `.demo/tryon-layering/`에만 보관합니다.
- 최종 상태: 로컬/멘토 API 정상, GPU ready, 진행 중 접수 0, 테스트 계정 잔여 0.
  기존 **계정 7개·의류 45벌·저장 착샷 3개·이미지 파일 117개·자격 파일 4개**를 보존했습니다.
  원래 DB 업무 필드와 파일의 해시를 대조했습니다. 새 메타데이터 컬럼은 비교에서 분리했고,
  정상 화면 조회로 추가된 추천 기록도 유지했습니다. `.env`를 읽거나 수정하지 않았습니다.

재검증 명령(기존 `.env`를 읽지 않음):

```powershell
$env:FITJANG_SKIP_DOTENV = "1"
$env:TEST_POSTGRES = "1"
$env:TEST_DATABASE_URL = "postgresql+psycopg://fitjang:fitjang@127.0.0.1:5432/fitjang_test"
python -m pytest
# OpenCV는 기존 GPU venv의 모델 의존성입니다. 테스트 러너 pytest가 필요합니다.
& .demo/tryon-runtime/venv/Scripts/python.exe -m pytest tryon_server/tests --confcutdir=tryon_server/tests
```

## 범위와 한계

앞이 넓게 열리고 몸통이 정면으로 보이는, 주된 원단 색이 비교적 일관된 제품 사진에 적합합니다.
닫힌 코트·안감이 개방부를 채운 제품 사진·복잡한 무늬·원단과 이너가 비슷한 색·가림이 큰 자세는
개방부 검출이 실패하거나 부정확할 수 있습니다. 실패 시 이전 상의를 보존하고 안내합니다.
지퍼·목둘레·밑단·두꺼운 후드와 아우터 사이의 접촉·그림자에는 여전히 생성/합성 흔적이 남을 수 있습니다.
의류 물리 시뮬레이션이나 여러 의상을 동시에 조건으로 받는 모델로 교체한 것은 아닙니다.
이번 결과를 실제 착용감·정확한 핏 또는 모든 제품에 대한 품질 보장으로 해석해서는 안 됩니다.

## 생성 자산 기록

2026-11-11 후속 검증에서 시연 계정의 아우터를 열린 이미지로 교체했습니다.
밝은 코트의 배경/피부 오인 보정과 의류 수정 후 착샷 캐시 무효화를 추가했습니다.
누적 검증은 PostgreSQL **518 passed, 2 skipped**, 이미지 처리 **9 passed**입니다.
세 아우터의 실제 화면과 보존 내역은 [OPEN_OUTERWEAR_DEMO.md](OPEN_OUTERWEAR_DEMO.md)를 참고하세요.

생성 모드: built-in image generation, 신규 이미지, 참조 사진 없음, 불투명 흰 배경.
결과: `.demo/tryon-layering/open-bomber.png` (계정의 기존 옷장에는 추가/교체하지 않음).

사용 프롬프트:

> Use case: product-mockup. Asset type: synthetic virtual try-on test garment product photograph.
> Generate a single isolated olive green lightweight bomber jacket in a perfectly front-facing
> symmetrical view, sleeves naturally angled down slightly, both zipper sides completely unzipped
> and spread widely apart, clearly open front from neck to hem. The opening is a large continuous
> empty white gap, about one third of torso width, showing the white studio background all the way
> through; no inner shirt, no mannequin, no person, no hanger, no back lining filling this gap.
> Realistic olive nylon fabric, ribbed olive collar/cuffs/waist, silver zipper halves separated left
> and right, realistic seams and pockets. Pure white background, soft even catalog studio lighting,
> whole garment visible with margins, no labels, logo, words, watermark. This is new synthetic test
> data; do not use or reference any person from the conversation.
