# 위치 기반 날씨와 추천 연동

2026-11-08 기준. Open-Meteo의 현재 기온을 조회해 A의 규칙 추천 및 선택형 GPT 추천에 전달합니다.
기존 수동 기온 요청도 그대로 사용할 수 있습니다. 공통 계약은 `2.0`, DB head는 `0010`이며
이번 기능에 새 마이그레이션은 없습니다.

## 1. D 프론트의 현재 요청과 연결

프론트의 `BACKEND_API_URL`은 `http://127.0.0.1:8001/api/v1`입니다.
`connect/weather.py`의 `get_weather(token, lat, lon)`이 호출하는 `/weather`는
백엔드의 다음 경로와 연결됩니다. JWT가 필요합니다.

```http
GET /api/v1/weather?lat=37.5665&lon=126.978
Authorization: Bearer <access_token>
```

성공 응답 예시:

```json
{
  "temperature_c": 6.5,
  "condition": "맑음",
  "location_name": null,
  "source": "open_meteo",
  "weather_time": "2026-11-08T05:45:00Z",
  "fetched_at": "2026-11-08T05:46:00Z",
  "is_fallback": false,
  "attribution": "Weather data by Open-Meteo.com (CC BY 4.0)"
}
```

`location_name`은 별도 역지오코딩을 하지 않아 null입니다. 현재 D 커넥터가 사용하는
`temperature_c`, `condition`, `location_name` 형식에 맞췄습니다. 응답은 `Cache-Control: private, no-store`입니다.

- 성공: D가 응답의 `temperature_c`를 기존 추천 요청에 넣으면 해당 기온으로 추천됩니다.
- 조회 실패: **503 `WEATHER_UNAVAILABLE`**. D의 현재 홈 화면은 이 오류를 받아
  수동 추천 화면(`/recommend`)으로 이동할 수 있는 링크를 표시합니다.
- 위치 권한 거절/위치 없음: 자동 조회를 생략하고 수동 입력을 사용하세요.
  현재 D 코드가 선택하는 서울 기본 위치는 프론트 동작입니다. 백엔드는 위치를 추정하지 않습니다.
- 인증 누락·만료·탈퇴: **401**. D는 재로그인 흐름을 사용합니다.
- 좌표 누락·범위 초과·유효하지 않은 숫자: **422**.

GET에도 선택적으로 `temperature_c=18`을 보낼 수 있습니다. 자동 조회가 실패하면 그 값으로
200을 반환하고 `source=manual`, `is_fallback=true`로 표시합니다. 현재 D 커넥터는 이 옵션을 사용하지 않습니다.

## 2. 추천 API에서 직접 조회

`POST /api/v1/recommendations`에도 `lat`, `lon`을 함께 보낼 수 있습니다.
`occasion`, `preferred_styles`, `excluded_items` 등 기존 필드는 그대로 지원합니다.

```json
{
  "lat": 37.5665,
  "lon": 126.978,
  "temperature_c": 18,
  "occasion": "daily"
}
```

| 입력과 조회 결과 | 추천에 적용하는 기온 |
|---|---|
| 좌표 있고 자동 조회 성공 | 조회한 기온. 함께 보낸 수동 기온보다 우선 |
| 좌표 있고 조회 실패, 수동 기온 있음 | 입력한 기온. 0도도 유효 |
| 좌표 있고 조회 실패, 수동 기온 없음 | 503 `WEATHER_UNAVAILABLE`; 수동 기온을 받아 다시 요청 |
| 좌표 없이 수동 기온만 있음 | 외부 호출 없이 입력한 기온 |
| 좌표·기온 모두 없음 또는 좌표 한쪽만 있음 | 422 |

허용 범위는 위도 -90~90, 경도 -180~180, 기온 -90~60℃입니다. POST JSON에는 숫자를 사용합니다.
실패 시 서울 기온·0도·계절 평균 등 임의 값을 넣지 않습니다.

추천 응답과 조회 이력에는 `weather`가 추가됩니다. `context.temperature_c`는 실제 엔진에 전달한
기온이며, `weather`에는 출처·조회 시각·수동 대체 여부가 들어갑니다.
`weather.is_fallback`은 **날씨 조회의 수동 대체**, 최상위 `is_fallback`은 **추천 엔진의 대체 추천**입니다.

