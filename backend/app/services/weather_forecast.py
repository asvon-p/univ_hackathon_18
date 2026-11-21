"""Owner-bound weather quotes. Never authentication tokens or stored locations."""

from datetime import timedelta

import jwt

from backend.app.core.config import get_settings
from backend.app.core.errors import AppError, ErrorCode
from shared.weather_forecast import ForecastWindow, refresh_window


def forecast_token(snapshot, user_id):
    if snapshot is None:
        return None
    return jwt.encode(
        {
            "sub": str(user_id),
            "type": "weather_forecast",
            "iat": snapshot.fetched_at,
            "exp": min(snapshot.fetched_at + timedelta(minutes=30), snapshot.end_at),
            "forecast": snapshot.model_dump(mode="json"),
        },
        get_settings().JWT_SECRET,
        algorithm="HS256",
    )


def read_forecast_token(token, user_id):
    try:
        payload = jwt.decode(
            token,
            get_settings().JWT_SECRET,
            algorithms=["HS256"],
            options={"require": ["sub", "type", "exp", "iat", "forecast"]},
        )
        if payload["type"] != "weather_forecast" or payload["sub"] != str(user_id):
            raise ValueError("wrong quote owner or purpose")
        return refresh_window(ForecastWindow.model_validate(payload["forecast"]))
    except (jwt.InvalidTokenError, ValueError, TypeError, KeyError):
        raise AppError(
            422,
            ErrorCode.WEATHER_UNAVAILABLE,
            "예보가 만료되었거나 유효하지 않습니다. 기온 다시 조회를 누르거나 직접 입력해 주세요.",
        ) from None
