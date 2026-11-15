# 필수 의류 추천 구현과 검증

2026-11-12, 공통 계약 2.1. 기존 상의·하의·아우터 범위를 유지한다.

## 사용 방법

옷 상세의 **이 옷으로 코디**를 누르거나, 코디 추천 화면의 **꼭 입고 싶은 옷**에서 고른다.
상의·하의·아우터에서 각각 한 벌까지 지정할 수 있다. 빈 선택은 기존 자동 추천이다.
결과에 고정한 의류의 **필수 포함** 표시가 나오며 **조건 바꾸기**에서도 기온·스타일·선택을 유지한다.

기온 필터를 통과한 후보가 없거나 추천 엔진이 실패해도 지정한 옷은 변경하지 않는다.
대체 추천에는 기온 적합성을 직접 확인하라는 설명이 나온다. 이번 단계는 일정별 아우터 탈착을
다루지 않으며, 지정한 아우터는 결과 코디에 포함된다.

## API

```json
{
  "temperature_c": 18,
  "preferred_styles": ["minimal"],
  "required_garment_ids": ["본인 의류 UUID"],
  "excluded_items": []
}
```

`POST /api/v1/recommendations`, JWT 인증 필요. 기존 필드와 함께 `required_garment_ids`를 보낸다.
응답의 `context.required_garment_ids`와 DB 추천 문맥에 저장된다. 위 UUID는 설명용 자리 표시자다.

| 상황 | 응답 |
|---|---|
| 다른 사용자 또는 없는 의류 | 404 `GARMENT_NOT_FOUND` |
| 중복 ID·4개 이상·필수/제외 충돌 | 422 `VALIDATION_ERROR` |
| 같은 카테고리 두 벌 | 422 `REQUIRED_GARMENT_CONFLICT` |
| 세탁 중·비활성·분석 미완료·지원 밖 카테고리 | 422 `REQUIRED_GARMENT_UNAVAILABLE` |
| 처리 중 필수 의류 삭제·상태·카테고리 변경 | 409 `REQUIRED_GARMENT_CHANGED` |
| 최종 검증 후에도 유효한 코디 없음 | 422 `NO_FEASIBLE_OUTFIT` |

화면에서는 사용 가능한 본인 의류만 선택지로 제공한다. 주소를 조작해 선택 범위를 벗어나면 입력
화면에서 오류를 표시한다. 보안 검사는 프론트에 의존하지 않고 API에서도 수행한다.

## 조건이 유지되는 구조

1. 서비스가 JWT 사용자 범위에서 필수 의류를 조회해 소유권·상태·카테고리를 확인한다.
2. 공통 제약 함수가 후보 생성 전에 슬롯을 고정한다. 낮은 점수의 필수 옷도 후보에서 밀려나지 않는다.
3. local·mock·프론트 mock·OpenAI 후보 경로가 같은 고정 조건을 사용한다.
4. `SafeRecommender`가 결과 ID·슬롯·필수 의류·중복을 검사한다. 입력 복사로 엔진의 문맥 변경을 격리한다.
5. 저장 직전에 현재 DB 상태를 다시 읽고 잠근다. 필수 의류가 바뀌었으면 409로 종료한다.
6. 서비스의 마지막 대체 추천도 다시 검사하고, 유효한 코디만 한 트랜잭션으로 저장한다.

기존 추천 JSON을 확장하므로 새 테이블·마이그레이션은 없다. 과거 문맥에 필드가 없으면 `[]`를 적용한다.
계정·의류 ID·원본 이미지·과거 추천과 피드백을 재생성하지 않는다.

## GPT6-Luna의 판단 전용 역할

`OpenAIRecommender`는 검증된 후보의 번호·속성·규칙 점수와 필수 카테고리만 보낸다.
사용자 ID·의류 UUID·파일 경로·전신 사진은 보내지 않는다. 모델은 후보 번호의 순서와 판단 이유만 반환하며,
후보를 빼거나 추가·중복하거나 옷 ID를 반환하면 응답을 거부하고 규칙 추천으로 대체한다.

모델 설정 `gpt-6-luna`와 Responses API의 strict JSON Schema, `store:false`를 유지한다.
도구 호출이나 이미지 생성은 제공하지 않는다. JSON 형식 검증과 별도로 실제 후보 집합·필수 의류를 검사한다.
[OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs),
[GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna).

이번 검증에서 외부 OpenAI 유료 호출은 수행하지 않았다. 판단 계약·정상 순서 변경·오류·시간 초과·잘못된
후보 응답은 통제된 HTTP 응답으로 검증한다. 실제 GPT 응답 품질이나 해당 계정의 API 사용 가능성을 검증한 것은 아니다.
당시 no-dotenv 시연 실행기는 키를 비우고 local 모드를 사용했다. 후속 실제 연결 방법은 아래를 참고한다.

### GPT 판단을 로컬·멘토 시연에 연결하기 (2026-11-12 후속)

