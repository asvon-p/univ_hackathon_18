"""계약 테스트: 모든 PR 에서 실행됩니다.
A·B 가 함수 결과 형식을 바꾸면 여기서 먼저 실패합니다."""

import json
from pathlib import Path
from typing import get_args

import recommendation
import vision
from shared.contracts import (
    CONTRACT_VERSION,
    Color,
    FeedbackEvent,
    GarmentAttributes,
    RecommendationResult,
    StyleTag,
    UserContext,
    VisionResult,
)

ROOT = Path(__file__).resolve().parents[3]


def _mock_garments() -> list[GarmentAttributes]:
    return [
        GarmentAttributes(**g)
        for g in json.loads((ROOT / "mocks" / "garments.json").read_text(encoding="utf-8"))
    ]


def test_contract_version_format():
    major, minor = CONTRACT_VERSION.split(".")
    assert major.isdigit() and minor.isdigit()


def test_mock_garments_follow_contract():
    garments = _mock_garments()
    assert {g.category for g in garments} >= {"top", "bottom", "shoes"}
    for g in garments:
        assert g.color in get_args(Color)
        assert set(g.style_candidates) <= set(get_args(StyleTag))


def test_vision_extract_returns_vision_result(tmp_path):
    img = tmp_path / "sample.jpg"
    img.write_bytes(b"not-a-real-image")
    result = vision.extract(str(img), "G-test")
    assert isinstance(result, VisionResult)
    if result.success:
        assert result.attributes is not None
        assert result.attributes.garment_id == "G-test"


def test_recommend_returns_only_given_available_items():
    garments = _mock_garments()
    usable = {g.garment_id for g in garments if g.status == "available"}
    result = recommendation.recommend(UserContext(user_id="U1", temperature_c=5), garments)
    assert isinstance(result, RecommendationResult)
    assert len(result.outfits) <= 3
    for o in result.outfits:
        for gid in o.items.model_dump().values():
            assert gid is None or gid in usable


def test_context_11_preserves_legacy_inputs_and_supports_feedback():
    legacy = UserContext.model_validate({"user_id": "U1", "temperature_c": 5})
    assert legacy.recent_feedback == []
    legacy.recent_feedback.append(
        FeedbackEvent(recommendation_id="R1", outfit_id="O1", accepted=False, reason="색상이 취향과 다릅니다")
    )
    assert UserContext(user_id="U2", temperature_c=5).recent_feedback == []
    restored = UserContext.model_validate_json(legacy.model_dump_json())
    assert restored.recent_feedback[0].accepted is False
    assert isinstance(recommendation.recommend(restored, _mock_garments()), RecommendationResult)
