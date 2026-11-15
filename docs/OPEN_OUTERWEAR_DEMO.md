# 시연 계정의 열린 아우터 이미지 교체

2026-11-11. 로컬 테스트 계정과 멘토 시연 계정의 아우터 각각 3벌, 총 6벌을 교체했습니다.
의류를 삭제하거나 새 ID로 등록하지 않고 이미지 참조와 썸네일만 갱신했습니다.

## 반영 범위

두 서버는 같은 PostgreSQL `fitjang_test.ui_preview_local`과
`.demo/frontend-preview/persistent/storage`를 사용합니다. 따라서 재시작 후에도 유지됩니다.

| 계정 | 아우터 | 보존한 의류 ID |
| --- | --- | --- |
| local-test@example.com | 올리브 봄버 재킷 | `08a5b307-01dd-4f85-8c77-367befb131a6` |
| local-test@example.com | 인디고 데님 트러커 재킷 | `a5bc31f4-4290-47db-a008-0648e0875860` |
| local-test@example.com | 베이지 싱글 트렌치코트 | `2d6e17c9-7afc-406b-9a0f-14c7de2a803b` |
| mentor-demo@example.com | 올리브 봄버 재킷 | `49a077dc-ef4c-40ef-aacf-d405468a373a` |
| mentor-demo@example.com | 인디고 데님 트러커 재킷 | `1306cc07-4c84-4218-be81-af304e2ecfea` |
| mentor-demo@example.com | 베이지 싱글 트렌치코트 | `7c8dd440-be55-432f-ada4-5320f5df45e4` |

- 이름·색상·카테고리·스타일·보온도·수정한 속성·분석 기록·소유자·기존 추천 참조는 그대로입니다.
- 변경 필드는 대상 의류의 `original_path`, `cutout_path`, `updated_at`입니다.
  이전 누끼 이미지가 다시 선택되지 않도록 `cutout_path`는 NULL로 전환했습니다.
  이 경우 이미지 API는 새 원본으로 대체합니다. 옛 누끼 파일도 삭제하지 않았습니다.
- 새 JPEG 원본 6개와 384px WebP 썸네일 6개를 별도 파일로 저장했습니다.
  이전 원본을 덮어쓰거나 삭제하지 않았습니다.
- 다른 계정의 옷장 이미지와 모든 기존 전신 사진은 변경하지 않았습니다.

## 교체의 안전성 및 캐시

`backend/app/services/garment_image_replacement.py`는 공개 API가 아닌 관리용 서비스입니다.
소유자·아우터 여부·검토한 이미지 경로와 수정 시각을 확인하고 사용자/의류 행을 잠급니다.
진행 중인 착샷이 있으면 교체를 거부합니다. 모든 새 파일을 먼저 준비한 뒤 6벌을 한 번에 커밋합니다.
파일 쓰기나 DB 커밋이 실패해도 옛 파일과 DB 참조를 유지합니다.
실패 시 참조되지 않은 새 파일이 남을 수 있지만 기존 이미지가 사라지지는 않습니다.
이 파일도 해당 의류 삭제·회원 탈퇴의 폴더 정리 범위에 포함됩니다.

의류 ID를 유지하면 예전 착샷이 캐시에서 다시 나타날 수 있어 결과 조회에 `inputs_current`를 추가했습니다.
착샷 이후 포함된 의류가 수정되었거나 삭제되면 false이며, 프론트가 새 코디의 완료 결과로 재사용하지 않습니다.
기존 착샷은 내 착샷 이력에서 계속 조회할 수 있습니다. 추가 마이그레이션은 없으며 head는 `0015`입니다.

## 밝은 코트 보정

열린 코트를 검증하면서 제품 분할기가 흰 배경의 그림자를 베이지 원단으로 포함하는 사례를 확인했습니다.
이 때문에 흰 이너를 코트 원단으로 오인해 개방부 검출이 실패했습니다.
주 원단이 유채색인 경우 더 밝고 채도가 낮은 배경 군집을 원단 팔레트에서 제외하도록 보정했습니다.
생성 모델이 이너를 짧게 만들어 배/바지가 드러나는 경우도, 기존 상의 마스크와 중앙 탐색 범위 안에서
개방부로 연결해 기존 상의를 복원합니다. 얼굴 인식이나 신원 저장은 수행하지 않습니다.

