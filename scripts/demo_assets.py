"""외부 사진 없이 재현할 수 있는 합성 시연 의류와 투명 PNG.

사진 분석 품질 평가용 데이터가 아닌, API/옷장/코디 화면용 도형 샘플입니다.
"""

from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageDraw


@dataclass(frozen=True)
class DemoGarment:
    key: str
    category: str
    subcategory: str
    color: str
    fill: str
    style: str
    thermal: int
    status: str = "available"


CATALOG = (
    DemoGarment("white-tee", "top", "t-shirt", "white", "#f3f3ec", "minimal", 1),
    DemoGarment("navy-knit", "top", "sweater", "navy", "#344764", "minimal", 4),
    DemoGarment("green-hoodie", "top", "hoodie", "green", "#577b5c", "casual", 3),
    DemoGarment("gray-shirt", "top", "shirt", "gray", "#a7aab0", "formal", 2, "laundry"),
    DemoGarment("blue-jeans", "bottom", "jeans", "denim", "#547b9c", "casual", 3),
    DemoGarment("black-slacks", "bottom", "slacks", "black", "#35383e", "minimal", 2),
    DemoGarment("beige-pants", "bottom", "cotton_pants", "beige", "#bdad8e", "casual", 2, "inactive"),
    DemoGarment("white-sneakers", "shoes", "sneakers", "white", "#ecece3", "casual", 2),
    DemoGarment("black-loafers", "shoes", "loafers", "black", "#343437", "formal", 2),
    DemoGarment("brown-boots", "shoes", "boots", "brown", "#7d5c47", "vintage", 4),
    DemoGarment("camel-coat", "outerwear", "coat", "camel", "#bc9369", "minimal", 5),
    DemoGarment("olive-jacket", "outerwear", "jacket", "olive", "#79815d", "casual", 3),
)


def garment_images(item: DemoGarment) -> tuple[bytes, bytes]:
    """원본 JPEG와 실제 alpha 채널이 있는 cutout PNG를 반환합니다."""
    cutout = Image.new("RGBA", (480, 560), (0, 0, 0, 0))
    draw = ImageDraw.Draw(cutout)
    outline = "#242b36"
    if item.category in ("top", "outerwear"):
        hem = 470 if item.category == "outerwear" else 380
        points = [
            (178, 105),
            (135, 120),
            (65, 215),
            (122, 258),
            (152, 217),
            (152, hem),
            (328, hem),
            (328, 217),
            (358, 258),
            (415, 215),
            (345, 120),
            (302, 105),
        ]
        draw.polygon(points, fill=item.fill, outline=outline, width=5)
        draw.arc((178, 70, 302, 152), 0, 180, fill=outline, width=5)
        if item.category == "outerwear":
            draw.line((240, 148, 240, hem), fill=outline, width=4)
            for y in range(220, hem - 20, 60):
                draw.ellipse((248, y, 256, y + 8), fill=outline)
        else:
            draw.line((165, hem - 18, 315, hem - 18), fill=outline, width=2)
    elif item.category == "bottom":
        draw.polygon(
            [(150, 90), (330, 90), (350, 462), (263, 462), (240, 255), (217, 462), (130, 462)],
            fill=item.fill,
            outline=outline,
            width=5,
        )
        draw.line((150, 120, 330, 120), fill=outline, width=4)
        draw.line((240, 123, 240, 235), fill=outline, width=3)
    else:
        for y in (210, 335):
            draw.polygon(
                [(90, y), (178, y), (223, y + 35), (348, y + 45), (385, y + 70), (385, y + 95), (80, y + 95)],
                fill=item.fill,
                outline=outline,
                width=5,
            )
            draw.line((85, y + 78, 378, y + 78), fill=outline, width=4)
            for x in (210, 235, 260):
                draw.line((x, y + 35, x - 12, y + 53), fill=outline, width=3)
    original = Image.new("RGB", cutout.size, "#f4f2ec")
    original.paste(cutout, mask=cutout.getchannel("A"))
    ImageDraw.Draw(original).text((24, 524), "DEMO / " + item.key, fill="#344764")
    jpg, png = BytesIO(), BytesIO()
    original.save(jpg, format="JPEG", quality=90)
    cutout.save(png, format="PNG")
    return jpg.getvalue(), png.getvalue()
