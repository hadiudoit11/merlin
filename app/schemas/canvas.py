from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from datetime import datetime


class CanvasBase(BaseModel):
    name: str
    description: Optional[str] = None
    viewport_x: float = 0.0
    viewport_y: float = 0.0
    zoom_level: float = 1.0
    grid_enabled: bool = True
    snap_to_grid: bool = True
    grid_size: int = 20
    settings: Dict[str, Any] = {}


class CanvasCreate(CanvasBase):
    """Create canvas - optionally specify organization_id for org canvases."""
    organization_id: Optional[int] = None


class CanvasUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    viewport_x: Optional[float] = None
    viewport_y: Optional[float] = None
    zoom_level: Optional[float] = None
    grid_enabled: Optional[bool] = None
    snap_to_grid: Optional[bool] = None
    grid_size: Optional[int] = None
    settings: Optional[Dict[str, Any]] = None


class CanvasResponse(CanvasBase):
    id: int
    owner_id: Optional[int] = None
    organization_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CanvasWithNodesResponse(CanvasResponse):
    nodes: List["NodeResponse"] = []
    connections: List["NodeConnectionResponse"] = []

    class Config:
        from_attributes = True


# Import at end to avoid circular imports
from app.schemas.node import NodeResponse, NodeConnectionResponse  # noqa: E402
CanvasWithNodesResponse.model_rebuild()
