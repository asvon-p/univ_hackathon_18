import json

import httpx
import pytest

from backend.app.adapters.openai_recommender import LocalFallback, OpenAIRecommender
from backend.app.adapters.recommender import SafeRecommender
from backend.app.core.config import Settings
from shared.contracts import GarmentAttributes, UserContext

GARMENTS = [
    GarmentAttributes(garment_id="private-top", category="top", color="white", thermal_level=3),
    GarmentAttributes(garment_id="private-bottom", category="bottom", color="black", thermal_level=3),
    GarmentAttributes(
        garment_id="private-outer", category="outerwear", subcategory="shirt", color="navy", thermal_level=4
    ),
    GarmentAttributes(garment_id="legacy-shoes", category="shoes"),
]
CONTEXT = UserContext(user_id="private-user", temperature_c=18, preferred_styles=["꾸안꾸"])


def settings(**values):
    return Settings(_env_file=None, OPENAI_API_KEY="test-only-key", **values)


def completed(rows):
    return {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps({"outfits": rows}),
                    }
                ],
            }
        ],
    }


def test_receipt_storage_failure_prevents_paid_http_and_falls_back(monkeypatch):
    from backend.app.services import api_usage

    def fail_receipt(*args):
        raise RuntimeError("receipt database unavailable")

    seen = []
    monkeypatch.setattr(api_usage, "begin_usage", fail_receipt)
    engine = OpenAIRecommender(
        settings(API_USAGE_ENABLED=True), transport=httpx.MockTransport(lambda r: seen.append(r))
    )
    result = SafeRecommender(engine, 1, LocalFallback()).recommend(CONTEXT, GARMENTS)
    assert result.is_fallback and result.provider == "fallback"
    assert result.result.outfits
    assert not seen


def test_openai_ranks_only_local_candidates_using_responses_schema():
    seen = []

    def respond(request):
        assert str(request.url) == "https://api.openai.com/v1/responses"
        body = json.loads(request.content)
        seen.append(body)
        data = json.loads(body["input"])
        assert "category는 착용 역할" in body["instructions"]
        for candidate in data["candidates"]:
            outer = candidate["items"].get("outerwear")
            if outer:
                assert outer["category"] == "outerwear" and outer["subcategory"] == "shirt"
        count = len(data["candidates"])
        return httpx.Response(
            200,
            json=completed(
                [{"candidate_id": i, "reason": f"한국어 이유 {i}"} for i in reversed(range(count))]
            ),
        )

    engine = OpenAIRecommender(settings(), transport=httpx.MockTransport(respond))
    result = SafeRecommender(engine, 1, LocalFallback()).recommend(CONTEXT, GARMENTS)
    assert result.provider == "openai" and not result.is_fallback
    assert result.result.outfits[0].items.outerwear == "private-outer"
    assert all(o.items.shoes is None for o in result.result.outfits)
    body = seen[0]
    assert body["model"] == "gpt-6-luna" and body["store"] is False
    assert body["text"]["format"]["strict"] is True
    assert body["max_output_tokens"] == 1200
    assert "꾸안꾸" in body["input"]
    for forbidden in ("private-user", "private-top", "legacy-shoes", "cutout_path", "user_id"):
        assert forbidden not in body["input"]


def test_judge_receives_season_and_remaining_range_but_cannot_remove_warm_outer():
    from datetime import datetime

    from backend.tests.test_remaining_forecast import hourly_day
    from shared.weather_forecast import KST, remaining_forecast

    now = datetime(2026, 11, 21, 14, 30, tzinfo=KST)
    context = CONTEXT.model_copy(
        update={
            "temperature_c": 25,
            "clothing_season": "autumn",
            "weather_forecast": remaining_forecast(hourly_day(now), now, now=now),
            "required_garment_ids": ["private-top"],
            "outerwear_policy": "removable",
        }
    )
    seen = []

    def respond(request):
        payload = json.loads(json.loads(request.content)["input"])
        seen.append(payload)
        assert payload["clothing_season"] == "autumn"
        assert payload["remaining_temperature_range"]["minimum_c"] == 3
        assert payload["remaining_temperature_range"]["maximum_c"] == 25
        assert all(c["items"]["outerwear"] for c in payload["candidates"])
        return httpx.Response(200, json=completed([{"candidate_id": 0, "reason": "밤 추위에 대비한 아우터"}]))

    result = SafeRecommender(
        OpenAIRecommender(settings(), transport=httpx.MockTransport(respond)), 2, LocalFallback()
    ).recommend(context, GARMENTS)
    assert seen and not result.is_fallback
    assert result.result.outfits[0].items.top == "private-top"
    assert result.result.outfits[0].items.outerwear == "private-outer"


