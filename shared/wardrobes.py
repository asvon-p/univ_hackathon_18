"""Logical wardrobe partitions; ownership remains attached to every garment."""

from typing import Literal

WardrobeScope = Literal["all", "personal", "example"]


def in_wardrobe(garment, wardrobe: WardrobeScope) -> bool:
    return wardrobe == "all" or bool(garment.is_example) == (wardrobe == "example")


def default_wardrobe(garments) -> str:
    return "personal" if any(not g.is_example for g in garments) else "example"


def single_wardrobe(garments) -> Literal["personal", "example"] | None:
    """Classify a complete owned selection; empty/mixed selections have no scope."""
    scopes = {"example" if g.is_example else "personal" for g in garments}
    return next(iter(scopes)) if len(scopes) == 1 else None
