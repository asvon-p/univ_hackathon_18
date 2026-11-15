"""Shared demo recommendation settings; never load dotenv or expose the key to the UI."""

import logging

from scripts.openai_credentials import load_key

log = logging.getLogger(__name__)


def recommendation_environment(provider: str = "auto") -> dict[str, str]:
    if provider not in {"auto", "local", "openai"}:
        raise ValueError("Invalid demo recommender")
    key = ""
    if provider != "local":
        try:
            key = load_key()
        except (OSError, RuntimeError, UnicodeError):
            log.warning("OpenAI credential unavailable; local fallback remains enabled")
    return {
        "API_USAGE_ENABLED": "true",
        "RECOMMENDER_PROVIDER": "openai" if key or provider == "openai" else "local",
        "OPENAI_API_KEY": key,
        "LLM_API_KEY": "",
        "OPENAI_MODEL": "gpt-6-luna",
        "OPENAI_REASONING_EFFORT": "low",
        "OPENAI_TIMEOUT_SEC": "12",
        "OPENAI_MAX_OUTPUT_TOKENS": "1200",
        "RECOMMEND_TIMEOUT_SEC": "15",
    }


def frontend_environment(env: dict[str, str]) -> dict[str, str]:
    return {**env, "OPENAI_API_KEY": "", "LLM_API_KEY": ""}
