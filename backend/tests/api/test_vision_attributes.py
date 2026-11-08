from uuid import UUID

import pytest

from backend.app.adapters.vision import LocalVision, SafeVision
from backend.app.db.models import Garment
from backend.app.deps import get_vision
from backend.tests.api.test_garments import image_bytes
from vision import pipeline
from vision.attributes import GROUPS, infer_attributes


def test_auto_attributes_persist_and_reanalysis_preserves_manual_fields(
    api_client, auth_account, account_factory, db_session, monkeypatch
):
    def predict(*args):
        scores = [
            8 if label == {"pattern": "check", "style": "casual", "thickness": "thick"}.get(group) else 0
            for group, labels in GROUPS.items()
            for label in labels
            for _ in range(2)
        ]
        attrs, denim = infer_attributes(scores, "shirt", [8, 0, 0])
        return "top", "shirt", "test-scores", attrs, denim

    monkeypatch.setattr(pipeline, "_predict", predict)
    api_client.app.dependency_overrides[get_vision] = lambda: SafeVision(
        LocalVision(classifier="demo", segmentation="off"), 10
    )
    headers = auth_account["headers"]
    foreign = account_factory()["headers"]
    uploaded = api_client.post(
        "/api/v1/garments", headers=headers, files={"file": ("shirt.png", image_bytes(), "image/png")}
    )
    assert uploaded.status_code == 201
    row = uploaded.json()
    path = "/api/v1/garments/" + row["garment_id"]
    expected = {"pattern": "check", "style_candidates": ["casual"], "thermal_level": 3, "color": "navy"}
    assert {k: row[k] for k in expected} == expected
    db_session.expunge_all()
    stored = db_session.get(Garment, UUID(row["garment_id"])).attributes
    assert stored.raw_output["result"]["attributes"]["pattern"] == "check"
    assert stored.pattern == "check" and stored.thermal_level == 3 and stored.source == "model"
    assert api_client.get(path, headers=foreign).status_code == 404
    assert api_client.post(path + "/reanalyze", headers=foreign).status_code == 404
    assert api_client.patch(path, headers=foreign, json={"pattern": "dot"}).status_code == 404
    assert api_client.get("/api/v1/garments", headers=foreign).json() == []
    edits = {"pattern": "solid", "style_candidates": [], "thermal_level": None, "color": "olive"}
    assert api_client.patch(path, headers=headers, json=edits).status_code == 200
    result = api_client.post(path + "/reanalyze", headers=headers).json()
    assert {k: result[k] for k in edits} == edits
    assert result["source"] == "user" and result["analysis_status"] == "done"
    assert {k: api_client.get(path, headers=headers).json()[k] for k in edits} == edits


@pytest.mark.parametrize("thermal", [0, 6])
def test_out_of_range_warmth_is_rejected(garment_client, thermal):
    row = garment_client.post(
        "/api/v1/garments", files={"file": ("shirt.png", image_bytes(), "image/png")}
    ).json()
    assert (
        garment_client.patch(
            "/api/v1/garments/" + row["garment_id"], json={"thermal_level": thermal}
        ).status_code
        == 422
    )
