"""Explicit, user-confirmed feedback categories; free text is not parsed as instructions."""

from typing import Literal

FeedbackCode = Literal["colors", "style", "too_warm", "too_cold", "combination"]
FEEDBACK_LABELS = {
    "colors": "색상 조합이 아쉬워요",
    "style": "제 스타일이 아니에요",
    "too_warm": "더울 것 같아요",
    "too_cold": "추울 것 같아요",
    "combination": "다른 조합을 보고 싶어요",
}
