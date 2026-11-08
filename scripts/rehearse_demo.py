"""실행 중인 로컬 시연 서버의 seed 계정으로 추천·피드백 리허설.

호출마다 실제 추천 2건과 수락 피드백 1건이 추가됩니다. 인증 정보는 출력하지 않습니다.
"""

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from backend.app.core.config import ROOT_DIR
from scripts.seed_demo import load_credentials


def expect(response, status: int, step: str):
    if response.status_code != status:
        raise RuntimeError(f"{step}: expected HTTP {status}, got {response.status_code}")
    return response.json()


def rehearse(client, email: str, password: str) -> dict:
    started = time.perf_counter()
    health = expect(client.get("/health"), 200, "Health")
    if health["db"] != "ok":
        raise RuntimeError("Database is not ready")
    login = expect(
        client.post("/api/v1/auth/login", json={"email": email, "password": password}), 200, "Login"
    )
    headers = {"Authorization": "Bearer " + login["access_token"]}
    garments = expect(client.get("/api/v1/garments", headers=headers), 200, "Wardrobe")
    images = 0
    for garment in garments:
        for key in ("image_url", "cutout_url"):
            if garment[key]:
                if client.get(garment[key], headers=headers).status_code != 200:
                    raise RuntimeError("A wardrobe image could not be loaded")
                images += 1
    result = expect(
        client.post("/api/v1/recommendations", headers=headers, json={"temperature_c": 18}),
        201,
        "Recommendation",
    )
    if result["is_fallback"] or result["provider"] != health["adapters"]["recommender"]:
        raise RuntimeError("Recommender used fallback; inspect server logs before the demonstration")
    path = f"/api/v1/recommendations/{result['recommendation_id']}"
    feedback = expect(
        client.post(
            path + "/feedback",
            headers=headers,
            json={
                "recommendation_id": result["recommendation_id"],
                "outfit_id": result["outfits"][0]["outfit_id"],
                "accepted": True,
                "reason": "시연 리허설: 추천 수락",
            },
        ),
        201,
        "Feedback",
    )
    login = expect(
        client.post("/api/v1/auth/login", json={"email": email, "password": password}), 200, "Relogin"
    )
    headers = {"Authorization": "Bearer " + login["access_token"]}
    if expect(client.get(path, headers=headers), 200, "Saved recommendation") != result:
        raise RuntimeError("Saved recommendation changed unexpectedly")
    events = expect(client.get(path + "/feedback", headers=headers), 200, "Saved feedback")
    if not any(event["feedback_id"] == feedback["feedback_id"] for event in events):
        raise RuntimeError("Feedback was not persisted")
    next_result = expect(
        client.post("/api/v1/recommendations", headers=headers, json={"temperature_c": 20}),
        201,
        "Next recommendation",
    )
    if next_result["is_fallback"] or next_result["provider"] != health["adapters"]["recommender"]:
        raise RuntimeError("Next recommendation used fallback; inspect server logs")
    if not any(
        event["recommendation_id"] == result["recommendation_id"]
        for event in next_result["context"]["recent_feedback"]
    ):
        raise RuntimeError("Recent feedback was not included in the next context")
    return {
        "passed": True,
        "verified_at": datetime.now(UTC).isoformat(),
        "adapters": health["adapters"],
        "contract_version": health["contract_version"],
        "garments": len(garments),
        "images_loaded": images,
        "recommendation_ids": [result["recommendation_id"], next_result["recommendation_id"]],
        "feedback_id": feedback["feedback_id"],
        "recent_feedback_verified": True,
        "total_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=ROOT_DIR / ".demo/reports/rehearsal.json")
    args = parser.parse_args()
    target = urlsplit(args.base_url)
    if target.hostname not in {"127.0.0.1", "localhost", "::1"} or target.scheme not in {"http", "https"}:
        raise RuntimeError("Use a local demo server URL")
    credentials = ROOT_DIR / ".env.demo"
    if not credentials.is_file():
        raise RuntimeError("Run start_demo.ps1 or seed_demo first")
    email, password = load_credentials(credentials)
    with httpx.Client(base_url=args.base_url, timeout=60, trust_env=False) as client:
        report = rehearse(client, email, password)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc) if isinstance(exc, RuntimeError) else f"Rehearsal failed ({type(exc).__name__})")
        raise SystemExit(1) from None