현재 D처럼 GET 날씨를 조회한 뒤 기온만 POST하면 백엔드는 그 값의 출처를 확인할 수 없으므로
추천 기록의 `weather.source`는 `manual`입니다. 이는 클라이언트가 제공한 기온이라는 의미입니다.
자동 조회 출처까지 추천 이력에 남기려면 추천 POST에 좌표를 보내세요. 유효한 캐시가 있으면 재사용합니다.
기존 추천 이력에 날씨 메타데이터가 없으면 `weather=null`이며 기존 결과는 보존됩니다.

기존 본인 옷장·추천 소유권 검증과 사용자별 분당 10회 제한을 유지합니다.
인증·본문 검증 후 발생한 날씨 조회 실패도 추천 요청 1회로 계산합니다.
`GET /weather`는 추천 횟수를 소비하지 않습니다.

## 3. 설정과 실행

`.env`는 사용자의 명시적인 금지 해제 전까지 에이전트가 읽거나 수정하지 않습니다.
이번 구현·검증에서도 접근하지 않았으며, 이 제한을 루트 `AGENTS.md`에 기록했습니다.
설정 예시는 `.env.example`만 수정했습니다.

| 설정 | 기본값 | 용도 |
|---|---|---|
| `WEATHER_PROVIDER` | `open_meteo` | 자동 조회. `manual`이면 수동 입력만 사용 |
| `WEATHER_TIMEOUT_SEC` | `3` | 외부 조회 전체 대기 제한(초), 최대 10 |
| `WEATHER_CACHE_TTL_SEC` | `300` | 성공 결과 캐시(초), 0이면 사용하지 않음, 최대 900 |
| `WEATHER_CACHE_MAX_ENTRIES` | `256` | 프로세스당 캐시 항목 수, 최대 4096 |
| `WEATHER_API_KEY` | 빈 값 | 향후 키 기반 공급자용 예약값. Open-Meteo에서는 사용하지 않음 |

기존 설정에 `WEATHER_PROVIDER=manual`이 있으면 자동 조회가 켜지지 않습니다.
사용자가 직접 실행하는 PowerShell에서 아래 환경변수를 설정한 뒤 백엔드를 재시작하면
파일을 수정하지 않고 자동 모드를 선택할 수 있습니다. 프로세스 환경변수가 dotenv 값보다 우선합니다.

```powershell
$env:WEATHER_PROVIDER = "open_meteo"
```

이번 작업에서는 기존 시연 서버를 재시작하지 않았습니다. 검증은 별도 임시 서버에서 수행했습니다.
테스트용 `FITJANG_SKIP_DOTENV=1`은 `.env` 로딩 자체를 생략합니다. 일반 시연 실행에 이 값을 설정하면
JWT·DB 등 필요한 설정도 별도 환경변수로 제공해야 하므로 테스트용 터미널과 구분하세요.

## 4. 외부 서비스와 위치 처리

