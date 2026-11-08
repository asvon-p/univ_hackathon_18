"""D의 실제 checkout 경로를 TEAM_FRONTEND_ROOT로 지정하는 선택적 외부 계약 검증."""

import ast
import asyncio
import importlib
import importlib.util
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from scripts.demo_assets import CATALOG, garment_images
from shared.contracts import FeedbackEvent, UserContext


@pytest.mark.skipif(not os.environ.get("TEAM_FRONTEND_ROOT"), reason="D checkout: TEAM_FRONTEND_ROOT 필요")
@pytest.mark.parametrize(
    "live_api", [{"provider": "local", "invite_code": "", "weather_stub": True}], indirect=True
)
def test_team_frontend_connectors_over_real_http_and_postgres(live_api, monkeypatch):
    client, _, _ = live_api
    source = Path(os.environ["TEAM_FRONTEND_ROOT"]).resolve() / "connect"
    monkeypatch.setenv("FRONTEND_USE_MOCK", "false")
    monkeypatch.setenv("BACKEND_API_URL", str(client.base_url).rstrip("/") + "/api/v1")
    package_name = "fitjang_team_contract_connect"
    previous_path = sys.path[:]
    spec = importlib.util.spec_from_file_location(
        package_name,
        source / "__init__.py",
        submodule_search_locations=[str(source)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = module
    try:
        # 팀원 소스의 HTTP 연결 코드는 그대로 실행하고 .env 자동 로딩 호출만 생략합니다.
        tree = ast.parse((source / "__init__.py").read_text(encoding="utf-8"))
        tree.body = [
            node
            for node in tree.body
            if not (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "_load_env_file"
            )
        ]
        exec(compile(tree, str(source / "__init__.py"), "exec"), module.__dict__)
        auth = importlib.import_module(package_name + ".auth_profile")
        wardrobe = importlib.import_module(package_name + ".garment_storage")
        recommendation = importlib.import_module(package_name + ".recommendation")
        weather_api = importlib.import_module(package_name + ".weather")

        async def flow():
            email, password = f"frontend-{uuid4().hex}@example.com", "frontend-password-123"
            token = await auth.signup(email, password)
            token = (await auth.login(email, password)).access_token
            headers = {"Authorization": "Bearer " + token}
            assert (await auth.get_profile(token)).height_cm is None
            profile = auth.Profile(height_cm=170.5, weight_kg=60.5, age=24, gender="other")
            with pytest.raises(module.ApiError) as denied:
                await auth.save_profile(token, profile)
            assert denied.value.code == "BODY_INFO_CONSENT_REQUIRED"
            # D의 기존 화면에는 아직 동의 UI가 없으므로 백엔드 API에서 명시적으로 동의합니다.
            assert (
                client.patch("/api/v1/profile", headers=headers, json={"consent_body_info": True}).status_code
                == 200
            )
            saved = await auth.save_profile(token, profile)
            assert saved.height_cm == 170.5 and saved.weight_kg == 60.5 and saved.age == 24
            body_photo = garment_images(CATALOG[0])[0]  # synthetic fixture, no personal photo
            photo_version = await auth.upload_body_photo(token, "body.jpg", body_photo, "image/jpeg")
            assert photo_version and (await auth.get_profile(token)).body_photo_updated_at == photo_version
            downloaded, mime = await auth.fetch_body_photo(token)
            assert downloaded and mime == "image/jpeg"
            await auth.delete_body_photo(token)
            assert (await auth.get_profile(token)).body_photo_updated_at is None
            with pytest.raises(module.ApiError) as missing_photo:
                await auth.fetch_body_photo(token)
            assert missing_photo.value.code == "BODY_PHOTO_NOT_FOUND"
            garment_ids = []
            for category in ("top", "bottom"):
                photo, _ = garment_images(next(g for g in CATALOG if g.category == category))
                garment = await wardrobe.create_garment(token, "photo.jpg", photo, "image/jpeg")
                updated = await wardrobe.update_garment(
                    token,
                    garment.garment_id,
                    {
                        "category": category,
                        "name": category + " 이름",
                        "location": "기숙사",
                        "thermal_level": None,
                    },
                )
                assert updated.name == category + " 이름" and updated.location == "기숙사"
                image, mime = await wardrobe.fetch_image(token, garment.garment_id)
                assert image and mime.startswith("image/")
                garment_ids.append(garment.garment_id)
            assert len(await wardrobe.list_garments(token)) == 2
            current = await weather_api.get_weather(token, 37.5665, 126.978)
            assert current.temperature_c == 6.5 and current.condition == "맑음"
            result = await recommendation.recommend(
                token,
                UserContext(user_id="me", temperature_c=current.temperature_c, preferred_styles=["꾸안꾸"]),
            )
            assert result.outfits[0].items.shoes is None
            assert {result.outfits[0].items.top, result.outfits[0].items.bottom} == set(garment_ids)
            await recommendation.send_feedback(
                token,
                FeedbackEvent(
                    recommendation_id=result.recommendation_id,
                    outfit_id=result.outfits[0].outfit_id,
                    accepted=True,
                ),
            )
            path = f"/api/v1/recommendations/{result.recommendation_id}"
            assert client.get(path + "/feedback", headers=headers).json()[0]["accepted"] is True
            assert client.get(path, headers=headers).json()["context"]["temperature_c"] == 6.5
            # D는 ApiError를 받아 수동 입력 화면으로 전환할 수 있어야 합니다.
            with pytest.raises(module.ApiError) as unavailable:
                await weather_api.get_weather(token, 0, 0)
            assert unavailable.value.status == 503 and unavailable.value.code == "WEATHER_UNAVAILABLE"
            manual = await recommendation.recommend(token, UserContext(user_id="me", temperature_c=20))
            manual_path = f"/api/v1/recommendations/{manual.recommendation_id}"
            assert client.get(manual_path, headers=headers).json()["context"]["temperature_c"] == 20
            automatic = client.post(
                "/api/v1/recommendations", headers=headers, json={"lat": 37.5665, "lon": 126.978}
            )
            assert automatic.status_code == 201 and automatic.json()["weather"]["source"] == "open_meteo"
            automatic_path = f"/api/v1/recommendations/{automatic.json()['recommendation_id']}"
            assert client.get(automatic_path, headers=headers).json() == automatic.json()
            fallback = client.post(
                "/api/v1/recommendations", headers=headers, json={"lat": 0, "lon": 0, "temperature_c": 0}
            )
            assert fallback.status_code == 201 and fallback.json()["weather"]["is_fallback"]
            assert fallback.json()["context"]["temperature_c"] == 0
            assert client.delete("/api/v1/auth/me", headers=headers).status_code == 204
            with pytest.raises(module.ApiError) as withdrawn:
                await auth.get_profile(token)
            assert withdrawn.value.status == 401

        asyncio.run(flow())
    finally:
        sys.path[:] = previous_path
        for name in list(sys.modules):
            if name == package_name or name.startswith(package_name + "."):
                del sys.modules[name]
