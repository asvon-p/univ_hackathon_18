import numpy as np
import pytest

from vision.attributes import GROUPS, PROMPTS, infer_attributes


def scores_for(**winners):
    return np.array(
        [
            7.0 if label == winners.get(group) else 0.0
            for group, labels in GROUPS.items()
            for label in labels
            for _ in range(2)
        ]
    )


@pytest.mark.parametrize("pattern", GROUPS["pattern"])
def test_patterns_use_contract_labels(pattern):
    result, _ = infer_attributes(scores_for(pattern=pattern), "shirt", [7, 0, 0])
    assert result["pattern"] == pattern


def test_ambiguous_predictions_abstain():
    result, denim = infer_attributes(np.zeros(len(PROMPTS)), "shirt", np.zeros(22))
    assert result == {"pattern": "unknown", "style_candidates": [], "thermal_level": None}
    assert denim is False


@pytest.mark.parametrize(
    "subcategory,thickness,expected",
    [
        ("shirt", "thin", 1),
        ("shirt", "medium", 2),
        ("shirt", "thick", 3),
        ("t-shirt", "thin", 1),
        ("padding", "thick", 5),
        ("unknown", "thick", None),
    ],
)
def test_warmth_uses_visual_thickness_and_type_with_limits(subcategory, thickness, expected):
    result, _ = infer_attributes(scores_for(thickness=thickness), subcategory, [7, 0, 0])
    assert result["thermal_level"] == expected


def test_style_candidates_are_ranked_limited_and_material_is_separate():
    scores = scores_for(material="denim")
    start = len(GROUPS["pattern"]) * 2
    scores[start : start + 8] = 4
    result, denim = infer_attributes(scores, "jeans", [7, 0, 0])
    assert 1 <= len(result["style_candidates"]) <= 3
    assert len(result["style_candidates"]) == len(set(result["style_candidates"]))
    assert denim is True


@pytest.mark.parametrize("bad", [np.zeros(3), np.full(len(PROMPTS), np.nan)])
def test_invalid_model_output_is_rejected(bad):
    with pytest.raises(ValueError):
        infer_attributes(bad, "shirt", [7, 0])
