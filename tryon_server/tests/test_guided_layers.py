import pytest
from PIL import Image


@pytest.mark.parametrize("explicit", [True, False])
def test_guided_opening_keeps_top_and_jacket_even_same_color(explicit):
    import numpy as np

    from tryon_server.layering import guided_inner_top

    before = Image.new("RGB", (200, 300), (180, 100, 30))
    generated = Image.new("RGB", (200, 300), (40, 90, 50))
    labels = np.zeros((300, 200), dtype="uint8")
    labels[70:220, 30:170] = 3
    labels[20:60, 70:130] = 1
    coat = Image.new("RGBA", (200, 300), (40, 90, 50, 255))
    points = [[0.4, 0], [0.6, 0], [0.65, 1], [0.35, 1]] if explicit else []
    result, status = guided_inner_top(before, generated, coat, labels, labels, points)
    assert status["status"] == ("applied" if explicit else "approximate")
    expected = before if explicit else generated
    assert result.getpixel((100, 140)) == expected.getpixel((100, 140))
    if not explicit:
        assert status["reason"] == "opening_unconfirmed"
    assert result.getpixel((50, 140)) == generated.getpixel((50, 140))
    assert result.getpixel((100, 40)) == before.getpixel((100, 40))


def test_guided_boundary_keeps_restoration_inside_opening_and_neckline():
    import numpy as np
    from PIL import ImageDraw

    from tryon_server.layering import guided_inner_top

    before = Image.new("RGB", (200, 300), (80, 80, 100))
    ImageDraw.Draw(before).rectangle((30, 80, 170, 190), fill=(220, 210, 170))
    generated = before.copy()
    draw = ImageDraw.Draw(generated)
    draw.rectangle((30, 65, 170, 220), fill=(40, 90, 50))
    draw.rectangle((85, 80, 115, 220), fill="white")
    draw.rectangle((32, 205, 168, 220), fill="white")
    labels = np.zeros((300, 200), dtype="uint8")
    labels[80:191, 30:171] = 3
    new_labels = labels.copy()
    new_labels[65:221, 30:171] = 3
    coat = Image.new("RGBA", (200, 300), (40, 90, 50, 255))
    result, _ = guided_inner_top(
        before, generated, coat, labels, new_labels, [[0.4, 0], [0.6, 0], [0.65, 1], [0.35, 1]]
    )
    assert result.getpixel((100, 140)) == before.getpixel((100, 140))
    assert result.getpixel((100, 215)) == before.getpixel((100, 215))
    # A connected bright hem may be outer fabric. Color alone cannot widen the
    # opening and paint the selected inner shirt/trousers across a jacket panel.
    assert result.getpixel((60, 215)) == generated.getpixel((60, 215))
    assert result.getpixel((100, 70)) == before.getpixel((100, 70))
    assert result.getpixel((50, 140)) == generated.getpixel((50, 140))


def test_long_coat_opening_preserves_trousers_below_shirt():
    import numpy as np
    from PIL import ImageDraw

    from tryon_server.layering import guided_inner_top

    before = Image.new("RGB", (200, 300), (45, 60, 90))
    ImageDraw.Draw(before).rectangle((30, 70, 170, 170), fill=(220, 210, 170))
    generated = Image.new("RGB", (200, 300), (150, 120, 90))
    labels = np.zeros((300, 200), dtype="uint8")
    labels[70:171, 30:171] = 3
    labels[171:290, 60:140] = 6
    coat_labels = labels.copy()
    coat_labels[60:275, 30:171] = 3
    coat = Image.new("RGBA", (200, 300), (150, 120, 90, 255))
    result, status = guided_inner_top(
        before, generated, coat, labels, coat_labels, [[0.4, 0], [0.6, 0], [0.65, 1], [0.35, 1]]
    )
    assert status["status"] == "applied"
    assert result.getpixel((100, 135)) == before.getpixel((100, 135))
    assert result.getpixel((100, 240)) == before.getpixel((100, 240))
    assert result.getpixel((45, 240)) == generated.getpixel((45, 240))


def test_long_coat_product_collar_offset_does_not_erase_neckline():
    import numpy as np

    from tryon_server.layering import guided_inner_top

    before = Image.new("RGB", (200, 300), (220, 210, 170))
    generated = Image.new("RGB", (200, 300), (150, 120, 90))
    labels = np.zeros((300, 200), dtype="uint8")
    labels[70:171, 30:171] = 3
    coat_labels = labels.copy()
    coat_labels[60:275, 30:171] = 3
    coat = Image.new("RGBA", (200, 300), (150, 120, 90, 255))
    result, _ = guided_inner_top(
        before, generated, coat, labels, coat_labels, [[0.45, 0.08], [0.55, 0.08], [0.6, 0.95], [0.4, 0.95]]
    )
    assert result.getpixel((100, 80)) == before.getpixel((100, 80))
    assert result.getpixel((50, 80)) == generated.getpixel((50, 80))


