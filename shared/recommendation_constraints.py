"""Shared, deterministic requirements. Providers cannot relax these constraints."""

from shared.contracts import RECOMMENDATION_CATEGORIES, GarmentAttributes, UserContext


def required_by_category(
    context: UserContext, garments: list[GarmentAttributes]
) -> dict[str, GarmentAttributes]:
    ids = context.required_garment_ids
    if len(ids) > 3 or len(ids) != len(set(ids)) or set(ids) & set(context.excluded_items):
        raise ValueError("Conflicting required garments")
    usable = {
        g.garment_id: g
        for g in garments
        if g.status == "available"
        and g.category in RECOMMENDATION_CATEGORIES
        and g.garment_id not in context.excluded_items
    }
    selected = {}
    for gid in ids:
        garment = usable.get(gid)
        if garment is None or garment.category in selected:
            raise ValueError("Required garment unavailable or duplicate category")
        selected[garment.category] = garment
    return selected
