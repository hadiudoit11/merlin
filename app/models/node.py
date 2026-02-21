from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Float, JSON, Text, Enum
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

from app.core.database import Base


class NodeType(str, enum.Enum):
    DOC = "doc"
    SKILL = "skill"
    WEBHOOK = "webhook"
    API = "api"
    MCP = "mcp"
    OKR = "okr"
    METRIC = "metric"
    CUSTOM = "custom"


class Node(Base):
    __tablename__ = "nodes"

    id = Column(Integer, primary_key=True, index=True)

    # Node identity
    name = Column(String(255), nullable=False)
    node_type = Column(String(50), default=NodeType.CUSTOM.value)

    # Position on canvas
    position_x = Column(Float, default=0.0)
    position_y = Column(Float, default=0.0)

    # Size
    width = Column(Float, default=300.0)
    height = Column(Float, default=200.0)

    # Content (flexible based on node type)
    # For doc nodes: rich text content (HTML/JSON)
    # For integration nodes: connection config
    # For webhook nodes: endpoint config
    # For API nodes: request config
    # For MCP nodes: server connection config
    content = Column(Text, default="")

    # Configuration and metadata
    config = Column(JSON, default=dict)
    node_metadata = Column(JSON, default=dict)

    # Visual styling
    color = Column(String(50), default="#ffffff")
    border_color = Column(String(50), default="#e5e7eb")

    # State
    is_locked = Column(Integer, default=False)
    is_collapsed = Column(Integer, default=False)
    z_index = Column(Integer, default=0)

    # Parent canvas
    canvas_id = Column(Integer, ForeignKey("canvases.id"), nullable=False)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    canvas = relationship("Canvas", back_populates="nodes")

    # Connections (as source)
    outgoing_connections = relationship(
        "NodeConnection",
        foreign_keys="NodeConnection.source_node_id",
        back_populates="source_node",
        cascade="all, delete-orphan"
    )

    # Connections (as target)
    incoming_connections = relationship(
        "NodeConnection",
        foreign_keys="NodeConnection.target_node_id",
        back_populates="target_node",
        cascade="all, delete-orphan"
    )


class NodeConnection(Base):
    __tablename__ = "node_connections"

    id = Column(Integer, primary_key=True, index=True)

    # Connected nodes
    source_node_id = Column(Integer, ForeignKey("nodes.id"), nullable=False)
    target_node_id = Column(Integer, ForeignKey("nodes.id"), nullable=False)

    # Connection type (data flow, reference, dependency, etc.)
    connection_type = Column(String(50), default="default")

    # Visual styling
    color = Column(String(50), default="#6b7280")
    style = Column(String(50), default="solid")  # solid, dashed, dotted

    # Connection label
    label = Column(String(255))

    # Metadata
    config = Column(JSON, default=dict)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    source_node = relationship(
        "Node",
        foreign_keys=[source_node_id],
        back_populates="outgoing_connections"
    )
    target_node = relationship(
        "Node",
        foreign_keys=[target_node_id],
        back_populates="incoming_connections"
    )
