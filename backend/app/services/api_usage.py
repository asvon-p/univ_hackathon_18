"""Only token receipts; no prompts, credentials, photos or raw model responses."""

import logging
from uuid import UUID, uuid4

from backend.app.db.models import ApiUsage, User
from backend.app.db.session import get_sessionmaker

log = logging.getLogger(__name__)


def begin_usage(user_id, model):
    # Commit before paid HTTP. If receipt storage is unavailable, fail into local recommendation.
    with get_sessionmaker()() as db:
        uid = UUID(user_id)
        uid = uid if db.get(User, uid) else None
        row = ApiUsage(id=uuid4(), user_id=uid, model=model, status="pending")
        db.add(row)
        db.commit()
        return row.id


def estimate(model, usage, tier="default"):
    if model != "gpt-6-luna" or tier not in ("default", "standard", None):
        return None
    incoming, outgoing = usage.get("input_tokens"), usage.get("output_tokens")
    if type(incoming) is not int or type(outgoing) is not int:
        return None
    details = usage.get("input_tokens_details") or {}
    if not isinstance(details, dict):
        return None
    cached = details.get("cached_tokens", 0)
    writes = details.get("cache_creation_tokens", 0)
    if (
        type(cached) is not int
        or type(writes) is not int
        or not 0 <= cached + writes <= incoming
        or min(incoming, outgoing, cached, writes) < 0
    ):
        return None
    multiplier = 2 if incoming > 272000 else 1
    return (
        (incoming - cached - writes) * 0.10 * multiplier
        + cached * 0.01 * multiplier
        + writes * 0.125 * multiplier
        + outgoing * 0.50 * (1.5 if incoming > 272000 else 1)
    ) / 1_000_000


def finish_usage(receipt_id, response=None):
    if receipt_id is None:
        return
    try:
        try:
            data = response.json() if response is not None else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        usage = data.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        with get_sessionmaker()() as db:
            row = db.get(ApiUsage, receipt_id)
            if row is None:
                return
            row.status = (
                ("response_received" if response.status_code == 200 else "http_error")
                if response is not None
                else "network_unknown"
            )
            details = usage.get("input_tokens_details") or {}

            def valid(value):
                return value if type(value) is int and 0 <= value <= 2147483647 else None

            row.input_tokens = valid(usage.get("input_tokens"))
            row.output_tokens = valid(usage.get("output_tokens"))
            row.cached_tokens = valid(details.get("cached_tokens")) if isinstance(details, dict) else None
            row.estimated_usd = estimate(row.model, usage, data.get("service_tier", "default"))
            row.price_basis = (
                "OpenAI standard USD/1M 0.10/0.01/0.50; checked 2026-11-13"
                if row.estimated_usd is not None
                else None
            )
            db.commit()
    except Exception:
        log.warning("API usage receipt remains unknown; consult provider billing")
