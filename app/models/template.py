"""Node template context models for AI-driven content generation."""
from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey,
    Text, JSON, Boolean, Enum, UniqueConstraint
)
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

from app.core.database import Base


class TemplateScope(str, enum.Enum):
    """Scope levels for template configuration."""
    SYSTEM = "system"           # Built-in defaults
    ORGANIZATION = "organization"  # Org-level overrides
    USER = "user"               # User-level overrides


class NodeTemplateContext(Base):
    """
    AI context templates for node types.

    Supports cascading configuration:
    - System defaults (built-in)
    - Organization overrides
    - User overrides (works for solo users too)

    Resolution order: User → Organization → System
    """
    __tablename__ = "node_template_contexts"

    id = Column(Integer, primary_key=True, index=True)

    # Scope configuration
    scope = Column(
        String(20),
        default=TemplateScope.SYSTEM.value,
        nullable=False,
        index=True
    )
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )

    # What this template configures
    node_type = Column(String(50), nullable=False, index=True)  # "problem", "doc", "keyresult", etc.
    subtype = Column(String(50), nullable=True, index=True)      # "prd", "tech_spec", "rfc", etc.

    # Display info
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    icon = Column(String(50), nullable=True)  # Icon identifier for UI
    color = Column(String(50), nullable=True)  # Color for UI

    # AI Prompts
    system_prompt = Column(Text, nullable=True)  # System context for AI
    generation_prompt = Column(Text, nullable=True)  # Template for generating content
    refinement_prompt = Column(Text, nullable=True)  # Template for refining/improving content

    # Structure
    structure_schema = Column(JSON, nullable=True)  # Expected output structure
    example_content = Column(Text, nullable=True)   # Example for reference

    # Connection rules (can override system defaults)
    allowed_inputs = Column(JSON, default=list)   # Node types that can connect TO this
    allowed_outputs = Column(JSON, default=list)  # Node types this can connect TO

    # Behavior
    is_default = Column(Boolean, default=False)   # Default template for this node_type
    is_active = Column(Boolean, default=True)
    priority = Column(Integer, default=0)          # For ordering in UI

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    organization = relationship("Organization", backref="templates")
    user = relationship("User", backref="templates")

    __table_args__ = (
        # Ensure unique template per scope/node_type/subtype combination
        UniqueConstraint(
            'scope', 'organization_id', 'user_id', 'node_type', 'subtype',
            name='uq_template_scope_type'
        ),
    )

    def __repr__(self):
        return f"<NodeTemplateContext {self.scope}:{self.node_type}/{self.subtype}>"


