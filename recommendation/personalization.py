"""Small, bounded ranking adjustments. Never change eligibility or required garments."""

from shared.contracts import GarmentAttributes, UserContext
from shared.feedback import effective_reason_codes


def feedback_adjustment(context: UserContext, *items: GarmentAttributes | None) -> float:
    current = [g for g in items if g is not None]
    ids = {g.garment_id for g in current}
    seen = set()
    adjustments = []
    for event in context.recent_feedback:  # newest first; latest opinion for a combination wins
        codes = effective_reason_codes(event.reason_codes, event.reason, accepted=event.accepted)
        old = event.garments
        key = frozenset(g.garment_id for g in old)
        if not key or key in seen:
            continue
        seen.add(key)
        exact = ids == key
        if event.accepted:
            adjustments.append(6.0 if exact else 0.0)
            continue
        penalty = 15.0 if exact else 0.0
        pairs = [(g, p) for g in current for p in old if g.category == p.category]
        if pairs and "colors" in codes:
            similarities = []
            for g, p in pairs:
                a = set([g.color, *g.secondary_colors]) - {"multi", "unknown"}
                b = set([p.color, *p.secondary_colors]) - {"multi", "unknown"}
                similarities.append(len(a & b) / len(a | b) if a | b else 0)
            penalty = max(penalty, 10 * sum(similarities) / len(pairs))
        if pairs and "style" in codes:
            similarity = sum(bool(set(g.style_candidates) & set(p.style_candidates)) for g, p in pairs) / len(
                pairs
            )
            penalty = max(penalty, 10 * similarity)
        thermal = [
            (g.thermal_level, p.thermal_level) for g, p in pairs if g.thermal_level and p.thermal_level
        ]
        if thermal:
            if "too_warm" in codes and "too_cold" not in codes:
                penalty = max(penalty, 8 * sum(a >= b for a, b in thermal) / len(thermal))
            if "too_cold" in codes and "too_warm" not in codes:
                penalty = max(penalty, 8 * sum(a <= b for a, b in thermal) / len(thermal))
        adjustments.append(-penalty)
    # Repeated feedback cannot overwhelm weather, schedule or style scoring.
    return round(max(-15.0, min(6.0, sum(adjustments))), 2)
