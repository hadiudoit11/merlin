"""Pydantic schemas for AI provider settings."""
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class SettingsUpdate(BaseModel):
    """Update settings (API keys and preferences)."""
    # API Keys (optional - only update if provided)
    anthropic_api_key: Optional[str] = Field(None, description="Anthropic API key")
    openai_api_key: Optional[str] = Field(None, description="OpenAI API key")
    huggingface_api_key: Optional[str] = Field(None, description="HuggingFace API key")
    pinecone_api_key: Optional[str] = Field(None, description="Pinecone API key")

    # Pinecone config
    pinecone_environment: Optional[str] = Field(None, description="Pinecone environment")
    pinecone_index_name: Optional[str] = Field(None, description="Pinecone index name")

    # Preferences
    preferred_llm_provider: Optional[str] = Field(None, description="anthropic or openai")
    preferred_llm_model: Optional[str] = Field(None, description="Model name")
    preferred_embedding_provider: Optional[str] = Field(None, description="huggingface or openai")
    preferred_embedding_model: Optional[str] = Field(None, description="Embedding model name")


class SettingsResponse(BaseModel):
    """Settings response with masked keys."""
    source: str  # "organization", "user", or "system"
    is_editable: bool
    has_custom_settings: bool

    # Masked keys (for display only)
    anthropic_api_key_masked: Optional[str] = None
    openai_api_key_masked: Optional[str] = None
    huggingface_api_key_masked: Optional[str] = None
    pinecone_api_key_masked: Optional[str] = None

    # Key presence flags
    has_anthropic_key: bool = False
    has_openai_key: bool = False
    has_huggingface_key: bool = False
    has_pinecone_key: bool = False

    # Config
    pinecone_environment: Optional[str] = None
    pinecone_index_name: Optional[str] = None

    # Preferences
    preferred_llm_provider: str
    preferred_llm_model: str
    preferred_embedding_provider: str
    preferred_embedding_model: str

    # Status
    is_verified: bool = False
    last_verified_at: Optional[str] = None
    settings_id: Optional[int] = None


class IndexCanvasRequest(BaseModel):
    """Request to index a canvas."""
    canvas_id: int


class IndexCanvasResponse(BaseModel):
    """Response from indexing a canvas."""
    status: str
    indexed: int
    namespace: str
    canvas_id: int


class SearchRequest(BaseModel):
    """Semantic search request."""
    query: str = Field(..., min_length=1, description="Search query")
    canvas_id: Optional[int] = Field(None, description="Limit to specific canvas")
    node_types: Optional[List[str]] = Field(None, description="Filter by node types")
    top_k: int = Field(10, ge=1, le=100, description="Number of results")


class SearchResult(BaseModel):
    """Single search result."""
    node_id: int
    canvas_id: int
    node_type: str
    node_name: str
    canvas_name: str
    score: float


class SearchResponse(BaseModel):
    """Search results."""
    query: str
    results: List[SearchResult]
    total: int
