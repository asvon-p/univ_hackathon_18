from io import BytesIO

import pytest
from PIL import Image, ImageCms

from backend.app.core.errors import AppError
from backend.app.services import images


def heic_photo(size=(240, 360), *, orientation=None, multiple=False):
    image = Image.new("RGB", size, "#c03131")
    exif = Image.Exif()
    exif[270] = "private test metadata"
    if orientation:
        exif[274] = orientation
    output = BytesIO()
    kwargs = {"exif": exif, "quality": 80}
    if multiple:
        kwargs.update(save_all=True, append_images=[Image.new("RGB", (360, 240), "blue")], primary_index=1)
    image.save(output, "HEIF", **kwargs)
    return output.getvalue()


@pytest.mark.parametrize("orientation", [None, 6, 8])
def test_heic_orientation_and_private_metadata(orientation):
    with Image.open(BytesIO(images.normalize_image(heic_photo(orientation=orientation)))) as output:
        assert output.format == "JPEG" and output.mode == "RGB"
        # The HEIF encoder already applies irot; decoder must not rotate EXIF a second time.
        assert output.size == (240, 360)
        assert not output.getexif() and "xmp" not in output.info


def test_heic_uses_primary_frame():
    with Image.open(BytesIO(images.normalize_image(heic_photo(multiple=True)))) as output:
        assert output.size == (360, 240)
        assert output.getpixel((100, 100))[2] > 200


def test_icc_color_conversion_and_metadata_removal():
    image = Image.new("RGB", (3000, 1500), "red")
    raw = BytesIO()
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    image.save(raw, "PNG", icc_profile=profile)
    with Image.open(BytesIO(images.normalize_image(raw.getvalue()))) as result:
        assert result.size == (2048, 1024)
        assert not result.info.get("icc_profile")
        assert result.getpixel((100, 100))[0] > 240


@pytest.mark.parametrize("data", [b"fake.HEIC", heic_photo()[:100]])
def test_corrupt_heic_is_415(data):
    with pytest.raises(AppError) as error:
        images.normalize_image(data)
    assert error.value.status == 415


def test_heic_pixel_limit_checked_before_decode(monkeypatch):
    monkeypatch.setattr(images, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(AppError) as error:
        images.normalize_image(heic_photo())
    assert error.value.status == 415


def test_heic_cannot_masquerade_as_cutout():
    with pytest.raises(AppError) as error:
        images.normalize_image(heic_photo(), cutout=True)
    assert error.value.status == 415
