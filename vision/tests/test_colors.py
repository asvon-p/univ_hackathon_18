import numpy as np
import pytest
from PIL import Image, ImageDraw

from vision.colors import dominant_color


@pytest.mark.parametrize(
    "rgb,expected",
    [
        ((18, 18, 18), "black"),
        ((218, 220, 219), "white"),
        ((90, 90, 94), "gray"),
        ((234, 226, 207), "ivory"),
        ((196, 174, 139), "beige"),
        ((114, 80, 43), "brown"),
        ((177, 128, 72), "camel"),
        ((36, 49, 74), "navy"),
        ((0, 0, 128), "navy"),
        ((30, 80, 210), "blue"),
        ((151, 202, 235), "sky_blue"),
        ((57, 132, 68), "green"),
        ((92, 102, 62), "olive"),
        ((218, 25, 25), "red"),
        ((110, 24, 38), "burgundy"),
        ((230, 161, 184), "pink"),
        ((127, 47, 171), "purple"),
        ((240, 210, 25), "yellow"),
        ((239, 121, 25), "orange"),
    ],
)
def test_palette_foreground_not_background(rgb, expected):
    image = Image.new("RGB", (256, 256), "magenta")
    cutout = Image.new("RGBA", image.size)
    ImageDraw.Draw(cutout).rectangle((80, 60, 176, 220), fill=(*rgb, 255))
    before = cutout.tobytes()
    assert dominant_color(image, cutout) == expected
    assert cutout.tobytes() == before


def test_white_folds_do_not_become_gray():
    values = np.linspace(145, 240, 240).astype(np.uint8)
    image = Image.fromarray(np.repeat(np.tile(values, (240, 1))[..., None], 3, axis=2))
    assert dominant_color(image, image.convert("RGBA")) == "white"


def test_denim_material_only_changes_blue_family():
    blue = Image.new("RGB", (100, 100), (34, 63, 97))
    red = Image.new("RGB", (100, 100), (210, 30, 30))
    assert dominant_color(blue, None, denim=True) == "denim"
    assert dominant_color(blue, None) == "navy"
    assert dominant_color(red, None, denim=True) == "red"


def test_multiple_colors_and_small_trim_are_distinguished():
    image = Image.new("RGB", (100, 100), "red")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 10, 99), fill="blue")
    assert dominant_color(image, image.convert("RGBA")) == "red"
    draw.rectangle((0, 0, 49, 99), fill="blue")
    assert dominant_color(image, image.convert("RGBA")) == "multi"


def test_transparent_or_too_small_foreground_is_unknown():
    cutout = Image.new("RGBA", (100, 100))
    assert dominant_color(Image.new("RGB", (100, 100), "blue"), cutout) == "unknown"
