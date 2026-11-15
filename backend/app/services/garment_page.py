"""Bounded wardrobe reads. Recommendation's full wardrobe query stays separate."""

import base64
import hashlib
import json
from datetime import datetime
from uuid import UUID

from sqlalchemy import String, and_, case, cast, func, literal, or_, select
from sqlalchemy.orm import contains_eager

from backend.app.core.errors import AppError, ErrorCode
from backend.app.db.models import Garment as G
from backend.app.db.models import GarmentAttribute as A
from backend.app.mappers import garment_view
from shared.garment_terms import COLOR_WORDS, LABELS


def search_condition(query):
    fields = [func.coalesce(G.name, ""), func.coalesce(G.location, "")]
    for key in ("category", "subcategory", "color", "pattern", "status"):
        col = G.status if key == "status" else getattr(A, key)
        fields.extend([func.coalesce(col, ""), case(LABELS[key], value=col, else_="")])
    fields.append(case(COLOR_WORDS, value=A.color, else_=""))
    styles = cast(A.style_candidates, String)
    fields.append(func.coalesce(styles, ""))
    fields.extend(
        case((styles.contains(code, autoescape=True), label), else_="")
        for code, label in LABELS["style"].items()
    )
    text = literal("")
    for field in fields:
        text = text + literal(" ") + field
    text = func.lower(func.replace(func.replace(text, "_", " "), "-", " "))
    compact = func.replace(text, " ", "")
    return and_(
        *(
            or_(text.contains(t, autoescape=True), compact.contains(t, autoescape=True))
            for t in query.lower().split()
        )
    )


def garment_page(db, user_id, *, category=None, status=None, q="", cursor=None, limit=24):
    q = " ".join(q.split())
    scope = hashlib.sha256(f"{user_id}|{category}|{status}|{q}".encode()).hexdigest()[:24]
    boundary = None
    if cursor:
        try:
            data = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            if data["scope"] != scope:
                raise ValueError()
            stamp = datetime.fromisoformat(data["at"])
            if stamp.tzinfo is None:
                raise ValueError()
            boundary = (stamp, UUID(data["id"]))
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise AppError(
                422, ErrorCode.VALIDATION_ERROR, "목록 위치가 유효하지 않습니다. 옷장을 새로 열어 주세요."
            ) from None
    conditions = [G.user_id == user_id]
    if status:
        conditions.append(G.status == status)
    if q:
        conditions.append(search_condition(q))
    counts = dict(
        db.execute(
            select(A.category, func.count())
            .select_from(G)
            .join(G.attributes)
            .where(*conditions)
            .group_by(A.category)
        ).all()
    )
    total = db.scalar(select(func.count()).select_from(G).where(G.user_id == user_id))
    all_count = sum(counts.values())
    matched = counts.get(category, 0) if category else all_count
    if category:
        conditions.append(A.category == category)
    if boundary:
        at, gid = boundary
        conditions.append(or_(G.created_at < at, and_(G.created_at == at, G.id < gid)))
    rows = list(
        db.scalars(
            select(G)
            .join(G.attributes)
            .options(contains_eager(G.attributes))
            .where(*conditions)
            .order_by(G.created_at.desc(), G.id.desc())
            .limit(limit + 1)
        )
    )
    more = len(rows) > limit
    rows = rows[:limit]
    views = [garment_view(row) for row in rows]
    next_cursor = None
    if more:
        last = views[-1]
        data = {"scope": scope, "at": last.created_at.isoformat(), "id": last.garment_id}
        next_cursor = base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
    return {
        "items": views,
        "next_cursor": next_cursor,
        "has_more": more,
        "total": total,
        "all_count": all_count,
        "matched_count": matched,
        "counts": {c: counts.get(c, 0) for c in LABELS["category"]},
    }
