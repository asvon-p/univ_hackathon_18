"""Deterministic schedule evaluation. Scores are heuristics, not comfort probabilities."""

from heapq import nlargest

from recommendation.personalization import feedback_adjustment
from recommendation.rules import iter_outfits
from shared.contracts import Outfit, OutfitItems, RecommendationResult
from shared.day_plans import PURPOSE_LABELS, WearingStep
from shared.thermal_policy import evaluate_thermal_check, outfit_violation, thermal_checks

POLICY_VERSION = "day-plan-v4-season-forecast"
PURPOSE_OCCASION = {
    "class": "daily",
    "presentation": "work",
    "interview": "interview",
    "work": "work",
    "exercise": "exercise",
    "date": "date",
    "travel": "travel",
    "other": None,
}


def evaluate(context, top, bottom, outer):
    """Return server-owned explanations, or None for a confirmed hard-style conflict."""
    if outfit_violation(context, top, bottom, outer):
        return None
    from recommendation.ranking import (
        calculate_occasion_score,
        calculate_palette_score,
        calculate_style_score,
        supported_preferences,
    )

    events = context.day_plan.events
    if any(
        e.required_style
        and (e.required_style not in top.style_candidates or e.required_style not in bottom.style_candidates)
        for e in events
    ):
        return None
    limitations = [
        "일정 적합도는 의류 태그·보온도에 따른 참고 판단이며 실제 착용감이나 드레스코드를 보장하지 않습니다"
    ]
    if any(e.purpose in ("interview", "presentation") for e in events) and any(
        e.purpose == "exercise" and e.intensity == "high" for e in events
    ):
        limitations.append("격식 있는 일정과 강도 높은 운동이 함께 있어 별도 운동복이 필요할 수 있습니다")
    steps, weights, scores = [], [], []
    removable = context.outerwear_policy == "removable"
    for index, event in enumerate(events):
        purpose_score = (
            calculate_occasion_score(
                top.style_candidates, bottom.style_candidates, PURPOSE_OCCASION[event.purpose]
            )
            / 60
            * 100
        )
        minutes = (event.end_at - event.start_at).total_seconds() / 60
        weight = max(0.25, min(minutes / 60, 2)) * (2 if event.priority == "high" else 1)
        note = [f"{PURPOSE_LABELS[event.purpose]}의 분위기와 활동 강도 고려"]
        check = thermal_checks(context)[index]
        thermal, action, thermal_note = evaluate_thermal_check(context, check, top, bottom, outer)
        note.append(thermal_note)
        score = 0.6 * purpose_score + 0.4 * thermal
        if context.weather_forecast:
            note.append("현재 시각부터 오늘 24시 전까지의 시간별 예보 기준")
        elif event.temperature_min_c is None:
            note.append("시간별 예보 없음: 현재 또는 수동 기온 기준")
        if thermal < 50:
            limitations.append(
                f"일정 {index + 1}: 추운 시간과 더운 시간에 모두 편안한 복장이 아닐 수 있습니다"
            )
        if outer and not removable:
            note.append("계속 입기 설정을 유지; 더우면 조건 변경 필요")
        if event.required_style:
            note.append(f"상의·하의의 {event.required_style} 태그 조건 충족; 실제 복장 규정은 직접 확인")
        steps.append(
            WearingStep(
                event_index=index,
                start_at=event.start_at,
                end_at=event.end_at,
                purpose=event.purpose,
                environment=event.environment,
                outerwear_action=action,
                suitability_score=round(score, 2),
                note=" · ".join(note),
            )
        )
        weights.append(weight)
        scores.append(score)
    mean = sum(s * w for s, w in zip(scores, weights, strict=True)) / sum(weights)
    worst = min(scores)
    schedule = 0.6 * mean + 0.4 * worst
    color = calculate_palette_score(top, bottom, outer) / 40 * 100
    style = (
        calculate_style_score(
            top.style_candidates,
            bottom.style_candidates,
            context.preferred_styles,
            outer.style_candidates if outer else None,
        )
        / 60
        * 100
    )
    base = 0.5 * color + 0.5 * style if supported_preferences(context.preferred_styles) else color
    total = 0.6 * schedule + 0.4 * base
    return dict(
        wearing_plan=steps,
        limitations=list(dict.fromkeys(limitations)),
        score_components={
            "schedule_mean": round(mean, 2),
            "schedule_worst": round(worst, 2),
            "schedule": round(schedule, 2),
            "color_style": round(base, 2),
            "feedback": feedback_adjustment(context, top, bottom, outer),
            "total": round(total + feedback_adjustment(context, top, bottom, outer), 2),
        },
        policy_version=POLICY_VERSION,
    )


def recommend_day(context, garments):
    def candidates():
        for index, (top, bottom, outer) in enumerate(iter_outfits(garments, context)):
            details = evaluate(context, top, bottom, outer)
            if details is not None:
                yield details["score_components"]["total"], -index, top, bottom, outer, details

    winners = nlargest(3, candidates(), key=lambda c: c[:2])
    outfits = []
    for rank, (score, _, top, bottom, outer, details) in enumerate(winners, 1):
        outfits.append(
            Outfit(
                outfit_id=f"day-{rank}",
                items=OutfitItems(
                    top=top.garment_id,
                    bottom=bottom.garment_id,
                    outerwear=outer.garment_id if outer else None,
                ),
                style_tags=list(dict.fromkeys(top.style_candidates + bottom.style_candidates)),
                reason="같은 상의·하의를 유지하며 일정의 길이·우선순위·목적과 입력한 구간 기온을 고려한 코디",
                score=score,
                **details,
            )
        )
    return RecommendationResult(recommendation_id="day-plan", outfits=outfits)
