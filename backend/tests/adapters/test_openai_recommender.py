import json

import httpx
import pytest

from backend.app.adapters.openai_recommender import LocalFallback, OpenAIRecommender
from backend.app.adapters.recommender import SafeRecommender
from backend.app.core.config import Settings
from shared.contracts import GarmentAttributes, UserContext

GARMENTS = [
    GarmentAttributes(garment_id="private-top", category="top", color="white"),
    GarmentAttributes(garment_id="private-bottom", category="bottom", color="black"),
    GarmentAttributes(garment_id="private-outer", category="outerwear", color="navy"),
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


def test_openai_ranks_only_local_candidates_using_responses_schema():
    seen = []

    def respond(request):
        assert str(request.url) == "https://api.openai.com/v1/responses"
        body = json.loads(request.content)
        seen.append(body)
        data = json.loads(body["input"])
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


def test_local_fallback_still_works_when_weather_eliminates_every_candidate():
    thick = [g.model_copy(update={"thermal_level": 5}) for g in GARMENTS]
    result = LocalFallback().recommend(UserContext(user_id="u", temperature_c=30), thick)
    assert result.outfits and result.outfits[0].items.shoes is None
    assert result.outfits[0].items.outerwear is None
