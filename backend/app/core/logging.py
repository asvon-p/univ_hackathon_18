import logging


class WeatherQueryFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Uvicorn access 로그에서 날씨 GET의 원 좌표를 제거합니다.
        if isinstance(record.args, tuple) and len(record.args) == 5:
            args = list(record.args)
            if isinstance(args[2], str) and args[2].split("?", 1)[0] == "/api/v1/weather":
                args[2] = "/api/v1/weather"
                record.args = tuple(args)
        return True


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, WeatherQueryFilter) for f in access.filters):
        access.addFilter(WeatherQueryFilter())
    # httpx INFO 로그는 외부 요청 URL에 포함된 좌표를 기록할 수 있습니다.
    logging.getLogger("httpx").setLevel(logging.WARNING)
