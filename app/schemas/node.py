from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from datetime import datetime

from app.models.node import NodeType


class NodeBase(BaseModel):
    name: str
    node_type: str = NodeType.CUSTOM.value
    position_x: float = 0.0
    position_y: float = 0.0
    width: float = 300.0
    height: float = 200.0
    content: str = ""
    config: Dict[str, Any] = {}
    node_metadata: Dict[str, Any] = {}
    color: str = "#ffffff"
    border_color: str = "#e5e7eb"
    is_locked: bool = False
    is_collapsed: bool = False
    z_index: int = 0


class NodeCreate(NodeBase):
    canvas_id: int


class NodeUpdate(BaseModel):
    name: Optional[str] = None
    node_type: Optional[str] = None
    position_x: Optional[float] = None
    position_y: Optional[float] = None
    width: Optional[float] = None
    height: Optional[float] = None
    content: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    node_metadata: Optional[Dict[str, Any]] = None
    color: Optional[str] = None
    border_color: Optional[str] = None
    is_locked: Optional[bool] = None
    is_collapsed: Optional[bool] = None
    z_index: Optional[int] = None


class NodeResponse(NodeBase):
    id: int
    canvas_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class NodePositionUpdate(BaseModel):
    position_x: float
    position_y: float


class NodeBatchPositionUpdate(BaseModel):
    nodes: List[Dict[str, Any]]  # [{id, position_x, position_y}, ...]


# Node Connections
class NodeConnectionBase(BaseModel):
    source_node_id: int
    target_node_id: int
    connection_type: str = "default"
    color: str = "#6b7280"
    style: str = "solid"
    label: Optional[str] = None
    config: Dict[str, Any] = {}


class NodeConnectionCreate(NodeConnectionBase):
    pass


class NodeConnectionResponse(NodeConnectionBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True
