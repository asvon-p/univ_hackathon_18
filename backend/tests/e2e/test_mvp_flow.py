import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from scripts.demo_assets import CATALOG, garment_images


@pytest.mark.parametrize("live_api", ["mock", "local"], indirect=True)
def test_signup_to_wardrobe_recommendation_feedback_over_real_http(live_api, tmp_path):
    client, provider, _ = live_api
    started = time.perf_counter()
    health = client.get("/health").json()
    assert health["db"] == "ok" and health["contract_version"] == "2.0"
    assert health["adapters"]["vision"] == health["adapters"]["recommender"] == provider
    email, password = f"e2e-{uuid4().hex}@example.com", "e2e-only-password-123"
    signup = {"email": email, "password": password, "nickname": "통합 시연", "invite_code": "e2e-invite-only"}
    assert client.post("/api/v1/auth/signup", json={**signup, "invite_code": "wrong"}).status_code == 403
    created = client.post("/api/v1/auth/signup", json=signup)
    assert created.status_code == 201
    user_id = created.json()["id"]
    login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    assert client.get("/api/v1/auth/me", headers=headers).json()["id"] == user_id
    assert (
        client.patch("/api/v1/profile", headers=headers, json={"preferred_styles": ["minimal"]}).status_code
        == 200
    )
    assert client.get("/api/v1/garments", headers=headers).json() == []
    assert (
        client.post("/api/v1/recommendations", headers=headers, json={"temperature_c": 8}).status_code == 422
    )

    garments, upload_ms, model_versions = {}, [], []
    for category in ("top", "bottom", "outerwear"):
        item = next(item for item in CATALOG if item.category == category)
        photo, _ = garment_images(item)
        upload_started = time.perf_counter()
        response = client.post(
            "/api/v1/garments", headers=headers, files={"file": ("photo.jpg", photo, "image/jpeg")}
        )
        upload_ms.append(round((time.perf_counter() - upload_started) * 1000, 2))
        assert response.status_code == 201
        garment = response.json()
        assert garment["analysis_status"] == "done", garment
        model_versions.append(garment["model_version"])
        # 실제 local 모듈의 stub도 거칩니다. 이후 사용자 수정 API로 슬롯을 확정합니다.
        if provider == "local":
            assert garment["model_version"] != "mock-1"
        patch = client.patch(
            f"/api/v1/garments/{garment['garment_id']}",
            headers=headers,
            json={
                "category": category,
                "color": item.color,
                "style_candidates": ["minimal"],
                "thermal_level": None,
            },
        )
        assert patch.status_code == 200 and patch.json()["source"] == "user"
        garments[category] = patch.json()
        media = client.get(garment["image_url"], headers=headers)
        assert media.status_code == 200 and media.headers["content-type"] == "image/jpeg"
        assert client.get(garment["image_url"]).status_code == 401
    assert len(client.get("/api/v1/garments", headers=headers).json()) == 3
    top = garments["top"]["garment_id"]
    assert (
        client.patch(f"/api/v1/garments/{top}", headers=headers, json={"status": "laundry"}).status_code
        == 200
    )
    assert (
        client.post("/api/v1/recommendations", headers=headers, json={"temperature_c": 8}).json()["code"]
        == "NOT_ENOUGH_GARMENTS"
    )
    assert (
        client.patch(f"/api/v1/garments/{top}", headers=headers, json={"status": "available"}).status_code
        == 200
    )
    recommendation_started = time.perf_counter()
    response = client.post(
        "/api/v1/recommendations", headers=headers, json={"temperature_c": 8, "occasion": "daily"}
    )
    recommendation_ms = round((time.perf_counter() - recommendation_started) * 1000, 2)
    assert response.status_code == 201
    result = response.json()
    assert result["provider"] == provider and result["is_fallback"] is False
    assert result["context"]["user_id"] == user_id and result["context"]["preferred_styles"] == ["minimal"]
    assert result["context"]["recent_feedback"] == []
    assert result["outfits"] and set(result["garments"]) <= {g["garment_id"] for g in garments.values()}
    assert all(outfit["items"]["shoes"] is None for outfit in result["outfits"])
    UUID(result["recommendation_id"])
    outfit_id = result["outfits"][0]["outfit_id"]
    UUID(outfit_id)
    path = f"/api/v1/recommendations/{result['recommendation_id']}"
    events = []
    for accepted, reason in ((True, "첫 코디 수락"), (False, "다른 스타일도 보고 싶어요")):
        feedback = client.post(
            path + "/feedback",
            headers=headers,
            json={
                "recommendation_id": result["recommendation_id"],
                "outfit_id": outfit_id,
                "accepted": accepted,
                "reason": reason,
            },
        )
        assert feedback.status_code == 201 and feedback.json()["accepted"] is accepted
        events.append(feedback.json())
    assert events[0]["feedback_id"] != events[1]["feedback_id"]

    # 새 로그인/HTTP 요청/서버 DB 세션에서도 저장된 결과를 그대로 읽습니다.
    relogin = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    headers = {"Authorization": "Bearer " + relogin.json()["access_token"]}
    assert client.get(path, headers=headers).json() == result
    assert client.get("/api/v1/recommendations", headers=headers).json() == [result]
    assert client.get(path + "/feedback", headers=headers).json() == list(reversed(events))
    next_result = client.post("/api/v1/recommendations", headers=headers, json={"temperature_c": 20}).json()
    assert next_result["provider"] == provider
    assert [f["accepted"] for f in next_result["context"]["recent_feedback"]] == [False, True]

    other_signup = {**signup, "email": f"other-{uuid4().hex}@example.com"}
    assert client.post("/api/v1/auth/signup", json=other_signup).status_code == 201
    other_login = client.post(
        "/api/v1/auth/login", json={"email": other_signup["email"], "password": password}
    )
    other = {"Authorization": "Bearer " + other_login.json()["access_token"]}
    for private_path in (f"/api/v1/garments/{top}", garments["top"]["image_url"], path, path + "/feedback"):
        assert client.get(private_path, headers=other).status_code == 404
    assert client.get("/api/v1/garments", headers=other).json() == []
    assert client.get("/api/v1/recommendations", headers=other).json() == []
    assert (
        client.post(
            path + "/feedback",
            headers=other,
            json={
                "recommendation_id": result["recommendation_id"],
                "outfit_id": outfit_id,
                "accepted": True,
            },
        ).status_code
        == 404
    )
    report = {
        "passed": True,
        "transport": "real HTTP / uvicorn",
        "database": "PostgreSQL isolated schema",
        "provider": provider,
        "contract_version": health["contract_version"],
        "vision_model_versions": model_versions,
        "manual_attribute_correction": True,
        "gpu": health["gpu"],
        "upload_ms": upload_ms,
        "recommendation_http_ms": recommendation_ms,
        "recommendation_engine_ms": result["latency_ms"],
        "total_ms": round((time.perf_counter() - started) * 1000, 2),
        "verified_at": datetime.now(UTC).isoformat(),
        "scope": "Integration with synthetic images and current modules; not an AI quality/GPU benchmark",
    }
    output = Path(os.environ.get("E2E_REPORT_DIR", tmp_path))
    output.mkdir(parents=True, exist_ok=True)
    (output / f"e2e-{provider}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
