"""Per-slot warmth requirements, shared by every recommender and final validation.

These are product heuristics (1..5), not a measurement of thermal comfort.
An outer layer can never compensate for a top or bottom below its minimum.
"""

from dataclasses import dataclass, replace

from shared.feedback import effective_reason_codes

POLICY_VERSION = "slot-warmth-v3-season-forecast"
ROLE_LABELS = {"top": "상의", "bottom": "하의", "outerwear": "아우터"}
LEVEL_LABELS = {1: "매우 얇음", 2: "얇음", 3: "보통", 4: "두꺼움", 5: "매우 두꺼움"}


@dataclass(frozen=True)
class ThermalPolicy:
    top: int
    bottom: int
    outerwear: int
    outer_required: bool
    maximum: int = 5
    band: str = "freezing"


def temperature_policy(temperature: float) -> ThermalPolicy:
    if temperature < 0:
        return ThermalPolicy(4, 4, 5, True)
    if temperature <= 5:
        return ThermalPolicy(3, 3, 4, True, band="cold")
    if temperature <= 15:
        return ThermalPolicy(3, 3, 3, True, band="cool")
    if temperature <= 22:
        return ThermalPolicy(2, 2, 2, False, 4, "mild")
    return ThermalPolicy(1, 1, 1, False, 2, "hot")


@dataclass(frozen=True)
class ThermalCheck:
    low: float
    high: float
    environment: str
    label: str


def thermal_checks(context):
    forecast = context.weather_forecast
    low, high = (forecast.minimum_c, forecast.maximum_c) if forecast else (context.temperature_c,) * 2
    if context.day_plan is None:
        return [ThermalCheck(low, high, "outdoor", "남은 시간 예보" if forecast else "현재 기온")]
    checks = []
    for index, event in enumerate(context.day_plan.events, 1):
        # Indoor events never erase outside exposure. Explicit event temperatures may strengthen it.
        checks.append(
            ThermalCheck(
                min(low, event.temperature_min_c) if event.temperature_min_c is not None else low,
                max(high, event.temperature_max_c) if event.temperature_max_c is not None else high,
                event.environment,
                f"일정 {index}",
            )
        )
    return checks


def temperature_checks(context):
    return [(c.low, c.label) for c in thermal_checks(context)]


def check_policy(context, check, temperature):
    policy = temperature_policy(temperature)
    season = context.clothing_season or context.season
    if check.environment == "indoor" and season == "winter":
        policy = replace(policy, top=max(1, policy.top - 1))
    if check.environment == "indoor" and season == "summer" and not policy.outer_required:
        policy = replace(policy, outerwear=1)
    return policy


def garment_violation(garment, role, temperature, *, maximum=True, policy=None):
    policy = policy or temperature_policy(temperature)
    minimum = getattr(policy, role)
    if garment is None:
        if role == "outerwear" and not policy.outer_required:
            return None
        return f"{ROLE_LABELS[role]}가 필요합니다"
    level = garment.thermal_level
    if level is None:
        return f"{ROLE_LABELS[role]} 보온도를 확인해 주세요"
    if level < minimum:
        return f"{ROLE_LABELS[role]}는 {LEVEL_LABELS[minimum]}({minimum}) 이상이어야 합니다"
    if maximum and level > policy.maximum:
        return f"{ROLE_LABELS[role]}가 기온에 비해 두껍습니다"
    # A known short garment must not pass winter checks merely due to a wrong warmth tag.
    if policy.outer_required and (
        (role == "top" and garment.subcategory in {"short_sleeve", "short-sleeve", "반팔"})
        or (role == "bottom" and garment.subcategory == "shorts")
    ):
        return f"추운 날씨에는 반팔·반바지를 추천하지 않습니다 ({ROLE_LABELS[role]})"
    return None


def thermal_feedback_violation(context, top, bottom, outer):
    """Most recent comparable thermal opinion must yield a real warmth change.

    Feedback is already scoped by owner/wardrobe. Old opinions at a different
    temperature do not become a permanent winter/summer preference.
    """
    current = {"top": top, "bottom": bottom, "outerwear": outer}
    seen = set()
    checks = temperature_checks(context)
    if not checks:
        return None
    temperature = min(t for t, _ in checks)
    for event in context.recent_feedback:
        key = frozenset(g.garment_id for g in event.garments)
        if not key or key in seen:
            continue
        seen.add(key)
        if event.accepted or event.temperature_c is None or abs(event.temperature_c - temperature) > 5:
            continue
        codes = effective_reason_codes(event.reason_codes, event.reason, accepted=event.accepted)
        directions = set(codes) & {"too_cold", "too_warm"}
        if len(directions) != 1:
            continue
        old = {g.wearing_role: g for g in event.garments}
        differences = []
        for role in ("top", "bottom", "outerwear"):
            previous, now = old.get(role), current[role]
            before = previous.thermal_level if previous else 0
            after = now.thermal_level if now else 0
            if before is None or after is None:
                continue
            differences.append(after - before)
        if not differences:
            continue
        if "too_cold" in directions:
            improved = min(differences) >= 0 and max(differences) > 0
        else:
            improved = max(differences) <= 0 and min(differences) < 0
        return None if improved else "최근 춥다·덥다 피드백에 맞춰 보온도가 달라진 조합이 필요합니다"
    return None