def test_shirt_contour_keeps_collar_buttons_and_removes_shoulder_halo():
    import numpy as np
    from PIL import ImageDraw

    from tryon_server.layering import guided_inner_top

    base = Image.new("RGB", (200, 300), (210, 210, 210))
    draw = ImageDraw.Draw(base)
    draw.rectangle((25, 70, 175, 210), fill=(230, 210, 170))
    for y in range(75, 210, 5):
        draw.line((25, y, 175, y), fill=(200, 180, 140))
    output = Image.new("RGB", base.size, (210, 210, 210))
    draw = ImageDraw.Draw(output)
    blue = (40, 65, 100)
    draw.rectangle((35, 65, 165, 220), fill=blue)
    draw.polygon([(98, 70), (105, 70), (118, 210), (92, 210)], fill="white")
    # Dark collar shadows and a white button are real shirt details, not opening.
    draw.rectangle((86, 70, 93, 84), fill=(12, 18, 30))
    draw.ellipse((81, 120, 86, 125), fill="white")
    old = np.zeros((300, 200), dtype="uint8")
    old[70:211, 25:176] = 3
    old[15:50, 80:120] = 1
    new = np.zeros_like(old)
    new[65:221, 35:166] = 3
    garment = Image.new("RGBA", (200, 300), (*blue, 255))
    points = [[0.47, 0.1], [0.53, 0.1], [0.55, 0.95], [0.45, 0.95]]
    actual, meta = guided_inner_top(base, output, garment, old, new, points)
    assert meta["contour_aligned"] and meta["status"] == "applied"
    assert actual.getpixel((102, 150)) == base.getpixel((102, 150))
    assert actual.getpixel((89, 78)) == output.getpixel((89, 78))
    assert actual.getpixel((83, 122)) == output.getpixel((83, 122))
    assert actual.getpixel((29, 100)) == output.getpixel((29, 100))
    assert actual.getpixel((100, 30)) == base.getpixel((100, 30))
    assert actual.getpixel((50, 130)) == output.getpixel((50, 130))


def test_collapsed_shirt_opening_moves_placket_and_buttons_with_fabric():
    import numpy as np
    from PIL import ImageDraw

    from tryon_server.layering import guided_inner_top

    base = Image.new("RGB", (200, 300), (215, 200, 160))
    output = Image.new("RGB", base.size, (40, 65, 100))
    draw = ImageDraw.Draw(output)
    draw.rectangle((97, 70, 103, 210), fill="white")
    draw.rectangle((91, 125, 93, 129), fill=(250, 240, 180))
    labels = np.zeros((300, 200), dtype="uint8")
    labels[70:211, 30:171] = 3
    labels[20:50, 80:120] = 1
    garment = Image.new("RGBA", base.size, (40, 65, 100, 255))
    actual, meta = guided_inner_top(
        base,
        output,
        garment,
        labels,
        labels,
        [[0.43, 0], [0.57, 0], [0.57, 1], [0.43, 1]],
    )
    assert meta["panel_adjusted_rows"] > 50
    assert actual.getpixel((94, 150)) == base.getpixel((94, 150))
    assert actual.getpixel((106, 150)) == base.getpixel((106, 150))
    # Button translates with the left placket; it is neither erased nor painted over.
    rgb = np.asarray(actual)
    buttons = (rgb[125:130, :, 0] > 235) & (rgb[125:130, :, 1] > 220)
    _, button_x = np.where(buttons)
    assert len(button_x) >= 4 and button_x.max() < 91
    assert actual.getpixel((45, 150)) == output.getpixel((45, 150))
    assert actual.getpixel((100, 30)) == base.getpixel((100, 30))


def test_panel_alignment_does_not_invent_an_unobserved_opening():
    import numpy as np

    from tryon_server.layering import _open_panels

    generated = Image.new("RGB", (100, 200), "blue")
    labels = np.full((200, 100), 3, dtype="uint8")
    empty = np.zeros_like(labels, dtype=bool)
    projected = empty.copy()
    projected[40:150, 40:60] = True
    actual, new_labels, hole, count = _open_panels(generated, labels, empty, projected, 20, 80, 40, 150)
    assert count == 0 and actual.tobytes() == generated.tobytes()
    assert np.array_equal(labels, new_labels) and not hole.any()
