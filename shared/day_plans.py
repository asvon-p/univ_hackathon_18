"""Confirmed structured schedules; no inferred locations, indoor weather or dress codes."""

from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Purpose = Literal["class", "presentation", "interview", "work", "exercise", "date", "travel", "other"]
Environment = Literal["indoor", "outdoor", "mixed", "unknown"]
OuterwearPolicy = Literal["always_worn", "removable"]

PURPOSE_LABELS = {
    "class": "수업",
    "presentation": "발표",
    "interview": "면접",
    "work": "아르바이트",
    "exercise": "운동",
    "date": "약속",
    "travel": "이동",
    "other": "기타",
}
ENVIRONMENT_LABELS = {"indoor": "실내", "outdoor": "실외", "mixed": "실내·외 혼합", "unknown": "모름"}


class DayEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_at: AwareDatetime
    end_at: AwareDatetime
    purpose: Purpose
    environment: Environment
    intensity: Literal["low", "normal", "high"] = "normal"
    priority: Literal["normal", "high"] = "normal"
    outdoor_minutes: int | None = Field(default=None, ge=0, le=1440, strict=True)
    temperature_min_c: float | None = Field(default=None, ge=-50, le=60, allow_inf_nan=False)
    temperature_max_c: float | None = Field(default=None, ge=-50, le=60, allow_inf_nan=False)
    required_style: Literal["formal", "sporty"] | None = None

    @model_validator(mode="after")
    def validate_event(self):
        minutes = (self.end_at - self.start_at).total_seconds() / 60
        if not 1 <= minutes <= 1440:
            raise ValueError("일정 종료는 시작보다 늦어야 하며 최소 1분이어야 합니다")
        if (self.temperature_min_c is None) != (self.temperature_max_c is None):
            raise ValueError("기온 범위의 최저·최고를 함께 입력하세요")
        if self.temperature_min_c is not None and self.temperature_min_c > self.temperature_max_c:
            raise ValueError("최저 기온은 최고 기온보다 높을 수 없습니다")
        if self.outdoor_minutes is not None and (
            self.environment != "mixed" or self.outdoor_minutes > minutes
        ):
            raise ValueError("야외 시간은 혼합 일정에서 일정 길이 이내로 입력하세요")
        return self


class DayPlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    timezone: str = Field(default="Asia/Seoul", max_length=80)
    events: list[DayEvent] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def validate_day(self):
        try:
            zone = ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("유효한 IANA 시간대를 입력하세요") from None
        ordered = sorted(self.events, key=lambda e: e.start_at)
        midnight = datetime.combine(self.date + timedelta(days=1), time(), zone)
        for event in ordered:
            start, end = event.start_at.astimezone(zone), event.end_at.astimezone(zone)
            if start.date() != self.date or (end.date() != self.date and end != midnight):
                raise ValueError("한 날짜의 일정만 저장하세요. 자정을 넘으면 날짜별로 나누세요")
            if event.start_at.utcoffset() != start.utcoffset() or event.end_at.utcoffset() != end.utcoffset():
                raise ValueError("시각의 UTC 오프셋과 선택한 시간대가 다릅니다")
        if any(a.end_at > b.start_at for a, b in zip(ordered, ordered[1:], strict=False)):
            raise ValueError("일정 시간이 겹칩니다. 시작·종료 시각을 수정하세요")
        self.events = ordered
        return self


class DayPlanSnapshot(DayPlanInput):
    id: str
    version: int = Field(ge=1)


class WearingStep(BaseModel):
    event_index: int
    start_at: AwareDatetime
    end_at: AwareDatetime
    purpose: Purpose
    environment: Environment
    outerwear_action: Literal["wear", "carry", "none", "adjust"]
    suitability_score: float
    note: str
