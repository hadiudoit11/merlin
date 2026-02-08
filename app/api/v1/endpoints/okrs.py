from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from typing import List

from app.core.database import get_session
from app.models.okr import Objective, KeyResult
from app.models.user import User
from app.schemas.okr import (
    ObjectiveCreate, ObjectiveUpdate, ObjectiveResponse, ObjectiveWithKeyResultsResponse,
    KeyResultCreate, KeyResultUpdate, KeyResultResponse
)
from app.api.v1.endpoints.auth import get_current_user

router = APIRouter()


# Objectives
@router.get("/objectives", response_model=List[ObjectiveWithKeyResultsResponse])
async def list_objectives(
    level: str = None,
    status: str = None,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    query = select(Objective).options(selectinload(Objective.key_results)).where(
        Objective.owner_id == current_user.id
    )
    if level:
        query = query.where(Objective.level == level)
    if status:
        query = query.where(Objective.status == status)

    result = await session.execute(query)
    return result.scalars().all()


@router.post("/objectives", response_model=ObjectiveResponse, status_code=status.HTTP_201_CREATED)
async def create_objective(
    objective_data: ObjectiveCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    objective = Objective(
        **objective_data.model_dump(),
        owner_id=current_user.id
    )
    session.add(objective)
    await session.commit()
    await session.refresh(objective)
    return objective


@router.get("/objectives/{objective_id}", response_model=ObjectiveWithKeyResultsResponse)
async def get_objective(
    objective_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    result = await session.execute(
        select(Objective)
        .options(selectinload(Objective.key_results))
        .where(Objective.id == objective_id, Objective.owner_id == current_user.id)
    )
    objective = result.scalar_one_or_none()
    if not objective:
        raise HTTPException(status_code=404, detail="Objective not found")
    return objective


@router.put("/objectives/{objective_id}", response_model=ObjectiveResponse)
async def update_objective(
    objective_id: int,
    objective_data: ObjectiveUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    result = await session.execute(
        select(Objective).where(Objective.id == objective_id, Objective.owner_id == current_user.id)
    )
    objective = result.scalar_one_or_none()
    if not objective:
        raise HTTPException(status_code=404, detail="Objective not found")

    update_data = objective_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(objective, field, value)

    await session.commit()
    await session.refresh(objective)
    return objective


@router.delete("/objectives/{objective_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_objective(
    objective_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    result = await session.execute(
        select(Objective).where(Objective.id == objective_id, Objective.owner_id == current_user.id)
    )
    objective = result.scalar_one_or_none()
    if not objective:
        raise HTTPException(status_code=404, detail="Objective not found")

    await session.delete(objective)
    await session.commit()


# Key Results
@router.post("/key-results", response_model=KeyResultResponse, status_code=status.HTTP_201_CREATED)
async def create_key_result(
    kr_data: KeyResultCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    # Verify objective ownership
    result = await session.execute(
        select(Objective).where(Objective.id == kr_data.objective_id, Objective.owner_id == current_user.id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Objective not found")

    key_result = KeyResult(**kr_data.model_dump())
    session.add(key_result)
    await session.commit()
    await session.refresh(key_result)
    return key_result


@router.put("/key-results/{kr_id}", response_model=KeyResultResponse)
async def update_key_result(
    kr_id: int,
    kr_data: KeyResultUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    result = await session.execute(
        select(KeyResult)
        .join(Objective)
        .where(KeyResult.id == kr_id, Objective.owner_id == current_user.id)
    )
    key_result = result.scalar_one_or_none()
    if not key_result:
        raise HTTPException(status_code=404, detail="Key result not found")

    update_data = kr_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(key_result, field, value)

    await session.commit()
    await session.refresh(key_result)
    return key_result


@router.delete("/key-results/{kr_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_key_result(
    kr_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    result = await session.execute(
        select(KeyResult)
        .join(Objective)
        .where(KeyResult.id == kr_id, Objective.owner_id == current_user.id)
    )
    key_result = result.scalar_one_or_none()
    if not key_result:
        raise HTTPException(status_code=404, detail="Key result not found")

    await session.delete(key_result)
    await session.commit()
