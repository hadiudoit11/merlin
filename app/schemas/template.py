"""Pydantic schemas for node template contexts."""
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime

from app.models.template import TemplateScope


class TemplateBase(BaseModel):
    """Base template fields."""
    node_type: str
    subtype: Optional[str] = None
    name: str
    description: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    system_prompt: Optional[str] = None
    generation_prompt: Optional[str] = None
    refinement_prompt: Optional[str] = None
    structure_schema: Optional[Dict[str, Any]] = None
    example_content: Optional[str] = None
    allowed_inputs: List[str] = Field(default_factory=list)
    allowed_outputs: List[str] = Field(default_factory=list)
    is_default: bool = False
    is_active: bool = True
    priority: int = 0


class TemplateCreate(TemplateBase):
    """Create a new template (org or user scope)."""
    # Scope is determined by the endpoint (org or user)
    pass


class TemplateUpdate(BaseModel):
    """Update an existing template."""
    name: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    system_prompt: Optional[str] = None
    generation_prompt: Optional[str] = None
    refinement_prompt: Optional[str] = None
    structure_schema: Optional[Dict[str, Any]] = None
    example_content: Optional[str] = None
    allowed_inputs: Optional[List[str]] = None
    allowed_outputs: Optional[List[str]] = None
    is_default: Optional[bool] = None
    is_active: Optional[bool] = None
    priority: Optional[int] = None


class TemplateResponse(TemplateBase):
    """Template response with metadata."""
    id: int
    scope: str
    organization_id: Optional[int] = None
    user_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class TemplateResolved(BaseModel):
    """A resolved template with source info."""
    template: TemplateResponse
    source_scope: str  # Where this template came from
    is_overridden: bool = False  # True if this overrides a lower-priority template


class TemplateListResponse(BaseModel):
    """List of templates grouped by node type."""
    templates: List[TemplateResponse]
    node_types: List[str]  # Unique node types in the list


class GenerationRequest(BaseModel):
    """Request to generate content using a template."""
    template_id: Optional[int] = None  # Use specific template, or resolve automatically
    node_type: str
    subtype: Optional[str] = None
    context: Dict[str, Any] = Field(default_factory=dict)  # Connected nodes, parent info, etc.
    user_input: Optional[str] = None  # Additional user guidance


class GenerationResponse(BaseModel):
    """Response from content generation."""
    content: str
    template_used: TemplateResponse
    tokens_used: Optional[int] = None
