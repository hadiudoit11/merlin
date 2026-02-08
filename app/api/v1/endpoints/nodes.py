from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
import logging

from app.core.database import get_session, async_session_maker
from app.models.node import Node, NodeConnection
from app.models.canvas import Canvas
from app.models.user import User
from app.schemas.node import (
    NodeCreate, NodeUpdate, NodeResponse,
    NodeConnectionCreate, NodeConnectionResponse,
    NodePositionUpdate, NodeBatchPositionUpdate
)
from app.api.v1.endpoints.auth import get_current_user
from app.services.indexing_service import CanvasIndexingService

router = APIRouter()
logger = logging.getLogger(__name__)


async def background_index_node(node_id: int, user_id: int):
    """Background task to index a single node."""
    try:
        async with async_session_maker() as session:
            await CanvasIndexingService.index_node(session, node_id, user_id)
            logger.info(f"Indexed node {node_id}")
    except Exception as e:
        logger.warning(f"Failed to index node {node_id}: {e}")


async def background_delete_node_index(node_id: int, user_id: int):
    """Background task to remove a node from the index."""
    try:
        async with async_session_maker() as session:
            await CanvasIndexingService.delete_node_from_index(session, node_id, user_id)
            logger.info(f"Removed node {node_id} from index")
    except Exception as e:
        logger.warning(f"Failed to remove node {node_id} from index: {e}")


async def verify_canvas_access(canvas_id: int, user_id: int, session: AsyncSession) -> Canvas:
    result = await session.execute(
        select(Canvas).where(Canvas.id == canvas_id, Canvas.owner_id == user_id)
    )
    canvas = result.scalar_one_or_none()
    if not canvas:
        raise HTTPException(status_code=404, detail="Canvas not found")
    return canvas


@router.get("/", response_model=List[NodeResponse])
async def list_nodes(
    canvas_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    await verify_canvas_access(canvas_id, current_user.id, session)
    result = await session.execute(
        select(Node).where(Node.canvas_id == canvas_id)
    )
    return result.scalars().all()


@router.post("/", response_model=NodeResponse, status_code=status.HTTP_201_CREATED)
async def create_node(
    node_data: NodeCreate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    await verify_canvas_access(node_data.canvas_id, current_user.id, session)

    node = Node(**node_data.model_dump())
    session.add(node)
    await session.commit()
    await session.refresh(node)

    # Index node in background
    background_tasks.add_task(background_index_node, node.id, current_user.id)

    return node


@router.get("/{node_id}", response_model=NodeResponse)
async def get_node(
    node_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    result = await session.execute(select(Node).where(Node.id == node_id))
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    await verify_canvas_access(node.canvas_id, current_user.id, session)
    return node


@router.put("/{node_id}", response_model=NodeResponse)
async def update_node(
    node_id: int,
    node_data: NodeUpdate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    result = await session.execute(select(Node).where(Node.id == node_id))
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    await verify_canvas_access(node.canvas_id, current_user.id, session)

    update_data = node_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(node, field, value)

    await session.commit()
    await session.refresh(node)

    # Re-index node if content changed
    if "content" in update_data or "name" in update_data:
        background_tasks.add_task(background_index_node, node.id, current_user.id)

    return node


@router.patch("/{node_id}/position", response_model=NodeResponse)
async def update_node_position(
    node_id: int,
    position: NodePositionUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    result = await session.execute(select(Node).where(Node.id == node_id))
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    await verify_canvas_access(node.canvas_id, current_user.id, session)

    node.position_x = position.position_x
    node.position_y = position.position_y

    await session.commit()
    await session.refresh(node)
    return node


@router.patch("/batch/positions")
async def batch_update_positions(
    batch: NodeBatchPositionUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    for node_update in batch.nodes:
        node_id = node_update.get("id")
        result = await session.execute(select(Node).where(Node.id == node_id))
        node = result.scalar_one_or_none()
        if node:
            node.position_x = node_update.get("position_x", node.position_x)
            node.position_y = node_update.get("position_y", node.position_y)

    await session.commit()
    return {"status": "updated", "count": len(batch.nodes)}


@router.delete("/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(
    node_id: int,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    result = await session.execute(select(Node).where(Node.id == node_id))
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    await verify_canvas_access(node.canvas_id, current_user.id, session)

    # Remove from index in background
    background_tasks.add_task(background_delete_node_index, node_id, current_user.id)

    await session.delete(node)
    await session.commit()


# Node Connections
@router.post("/connections", response_model=NodeConnectionResponse, status_code=status.HTTP_201_CREATED)
async def create_connection(
    connection_data: NodeConnectionCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    # Verify both nodes exist and user has access
    source_result = await session.execute(select(Node).where(Node.id == connection_data.source_node_id))
    source_node = source_result.scalar_one_or_none()
    if not source_node:
        raise HTTPException(status_code=404, detail="Source node not found")

    target_result = await session.execute(select(Node).where(Node.id == connection_data.target_node_id))
    target_node = target_result.scalar_one_or_none()
    if not target_node:
        raise HTTPException(status_code=404, detail="Target node not found")

    await verify_canvas_access(source_node.canvas_id, current_user.id, session)

    connection = NodeConnection(**connection_data.model_dump())
    session.add(connection)
    await session.commit()
    await session.refresh(connection)
    return connection


@router.delete("/connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    connection_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    result = await session.execute(select(NodeConnection).where(NodeConnection.id == connection_id))
    connection = result.scalar_one_or_none()
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    # Verify access via source node
    source_result = await session.execute(select(Node).where(Node.id == connection.source_node_id))
    source_node = source_result.scalar_one_or_none()
    if source_node:
        await verify_canvas_access(source_node.canvas_id, current_user.id, session)

    await session.delete(connection)
    await session.commit()
