from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from shared.garment_layers import refined_layer, restore_source_rgb
from shared.outerwear_opening import detect_opening


def jacket(*, opened=True, lining=False, light=False, neck_only=False):
    image = Image.new("RGBA", (240, 320))
    draw = ImageDraw.Draw(image)
    color = (225, 220, 210, 255) if light else (45, 85, 55, 255)
    draw.rectangle((25, 20, 215, 300), fill=color)
    if opened or neck_only:
        points = [
            (105, 40),
            (135, 40),
            (150, 280 if not neck_only else 90),
            (90, 280 if not neck_only else 90),
        ]
        draw.polygon(points, fill=(180, 120, 100, 255) if lining else (255, 255, 255, 0))
    return image


@pytest.mark.parametrize("lining,light", [(False, False), (False, True), (True, False)])
def test_long_opening_is_automatic_without_removing_panels(lining, light):
    im = jacket(lining=lining, light=light)
    original = im.tobytes()
    result = detect_opening(im)
    assert result["status"] == "detected" and len(result["points"]) == 4
    png = Image.open(BytesIO(refined_layer(im, result["points"], opening_mode="auto")))
    assert png.getpixel((120, 160))[3] == 0
    assert png.getpixel((60, 160))[3] == 255
    assert png.info["fitjang_opening_mode"] == "auto"
    assert im.tobytes() == original


@pytest.mark.parametrize("mode", ["closed", "neck", "logo", "blank"])
def test_uncertain_or_closed_coats_are_not_cut(mode):
    im = jacket(opened=False, neck_only=mode == "neck")
    if mode == "logo":
        ImageDraw.Draw(im).rectangle((100, 120, 140, 150), fill="white")
    if mode == "blank":
        im = Image.new("RGBA", im.size)
    result = detect_opening(im)
    assert result["status"] == "needs_review" and result["points"] == []


def test_detection_coordinates_are_resolution_independent():
    first = detect_opening(jacket())
    second = detect_opening(jacket().resize((960, 1280)))
    assert second["status"] == "detected"
    assert all(
        abs(a - b) < 0.02
        for p, q in zip(first["points"], second["points"], strict=True)
        for a, b in zip(p, q, strict=True)
    )


def soft_open_shirt():
    """A long empty gap with the segmenter's varying, premultiplied soft alpha."""
    import numpy as np

    original = jacket(opened=False).convert("RGB")
    ImageDraw.Draw(original).rectangle((108, 40, 132, 290), fill="white")
    cutout = jacket(opened=False)
    rgba = np.asarray(cutout).copy()
    for y in range(40, 291):
        alpha = round(230 - (y - 40) / 250 * 190)
        rgba[y, 108:133] = (alpha, alpha, alpha, alpha)
    return original, Image.fromarray(rgba)


def test_soft_open_shirt_recovers_original_rgb_and_detects_continuous_gap():
    original, cutout = soft_open_shirt()
    before = cutout.tobytes(), original.tobytes()
    aligned = restore_source_rgb(cutout, original)
    assert aligned.getpixel((120, 150))[:3] == (255, 255, 255)
    assert aligned.getchannel("A").tobytes() == cutout.getchannel("A").tobytes()
    opening = detect_opening(aligned)
    assert opening["status"] == "detected"
    output = Image.open(BytesIO(refined_layer(aligned, opening["points"], opening_mode="auto")))
    assert output.getpixel((120, 160))[3] == 0
    assert output.getpixel((65, 160)) == aligned.getpixel((65, 160))
    assert (cutout.tobytes(), original.tobytes()) == before


def test_refinement_never_spreads_fabric_across_wide_soft_hole():
    image = jacket(opened=False)
    ImageDraw.Draw(image).rectangle((95, 40, 145, 280), fill=(240, 240, 240, 128))
    result = Image.open(BytesIO(refined_layer(image)))
    assert result.getpixel((120, 160)) == (240, 240, 240, 128)
    assert result.getpixel((60, 160)) == image.getpixel((60, 160))


def test_rgb_repair_requires_matching_dimensions_and_opaque_fabric():
    source, cutout = soft_open_shirt()
    for other in (source.resize((100, 100)), Image.new("RGB", source.size, "red")):
        assert restore_source_rgb(cutout, other).tobytes() == cutout.tobytes()
