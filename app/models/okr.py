from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Float, JSON, Text
from sqlalchemy.orm import relationship
from datetime import datetime

from app.core.database import Base


class Objective(Base):
    __tablename__ = "objectives"

    id = Column(Integer, primary_key=True, index=True)

    # Objective details
    title = Column(String(500), nullable=False)
    description = Column(Text)

    # Time period
    start_date = Column(DateTime)
    end_date = Column(DateTime)

    # Status
    status = Column(String(50), default="active")  # active, completed, cancelled

    # Hierarchy (for nested OKRs)
    parent_id = Column(Integer, ForeignKey("objectives.id"), nullable=True)

    # Company/Team level
    level = Column(String(50), default="company")  # company, team, individual

    # Ownership
    owner_id = Column(Integer, ForeignKey("users.id"))

    # Canvas node reference (if displayed on canvas)
    node_id = Column(Integer, ForeignKey("nodes.id"), nullable=True)

    # Extra data
    extra_data = Column(JSON, default=dict)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    key_results = relationship("KeyResult", back_populates="objective", cascade="all, delete-orphan")
    children = relationship("Objective", backref="parent", remote_side=[id])


class KeyResult(Base):
    __tablename__ = "key_results"

    id = Column(Integer, primary_key=True, index=True)

    # Key result details
    title = Column(String(500), nullable=False)
    description = Column(Text)

    # Measurement
    metric_type = Column(String(50), default="percentage")  # percentage, number, currency, boolean
    target_value = Column(Float, nullable=False)
    current_value = Column(Float, default=0.0)
    start_value = Column(Float, default=0.0)

    # Status
    status = Column(String(50), default="on_track")  # on_track, at_risk, behind, completed

    # Parent objective
    objective_id = Column(Integer, ForeignKey("objectives.id"), nullable=False)

    # Linked metric (if tracking from external source)
    linked_metric_id = Column(Integer, ForeignKey("metrics.id"), nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    objective = relationship("Objective", back_populates="key_results")
    linked_metric = relationship("Metric", back_populates="key_results")

    @property
    def progress(self) -> float:
        if self.target_value == self.start_value:
            return 100.0 if self.current_value >= self.target_value else 0.0
        return ((self.current_value - self.start_value) / (self.target_value - self.start_value)) * 100


class Metric(Base):
    __tablename__ = "metrics"

    id = Column(Integer, primary_key=True, index=True)

    # Metric identity
    name = Column(String(255), nullable=False)
    description = Column(Text)

    # Current value
    value = Column(Float, default=0.0)
    unit = Column(String(50))  # %, $, users, etc.

    # Data source
    source_type = Column(String(50), default="manual")  # manual, api, integration
    source_config = Column(JSON, default=dict)  # API endpoint, integration ID, etc.

    # Refresh settings
    refresh_interval = Column(Integer, default=3600)  # seconds
    last_refreshed = Column(DateTime)

    # Historical data
    history = Column(JSON, default=list)  # [{timestamp, value}, ...]

    # Display settings
    display_format = Column(String(50), default="number")
    color = Column(String(50), default="#3b82f6")

    # Canvas node reference
    node_id = Column(Integer, ForeignKey("nodes.id"), nullable=True)

    # Ownership
    owner_id = Column(Integer, ForeignKey("users.id"))

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    key_results = relationship("KeyResult", back_populates="linked_metric")
