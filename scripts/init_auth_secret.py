"""로컬 .env의 비어 있거나 예제인 JWT 키만 초기화합니다. 키는 출력하지 않습니다."""

import secrets
from pathlib import Path

from dotenv import dotenv_values, set_key


def main() -> None:
    path = Path(__file__).resolve().parents[1] / ".env"
    if not path.is_file():
        raise SystemExit("먼저 .env.example을 .env로 복사하세요.")
    value = dotenv_values(path).get("JWT_SECRET") or ""
    if len(value.encode("utf-8")) >= 32 and not value.startswith("change-me"):
        print("JWT secret already configured; preserved.")
        return
    set_key(str(path), "JWT_SECRET", secrets.token_urlsafe(48))
    print("Generated a local JWT secret in .env (value not displayed).")


if __name__ == "__main__":
    main()