@pytest.mark.parametrize(
    "failure",
    [
        "401",
        "429",
        "500",
        "timeout",
        "refusal",
        "incomplete",
        "duplicate",
        "unknown",
        "omitted",
        "extra-field",
        "invalid-json",
        "empty",
        "non-json",
    ],
)
def test_openai_failure_uses_validated_team_rules(failure, caplog):
    def respond(request):
        if failure.isdigit():
            return httpx.Response(int(failure), json={"error": "secret-response-must-not-be-logged"})
        if failure == "timeout":
            raise httpx.ReadTimeout("timeout", request=request)
        if failure == "non-json":
            return httpx.Response(200, text="upstream HTML")
        rows = [{"candidate_id": i, "reason": "이유"} for i in range(2)]
        if failure == "duplicate":
            rows[1]["candidate_id"] = 0
        if failure == "unknown":
            rows[1]["candidate_id"] = 2
        if failure == "omitted":
            rows.pop()
        if failure == "extra-field":
            rows[0]["top"] = "foreign-id"
        body = completed(rows)
        if failure == "refusal":
            body["output"][0]["content"] = [{"type": "refusal", "refusal": "no"}]
        if failure == "incomplete":
            body["status"] = "incomplete"
        if failure == "invalid-json":
            body["output"][0]["content"][0]["text"] = "not json"
        if failure == "empty":
            body["output"] = []
        return httpx.Response(200, json=body)

    engine = OpenAIRecommender(settings(), transport=httpx.MockTransport(respond))
    outcome = SafeRecommender(engine, 1, LocalFallback()).recommend(CONTEXT, GARMENTS)
    expected = LocalFallback().recommend(CONTEXT, GARMENTS)
    assert outcome.is_fallback and outcome.provider == "fallback"
    assert outcome.result == expected
    assert "test-only-key" not in caplog.text
    assert "secret-response-must-not-be-logged" not in caplog.text


def test_missing_key_falls_back_without_network():
    engine = OpenAIRecommender(Settings(_env_file=None))
    result = SafeRecommender(engine, 1, LocalFallback()).recommend(CONTEXT, GARMENTS)
    assert result.is_fallback and result.result.outfits


def test_local_fallback_does_not_relax_weather_when_every_candidate_is_unsuitable():
    thick = [g.model_copy(update={"thermal_level": 5}) for g in GARMENTS]
    result = LocalFallback().recommend(UserContext(user_id="u", temperature_c=30), thick)
    assert not result.outfits


@pytest.mark.parametrize("fail", [False, True])
def test_gpt_judges_only_candidates_containing_required_items(fail):
    context = CONTEXT.model_copy(update={"temperature_c": 18, "required_garment_ids": ["private-outer"]})
    seen = []

    def respond(request):
        body = json.loads(request.content)
        payload = json.loads(body["input"])
        seen.append(payload)
        assert body["model"] == "gpt-6-luna"
        assert "tools" not in body and body["store"] is False
        assert payload["required_slots"] == ["outerwear"]
        assert all("outerwear" in c["items"] for c in payload["candidates"])
        assert "private-" not in body["input"]
        if fail:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json=completed(
                [
                    {"candidate_id": c["candidate_id"], "reason": "제공된 후보 판단"}
                    for c in reversed(payload["candidates"])
                ]
            ),
        )

    engine = OpenAIRecommender(settings(), transport=httpx.MockTransport(respond))
    outcome = SafeRecommender(engine, 1, LocalFallback()).recommend(context, GARMENTS)
    assert seen and outcome.is_fallback == fail
    assert outcome.result.outfits
    assert all(o.items.outerwear == "private-outer" for o in outcome.result.outfits)


@pytest.mark.parametrize("fail", [False, True])
def test_reference_and_schedule_judgment_has_no_photos_or_private_ids(fail):
    from backend.tests.api.test_day_plans import plan_body
    from shared.contracts import DayPlanSnapshot, StyleReferenceSnapshot

    context = CONTEXT.model_copy(
        update={
            "required_garment_ids": ["private-top"],
            "day_plan": DayPlanSnapshot(id="private-plan", version=1, **plan_body()),
            "style_reference": StyleReferenceSnapshot(
                id="private-reference",
                version=1,
                features=[{"category": "top", "color": "white", "style_candidates": ["minimal"]}],
            ),
        }
    )
    seen = []

    def respond(request):
        body = json.loads(request.content)
        payload = json.loads(body["input"])
        seen.append(payload)
        assert payload["reference_features"][0]["color"] == "white"
        assert len(payload["schedule"]) == 2
        assert all(c["reference_judgment"] for c in payload["candidates"])
        assert "private-" not in body["input"] and "image" not in body["input"]
        assert "extraction" not in body["input"] and body["store"] is False
        if fail:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json=completed(
                [
                    {"candidate_id": c["candidate_id"], "reason": "확인된 특징 비교"}
                    for c in reversed(payload["candidates"])
                ]
            ),
        )

    outcome = SafeRecommender(
        OpenAIRecommender(settings(), transport=httpx.MockTransport(respond)), 2, LocalFallback()
    ).recommend(context, GARMENTS)
    assert seen and outcome.is_fallback == fail
    assert outcome.result.outfits
    assert all(
        o.items.top == "private-top" and len(o.wearing_plan) == 2 and o.reference_matches
        for o in outcome.result.outfits
    )
