"""API endpoints for task management."""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

from app.core.database import get_session
from app.api.v1.endpoints.auth import get_current_user
from app.models.user import User
from app.models.task import Task, TaskStatus, TaskPriority, TaskSource
from app.services.settings_service import SettingsService

router = APIRouter()


# ============ Schemas ============

class TaskBase(BaseModel):
    title: str
    description: Optional[str] = None
    assignee_name: Optional[str] = None
    assignee_email: Optional[str] = None
    due_date: Optional[datetime] = None
    due_date_text: Optional[str] = None
    priority: str = TaskPriority.MEDIUM.value
    canvas_id: Optional[int] = None
    tags: List[str] = []


class TaskCreate(TaskBase):
    pass


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    assignee_name: Optional[str] = None
    assignee_email: Optional[str] = None
    due_date: Optional[datetime] = None
    due_date_text: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    canvas_id: Optional[int] = None
    tags: Optional[List[str]] = None


class LinkedNode(BaseModel):
    id: int
    name: str
    node_type: str

    class Config:
        from_attributes = True


class TaskResponse(BaseModel):
    id: int
    title: str
    description: Optional[str]
    assignee_name: Optional[str]
    assignee_email: Optional[str]
    due_date: Optional[datetime]
    due_date_text: Optional[str]
    status: str
    priority: str
    source: str
    source_id: Optional[str]
    source_url: Optional[str]
    context: Optional[str]
    canvas_id: Optional[int]
    tags: List[str]
    is_overdue: bool
    linked_nodes: List[LinkedNode] = []
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class TaskListResponse(BaseModel):
    tasks: List[TaskResponse]
    total: int
    page: int
    page_size: int


class TaskStats(BaseModel):
    total: int
    pending: int
    in_progress: int
    completed: int
    overdue: int


# ============ Endpoints ============

