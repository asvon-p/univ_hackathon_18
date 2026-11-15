from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import Field
from sqlalchemy.orm import Session

from backend.app.deps import get_current_user_id, get_db
from backend.app.services.day_plans import DayPlanService
from shared.day_plans import DayPlanInput, DayPlanSnapshot

router = APIRouter(prefix="/api/v1/day-plans", tags=["day-plans"])


class DayPlanUpdate(DayPlanInput):
    expected_version: int = Field(ge=1, strict=True)


@router.post("", response_model=DayPlanSnapshot, status_code=201)
def create(body: DayPlanInput, user_id: UUID = Depends(get_current_user_id), db: Session = Depends(get_db)):
    return DayPlanService(db).save(user_id, body)


@router.get("", response_model=list[DayPlanSnapshot])
def list_plans(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user_id: UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    return DayPlanService(db).list(user_id, limit, offset)


@router.get("/{plan_id}", response_model=DayPlanSnapshot)
def get(plan_id: UUID, user_id: UUID = Depends(get_current_user_id), db: Session = Depends(get_db)):
    service = DayPlanService(db)
    return service.view(service.owned(user_id, plan_id))


@router.patch("/{plan_id}", response_model=DayPlanSnapshot)
def update(
    plan_id: UUID,
    body: DayPlanUpdate,
    user_id: UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    return DayPlanService(db).save(user_id, body, plan_id, body.expected_version)


@router.delete("/{plan_id}", status_code=204)
def delete(
    plan_id: UUID,
    expected_version: int = Query(ge=1),
    user_id: UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    DayPlanService(db).delete(user_id, plan_id, expected_version)
    return Response(status_code=204)
