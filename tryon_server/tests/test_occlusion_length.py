"""Synthetic geometry/textures, no personal photos or GPU model downloads."""

import numpy as np
import pytest
from PIL import Image, ImageDraw

from tryon_server.garment_length import check_bottom_length
from tryon_server.layering import guided_inner_top, preserve_unedited_regions


@pytest.mark.parametrize("hem", [170, 270])
def test_connected_inner_print_cannot_expand_over_outer_panels_or_long_coat(hem):
    base = Image.new("RGB", (200, 300), (30, 50, 130))
    ImageDraw.Draw(base).rectangle((30, 70, 170, 170), fill=(240, 90, 20))
    generated = Image.new("RGB", base.size, (60, 70, 80))
    draw = ImageDraw.Draw(generated)
    # White/graphic-looking connected stripe extending well outside the opening.
    draw.rectangle((93, 70, 107, hem), fill="white")
    draw.rectangle((62, 100, 138, 115), fill="white")
    labels = np.zeros((300, 200), dtype="uint8")
    labels[70:171, 30:171] = 3
    labels[171:290, 60:140] = 6
    out_labels = labels.copy()
    out_labels[65 : hem + 1, 30:171] = 3
    coat = Image.new("RGBA", base.size, (60, 70, 80, 255))
    result, info = guided_inner_top(
        base, generated, coat, labels, out_labels, [[0.46, 0], [0.54, 0], [0.56, 1], [0.44, 1]]
    )
    assert info["opening_bounded"]
    assert result.getpixel((100, 130)) == base.getpixel((100, 130))
    assert result.getpixel((70, 108)) == generated.getpixel((70, 108))
    if hem > 200:
        assert result.getpixel((100, 240)) == base.getpixel((100, 240))
        assert result.getpixel((50, 240)) == generated.getpixel((50, 240))
    else:
        assert result.getpixel((100, 240)) == base.getpixel((100, 240))


def test_top_cannot_turn_completed_jeans_into_shorts():
    before = Image.new("RGB", (120, 240), (20, 40, 100))
    generated = Image.new("RGB", before.size, (180, 120, 90))
    old = np.zeros((240, 120), dtype="uint8")
    old[35:100, 30:90] = 3
    old[100:215, 35:85] = 6
    new = old.copy()
    new[135:215, 35:85] = 14  # later top inference hallucinates bare calves
    result = preserve_unedited_regions(before, generated, old, new, "tops")
    assert result.getpixel((50, 190)) == before.getpixel((50, 190))
    assert result.getpixel((50, 60)) == generated.getpixel((50, 60))


def test_closed_lower_coat_is_not_punched_out_by_product_projection():
    before = Image.new("RGB", (200, 300), (25, 40, 110))
    generated = Image.new("RGB", before.size, (150, 120, 90))
    ImageDraw.Draw(generated).rectangle((90, 70, 110, 165), fill="white")
    old = np.zeros((300, 200), dtype="uint8")
    old[70:171, 30:171] = 3
    old[171:290, 60:140] = 6
    new = old.copy()
    new[65:275, 30:171] = 3
    coat = Image.new("RGBA", before.size, (150, 120, 90, 255))
    result, info = guided_inner_top(
        before, generated, coat, old, new, [[0.4, 0], [0.6, 0], [0.65, 1], [0.35, 1]]
    )
    assert info["contour_aligned"]
    assert result.getpixel((100, 130)) == before.getpixel((100, 130))
    assert result.getpixel((100, 240)) == generated.getpixel((100, 240))


def test_long_outerwear_can_occlude_legs_but_not_outside_its_silhouette():
    before = Image.new("RGB", (120, 240), (20, 40, 100))
    generated = Image.new("RGB", before.size, (60, 70, 80))
    old = np.zeros((240, 120), dtype="uint8")
    old[35:100, 30:90] = 3
    old[100:225, 30:90] = 6
    new = old.copy()
    new[35:195, 25:95] = 4
    result = preserve_unedited_regions(before, generated, old, new, "tops")
    assert result.getpixel((40, 175)) == generated.getpixel((40, 175))
    assert result.getpixel((40, 215)) == before.getpixel((40, 215))


def pose():
    points = np.zeros((18, 2))
    for ids, x in [((8, 9, 10), 0.35), ((11, 12, 13), 0.65)]:
        points[list(ids)] = [(x, 0.35), (x, 0.60), (x, 0.90)]
    return {"bodies": {"candidate": points, "subset": np.arange(18)[None, :]}}


@pytest.mark.parametrize(
    "expected,hem,status",
    [
        ("long", 92, "matched"),
        ("long", 55, "mismatch"),
        ("short", 50, "matched"),
        ("short", 92, "mismatch"),
    ],
)
def test_length_is_validated_on_both_legs(expected, hem, status):
    labels = np.zeros((100, 100), dtype="uint8")
    labels[30:hem, 28:43] = 6
    labels[30:hem, 58:73] = 6
    assert check_bottom_length(labels, pose(), expected)["status"] == status


def test_missing_pose_never_claims_length_was_verified():
    assert check_bottom_length(np.zeros((100, 100)), {}, "long")["status"] == "unverified"
    incomplete = pose()
    incomplete["bodies"]["subset"][0, 10] = -1
    assert check_bottom_length(np.zeros((100, 100)), incomplete, "short")["status"] == "unverified"
