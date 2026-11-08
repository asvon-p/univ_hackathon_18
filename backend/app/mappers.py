"""ORM → API 표시 모델 변환. 팀 공통 계약은 변경하지 않습니다."""

from datetime import UTC, datetime

from backend.app.db.models import FeedbackEvent, Garment, Recommendation
from backend.app.schemas.feedback import FeedbackResponse
from backend.app.schemas.garment import GarmentView
from backend.app.schemas.recommendation import RecommendationResponse
from shared.contracts import FeedbackEvent as FeedbackContract
from shared.contracts import GarmentAttributes, Outfit, OutfitItems, UserContext


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def garment_view(garment: Garment) -> GarmentView:
    attributes = garment.attributes
    return GarmentView(
        garment_id=str(garment.id),
        name=garment.name,
        location=garment.location,
        category=attributes.category,
        subcategory=attributes.subcategory,
        color=attributes.color,
        pattern=attributes.pattern,
        style_candidates=attributes.style_candidates,
        thermal_level=attributes.thermal_level,
        status=garment.status,
        cutout_path=garment.cutout_path,
        image_url=f"/media/{garment.original_path}",
        cutout_url=f"/media/{garment.cutout_path}" if garment.cutout_path else None,
        analysis_status=garment.analysis_status,
        source=attributes.source,
        model_version=attributes.model_version,
        error=attributes.error,
        analyzed_at=_utc(attributes.analyzed_at),
        created_at=_utc(garment.created_at),
        updated_at=_utc(garment.updated_at),
    )


def garment_contract(garment: Garment) -> GarmentAttributes:
    return GarmentAttributes.model_validate(
        garment_view(garment).model_dump(include=set(GarmentAttributes.model_fields))
    )


def recommendation_view(record: Recommendation, garments: dict[str, GarmentView]) -> RecommendationResponse:
    outfits = [
        Outfit(
            outfit_id=str(row.id),
            items=OutfitItems(
                top=str(row.top_id),
                bottom=str(row.bottom_id),
                shoes=str(row.shoes_id) if row.shoes_id else None,
                outerwear=str(row.outerwear_id) if row.outerwear_id else None,
            ),
            style_tags=row.style_tags,
            reason=row.reason,
            score=row.score,
        )
        for row in record.outfits
    ]
    used = {gid for outfit in outfits for gid in outfit.items.model_dump().values() if gid}
    return RecommendationResponse(
        recommendation_id=str(record.id),
        outfits=outfits,
        garments={gid: garments[gid] for gid in sorted(used) if gid in garments},
        context=UserContext.model_validate(record.context),
        weather=record.context.get("weather"),
        provider=record.provider,
        is_fallback=record.provider == "fallback",
        latency_ms=record.latency_ms,
        created_at=_utc(record.created_at),
    )


def feedback_view(record: FeedbackEvent) -> FeedbackResponse:
    return FeedbackResponse(
        feedback_id=record.id,
        recommendation_id=record.recommendation_id,
        outfit_id=record.outfit_id,
        accepted=record.accepted,
        reason=record.reason,
        created_at=_utc(record.created_at),
    )


def feedback_contract(record: FeedbackEvent) -> FeedbackContract:
    return FeedbackContract(
        recommendation_id=str(record.recommendation_id),
        outfit_id=str(record.outfit_id),
        accepted=record.accepted,
        reason=record.reason,
    )