## 검증 결과

- 실제 PostgreSQL 전체 회귀: **518 passed, 2 skipped**, 260.78초.
  외부 checkout/별도 실사 입력이 필요한 기존 선택 테스트 2개는 건너뛰었습니다.
  새 배치 교체의 ID·속성·원본 보존, 다른 사용자 접근 404, 파일 실패·커밋 실패,
  오래된 검토 정보·진행 중 요청 거부, 이미지 변경 후 착샷 캐시 무효화가 포함됩니다.
- GPU venv 이미지 처리 회귀: **9 passed**. Ruff와 `git diff --check`도 통과했습니다.
- 실제 GPU에서 알려진 합성 인물과 세 제품을 테스트했고 모두 `layering.status=applied`였습니다.
  아우터 단계 측정은 봄버 8.7초, 데님 8.2초, 코트 8.2초였습니다. 전체 코디 시간이나 처리 보장 수치는 아닙니다.
- 실제 6벌의 원본·누끼 대체·썸네일 API 모두 소유자 200 / 타 사용자 404,
  `Cache-Control: private, no-store`를 확인했습니다.
- 로컬 및 공개 멘토 브라우저에서 각각 기존 시연 계정의 열린 아우터 3벌을 확인했습니다.
- 별도 합성 인물 계정에서 로컬 화면의 착샷 버튼으로 하의 → 상의 → 코트 생성과 완료 표시를 확인했습니다.
  같은 임시 계정에서 데님/봄버도 프론트 HTTP 경로로 생성하고 공개 브라우저의 내 착샷 화면에서
  세 결과(각 576×864)를 모두 확인했습니다. 세 결과 모두 상의 보존 적용, 현재 입력 일치 상태였습니다.
- 공개 DNS의 Funnel 주소로 연결한 HTTPS 요청에서도 세 결과가 200이었고,
  로컬 이미지와 바이트가 같았습니다. TLS 인증서 검증은 유지했습니다.
- 검증용 임시 계정과 관련 DB/서비스 저장 파일은 삭제했습니다. 기존 브라우저 로그인 계정을 복원했습니다.
- 최종 상태: 로컬/멘토 API 정상, GPU ready, 접수 및 GPU 대기 0, 임시 계정 잔여 0.

보존 대조: 기존 **계정 7개·프로필 7개·의류/속성 각 45개·피드백 7개·착샷 3개**가 그대로입니다.
이전 파일 **117개는 해시가 모두 동일**하고, 새 이미지/썸네일 12개를 더해 총 129개입니다.
기존 자격 파일 4개도 동일합니다. 기존 추천/코디 84/146개를 모두 보존했고 정상 화면 조회로
4/12개가 추가되어 최종 88/158개입니다. 원래 업무 필드는 대상 6벌의 위 세 필드를 제외하고 모두 대조했습니다.
첨부된 개인 사진은 열거나 테스트에 사용하지 않았습니다. `.env`는 직접·간접 모두 읽거나 수정하지 않았습니다.

## 증거 및 복구

Git 제외 경로 `.demo/open-outerwear/`:

- `plan.json`, `applied.json`: 대상 ID와 이전/새 경로, 교체 기록.
- `data-before.json`, `preservation-report.json`: 기존 데이터/파일 해시와 보존 결과.
- `image-access-report.json`, `live-results-report.json`, `final-health.json`: 접근·GPU·공개 HTTPS·상태 검사.
- `full-tests.log/xml`, `gpu-tests.log/xml`: 테스트 결과.
- `ui-local-wardrobe.png`, `ui-mentor-wardrobe.png`: 실제 계정의 옷장 화면.
- `ui-local-tryon.png`, `ui-mentor-tryons.png`: 별도 합성 인물의 실제 화면.
- `ui-result-{trench,denim,bomber}.jpg`: 합성 착샷 증거. 서비스 계정 정리 후에도 로컬 검증 산출물로 남습니다.

현재 상태 재검증:

