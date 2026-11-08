"""APP_ENV=demo python -m scripts.seed_demo: 시연 계정과 의류 12벌 추가.

기존 사용자/의류를 초기화하지 않습니다. 비밀번호는 git에서 제외된 .env.demo에만 보관합니다.
"""

import json
import secrets
from pathlib import Path
from uuid import uuid4, uuid5

from dotenv import dotenv_values
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import ROOT_DIR, get_settings
from backend.app.core.security import hash_password, verify_password
from backend.app.db.models import Garment, GarmentAttribute, StorageDeletionJob, User, UserProfile
from backend.app.db.session import get_sessionmaker
from backend.app.schemas.auth import SignupRequest
from backend.app.schemas.garment import ANALYSIS_FIELDS
from backend.app.storage.local import LocalStorage
from scripts.demo_assets import CATALOG, garment_images


def load_credentials(path: Path) -> tuple[str, str]:
    if not path.exists():
        data = f"DEMO_EMAIL=demo@example.com\nDEMO_PASSWORD={secrets.token_urlsafe(24)}\n"
        # 기존 파일을 덮어쓰지 않습니다. 값은 표준 출력/로그에 기록하지 않습니다.
        with path.open("x", encoding="utf-8") as stream:
            stream.write(data)
    values = dotenv_values(path)
    email = values.get("DEMO_EMAIL")
    password = values.get("DEMO_PASSWORD")
    if not email or not password:
        raise RuntimeError(".env.demo에 DEMO_EMAIL과 DEMO_PASSWORD를 설정하세요")
    try:
        signup = SignupRequest(email=email, password=password, nickname="시연 사용자")
    except ValueError:
        # ValidationError의 원문에 입력된 비밀번호가 포함될 수 있습니다.
        raise RuntimeError(".env.demo의 이메일 또는 비밀번호 형식이 올바르지 않습니다") from None
    return str(signup.email), signup.password.get_secret_value()


def seed_demo(db: Session, storage: LocalStorage, email: str, password: str) -> dict:
    signup = SignupRequest(email=email, password=password, nickname="시연 사용자")
    email = str(signup.email)
    created_files = []
    created = 0
    try:
        existing = db.scalar(select(User).where(User.email == email).with_for_update())
        if existing is not None:
            if not existing.is_demo:
                raise RuntimeError(
                    "같은 이메일의 일반 계정이 있습니다. .env.demo에서 다른 이메일을 사용하세요"
                )
            if not existing.is_active or not verify_password(password, existing.password_hash):
                raise RuntimeError(
                    "시연 계정의 상태/비밀번호가 일치하지 않습니다. 기존 계정은 변경하지 않았습니다"
                )
            user_id = existing.id
        else:
            # 탈퇴 후 같은 시연 이메일로 재생성해도 기존 JWT의 sub를 다시 쓰지 않습니다.
            user_id = uuid4()
            db.add(
                User(
                    id=user_id,
                    email=email,
                    password_hash=hash_password(password),
                    nickname="시연 사용자",
                    is_demo=True,
                )
            )
            db.flush()
            db.add(UserProfile(user_id=user_id, preferred_styles=["minimal", "casual"], disliked_colors=[]))
        for item in CATALOG:
            garment_id = uuid5(user_id, item.key)
            # 같은 UUID를 재생성하기 전에 진행 중인 정리를 기다리고 오래된 작업을 취소합니다.
            pending = db.get(StorageDeletionJob, garment_id, with_for_update=True)
            if pending is not None:
                db.delete(pending)
            prefix = f"{user_id}/{garment_id}"
            original_key, cutout_key = f"{prefix}/original.jpg", f"{prefix}/cutout.png"
            garment = db.get(Garment, garment_id)
            if garment is None:
                garment = Garment(
                    id=garment_id,
                    user_id=user_id,
                    status=item.status,
                    analysis_status="done",
                    original_path=original_key,
                    cutout_path=cutout_key,
                    attributes=GarmentAttribute(
                        category=item.category,
                        subcategory=item.subcategory,
                        color=item.color,
                        pattern="solid",
                        style_candidates=[item.style],
                        thermal_level=item.thermal,
                        source="user",
                        user_edited_fields=sorted(ANALYSIS_FIELDS),
                        model_version="demo-seed-v1",
                        raw_output={"synthetic": True, "catalog_key": item.key},
                    ),
                )
                db.add(garment)
                created += 1
            elif garment.user_id != user_id or garment.original_path != original_key:
                raise RuntimeError("시연 의류 ID 충돌: 기존 데이터를 변경하지 않았습니다")
            original, cutout = garment_images(item)
            for key, data in ((original_key, original), (cutout_key, cutout)):
                if key not in (garment.original_path, garment.cutout_path):
                    continue
                if not storage.exists(key):
                    storage.save(key, data)
                    created_files.append(Path(storage.local_path(key)))
        db.commit()
    except Exception:
        db.rollback()
        for path in created_files:
            path.unlink(missing_ok=True)  # 이번 실행이 만든 개별 파일만 정리합니다.
        raise
    return {
        "user_id": str(user_id),
        "catalog_size": len(CATALOG),
        "created_garments": created,
        "created_files": len(created_files),
        "credentials_file": ".env.demo",
    }


def main() -> int:
    settings = get_settings()
    if settings.APP_ENV != "demo":
        raise RuntimeError("시연 데이터는 APP_ENV=demo에서 실행하세요")
    email, password = load_credentials(ROOT_DIR / ".env.demo")
    with get_sessionmaker()() as db:
        result = seed_demo(db, LocalStorage(settings.STORAGE_DIR), email, password)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    # 콘솔에 DB URL, 인증 정보 또는 SQL 파라미터를 포함한 traceback을 노출하지 않습니다.
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError) as exc:
        print(str(exc))
        raise SystemExit(1) from None
    except Exception as exc:
        print(f"Seed failed ({type(exc).__name__}). Check DB migration and .env.demo configuration.")
        raise SystemExit(1) from None
