"""Conservative zero-shot garment attributes; scores are not calibrated confidence.

Thermal level is a visual estimate for outfit ranking, not measured insulation.
No person attributes are inferred. Uncertain fields remain editable unknowns.
"""

from __future__ import annotations

import numpy as np

# Two descriptions per label reduce dependence on one particular wording.
GROUPS = {
    "pattern": {
        "solid": ("plain clothing", "solid-colored clothing"),
        "stripe": ("striped print clothing", "clothing with contrasting colored stripes"),
        "check": ("plaid clothing", "clothing with a checked pattern"),
        "dot": ("polka dot clothing", "clothing with a polka dot pattern"),
        "floral": ("floral print clothing", "clothing with a flower pattern"),
        "graphic": ("graphic print clothing", "clothing with a printed picture or slogan"),
        "other": ("animal print clothing", "clothing with a camouflage pattern"),
    },
    "style": {
        "casual": ("casual everyday clothing", "relaxed casual everyday fashion"),
        "minimal": (
            "minimalist clothing with clean simple lines",
            "understated minimal fashion without decoration",
        ),
        "street": ("urban streetwear clothing", "oversized hip hop street fashion"),
        "formal": ("formal business office clothing", "tailored professional dress clothing"),
        "dandy": ("smart casual preppy clothing", "neat dressy preppy fashion"),
        "sporty": ("athletic sportswear clothing", "sporty activewear for exercise"),
        "vintage": ("retro vintage clothing", "nostalgic old fashioned vintage fashion"),
        "feminine": (
            "delicate romantic clothing with ruffles or lace",
            "soft elegant floral feminine fashion",
        ),
        "amekaji": ("American heritage workwear clothing", "rugged Americana denim or military workwear"),
    },
    "thickness": {
        "thin": ("thin lightweight summer fabric", "light airy unlined clothing"),
        "medium": ("medium weight everyday fabric", "regular weight cotton clothing"),
        "thick": ("thick heavy insulated winter fabric", "warm bulky wool fleece or padded clothing"),
    },
    "material": {
        "denim": ("blue denim fabric with visible jeans stitching", "indigo washed denim clothing"),
        "cotton": ("smooth cotton fabric", "cotton shirt or sweatshirt fabric"),
        "wool": ("knitted wool fabric", "wool knit clothing"),
        "synthetic": ("nylon polyester fabric", "synthetic windproof clothing"),
        "leather": ("leather fabric", "smooth leather clothing"),
    },
}
PROMPTS = tuple(
    f"a product photo of {caption}"
    for group in GROUPS.values()
    for pair in group.values()
    for caption in pair
)

THERMAL_BASE = {
    "t-shirt": 1,
    "shirt": 2,
    "blouse": 1,
    "hoodie": 3,
    "sweater": 4,
    "sweatshirt": 3,
    "knit_vest": 3,
    "jeans": 3,
    "slacks": 2,
    "cotton_pants": 2,
    "shorts": 1,
    "skirt": 2,
    "jogger": 3,
    "coat": 4,
    "padding": 5,
    "jacket": 3,
    "cardigan": 3,
    "blazer": 3,
    "windbreaker": 2,
}


def probabilities(scores) -> np.ndarray:
    scores = np.asarray(scores, dtype=np.float64)
    if scores.size == 0 or not np.isfinite(scores).all():
        raise ValueError("invalid classifier scores")
    weights = np.exp(scores - scores.max())
    return weights / weights.sum()


def confident_label(labels, scores, minimum=0.45, margin=0.12):
    """Abstention gates; these relative scores are NOT an accuracy guarantee."""
    probs = probabilities(scores)
    order = np.argsort(-probs)
    if probs[order[0]] < minimum or probs[order[0]] - probs[order[1]] < margin:
        return None
    return list(labels)[order[0]]


def grouped_scores(scores) -> dict[str, np.ndarray]:
    scores = np.asarray(scores, dtype=np.float64)
    if scores.shape != (len(PROMPTS),) or not np.isfinite(scores).all():
        raise ValueError("invalid attribute scores")
    result, offset = {}, 0
    for name, labels in GROUPS.items():
        count = len(labels) * 2
        result[name] = scores[offset : offset + count].reshape(-1, 2).mean(axis=1)
        offset += count
    return result


def infer_attributes(scores, subcategory: str, category_scores) -> tuple[dict, bool]:
    groups = grouped_scores(scores)
    pattern = confident_label(GROUPS["pattern"], groups["pattern"]) or "unknown"
    if pattern not in {"solid", "unknown"}:
        # Knit ribs and seams can resemble prints; require stronger evidence.
        pattern = confident_label(GROUPS["pattern"], groups["pattern"], 0.60, 0.20) or "unknown"
    style_probs = probabilities(groups["style"])
    order = np.argsort(-style_probs)
    styles = []
    if style_probs[order[0]] >= 0.24:
        styles = [
            list(GROUPS["style"])[i]
            for i in order[:3]
            if style_probs[i] >= max(0.16, style_probs[order[0]] * 0.55)
        ]
    thermal = None
    # Closely related subtypes can disagree while sharing the same warmth level.
    category_probs = probabilities(category_scores)
    if category_probs.max() >= 0.20 and subcategory in THERMAL_BASE:
        thermal = THERMAL_BASE[subcategory]
        thickness = confident_label(GROUPS["thickness"], groups["thickness"], 0.65, 0.25)
        thermal = max(1, min(5, thermal + {"thin": -1, "thick": 1}.get(thickness, 0)))
    material = confident_label(GROUPS["material"], groups["material"], 0.60, 0.25)
    return {"pattern": pattern, "style_candidates": styles, "thermal_level": thermal}, material == "denim"