def outfit_violation(context, top, bottom, outer):
    checks = thermal_checks(context)
    for check in checks:
        policy = check_policy(context, check, check.low)
        for role, garment in (("top", top), ("bottom", bottom), ("outerwear", outer)):
            # Schedule layers can be carried in warm intervals. Enforce minima,
            # then rank excess warmth separately rather than discarding a full day.
            issue = garment_violation(
                garment,
                role,
                check.low,
                policy=policy,
                maximum=False,
            )
            if issue:
                return f"{check.label} {check.low:g}°C: {issue}"
    # Cold periods justify carrying a warm layer through the heat; an all-hot day does not.
    ceiling = temperature_policy(min(c.low for c in checks)).maximum
    for role, garment in (("top", top), ("bottom", bottom), ("outerwear", outer)):
        if garment is not None and garment.thermal_level > ceiling:
            return (
                f"{ROLE_LABELS[role]}는 남은 시간 기온에 맞춰 "
                f"{LEVEL_LABELS[ceiling]}({ceiling}) 이하여야 합니다"
            )
    return thermal_feedback_violation(context, top, bottom, outer)


def evaluate_thermal_check(context, check, top, bottom, outer):
    """Evaluate each endpoint; removable layers can be worn cold and carried warm."""
    summer_indoor = check.environment == "indoor" and (context.clothing_season or context.season) == "summer"
    actions, scores = [], []
    for temperature in (check.low, check.high):
        policy = check_policy(context, check, temperature)

        def score(worn, policy=policy):
            penalties = []
            for role, garment in (("top", top), ("bottom", bottom), ("outerwear", outer if worn else None)):
                if garment is None:
                    penalties.append(100 if role == "outerwear" and policy.outer_required else 0)
                    continue
                if garment.thermal_level is None:
                    penalties.append(100)
                    continue
                maximum = (
                    2
                    if role == "outerwear" and summer_indoor and not policy.outer_required
                    else policy.maximum
                )
                penalties.append(
                    40 * max(0, getattr(policy, role) - garment.thermal_level)
                    + 20 * max(0, garment.thermal_level - maximum)
                )
            result = max(0, 100 - max(penalties))
            if (
                summer_indoor
                and not policy.outer_required
                and not (worn and outer and outer.thermal_level in (1, 2))
            ):
                result = max(0, result - 30)
            return result

        on, off = score(outer is not None), score(False)
        wear = outer is not None and (context.outerwear_policy == "always_worn" or on > off)
        scores.append(on if wear else off)
        actions.append("wear" if wear else "carry" if outer else "none")
    action = actions[0] if actions[0] == actions[1] else "adjust"
    note = f"실내외 공통 기준 {check.low:g}~{check.high:g}°C"
    if check.environment == "indoor" and (context.clothing_season or context.season) == "winter":
        note += " · 겨울 실내: 상의 최소 보온도만 실외보다 1단계 낮춤"
    if summer_indoor:
        note += " · 여름 실내: 냉방 대비 얇은 아우터(1~2) 우선; 실제 실내 온도는 알 수 없음"
        if not outer or outer.thermal_level not in (1, 2):
            note += " · 얇은 냉방 대비 아우터를 포함하지 못함"
    if action == "adjust":
        note += " · 추운 때 아우터 착용, 더운 때 벗고 소지"
    return min(scores), action, note


def thermal_adjustment(context, top, bottom, outer):
    scores = [evaluate_thermal_check(context, c, top, bottom, outer)[0] for c in thermal_checks(context)]
    return round(-0.3 * (100 - sum(scores) / len(scores)), 2)


def requirements_notice(context):
    checks = temperature_checks(context)
    if not checks:
        return "일정·필수 의류·최근 피드백 조건을 확인해 주세요"
    temperature = min(t for t, _ in checks)
    policies = [check_policy(context, check, check.low) for check in thermal_checks(context)]
    policy = ThermalPolicy(
        *(max(getattr(p, role) for p in policies) for role in ("top", "bottom", "outerwear")),
        any(p.outer_required for p in policies),
        temperature_policy(temperature).maximum,
    )
    roles = ["outerwear", "top", "bottom"] if policy.outer_required else ["top", "bottom"]
    levels = ", ".join(f"{ROLE_LABELS[r]} {LEVEL_LABELS[getattr(policy, r)]} 이상" for r in roles)
    upper = (
        f" 각 의류가 {LEVEL_LABELS[policy.maximum]}({policy.maximum}) 이하여야 합니다."
        if policy.maximum < 5
        else ""
    )
    return (
        f"{temperature:g}°C 기준 {levels}인 옷이 필요합니다.{upper} "
        "옷의 보온도와 필수 선택을 확인해 주세요. 최근 춥다·덥다 의견이 있으면 "
        "이전보다 보온도가 달라진 조합이 필요합니다"
    )
