from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from backend.app.core.errors import AppError
from backend.app.db.models import Recommendation, RecommendationRateLimit
from backend.app.services.recommendation_rate_limit import RecommendationRateLimiter
from scripts.demo_assets import CATALOG, garment_images


@pytest.mark.parametrize("live_api", ["mock"], indirect=True)
def test_concurrent_http_requests_and_separate_process_share_persistent_user_quota(live_api):
    client, _, context = live_api
    path = "/api/v1/recommendations"

    def account():
        credentials = {"email": f"limit-{uuid4().hex}@example.com", "password": "e2e-only-password-123"}
        signup = client.post(
            "/api/v1/auth/signup",
            json={**credentials, "nickname": "limit", "invite_code": "e2e-invite-only"},
        )
        assert signup.status_code == 201
        login = client.post("/api/v1/auth/login", json=credentials)
        assert login.status_code == 200
        return UUID(signup.json()["id"]), {"Authorization": "Bearer " + login.json()["access_token"]}

    user_id, headers = account()
    other_id, other_headers = account()
    for category in ("top", "bottom", "shoes"):
        upload = client.post(
            "/api/v1/garments",
            headers=headers,
            files={"file": ("garment.jpg", garment_images(CATALOG[0])[0], "image/jpeg")},
        )
        assert upload.status_code == 201
        garment_id = upload.json()["garment_id"]
        assert (
            client.patch(
                f"/api/v1/garments/{garment_id}", headers=headers, json={"category": category}
            ).status_code
            == 200
        )

    with ThreadPoolExecutor(max_workers=12) as executor:
        responses = list(
            executor.map(lambda _: client.post(path, headers=headers, json={"temperature_c": 5}), range(20))
        )
    assert sorted(response.status_code for response in responses) == [201] * 10 + [429] * 10
    for response in responses:
        if response.status_code == 429:
            assert response.json()["code"] == "RECOMMENDATION_RATE_LIMITED"
            assert 1 <= int(response.headers["Retry-After"]) <= 60
    # 동일 IP의 다른 로그인 사용자는 허용되며, 옷 부족 오류만 받습니다.
    other = client.post(path, headers=other_headers, json={"temperature_c": 5})
    assert other.status_code == 422 and other.json()["code"] == "NOT_ENOUGH_GARMENTS"

    engine = create_engine(context["database_url"], connect_args={"connect_timeout": 3})
    try:
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(Recommendation)) == 10
            assert len(db.get(RecommendationRateLimit, user_id).request_times) == 10
            assert len(db.get(RecommendationRateLimit, other_id).request_times) == 1
        # 서버와 다른 프로세스의 새 세션도 DB에 확정된 같은 한도를 확인합니다.
        with Session(engine) as db:
            with pytest.raises(AppError) as caught:
                RecommendationRateLimiter(db).consume(user_id)
            assert caught.value.status == 429
        # 60초가 지난 상태로 옮겨 실제 DB 시계 기반 만료를 검증합니다(대기 불필요).
        with Session(engine) as db:
            now = float(db.scalar(select(func.extract("epoch", func.clock_timestamp()))))
            db.get(RecommendationRateLimit, user_id).request_times = [now - 61] * 10
            db.commit()
        resumed = client.post(path, headers=headers, json={"temperature_c": 5})
        assert resumed.status_code == 201, resumed.text
        with Session(engine) as db:
            assert len(db.get(RecommendationRateLimit, user_id).request_times) == 1
            assert db.scalar(select(func.count()).select_from(Recommendation)) == 11
    finally:
        engine.dispose()