`scripts.frontend_preview`와 `scripts.mentor_demo`는 공통 설정을 사용한다.
기본 `--recommender auto`는 사용 가능한 키가 있으면 `openai`, 없으면 `local`을 사용한다.
`--recommender local`은 키를 읽지 않고 규칙 추천만 사용한다. `--recommender openai`는
GPT 판단을 시도하며 키 누락을 포함한 실패에서도 local 대체 추천을 제공한다.
로그인 후 자동 실행되는 멘토 서버도 같은 기본 설정을 사용한다.

프로젝트 폴더의 사용자 터미널에서 한 번만 실행한다. 키는 채팅에 붙여 넣지 않는다.

```powershell
conda activate univ18
python -m scripts.openai_credentials --configure
```

숨겨진 입력으로 받은 키를 Windows DPAPI의 현재 사용자 범위로 암호화해
Git 제외 경로 `.demo/openai-judge/key.dpapi`에 저장한다. `.env`는 읽거나 수정하지 않는다.
같은 PC·Windows 사용자로 재시작하면 복호화해서 백엔드 프로세스에만 전달한다.
프론트·GPU·터널 프로세스 환경에서는 OpenAI 키를 비운다. 명시적으로 설정한
프로세스 환경변수 `OPENAI_API_KEY`(또는 기존 `LLM_API_KEY`)가 있으면 암호화 파일보다 우선한다.
키 교체 후에는 서버 재시작이 필요하다. 저장소를 다른 PC로 복사하면 그 PC에서 새로 등록한다.
`python -m scripts.openai_credentials`는 설정 여부만 출력한다.

판단 흐름:

1. JWT 사용자 소유의 필수 의류·일정 버전·확인한 참고 사진 버전을 검증한다.
2. local 엔진으로 조건을 만족하는 후보를 최대 3개 만들고 서버에서 다시 검사한다.
3. `gpt-6-luna`에 의류 속성, 선호, 익명화한 일정 조건, 참고 특징, 후보 평가를 전송한다.
   사진·얼굴·파일 경로·사용자/의류/일정/참고 사진 UUID·실제 일정 날짜와 시각은 보내지 않는다.
4. 모델은 모든 후보 번호를 한 번씩 반환하고 후보별 한국어 판단 이유만 제공한다.
   의류 구성, 일정별 착용 계획, 참고 특징 비교와 규칙 점수는 서버에서 유지한다.
5. 순서·이유를 DB의 코디 순위/설명으로 저장한다. `provider=openai`이면 화면에 AI 판단을 표시한다.
6. HTTP 인증/요청 제한/서버 오류·시간 초과·거절·불완전 응답·후보 중복/누락·빈 이유는 local로 대체한다.
   기존 `provider=fallback`, `is_fallback=true` 계약으로 저장하고 화면에도 대체 사용을 표시한다.

시연 설정은 reasoning `low`, 출력 최대 1200토큰, HTTP 시간 제한 12초,
어댑터 제한 최대 13초다. 자동 재시도는 하지 않는다. `store:false`, 엄격한 JSON Schema,
고정된 OpenAI HTTPS 엔드포인트와 인증서 검증을 사용하며 프록시 환경변수는 사용하지 않는다.
GPT 순위와 별개로 `score`는 기존 규칙 점수이므로 최종 표시 순서에 따라 내림차순이 아닐 수 있다.
조건을 만족하는 후보가 없으면 기존 422를 유지하고 조건을 임의로 완화하지 않는다.

이 변경에는 DB 마이그레이션이나 기존 계정·사진의 변환이 없다.
검증 코드: `backend/tests/api/test_openai_judgment.py`,
`backend/tests/e2e/test_openai_judgment.py`, `backend/tests/test_recommendation_runtime.py`.
통제된 HTTP 응답을 이용하는 회귀 테스트와 실제 외부 유료 API 검증은 구분해서 기록한다.

후속 검증 상태:

- 판단·암호화 키·실제 PostgreSQL API 검증 35개와 기존 조건/공개 경계/자동 실행 및
  로컬·멘토 HTTP E2E 회귀 145개, 합계 **180개 통과**.
- 회귀 테스트의 GPT 성공·401/429/503·시간 초과·잘못된 후보/빈 이유 응답은 통제된 HTTP로 검증했다.
- 로컬 3000/8002와 공개 HTTPS 3001/8003에 반영했다. 사용자가 `.env` 외부의 암호화 저장소에
  키를 직접 등록했고, 기본 `auto` 실행으로 두 서버 모두 `openai`/`gpt-6-luna`를 선택했다.
- 등록 전 실제 브라우저에서 local 대체 안내를 확인했다. 등록 후에는 실제 OpenAI 호출로
  필수 아우터·2개 일정·참고 사진·선호 스타일을 지정한 추천과 AI 판단 안내를 확인했다.
  모델 이유의 내부 후보 번호 노출을 피하도록 프롬프트를 보완하고 핵심 30개 회귀를 다시 통과했다.
