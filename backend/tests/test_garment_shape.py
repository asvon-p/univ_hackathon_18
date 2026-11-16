from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from shared.garment_layers import refined_layer
from shared.garment_shape import shape_hints


@pytest.mark.parametrize(
    "role,kind,expected",
    [
        ("bottom", "long pants", "long"),
        ("bottom", "denim shorts", "short"),
        ("bottom", "긴바지", "long"),
        ("bottom", "반바지", "short"),
        ("bottom", "cropped pants", "unknown"),
    ],
)
def test_explicit_length_wins_over_square_photo(role, kind, expected):
    assert shape_hints(Image.new("RGB", (100, 100)), role, kind)["bottom_length"] == expected


def test_real_silhouette_not_square_upload_frame_determines_length():
    for hem, expected in [(95, "long"), (43, "short")]:
        im = Image.new("RGBA", (100, 100))
        ImageDraw.Draw(im).rectangle((25, 5, 75, hem), fill=(50, 60, 100, 255))
        assert shape_hints(im, "bottom", "pants")["bottom_length"] == expected


def test_regular_outer_shirt_is_not_mistaken_for_a_long_coat():
    im = Image.new("RGBA", (200, 300))
    ImageDraw.Draw(im).rectangle((20, 30, 180, 249), fill=(30, 60, 100, 255))
    assert shape_hints(im, "outerwear", "shirt")["outerwear_length"] == "regular"


@pytest.mark.parametrize("kind", ["long_coat", "롱패딩", "trench_coat"])
def test_coat_metadata_survives_layer_image(kind):
    image = Image.new("RGBA", (100, 200), (60, 70, 80, 255))
    data = refined_layer(image, wearing_role="outerwear", garment_type=kind)
    with Image.open(BytesIO(data)) as layer:
        assert layer.info["fitjang_wearing_role"] == "outerwear"
        assert layer.info["fitjang_outerwear_length"] == "long"