[Open-Meteo 문서](https://open-meteo.com/en/docs)의 현재 기상 데이터는 15분 간격 기상 모델 기반이며
현장 센서 실측값을 뜻하지 않습니다. 섭씨 단위와 응답 시각을 검사하고, 조회 시점보다 2시간 넘게
오래되거나 20분 넘게 미래인 데이터는 거부합니다. HTTP 오류·timeout·응답 형식 오류도 수동 입력 경로로 보냅니다.

위도·경도는 소수점 둘째 자리로 반올림해 Open-Meteo에 전송하고 메모리 캐시 키로 사용합니다.
캐시는 사용자 정보와 연결하지 않으며 만료 후 실패하면 오래된 값을 대신 사용하지 않습니다.
좌표는 DB와 추천 엔진/GPT 입력에 넣지 않습니다. 추천 테이블의 기존 `context` JSONB에
기온과 날씨 메타데이터만 추가하므로 데이터 변경 마이그레이션은 필요하지 않습니다.

백엔드 Uvicorn의 날씨 경로 access 로그에서 쿼리를 제거하고 httpx의 URL 정보 로그를 끕니다.
D 서버나 리버스 프록시의 위치 쿼리 로그 정책은 각 서버에서도 적용해야 합니다.

[Open-Meteo 무료 API 이용 조건](https://open-meteo.com/en/terms)은 비상업적 사용을 대상으로 합니다.
D는 날씨를 표시하는 화면에 `Weather data by Open-Meteo.com` 출처와
`https://open-meteo.com/` 링크를 표시해야 합니다. 현재 D의 Weather 모델은 추가 응답 필드를
보존하지 않으므로 고정 출처 링크를 화면에 넣거나 `attribution` 전달을 추가하세요.

## 5. 검증 결과와 재현

- 실제 PostgreSQL 전체 **279개 통과**, SQLite **260개 통과·19개 제외**.
- 날씨 어댑터/API 집중 테스트 **45개 통과**. 인증, 탈퇴 JWT, 좌표 검증, 0도,
  공급자 오류/잘못된 단위·시각/timeout, 캐시, 수동 대체, 본인 이력/타인 차단을 검증했습니다.
- D `feat/frontend@85ddedc`의 실제 `connect` 코드로 TCP HTTP E2E를 통과했습니다.
  가입→프로필→상의·하의 등록→날씨 조회→추천→피드백→수동 대체→탈퇴를 포함합니다.
  프론트의 dotenv 초기화 호출만 생략하고 HTTP 연결 코드는 사용했습니다.
- 자동 기온이 추천 엔진에 전달되고 PostgreSQL JSONB에 저장·재조회되는 것을 확인했습니다.
  E2E에서는 외부 날씨 HTTP만 고정 응답으로 대체해 재현성을 확보했습니다.
- 별도 실제 Open-Meteo 호출도 성공했습니다. 공개 서울시청 좌표에서 **5.7℃**,
  공급자 시각 **2026-11-08 05:45 UTC**, 조회 시각 **05:55 UTC**를 확인했습니다.
- 테스트에서는 `FITJANG_SKIP_DOTENV=1`을 설정하고, Python 테스트 프로세스가 `.env`를 열면
  실패하도록 검사합니다. 외부 OpenAI 유료 API는 호출하지 않았습니다.
- 기존 Starlette/httpx deprecation 경고 1건이 있으며 테스트 실패는 없습니다.
- Ruff 검사·포맷 확인 통과. 테스트 후 DB head `0010`, 잔여 테스트 스키마 0개,
  테스트 DB public의 기존 빈 데이터 상태를 확인했습니다.

아래 명령은 실행 중인 테스트 PostgreSQL과 `univ18` 환경을 사용합니다. Docker Compose를 호출하지 않습니다.

```powershell
conda activate univ18
$env:FITJANG_SKIP_DOTENV = "1"
$env:TEST_POSTGRES = "1"
$env:TEST_DATABASE_URL = "postgresql+psycopg://fitjang:fitjang@127.0.0.1:5432/fitjang_test"
# D 소스가 있는 경우 실제 connect 모듈 E2E까지 포함
$env:TEAM_FRONTEND_ROOT = "C:\path\to\team-checkout\frontend"
python -m pytest --tb=short
```

테스트는 `fitjang_test`에 임시 스키마·이미지 저장소를 만들고 종료 후 정리합니다.
이번 실행 보고서는 `.pytest_cache/weather-pg-full.xml`, `.pytest_cache/weather-sqlite-full.xml`,
집중 검증 보고서는 `.pytest_cache/weather-targeted.xml`입니다(Git 제외).
요약 결과는 `.demo/reports/weather-verification.json`에 저장했습니다(Git 제외).
이후 현재 프로젝트에 D 프론트를 통합하고 실제 화면의 자동 기온·추천·피드백 및 출처 표시까지 검증했습니다.
최신 PostgreSQL 결과는 **281개 통과**입니다([프론트 통합 보고서](FRONTEND_INTEGRATION.md)).
위치 권한 허용 경로는 별도이며 화면 검증에서는 서울 기본 좌표를 사용했습니다.
