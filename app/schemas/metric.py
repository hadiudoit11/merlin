from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from datetime import datetime


class MetricBase(BaseModel):
    name: str
    description: Optional[str] = None
    value: float = 0.0
    unit: Optional[str] = None
    source_type: str = "manual"
    source_config: Dict[str, Any] = {}
    refresh_interval: int = 3600
    display_format: str = "number"
    color: str = "#3b82f6"


class MetricCreate(MetricBase):
    node_id: Optional[int] = None


class MetricUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    value: Optional[float] = None
    unit: Optional[str] = None
    source_type: Optional[str] = None
    source_config: Optional[Dict[str, Any]] = None
    refresh_interval: Optional[int] = None
    display_format: Optional[str] = None
    color: Optional[str] = None
    node_id: Optional[int] = None


class MetricResponse(MetricBase):
    id: int
    owner_id: Optional[int] = None
    node_id: Optional[int] = None
    history: List[Dict[str, Any]] = []
    last_refreshed: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class MetricValueUpdate(BaseModel):
    value: float
