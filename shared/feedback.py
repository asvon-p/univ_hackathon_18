"""Feedback categories and conservative thermal phrase recognition, never instructions."""

import re
from typing import Literal

FeedbackCode = Literal["colors", "style", "too_warm", "too_cold", "combination"]
FEEDBACK_LABELS = {
    "colors": "색상 조합이 아쉬워요",
    "style": "제 스타일이 아니에요",
    "too_warm": "더울 것 같아요",
    "too_cold": "추울 것 같아요",
    "combination": "다른 조합을 보고 싶어요",
}


def effective_reason_codes(codes, reason, *, accepted=False):
    result = list(dict.fromkeys(codes or []))
    if accepted or set(result) & {"too_cold", "too_warm"} or not reason:
        return result
    text = re.sub(r"\s+", "", reason.lower())
    # Do not guess the polarity of negated/contrasting thermal statements.
    if re.search(r"춥지|추운건아|안추|덥지|안더|않|아니|않은|하지만|그런데", text):
        return result
    cold = bool(re.search(r"추워|춥|추울|추운|추운데|너무얇|얇아서|too ?cold", text))
    cold |= bool(re.search(r"(?:영하|-[0-9]+(?:도|°c|°)).*(?:반팔|반바지|얇)", text))
    warm = bool(re.search(r"더워|덥|더울|더운|더운데|너무두꺼|두꺼워서|too ?hot|too ?warm", text))
    if cold != warm:
        result.append("too_cold" if cold else "too_warm")
    return result
