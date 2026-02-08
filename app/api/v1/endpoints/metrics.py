from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from datetime import datetime

from app.core.database import get_session
from app.models.okr import Metric
from app.models.user import User
from app.schemas.metric import MetricCreate, MetricUpdate, MetricResponse, MetricValueUpdate
from app.api.v1.endpoints.auth import get_current_user

router = APIRouter()


@router.get("/", response_model=List[MetricResponse])
async def list_metrics(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    result = await session.execute(
        select(Metric).where(Metric.owner_id == current_user.id)
    )
    return result.scalars().all()


@router.post("/", response_model=MetricResponse, status_code=status.HTTP_201_CREATED)
async def create_metric(
    metric_data: MetricCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    metric = Metric(
        **metric_data.model_dump(),
        owner_id=current_user.id
    )
    session.add(metric)
    await session.commit()
    await session.refresh(metric)
    return metric


@router.get("/{metric_id}", response_model=MetricResponse)
async def get_metric(
    metric_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    result = await session.execute(
        select(Metric).where(Metric.id == metric_id, Metric.owner_id == current_user.id)
    )
    metric = result.scalar_one_or_none()
    if not metric:
        raise HTTPException(status_code=404, detail="Metric not found")
    return metric


@router.put("/{metric_id}", response_model=MetricResponse)
async def update_metric(
    metric_id: int,
    metric_data: MetricUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    result = await session.execute(
        select(Metric).where(Metric.id == metric_id, Metric.owner_id == current_user.id)
    )
    metric = result.scalar_one_or_none()
    if not metric:
        raise HTTPException(status_code=404, detail="Metric not found")

    update_data = metric_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(metric, field, value)

    await session.commit()
    await session.refresh(metric)
    return metric


@router.patch("/{metric_id}/value", response_model=MetricResponse)
async def update_metric_value(
    metric_id: int,
    value_update: MetricValueUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    result = await session.execute(
        select(Metric).where(Metric.id == metric_id, Metric.owner_id == current_user.id)
    )
    metric = result.scalar_one_or_none()
    if not metric:
        raise HTTPException(status_code=404, detail="Metric not found")

    # Add to history
    history = metric.history or []
    history.append({
        "timestamp": datetime.utcnow().isoformat(),
        "value": metric.value
    })
    metric.history = history

    # Update current value
    metric.value = value_update.value
    metric.last_refreshed = datetime.utcnow()

    await session.commit()
    await session.refresh(metric)
    return metric


@router.delete("/{metric_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_metric(
    metric_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    result = await session.execute(
        select(Metric).where(Metric.id == metric_id, Metric.owner_id == current_user.id)
    )
    metric = result.scalar_one_or_none()
    if not metric:
        raise HTTPException(status_code=404, detail="Metric not found")

    await session.delete(metric)
    await session.commit()
