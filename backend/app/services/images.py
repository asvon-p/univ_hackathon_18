"""파일 이름/MIME 대신 실제 바이트를 검사하고 메타데이터 없이 재인코딩합니다."""

import warnings
from io import BytesIO
from typing import BinaryIO

from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener

from backend.app.core.errors import AppError, ErrorCode

# Includes iPhone 48MP photographs, with a bound checked before decoding.
MAX_IMAGE_PIXELS = 64_000_000
register_heif_opener(thumbnails=False, depth_images=False, aux_images=False)


def read_upload(stream: BinaryIO, max_bytes: int) -> bytes:
    content = bytearray()
    while chunk := stream.read(min(64 * 1024, max_bytes - len(content) + 1)):
        content.extend(chunk)
        if len(content) > max_bytes:
            raise AppError(413, ErrorCode.UPLOAD_TOO_LARGE, "업로드 가능한 파일 크기를 초과했습니다")
    return bytes(content)


def normalize_image(data: bytes, *, cutout: bool = False) -> bytes:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                allowed = {"PNG"} if cutout else {"JPEG", "PNG", "HEIF"}
                if image.format not in allowed or image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ValueError("지원하지 않는 이미지 형식 또는 해상도")
                image.verify()
            with Image.open(BytesIO(data)) as image:
                image.load()
                oriented = ImageOps.exif_transpose(image)
                oriented.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
                # Convert Display P3/other embedded ICC colors before stripping metadata.
                profile = oriented.info.get("icc_profile")
                if profile:
                    oriented = ImageCms.profileToProfile(
                        oriented.convert("RGBA"),
                        ImageCms.ImageCmsProfile(BytesIO(profile)),
                        ImageCms.createProfile("sRGB"),
                        outputMode="RGBA",
                    )
                if cutout:
                    clean = oriented.convert("RGBA")
                else:
                    rgba = oriented.convert("RGBA")
                    clean = Image.new("RGB", rgba.size, "white")
                    clean.paste(rgba, mask=rgba.getchannel("A"))
                clean.info.clear()
                output = BytesIO()
                clean.save(output, format="PNG" if cutout else "JPEG", **({} if cutout else {"quality": 90}))
                return output.getvalue()
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        ImageCms.PyCMSError,
    ) as exc:
        raise AppError(
            415,
            ErrorCode.UPLOAD_UNSUPPORTED_TYPE,
            "정상적인 JPG, PNG, HEIC 또는 HEIF 이미지가 필요합니다 (최대 6,400만 픽셀)",
        ) from exc
