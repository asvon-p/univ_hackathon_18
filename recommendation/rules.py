from shared.contracts import GarmentAttributes, UserContext
from shared.recommendation_constraints import required_by_category
from shared.thermal_policy import garment_violation, outfit_violation, temperature_policy


def classify_clothes(garments: list[GarmentAttributes]):
    tops = []
    bottoms = []
    outerwear = []

    for garment in garments:
        # 세탁 중이거나 비활성화된 옷은 제외
        if garment.status != "available":
            continue

        # 사용자 착용 역할로 배치합니다. 셔츠 형태라도 outerwear면 겉옷입니다.
        if garment.wearing_role == "top":
            tops.append(garment)

        # 하의 분류
        elif garment.wearing_role == "bottom":
            bottoms.append(garment)

        # 아우터 분류
        elif garment.wearing_role == "outerwear":
            outerwear.append(garment)

    return tops, bottoms, outerwear


def get_temperature_category(temperature_c: float) -> str:
    band = temperature_policy(temperature_c).band
    return "very_cold" if band == "freezing" else band


def is_weather_suitable(thermal_level: int | None, temperature_c: float, role="top") -> bool:
    garment = GarmentAttributes(garment_id="check", category=role, thermal_level=thermal_level)
    return garment_violation(garment, role, temperature_c) is None


def filter_outfits_by_weather(outfits: list, temperature_c: float):
    context = UserContext(user_id="weather-filter", temperature_c=temperature_c)
    return [items for items in outfits if outfit_violation(context, *items) is None]


def iter_outfits(garments: list[GarmentAttributes], context: UserContext | None = None):
    tops, bottoms, outerwear = classify_clothes(garments)
    required = {}
    if context is not None:
        try:
            required = required_by_category(context, garments)
        except ValueError:
            return []
        tops = [g for g in tops if g.garment_id not in context.excluded_items]
        bottoms = [g for g in bottoms if g.garment_id not in context.excluded_items]
        outerwear = [g for g in outerwear if g.garment_id not in context.excluded_items]
    if "top" in required:
        tops = [required["top"]]
    if "bottom" in required:
        bottoms = [required["bottom"]]
    if "outerwear" in required:
        outerwear = [required["outerwear"]]

    for top in tops:
        for bottom in bottoms:
            # 아우터가 없는 코디
            if "outerwear" not in required and (
                context is None or outfit_violation(context, top, bottom, None) is None
            ):
                yield top, bottom, None

            # 아우터가 있는 코디
            for outer in outerwear:
                if context is None or outfit_violation(context, top, bottom, outer) is None:
                    yield top, bottom, outer


def generate_outfits(garments: list[GarmentAttributes], context: UserContext | None = None):
    return list(iter_outfits(garments, context))