@router.get("/", response_model=TaskListResponse)
async def list_tasks(
    status: Optional[str] = Query(None, description="Filter by status"),
    priority: Optional[str] = Query(None, description="Filter by priority"),
    source: Optional[str] = Query(None, description="Filter by source"),
    canvas_id: Optional[int] = Query(None, description="Filter by canvas"),
    assignee_email: Optional[str] = Query(None, description="Filter by assignee email"),
    overdue: Optional[bool] = Query(None, description="Filter overdue tasks"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """List tasks with optional filters."""
    # Get user's org
    org_id = await SettingsService.get_user_organization_id(session, current_user.id)

    # Build query
    query = select(Task).options(selectinload(Task.linked_nodes))

    if org_id:
        query = query.where(Task.organization_id == org_id)
    else:
        query = query.where(Task.user_id == current_user.id)

    # Apply filters
    if status:
        query = query.where(Task.status == status)
    if priority:
        query = query.where(Task.priority == priority)
    if source:
        query = query.where(Task.source == source)
    if canvas_id:
        query = query.where(Task.canvas_id == canvas_id)
    if assignee_email:
        query = query.where(Task.assignee_email == assignee_email)
    if overdue is True:
        query = query.where(
            Task.due_date < datetime.utcnow(),
            Task.status != TaskStatus.COMPLETED.value
        )

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await session.execute(count_query)
    total = total_result.scalar() or 0

    # Paginate
    query = query.order_by(Task.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await session.execute(query)
    tasks = list(result.scalars().all())

    return TaskListResponse(
        tasks=[TaskResponse.model_validate(t) for t in tasks],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/stats", response_model=TaskStats)
async def get_task_stats(
    canvas_id: Optional[int] = Query(None, description="Filter by canvas"),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get task statistics."""
    org_id = await SettingsService.get_user_organization_id(session, current_user.id)

    # Base query
    base_filter = []
    if org_id:
        base_filter.append(Task.organization_id == org_id)
    else:
        base_filter.append(Task.user_id == current_user.id)

    if canvas_id:
        base_filter.append(Task.canvas_id == canvas_id)

    # Total count
    total_result = await session.execute(
        select(func.count()).where(*base_filter)
    )
    total = total_result.scalar() or 0

    # Status counts
    pending_result = await session.execute(
        select(func.count()).where(*base_filter, Task.status == TaskStatus.PENDING.value)
    )
    pending = pending_result.scalar() or 0

    in_progress_result = await session.execute(
        select(func.count()).where(*base_filter, Task.status == TaskStatus.IN_PROGRESS.value)
    )
    in_progress = in_progress_result.scalar() or 0

    completed_result = await session.execute(
        select(func.count()).where(*base_filter, Task.status == TaskStatus.COMPLETED.value)
    )
    completed = completed_result.scalar() or 0

    # Overdue count
    overdue_result = await session.execute(
        select(func.count()).where(
            *base_filter,
            Task.due_date < datetime.utcnow(),
            Task.status != TaskStatus.COMPLETED.value
        )
    )
    overdue = overdue_result.scalar() or 0

    return TaskStats(
        total=total,
        pending=pending,
        in_progress=in_progress,
        completed=completed,
        overdue=overdue,
    )


@router.post("/", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    task_data: TaskCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Create a new task manually."""
    org_id = await SettingsService.get_user_organization_id(session, current_user.id)

    task = Task(
        organization_id=org_id,
        user_id=current_user.id,
        title=task_data.title,
        description=task_data.description,
        assignee_name=task_data.assignee_name,
        assignee_email=task_data.assignee_email,
        due_date=task_data.due_date,
        due_date_text=task_data.due_date_text,
        priority=task_data.priority,
        canvas_id=task_data.canvas_id,
        tags=task_data.tags,
        source=TaskSource.MANUAL.value,
        status=TaskStatus.PENDING.value,
    )

    session.add(task)
    await session.commit()
    await session.refresh(task)

    # For a new task, linked_nodes is empty - avoid lazy loading issue
    return TaskResponse(
        id=task.id,
        title=task.title,
        description=task.description,
        assignee_name=task.assignee_name,
        assignee_email=task.assignee_email,
        due_date=task.due_date,
        due_date_text=task.due_date_text,
        status=task.status,
        priority=task.priority,
        source=task.source,
        source_id=task.source_id,
        source_url=task.source_url,
        context=task.context,
        canvas_id=task.canvas_id,
        tags=task.tags or [],
        is_overdue=task.is_overdue,
        linked_nodes=[],  # New task has no linked nodes
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get a specific task."""
    org_id = await SettingsService.get_user_organization_id(session, current_user.id)

    query = select(Task).options(selectinload(Task.linked_nodes)).where(Task.id == task_id)

    if org_id:
        query = query.where(Task.organization_id == org_id)
    else:
        query = query.where(Task.user_id == current_user.id)

    result = await session.execute(query)
    task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found"
        )

    return TaskResponse.model_validate(task)


@router.put("/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: int,
    task_data: TaskUpdate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Update a task."""
    org_id = await SettingsService.get_user_organization_id(session, current_user.id)

    query = select(Task).options(selectinload(Task.linked_nodes)).where(Task.id == task_id)

    if org_id:
        query = query.where(Task.organization_id == org_id)
    else:
        query = query.where(Task.user_id == current_user.id)

    result = await session.execute(query)
    task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found"
        )

    # Update fields
    update_data = task_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(task, field, value)

    # Track completion
    if task_data.status == TaskStatus.COMPLETED.value and not task.completed_at:
        task.completed_at = datetime.utcnow()

    await session.commit()

    # Re-fetch with relationship loaded
    result = await session.execute(
        select(Task).options(selectinload(Task.linked_nodes)).where(Task.id == task_id)
    )
    task = result.scalar_one()

    return TaskResponse.model_validate(task)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Delete a task."""
    org_id = await SettingsService.get_user_organization_id(session, current_user.id)

    query = select(Task).where(Task.id == task_id)

    if org_id:
        query = query.where(Task.organization_id == org_id)
    else:
        query = query.where(Task.user_id == current_user.id)

    result = await session.execute(query)
    task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found"
        )

    await session.delete(task)
    await session.commit()


@router.post("/{task_id}/link/{node_id}", response_model=TaskResponse)
async def link_task_to_node(
    task_id: int,
    node_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Link a task to a canvas node."""
    from app.models.node import Node

    org_id = await SettingsService.get_user_organization_id(session, current_user.id)

    # Get task
    query = select(Task).options(selectinload(Task.linked_nodes)).where(Task.id == task_id)
    if org_id:
        query = query.where(Task.organization_id == org_id)
    else:
        query = query.where(Task.user_id == current_user.id)

    result = await session.execute(query)
    task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found"
        )

    # Get node
    node_result = await session.execute(
        select(Node).where(Node.id == node_id)
    )
    node = node_result.scalar_one_or_none()

    if not node:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Node not found"
        )

    # Link if not already linked
    if node not in task.linked_nodes:
        task.linked_nodes.append(node)
        await session.commit()

    # Re-fetch with relationship loaded
    result = await session.execute(
        select(Task).options(selectinload(Task.linked_nodes)).where(Task.id == task_id)
    )
    task = result.scalar_one()

    return TaskResponse.model_validate(task)


@router.delete("/{task_id}/link/{node_id}", response_model=TaskResponse)
async def unlink_task_from_node(
    task_id: int,
    node_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Remove link between task and node."""
    from app.models.node import Node

    org_id = await SettingsService.get_user_organization_id(session, current_user.id)

    # Get task
    query = select(Task).options(selectinload(Task.linked_nodes)).where(Task.id == task_id)
    if org_id:
        query = query.where(Task.organization_id == org_id)
    else:
        query = query.where(Task.user_id == current_user.id)

    result = await session.execute(query)
    task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found"
        )

    # Find and remove node link
    for node in task.linked_nodes:
        if node.id == node_id:
            task.linked_nodes.remove(node)
            await session.commit()
            break

    # Re-fetch with relationship loaded
    result = await session.execute(
        select(Task).options(selectinload(Task.linked_nodes)).where(Task.id == task_id)
    )
    task = result.scalar_one()

    return TaskResponse.model_validate(task)
