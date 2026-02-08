from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from datetime import datetime


class KeyResultBase(BaseModel):
    title: str
    description: Optional[str] = None
    metric_type: str = "percentage"
    target_value: float
    current_value: float = 0.0
    start_value: float = 0.0
    status: str = "on_track"


class KeyResultCreate(KeyResultBase):
    objective_id: int
    linked_metric_id: Optional[int] = None


class KeyResultUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    metric_type: Optional[str] = None
    target_value: Optional[float] = None
    current_value: Optional[float] = None
    start_value: Optional[float] = None
    status: Optional[str] = None
    linked_metric_id: Optional[int] = None


class KeyResultResponse(KeyResultBase):
    id: int
    objective_id: int
    linked_metric_id: Optional[int] = None
    progress: float
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ObjectiveBase(BaseModel):
    title: str
    description: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    status: str = "active"
    level: str = "company"
    extra_data: Dict[str, Any] = {}


class ObjectiveCreate(ObjectiveBase):
    parent_id: Optional[int] = None
    node_id: Optional[int] = None


class ObjectiveUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    status: Optional[str] = None
    level: Optional[str] = None
    parent_id: Optional[int] = None
    node_id: Optional[int] = None
    extra_data: Optional[Dict[str, Any]] = None


class ObjectiveResponse(ObjectiveBase):
    id: int
    parent_id: Optional[int] = None
    owner_id: Optional[int] = None
    node_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ObjectiveWithKeyResultsResponse(ObjectiveResponse):
    key_results: List[KeyResultResponse] = []

    class Config:
        from_attributes = True
