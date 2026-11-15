"""User-selected wearing role is independent of model-inferred garment shape."""

from uuid import UUID

import pytest

from backend.app.adapters.recommender import LocalRecommender, SafeRecommender
from backend.app.adapters.vision import SafeVision
from backend.app.db.models import Garment
from backend.app.deps import get_recommender, get_vision
from backend.tests.api.test_body_photo import PATH, picture
from backend.tests.api.test_recommendations import recommend, use_engine
from shared.contracts import GarmentAttributes, VisionResult


class ShirtVision:
    name = "shirt-test"
    shape = "shirt"
    fail = False

    def extract(self, image_path, garment_id):
        if self.fail:
            raise RuntimeError("test analysis unavailable")
        return VisionResult(
            success=True,
            attributes=GarmentAttributes(
                garment_id=garment_id,
                category="top",
                subcategory=self.shape,
                color="white",
                thermal_level=2,
            ),
        )


def upload(client, headers, **data):
    response = client.post(
        "/api/v1/garments",
        headers=headers,
        data=data,
        files={"file": ("synthetic.png", picture(), "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_selected_role_survives_initial_analysis_retry_and_manual_changes(
    api_client,
    auth_account,
    account_factory,
    db_session,
):
    vision = ShirtVision()
    api_client.app.dependency_overrides[get_vision] = lambda: SafeVision(vision, 2)
    h = auth_account["headers"]
    g = upload(api_client, h, wearing_role="outerwear")
    path = f"/api/v1/garments/{g['garment_id']}"
    assert g["wearing_role"] == g["category"] == "outerwear"
    assert g["garment_type"] == g["subcategory"] == "shirt"
    assert g["role_source"] == "user"
    row = db_session.get(Garment, UUID(g["garment_id"]))
    assert row.attributes.user_edited_fields == ["category"]
    assert row.attributes.raw_output["result"]["attributes"]["category"] == "top"
    # Only the selected role is frozen; unedited shape can still improve.
    vision.shape = "cardigan"
    retry = api_client.post(path + "/reanalyze", headers=h).json()
    assert retry["wearing_role"] == "outerwear" and retry["garment_type"] == "cardigan"
    edited = api_client.patch(path, headers=h, json={"garment_type": "shirt"})
    assert edited.status_code == 200
    assert api_client.post(path + "/reanalyze", headers=h).json()["garment_type"] == "shirt"
    vision.fail = True
    failed = api_client.post(path + "/reanalyze", headers=h).json()
    assert failed["analysis_status"] == "failed" and failed["wearing_role"] == "outerwear"
    assert failed["garment_type"] == "shirt"
    first_failed = upload(api_client, h, wearing_role="outerwear", garment_type="shirt")
    assert first_failed["analysis_status"] == "failed" and first_failed["wearing_role"] == "outerwear"
    vision.fail = False
    assert api_client.post(path + "/reanalyze", headers=h).json()["analysis_status"] == "done"
    # Changing the role alone must not reset shape; legacy API still works.
    changed = api_client.patch(path, headers=h, json={"wearing_role": "top"}).json()
    assert changed["garment_type"] == "shirt" and changed["wearing_role"] == "top"
    api_client.patch(path, headers=h, json={"category": "outerwear"})
    assert api_client.post(path + "/reanalyze", headers=h).json()["wearing_role"] == "outerwear"
    other = account_factory()["headers"]
    for response in (
        api_client.get(path, headers=other),
        api_client.patch(path, headers=other, json={"wearing_role": "top"}),
        api_client.post(path + "/reanalyze", headers=other),
    ):
        assert response.status_code == 404
    assert (
        api_client.get("/api/v1/garments?category=outerwear", headers=h).json()[0]["wearing_role"]
        == "outerwear"
    )


def test_automatic_role_and_ambiguous_alias_validation(api_client, auth_account):
    api_client.app.dependency_overrides[get_vision] = lambda: SafeVision(ShirtVision(), 2)
    h = auth_account["headers"]
    g = upload(api_client, h)
    assert g["wearing_role"] == "top" and g["role_source"] == "model"
    path = f"/api/v1/garments/{g['garment_id']}"
    for fields in (
        {"wearing_role": None},
        {"wearing_role": "shirt"},
        {"category": "top", "wearing_role": "outerwear"},
        {"subcategory": "shirt", "garment_type": "jacket"},
        {"garment_type": "x" * 51},
    ):
        assert api_client.patch(path, headers=h, json=fields).status_code == 422
    assert (
        api_client.post(
            "/api/v1/garments",
            headers=h,
            data={"wearing_role": "shirt"},
            files={"file": ("synthetic.png", picture(), "image/png")},
        ).status_code
        == 422
    )


@pytest.mark.parametrize("provider", ["local", "fallback"])
def test_outer_shirt_recommendation_and_tryon_use_role_not_shape(
    api_client,
    auth_account,
    account_factory,
    provider,
):
    h = auth_account["headers"]
    ids = {}
    for role, shape in [("top", "t_shirt"), ("bottom", "jeans"), ("outerwear", "shirt")]:
        g = upload(api_client, h, wearing_role=role, garment_type=shape)
        ids[role] = g["garment_id"]
        api_client.patch(f"/api/v1/garments/{ids[role]}", headers=h, json={"thermal_level": 2})
    if provider == "local":
        api_client.app.dependency_overrides[get_recommender] = lambda: SafeRecommender(LocalRecommender(), 2)
    else:

        def fail(*args):
            raise RuntimeError("test provider unavailable")

        use_engine(api_client, fail)
    response = recommend(api_client, auth_account, temperature_c=18, required_garment_ids=[ids["outerwear"]])
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["provider"] == provider and data["outfits"]
    assert all(
        o["items"]["outerwear"] == ids["outerwear"] and o["items"]["top"] == ids["top"]
        for o in data["outfits"]
    )
    assert data["garments"][ids["outerwear"]]["garment_type"] == "shirt"
    assert api_client.patch("/api/v1/profile", headers=h, json={"consent_body_info": True}).status_code == 200
    assert (
        api_client.put(PATH, headers=h, files={"file": ("synthetic.png", picture(), "image/png")}).status_code
        == 200
    )
    assert (
        api_client.post(
            "/api/v1/tryon-results", headers=h, json={"items": {"top": ids["outerwear"]}}
        ).status_code
        == 404
    )
    created = api_client.post("/api/v1/tryon-results", headers=h, json={"items": ids})
    assert created.status_code == 201, created.text
    path = "/api/v1/tryon-results/" + created.json()["id"]
    other = account_factory()["headers"]
    assert api_client.get(path, headers=other).status_code == 404
    saved = api_client.put(
        path + "/image", headers=h, files={"file": ("synthetic.png", picture(), "image/png")}
    )
    assert saved.status_code == 200 and saved.json()["inputs_current"]
    assert (
        api_client.patch(
            f"/api/v1/garments/{ids['outerwear']}", headers=h, json={"wearing_role": "top"}
        ).status_code
        == 200
    )
    assert api_client.get(path, headers=h).json()["inputs_current"] is False
    assert api_client.get(path + "/image", headers=h).status_code == 200
