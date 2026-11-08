import json
from uuid import UUID

import httpx
import pytest

from backend.app.adapters.openai_recommender import LocalFallback, OpenAIRecommender
from backend.app.adapters.recommender import LocalRecommender, MockRecommender, SafeRecommender
from backend.app.core.config import Settings
from backend.app.db.models import Garment, Recommendation, UserProfile
from backend.app.deps import get_recommender
from shared.contracts import GarmentAttributes, UserContext


def test_frontend_signup_immediate_login_and_profile_alias(api_client, account_factory, db_session):
    created = api_client.post(
        "/api/v1/auth/signup",
        json={
            "email": "frontend@example.com",
            "password": "password-123",
        },
    )
    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store"
    headers = {"Authorization": f"Bearer {created.json()['access_token']}"}
    assert api_client.get("/api/v1/auth/me", headers=headers).json()["id"] == created.json()["id"]
    path = "/api/v1/users/me/profile"
    values = {"height_cm": 172.5, "weight_kg": 60.5, "age": 24, "gender": "other"}
    denied = api_client.put(path, headers=headers, json=values)
    assert denied.status_code == 422 and denied.json()["code"] == "BODY_INFO_CONSENT_REQUIRED"
    saved = api_client.put(path, headers=headers, json={**values, "consent_body_info": True})
    assert saved.status_code == 200, saved.text
    assert all(saved.json()[key] == value for key, value in values.items())
    assert saved.json()["birth_year"] is None  # 나이에서 출생연도를 추정하지 않음
    db_session.expunge_all()
    assert api_client.get(path, headers=headers).json() == saved.json()
    assert api_client.get("/api/v1/profile", headers=headers).json() == saved.json()
    other = account_factory()
    assert api_client.get(path, headers=other["headers"]).json()["height_cm"] is None
    assert api_client.put(path, headers=headers, json={"consent_body_info": False}).status_code == 200
    assert db_session.get(UserProfile, UUID(created.json()["id"])).age is None


def test_frontend_metadata_image_and_failed_category_preserve_ownership(
    api_client,
    auth_account,
    account_factory,
    add_garment,
    db_session,
):
    gid = add_garment(auth_account, "top", analysis_status="failed")
    garment = db_session.get(Garment, UUID(gid))
    garment.attributes.category = None
    garment.cutout_path = None
    db_session.commit()
    path = f"/api/v1/garments/{gid}"
    response = api_client.patch(
        path,
        headers=auth_account["headers"],
        json={
            "name": "흰 셔츠",
            "location": "기숙사 옷장",
        },
    )
    assert response.status_code == 200
    assert response.json()["analysis_status"] == "failed"
    assert GarmentAttributes.model_validate(response.json()).category is None
    assert not {"name", "location"} & set(garment.attributes.user_edited_fields)
    image_path = path + "/image?kind=cutout"
    image = api_client.get(image_path, headers=auth_account["headers"])
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
    assert image.headers["cache-control"] == "private, no-store"
    assert api_client.get(image_path).status_code == 401
    other = account_factory()
    assert api_client.get(image_path, headers=other["headers"]).status_code == 404
    assert api_client.patch(path, headers=other["headers"], json={"name": "hacked"}).status_code == 404
    retried = api_client.post(path + "/reanalyze", headers=auth_account["headers"])
    assert retried.status_code == 200
    assert retried.json()["name"] == "흰 셔츠" and retried.json()["location"] == "기숙사 옷장"


@pytest.mark.parametrize("provider", ["mock", "local", "openai", "openai-fallback"])
def test_frontend_three_slot_request_persists_and_is_user_scoped(
    api_client,
    auth_account,
    account_factory,
    add_garment,
    db_session,
    provider,
):
    top = add_garment(auth_account, "top")
    bottom = add_garment(auth_account, "bottom")
    observed = []

    def remote(request):
        data = json.loads(json.loads(request.content)["input"])
        observed.append(data)
        if provider == "openai-fallback":
            return httpx.Response(429)
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "outfits": [
                                            {"candidate_id": 0, "reason": "상의와 하의의 색상이 어울려요."}
                                        ],
                                    }
                                ),
                            }
                        ],
                    }
                ],
            },
        )

    if provider.startswith("openai"):
        engine = OpenAIRecommender(
            Settings(_env_file=None, OPENAI_API_KEY="test-key"), transport=httpx.MockTransport(remote)
        )
    else:
        engine = MockRecommender() if provider == "mock" else LocalRecommender()
    api_client.app.dependency_overrides[get_recommender] = lambda: SafeRecommender(engine, 1, LocalFallback())
    payload = UserContext(user_id="me", temperature_c=18, preferred_styles=["꾸안꾸"]).model_dump()
    response = api_client.post("/api/v1/recommendations", headers=auth_account["headers"], json=payload)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["provider"] == ("fallback" if provider == "openai-fallback" else provider)
    assert body["context"]["user_id"] == auth_account["id"]
    assert body["outfits"][0]["items"] == {"top": top, "bottom": bottom, "outerwear": None, "shoes": None}
    record = db_session.get(Recommendation, UUID(body["recommendation_id"]))
    assert record.outfits[0].shoes_id is None
    path = f"/api/v1/recommendations/{body['recommendation_id']}"
    assert api_client.get(path, headers=auth_account["headers"]).json() == body
    other = account_factory()
    assert api_client.get(path, headers=other["headers"]).status_code == 404
    assert (
        api_client.post("/api/v1/recommendations", headers=other["headers"], json=payload).status_code == 422
    )
    if observed:
        assert len(observed) == 1 and all("shoes" not in row["items"] for row in observed[0]["candidates"])
    spoofed = {**payload, "user_id": other["id"]}
    assert (
        api_client.post("/api/v1/recommendations", headers=auth_account["headers"], json=spoofed).status_code
        == 422
    )
    spoofed = {**payload, "recent_feedback": [{"accepted": True}]}
    assert (
        api_client.post("/api/v1/recommendations", headers=auth_account["headers"], json=spoofed).status_code
        == 422
    )