- 합성 의류 후보 3개의 실제 Responses API 검증은 HTTP 200/`completed`, 응답 모델
  `gpt-6-luna`를 확인했다. 임시 계정의 실제 서비스 추천 6건 모두 `provider=openai`로 DB에 저장됐다.
- 최종 로컬·공개 브라우저 추천 두 건의 순위·이유·필수 의류·일정/참고 비교 저장을 PostgreSQL에서
  재확인했다. 해당 두 요청의 백엔드 처리시간은 각각 약 3.0초/2.8초였다(일반 성능 보장은 아님).
- 검증 계정 정리 후 기존 10개 테이블의 행 지문과 저장소 파일 경로·크기·수정 시각이 동일했다.
  소스 접근 차단을 유지하고 `.env` 직접/간접 접근은 하지 않았다.
- 자동 실행 감독은 `ready`, 두 API의 DB 연결은 `ok`, 공개 로그인 200과 소스 경로 차단을 확인했다.
  Windows 로그인 후에도 같은 암호화 키를 재사용한다.
- 산출물: 초기 대체 검증 `.demo/openai-judge-ui/`, 실제 GPT 화면/DB 검증 `.demo/openai-live-ui/`,
  테스트 결과 `.demo/openai-judge-tests.xml`, `.demo/openai-regression.xml`, `.demo/openai-live-regression.xml`.

## 테스트 범위

- 실제 PostgreSQL의 임시 스키마에 Alembic을 적용해 저장·조회·격리·상태 변경·대체 추천을 검증.
- 두 사용자의 실제 TCP 동시 요청에서 각자의 필수 의류가 유지되고 다른 사용자의 결과·의류 접근이 차단됨을 검증.
- GPT 판단 어댑터의 정상·실패 결과가 PostgreSQL에도 고정된 의류로 저장되는지 검증.
- 낮은 선호 점수의 필수 의류를 상위 3개 선택 전에 고정하는 회귀 테스트.
- 기존 필드만 있는 과거 추천 조회, 추천 커밋 실패, 피드백·요청 제한·프론트 흐름 회귀 검증.
- 별도 테스트 계정으로 브라우저 선택 → 일반 추천 → 조건 수정 → 대체 추천 → 옷 상세 진입 확인.
- 모바일 폭 390px에서 결과와 ‘필수 포함’ 표시를 확인. 실기기 촬영 검증은 이번 범위가 아니다.

모든 실행은 `FITJANG_SKIP_DOTENV=1`을 사용하며, 테스트와 시연 시작 경로의 audit hook이 `.env` 열기를 차단한다.
테스트 스키마·계정은 기존 시연 스키마와 분리한다. 테스트 산출물은 Git에서 제외된 `.demo/required-garments-ui`에 둔다.

주요 테스트:

- `backend/tests/api/test_required_garments.py`
- `backend/tests/test_required_ranking.py`
- `backend/tests/adapters/test_openai_recommender.py`
- `backend/tests/e2e/test_required_garments.py`

### 2026-11-12 실행 결과

- 실제 PostgreSQL을 사용한 추천·계약·통합 검증: 106건 통과.
- 추가 GPT 정상/실패 저장 검증과 기존 기능 회귀 검증: 102건 통과, 1건 건너뜀.
  건너뛴 항목은 별도 D 팀원 체크아웃을 요구하는 `TEAM_FRONTEND_ROOT` 테스트다.
  현재 통합 프론트의 실제 HTTP 및 브라우저 흐름은 별도로 검증했다.
- 실제 브라우저 조작과 PostgreSQL 결과 저장을 함께 확인하는 임시 시연 검증: 1건 통과.
- 변경한 Python 파일의 Ruff 검사와 `git diff --check` 통과.

로컬(프론트 3000/API 8002)과 멘토(프론트 3001/API 8003) 서버에 반영했다.
두 API에서 계약 2.1과 PostgreSQL 정상 연결을 확인했고,
[멘토 HTTPS 주소](https://sgy-host.taile39b25.ts.net:8443)의 로그인 화면은 200,
Python 소스 경로는 404를 유지했다. 이 최초 검증 당시 시연 추천 제공자는 `local`이었다.

재시작 전후 계정·의류·추천·코디·피드백·착샷 결과의 행 수 및 ID 집합과 이미지 파일의
경로·크기·수정 시각을 비교해 변경이 없음을 확인했다. 기존 사진 내용은 열지 않았다.
검증용 계정과 데이터는 분리된 PostgreSQL 임시 스키마에서만 만들고 검증 종료 후 정리했다.
화면 증빙은 Git에서 제외된 `.demo/required-garments-ui/mobile-fallback.jpg`에 보관한다.

## 후속 범위

하루 일정, 참고 이미지 재현, `outerwear_policy`, 신규 점수 가중치, 유료화 실험은 이번 변경에 포함하지 않는다.
기존 색상·상황·선호 스타일 점수는 유지한다.
