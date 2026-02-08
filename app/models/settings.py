"""AI Provider settings model for storing API keys and preferences."""
from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey,
    Text, Boolean, Enum, UniqueConstraint
)
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

from app.core.database import Base


class SettingsScope(str, enum.Enum):
    """Scope levels for settings."""
    SYSTEM = "system"           # System-wide defaults
    ORGANIZATION = "organization"  # Org-level (required for org members)
    USER = "user"               # Individual users (only for users without org)


class LLMProvider(str, enum.Enum):
    """Supported LLM providers."""
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


class EmbeddingProvider(str, enum.Enum):
    """Supported embedding providers."""
    HUGGINGFACE = "huggingface"
    OPENAI = "openai"


class AIProviderSettings(Base):
    """
    Stores AI provider API keys and preferences.

    Key resolution:
    - Org members: MUST use org keys (no personal keys allowed)
    - Individual users: Can set their own keys
    - Fallback: System defaults from environment

    All API keys are encrypted using envelope encryption.
    """
    __tablename__ = "ai_provider_settings"

    id = Column(Integer, primary_key=True, index=True)

    # Scope configuration
    scope = Column(
        String(20),
        default=SettingsScope.USER.value,
        nullable=False,
        index=True
    )
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        unique=True,  # One settings record per org
        index=True
    )
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )

    # ============ LLM Provider Keys (encrypted) ============
    # Anthropic
    anthropic_api_key_encrypted = Column(Text, nullable=True)
    anthropic_api_key_dek = Column(Text, nullable=True)  # Data Encryption Key

    # OpenAI
    openai_api_key_encrypted = Column(Text, nullable=True)
    openai_api_key_dek = Column(Text, nullable=True)

    # ============ Embedding Provider Keys (encrypted) ============
    # HuggingFace
    huggingface_api_key_encrypted = Column(Text, nullable=True)
    huggingface_api_key_dek = Column(Text, nullable=True)

    # ============ Vector DB Keys (encrypted) ============
    # Pinecone
    pinecone_api_key_encrypted = Column(Text, nullable=True)
    pinecone_api_key_dek = Column(Text, nullable=True)
    pinecone_environment = Column(String(100), nullable=True)  # e.g., "us-east-1-aws"
    pinecone_index_name = Column(String(100), nullable=True)   # e.g., "merlin-canvas"

    # ============ Preferences ============
    preferred_llm_provider = Column(
        String(20),
        default=LLMProvider.ANTHROPIC.value,
        nullable=False
    )
    preferred_llm_model = Column(
        String(100),
        default="claude-sonnet-4-20250514",
        nullable=False
    )
    preferred_embedding_provider = Column(
        String(20),
        default=EmbeddingProvider.HUGGINGFACE.value,
        nullable=False
    )
    preferred_embedding_model = Column(
        String(100),
        default="BAAI/bge-large-en-v1.5",
        nullable=False
    )

    # ============ Status ============
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)  # Keys have been tested
    last_verified_at = Column(DateTime, nullable=True)

    # ============ Timestamps ============
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # ============ Relationships ============
    organization = relationship("Organization", backref="ai_settings")
    user = relationship("User", backref="ai_settings")

    __table_args__ = (
        # Ensure one settings record per user (for non-org users)
        UniqueConstraint('scope', 'user_id', name='uq_user_settings'),
    )

    def __repr__(self):
        if self.organization_id:
            return f"<AIProviderSettings org={self.organization_id}>"
        elif self.user_id:
            return f"<AIProviderSettings user={self.user_id}>"
        else:
            return "<AIProviderSettings system>"


class CanvasIndex(Base):
    """
    Tracks indexing status for canvases.

    Each canvas is indexed into Pinecone for semantic search.
    Namespace: org_{org_id} or user_{user_id}
    """
    __tablename__ = "canvas_indexes"

    id = Column(Integer, primary_key=True, index=True)

    canvas_id = Column(
        Integer,
        ForeignKey("canvases.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True
    )

    # Namespace in Pinecone
    pinecone_namespace = Column(String(100), nullable=False, index=True)

    # Indexing status
    is_indexed = Column(Boolean, default=False)
    last_indexed_at = Column(DateTime, nullable=True)
    index_version = Column(Integer, default=0)  # Increment on re-index
    node_count = Column(Integer, default=0)     # Number of vectors

    # Error tracking
    last_error = Column(Text, nullable=True)
    error_count = Column(Integer, default=0)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    canvas = relationship("Canvas", backref="index_status")

    def __repr__(self):
        return f"<CanvasIndex canvas={self.canvas_id} indexed={self.is_indexed}>"
