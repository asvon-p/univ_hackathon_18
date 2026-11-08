from shared.contracts import GarmentAttributes


def classify_clothes(garments: list[GarmentAttributes]):
    tops = []
    bottoms = []
    outerwear = []

    for garment in garments:
        # 세탁 중이거나 비활성화된 옷은 제외
        if garment.status != "available":
            continue

        # 상의 분류
        if garment.category == "top":
            tops.append(garment)

        # 하의 분류
        elif garment.category == "bottom":
            bottoms.append(garment)

        # 아우터 분류
        elif garment.category == "outerwear":
            outerwear.append(garment)

    return tops, bottoms, outerwear


def get_temperature_category(temperature_c: float) -> str:
    if temperature_c <= 2:
        return "very_cold"
    elif temperature_c < 10:
        return "cold"
    elif temperature_c < 20:
        return "mild"
    else:
        return "hot"


def is_weather_suitable(thermal_level: int | None, temperature_c: float) -> bool:

    # 두께 정보가 없는 옷은 일단 허용
    if thermal_level is None:
        return True

    weather = get_temperature_category(temperature_c)

    if weather == "very_cold":
        return 5 <= thermal_level <= 5

    elif weather == "cold":
        return 4 <= thermal_level <= 5

    elif weather == "mild":
        return 2 <= thermal_level <= 4

    else:
        return 1 <= thermal_level <= 2


def filter_outfits_by_weather(outfits: list, temperature_c: float):
    filtered_outfits = []

    for top, bottom, outer in outfits:
        # 상의가 날씨에 적합한지 확인
        if not is_weather_suitable(top.thermal_level, temperature_c):
            continue

        # 하의가 날씨에 적합한지 확인
        if not is_weather_suitable(bottom.thermal_level, temperature_c):
            continue

        # 아우터가 있다면 날씨 적합도 확인
        if outer is not None:
            if not is_weather_suitable(outer.thermal_level, temperature_c):
                continue

        filtered_outfits.append((top, bottom, outer))

    return filtered_outfits


def generate_outfits(garments: list[GarmentAttributes]):
    tops, bottoms, outerwear = classify_clothes(garments)

    outfits = []

    for top in tops:
        for bottom in bottoms:
            # 아우터가 없는 코디
            outfits.append((top, bottom, None))

            # 아우터가 있는 코디
            for outer in outerwear:
                outfits.append((top, bottom, outer))

    return outfits