# Default system templates - these get seeded on first run
SYSTEM_DEFAULT_TEMPLATES = [
    # Objective
    {
        "node_type": "objective",
        "subtype": None,
        "name": "Objective",
        "description": "A qualitative goal describing what you want to achieve",
        "icon": "target",
        "color": "yellow-500",
        "system_prompt": """You are helping define OKR objectives. Objectives should be:
- Qualitative and inspirational
- Ambitious but achievable
- Time-bound (usually quarterly)
- Aligned with company/team strategy""",
        "generation_prompt": """Based on the context provided, suggest a clear objective.
Format: A concise statement (1-2 sentences) that describes what we want to achieve.
Example: "Become the go-to platform for product teams to plan and execute strategy.""",
        "allowed_inputs": [],
        "allowed_outputs": ["keyresult"],
        "is_default": True,
    },
    # Key Result
    {
        "node_type": "keyresult",
        "subtype": None,
        "name": "Key Result",
        "description": "A measurable outcome that indicates progress toward the objective",
        "icon": "chart-bar",
        "color": "amber-500",
        "system_prompt": """You are helping define Key Results. Key Results should be:
- Specific and measurable
- Have a clear target number/percentage
- Be achievable within the time period
- Directly indicate progress toward the objective""",
        "generation_prompt": """Given this objective: {parent_objective}

Suggest 2-3 measurable key results.
Format: "[Verb] [metric] from [current] to [target]"
Example: "Increase weekly active users from 1,000 to 5,000" """,
        "allowed_inputs": ["objective"],
        "allowed_outputs": ["metric", "problem"],
        "is_default": True,
    },
    # Metric
    {
        "node_type": "metric",
        "subtype": None,
        "name": "Metric",
        "description": "A trackable measurement that supports a key result",
        "icon": "activity",
        "color": "cyan-500",
        "system_prompt": """You are helping define metrics to track. Metrics should be:
- Quantifiable and trackable
- Updated regularly (daily/weekly)
- Clearly connected to the key result
- Have a defined data source""",
        "generation_prompt": """Given this key result: {parent_keyresult}

Suggest metrics to track progress.
Include: metric name, how to measure it, tracking frequency, and data source.""",
        "allowed_inputs": ["keyresult"],
        "allowed_outputs": [],
        "is_default": True,
    },
    # Problem
    {
        "node_type": "problem",
        "subtype": None,
        "name": "Problem",
        "description": "A blocker or gap that must be addressed to achieve the key result",
        "icon": "alert-circle",
        "color": "rose-500",
        "system_prompt": """You are helping articulate problems clearly. A well-defined problem:
- Describes the current state and pain point
- Explains the impact if not addressed
- Is specific enough to be actionable
- Connects to measurable outcomes""",
        "generation_prompt": """Given this key result: {parent_keyresult}

Identify problems/blockers that prevent achieving this outcome.
Format each problem as:
- Problem: [Clear statement of the issue]
- Impact: [What happens if not solved]
- Evidence: [Data or observations supporting this]""",
        "allowed_inputs": ["keyresult"],
        "allowed_outputs": ["doc"],
        "is_default": True,
    },
    # Doc - PRD
    {
        "node_type": "doc",
        "subtype": "prd",
        "name": "Product Requirements Document",
        "description": "A comprehensive spec for solving a problem",
        "icon": "file-text",
        "color": "blue-500",
        "system_prompt": """You are helping write a Product Requirements Document (PRD). A good PRD:
- Clearly defines the problem and success criteria
- Describes the solution at a high level
- Includes user stories and requirements
- Addresses edge cases and constraints
- Is actionable for engineering and design""",
        "generation_prompt": """Based on these problems:
{connected_problems}

And this key result target:
{parent_keyresult}

Generate a PRD with the following sections:
1. Overview - Problem summary and solution approach
2. Goals & Success Metrics - Tied to the key result
3. User Stories - Who benefits and how
4. Requirements - Functional and non-functional
5. Scope - What's included and excluded
6. Open Questions - Unknowns to resolve""",
        "structure_schema": {
            "sections": ["overview", "goals", "user_stories", "requirements", "scope", "open_questions"]
        },
        "allowed_inputs": ["problem"],
        "allowed_outputs": ["doc", "agent", "skill"],
        "is_default": True,
    },
    # Doc - Tech Spec
    {
        "node_type": "doc",
        "subtype": "tech_spec",
        "name": "Technical Specification",
        "description": "Implementation details for a PRD or feature",
        "icon": "code",
        "color": "emerald-500",
        "system_prompt": """You are helping write a Technical Specification. A good tech spec:
- Translates product requirements into technical approach
- Describes architecture and system design
- Identifies technical risks and mitigations
- Includes API contracts and data models
- Estimates complexity and dependencies""",
        "generation_prompt": """Based on this PRD:
{connected_prd}

Generate a technical specification with:
1. Technical Overview - High-level approach
2. Architecture - System design and components
3. Data Model - Key entities and relationships
4. API Design - Endpoints and contracts
5. Dependencies - External systems and libraries
6. Risks & Mitigations - Technical challenges
7. Implementation Plan - Phased approach""",
        "structure_schema": {
            "sections": ["overview", "architecture", "data_model", "api", "dependencies", "risks", "plan"]
        },
        "allowed_inputs": ["doc"],
        "allowed_outputs": ["doc", "agent"],
        "is_default": False,
    },
    # Doc - Generic
    {
        "node_type": "doc",
        "subtype": None,
        "name": "Document",
        "description": "A general-purpose document",
        "icon": "file",
        "color": "gray-500",
        "system_prompt": """You are helping write documentation. Focus on clarity and completeness.""",
        "generation_prompt": """Create a document based on the provided context.""",
        "allowed_inputs": ["problem", "doc", "objective", "keyresult"],
        "allowed_outputs": ["doc", "agent", "skill"],
        "is_default": True,
    },
]
