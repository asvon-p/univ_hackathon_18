"""Test-only server: replace OpenAI HTTP only; real registry, services, JWT and PostgreSQL."""

import json
import os
import time
from pathlib import Path
from uuid import uuid4

import httpx

from backend.app.adapters import registry
from backend.app.main import create_app
from backend.tests.adapters.test_openai_recommender import completed


def respond(request):
    assert request.url == "https://api.openai.com/v1/responses"
    if path := os.environ.get("TEST_OPENAI_CALLS_PATH"):
        # Unique files avoid lost concurrent append writes across Windows processes.
        folder = Path(path)
        folder.mkdir(exist_ok=True)
        (folder / f"{uuid4().hex}.call").touch(exist_ok=False)
    time.sleep(float(os.environ.get("TEST_OPENAI_DELAY", "0")))
    if os.environ["TEST_OPENAI_FAILURE"] == "1":
        return httpx.Response(503)
    payload = json.loads(json.loads(request.content)["input"])
    return httpx.Response(
        200,
        json={
            **completed(
                [
                    {"candidate_id": row["candidate_id"], "reason": f"후보 판단 {row['candidate_id']}"}
                    for row in reversed(payload["candidates"])
                ]
            ),
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 100,
                "input_tokens_details": {"cached_tokens": 200},
            },
        },
    )


app = create_app()
registry.get_recommender().inner.transport = httpx.MockTransport(respond)
