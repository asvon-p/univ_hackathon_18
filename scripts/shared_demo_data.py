"""Merge an offline mentor snapshot into the local store without replacing existing rows."""

import hashlib
from pathlib import Path

from sqlalchemy import select, text

from backend.app.db import models  # noqa: F401 - register tables
from backend.app.db.base import Base

LOCAL_DATABASE = "postgresql+psycopg://fitjang:fitjang@127.0.0.1:5432/fitjang_test"
LOCAL_SCHEMA = "ui_preview_local"


def merge_data(source, target, source_root: Path, target_root: Path):
    """Call with web writers stopped. Roll back DB and newly copied files on failure.

    Existing IDs must be byte-for-byte equivalent; email conflicts abort. Source stays intact.
    Cleanup queues must be empty before switching stores.
    """
    source_root, target_root = source_root.resolve(), target_root.resolve()
    if source_root == target_root:
        raise ValueError("Source and target storage must differ")
    created = []
    counts = {}
    try:
        with source.connect() as src, target.begin() as dst:
            if src.scalar(text("SELECT version_num FROM alembic_version")) != dst.scalar(
                text("SELECT version_num FROM alembic_version")
            ):
                raise RuntimeError("Migration versions differ")
            for table in Base.metadata.sorted_tables:
                rows = src.execute(select(table)).mappings().all()
                if table.name.endswith("deletion_jobs") and rows:
                    raise RuntimeError("Finish source image cleanup before merging")
                count = 0
                for row in rows:
                    condition = [column == row[column.name] for column in table.primary_key]
                    existing = dst.execute(select(table).where(*condition)).mappings().one_or_none()
                    if existing is not None:
                        if dict(existing) != dict(row):
                            raise RuntimeError(f"Conflicting existing row in {table.name}")
                        continue
                    dst.execute(table.insert().values(**dict(row)))
                    count += 1
                counts[table.name] = count
            for path in source_root.rglob("*"):
                if not path.is_file():
                    continue
                if path.is_symlink() or path.name.lower() == ".env":
                    raise RuntimeError("Unexpected source storage file")
                relative = path.relative_to(source_root)
                destination = (target_root / relative).resolve()
                if not destination.is_relative_to(target_root):
                    raise RuntimeError("Storage path escapes target")
                content = path.read_bytes()
                if destination.exists():
                    if hashlib.sha256(destination.read_bytes()).digest() != hashlib.sha256(content).digest():
                        raise RuntimeError("Conflicting existing image")
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("xb") as output:
                    created.append(destination)
                    output.write(content)
    except BaseException:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise
    return {"rows_added": counts, "files_added": len(created)}
