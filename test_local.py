from shared.contracts import GarmentAttributes
from recommendation.rules import classify_clothes

# 테스트용 옷 4개 만들기
garments = [
    GarmentAttributes(
        garment_id="G001",
        category="top",
        color="white",
        status="available"
    ),
    GarmentAttributes(
        garment_id="G002",
        category="bottom",
        color="blue",
        status="available"
    ),
    GarmentAttributes(
        garment_id="G003",
        category="outerwear",
        color="black",
        status="available"
    ),
    GarmentAttributes(
        garment_id="G004",
        category="top",
        color="gray",
        status="laundry"
    )
]

# 의류 분류 함수 실행
tops, bottoms, outerwear = classify_clothes(garments)

# 결과 출력
print("상의:", [g.garment_id for g in tops])
print("하의:", [g.garment_id for g in bottoms])
print("아우터:", [g.garment_id for g in outerwear])

# 기온 분류 함수 테스트
from recommendation.rules import get_temperature_category, is_weather_suitable

print("영하 5도:", get_temperature_category(-5))
print("2도:", get_temperature_category(2))
print("15도:", get_temperature_category(15))
print("20도:", get_temperature_category(20))
print("28도:", get_temperature_category(28))


# 날씨에 맞는 옷인지 테스트
from recommendation.rules import is_weather_suitable

print("영하 5도, 패딩(두께 5):", is_weather_suitable(5, -5))
print("영하 5도, 반팔(두께 1):", is_weather_suitable(1, -5))
print("15도, 맨투맨(두께 3):", is_weather_suitable(3, 15))
print("25도, 반팔(두께 1):", is_weather_suitable(1, 25))
print("25도, 패딩(두께 5):", is_weather_suitable(5, 25))

# 코디 조합 생성 테스트
from recommendation.rules import generate_outfits

test_garments = [
    GarmentAttributes(garment_id="T001", category="top"),
    GarmentAttributes(garment_id="T002", category="top"),
    GarmentAttributes(garment_id="B001", category="bottom"),
    GarmentAttributes(garment_id="B002", category="bottom"),
    GarmentAttributes(garment_id="O001", category="outerwear"),
]

outfits = generate_outfits(test_garments)

print("생성된 코디 수:", len(outfits))

for top, bottom, outer in outfits:
    print(
        "상의:", top.garment_id,
        "| 하의:", bottom.garment_id,
        "| 아우터:", outer.garment_id if outer else "없음"
    )

# 날씨별 코디 필터링 테스트
from recommendation.rules import filter_outfits_by_weather

weather_garments = [
    GarmentAttributes(
        garment_id="T001",
        category="top",
        thermal_level=1
    ),
    GarmentAttributes(
        garment_id="T002",
        category="top",
        thermal_level=4
    ),
    GarmentAttributes(
        garment_id="B001",
        category="bottom",
        thermal_level=2
    ),
    GarmentAttributes(
        garment_id="O001",
        category="outerwear",
        thermal_level=5
    ),
]

# 가능한 모든 코디 생성
all_outfits = generate_outfits(weather_garments)

# 25도에 적합한 코디만 선택
filtered_outfits = filter_outfits_by_weather(
    all_outfits, 25
)

print("전체 코디 수:", len(all_outfits))
print("25도에 적합한 코디 수:", len(filtered_outfits))

for top, bottom, outer in filtered_outfits:
    print(
        "상의:", top.garment_id,
        "| 하의:", bottom.garment_id,
        "| 아우터:", outer.garment_id if outer else "없음"
    )
# 기온 4단계 분류 테스트
print("\n=== 기온 4단계 테스트 ===")

for temp in [-5, 2, 5, 10, 15, 20, 25]:
    category = get_temperature_category(temp)
    print(f"{temp}°C → {category}")

# 옷 두께 적합도 테스트
print("\n=== 옷 두께 적합도 테스트 ===")

for temp in [-5, 5, 15, 25]:
    for level in [1, 2, 3, 4, 5]:
        suitable = is_weather_suitable(level, temp)
        print(f"기온 {temp}°C / 두께 {level} → {suitable}")
