"""Carry recommendation form state per navigation, without cross-tab/account storage."""

from urllib.parse import parse_qsl, urlencode, urlsplit

RECOMMENDATION_FIELDS = frozenset(
    {
        "temp",
        "wardrobe",
        "weather_mode",
        "clothing_season",
        "forecast_token",
        "season",
        "month",
        "occasion",
        "styles",
        "style_text",
        "required",
        "exclude",
        "schedule_id",
        "schedule_version",
        "outerwear_policy",
        "reference_id",
        "reference_version",
    }
)


def safe_back(value: str) -> str:
    """Accept only this app's recommendation form; retain repeated query fields."""
    if len(value) > 16384 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return "/recommend"
    try:
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc or parsed.path != "/recommend":
            return "/recommend"
        pairs = [
            (k, v)
            for k, v in parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=512)
            if k in RECOMMENDATION_FIELDS
        ]
    except ValueError:
        return "/recommend"
    query = urlencode(pairs)
    return "/recommend" + ("?" + query if query else "")


def with_schedule(back: str, plan_id=None, version=None) -> str:
    pairs = [
        (k, v)
        for k, v in parse_qsl(urlsplit(safe_back(back)).query, keep_blank_values=True)
        if k not in ("schedule_id", "schedule_version")
    ]
    if plan_id is not None:
        pairs += [("schedule_id", str(plan_id)), ("schedule_version", str(version))]
    query = urlencode(pairs)
    return "/recommend" + ("?" + query if query else "")


def after_schedule_delete(back: str, deleted_id: str) -> str:
    back = safe_back(back)
    selected = dict(parse_qsl(urlsplit(back).query)).get("schedule_id")
    return with_schedule(back) if selected == deleted_id else back