```powershell
$env:FITJANG_SKIP_DOTENV = "1"
python .demo/open-outerwear/replace_demo.py verify
```

이전 이미지로 되돌릴 필요가 있을 때만 `restore`를 사용합니다. 대상 계정과 새 경로가 맞는지 확인하고
착샷 진행 중이면 거부합니다. 속성/ID는 그대로이며 두 버전의 파일을 모두 남깁니다.
이 작업에서 복구를 실행하지는 않았습니다.

```powershell
$env:FITJANG_SKIP_DOTENV = "1"
python .demo/open-outerwear/replace_demo.py restore
```

## 생성 자산과 프롬프트

기본 출력은 불투명 흰 배경의 제품 사진입니다. 사람 사진을 참조하지 않았습니다.
실행 모드는 built-in image generation의 제품 이미지 편집입니다.
올리브 봄버는 이전에 검증한 합성 자산 `.demo/tryon-layering/open-bomber.png`를 재사용했습니다.
그 신규 생성 프롬프트는 [TRYON_LAYERING.md](TRYON_LAYERING.md#생성-자산-기록)에 있습니다.

최종 자산은 `.demo/open-outerwear/assets/outerwear-olive-bomber.png`,
`outerwear-indigo-denim.png`, `outerwear-beige-trench.png`입니다.
데님/코트의 참조는 `.demo/wardrobe-v2/`의 같은 이름 제품 사진입니다.
원시 생성 파일은 `C:/Users/user/.codex/generated_images/01a1b4e2-2a2a-7611-97ff-a0a7da151e49/`의
`exec-821d21a6-a2ad-471b-a2e1-f7f81369fffe.png`(데님),
`exec-925f6d8d-d493-47fa-a3cd-933fc74a49ed.png`(코트)입니다.

데님 편집 프롬프트:

> Use case: precise-object-edit. Edit target: the referenced indigo denim trucker jacket product photograph. Change only its presentation to a completely unbuttoned, open-front jacket. Keep its indigo denim hue, solid denim texture, orange-brown contrast stitching, silver metal buttons, two chest flap pockets, long sleeves and short trucker length. Spread the left and right front panels apart with a large continuous empty WHITE studio-background gap from neck all the way to hem, about one quarter to one third of torso width. Remove the visible back fabric/lining within this central opening so the white background shows through; no inner shirt or mannequin. Keep front-facing symmetry, natural slightly downward sleeves, soft even catalog lighting, entire garment visible and centered, pure white background. No person, no hanger, no text, no logo, no watermark. This is a garment-only synthetic test asset; do not reference any personal photographs.

코트 편집 프롬프트:

> Use case: precise-object-edit. Edit target: the referenced beige single-breasted trench coat catalog photograph. Change only the presentation to fully open and unbuttoned. Keep the same warm beige solid cotton fabric, long coat length, wide notched lapels, brown buttons, sleeve cuff tabs, pocket positions, seams and formal/minimal design. The left and right front panels hang naturally apart, framing a generous continuous WHITE empty studio-background gap from neckline down through the hem, about one quarter to one third of torso width; the gap must remain open all the way down. Remove the back/lining fabric from the central gap so it shows white background rather than beige lining. No belt tied across the opening. Preserve realistic volume and natural drape, perfectly front-facing centered composition, downward sleeves, whole coat visible with margins, pure white background, soft even catalog lighting. No mannequin, no person, no inner clothes, no hanger, no logo, text or watermark. Garment-only synthetic test asset; do not use or reference any personal photographs.

## 품질 한계

세 제품 모두 중앙 이너가 보이도록 보존되지만 지퍼·목둘레·밑단·그림자의 완전한 사실성을 보장하지 않습니다.
실제 봄버/데님 결과에는 니트 밑단 바깥쪽에 밝은 생성 흔적이 일부 남았습니다.
코트가 열리는 폭도 모델에 따라 달라집니다. 얼굴이 없는 제품 사진과 합성 인물의 제한된 검증이며,
닫힌 제품·복잡한 패턴·유사한 원단/이너 색상·가림이 큰 자세에서는 보존이 불확실할 수 있습니다.
