"""Coordinate-free hourly forecast snapshot for the remainder of a Korean day."""

from datetime import UTC, datetime, time, timedelta, timezone

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

KST = timezone(timedelta(hours=9))


class ForecastPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    at: AwareDatetime
    temperature_c: float = Field(ge=-90, le=60, allow_inf_nan=False, strict=True)


class ForecastWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_at: AwareDatetime
    end_at: AwareDatetime
    fetched_at: AwareDatetime
    minimum_c: float = Field(ge=-90, le=60, allow_inf_nan=False)
    maximum_c: float = Field(ge=-90, le=60, allow_inf_nan=False)
    hours: list[ForecastPoint] = Field(min_length=2, max_length=49)

    @model_validator(mode="after")
    def ordered(self):
        if self.minimum_c > self.maximum_c or self.start_at >= self.end_at:
            raise ValueError("invalid forecast range")
        if any(b.at - a.at != timedelta(hours=1) for a, b in zip(self.hours, self.hours[1:], strict=False)):
            raise ValueError("unordered forecast hours")
        return self


def remaining_forecast(hours, fetched_at, *, now=None):
    now = now or datetime.now(UTC)
    local = now.astimezone(KST)
    end = datetime.combine(local.date() + timedelta(days=1), time(), KST)
    current_hour = local.replace(minute=0, second=0, microsecond=0)
    points = [p for p in hours if current_hour <= p.at <= end]
    earlier = [p for p in points if p.at <= now]
    later = [p for p in points if p.at > now]
    if not earlier or not later or points[-1].at < end:
        raise ValueError("incomplete remaining-day forecast")
    # Interpolate only the current instant. Midnight is a support point, never a sampled hour today.
    left, right = earlier[-1], later[0]
    ratio = (now - left.at).total_seconds() / (right.at - left.at).total_seconds()
    current = left.temperature_c + ratio * (right.temperature_c - left.temperature_c)
    values = [current, *(p.temperature_c for p in later if p.at < end)]
    return ForecastWindow(
        start_at=now,
        end_at=end,
        fetched_at=fetched_at,
        minimum_c=round(min(values), 1),
        maximum_c=round(max(values), 1),
        hours=points,
    )


def refresh_window(snapshot, *, now=None):
    now = now or datetime.now(UTC)
    if now >= snapshot.end_at or now < snapshot.fetched_at - timedelta(minutes=1):
        raise ValueError("forecast date expired")
    if now - snapshot.fetched_at > timedelta(minutes=30):
        raise ValueError("forecast needs refresh")
    return remaining_forecast(snapshot.hours, snapshot.fetched_at, now=now)
