# CLAUDE.md - Merlin Backend

## Session Context & Task Log Instructions

**Purpose**: This file serves as persistent context across terminal/session restarts. Claude should always read this file at the start of a session to understand the current state of work.

### Rules for Claude
1. **Task Logging**: Maintain a running log in `TASK_LOG.md` at the project root. For each task, record:
   - What the task is
   - Why it's being done (reasoning/motivation)
   - Current status (pending, in-progress, completed, blocked)
   - Any decisions made and their rationale
2. **Update on completion**: When a task is finished, mark it as completed in `TASK_LOG.md` with a brief summary of what was done.
3. **Context preservation**: Before ending a session or when wrapping up work, update `TASK_LOG.md` so the next session has full context.
4. **Decision log**: Record architectural decisions, trade-offs, and rationale in `TASK_LOG.md`.
5. **Blockers & open questions**: Note anything blocked or needing user input in `TASK_LOG.md`.
6. **Always read `TASK_LOG.md` at the start of a session** to understand current state of work.

---

FastAPI backend for Miro-style product management canvas with nodes, OKRs, metrics, AI-powered templates, and external skills.

## Tech Stack

- **Framework**: FastAPI 0.109+
- **Database**: SQLAlchemy 2.0 (async) + SQLite (dev) / PostgreSQL (prod)
- **Migrations**: Alembic
- **Auth**: Auth0 (primary) + JWT fallback (python-jose + passlib)
- **Validation**: Pydantic v2
- **Vector Store**: Pinecone (for semantic search)
- **Embeddings**: HuggingFace (BAAI/bge-large-en-v1.5)
- **AI**: Anthropic Claude / OpenAI (configurable)

## Development Commands

```bash
# Setup
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# Run server
uvicorn app.main:app --reload     # Dev server (port 8000)

# Database
alembic upgrade head              # Run migrations
alembic revision --autogenerate -m "message"  # Create migration

# MCP Server (for Claude integration)
python mcp_server.py

# Testing
pytest -v
```

## Project Structure

```
Merlin/
├── app/
│   ├── main.py                   # FastAPI app entry
│   ├── core/
│   │   ├── config.py             # Settings (pydantic-settings)
│   │   ├── database.py           # SQLAlchemy async setup
│   │   ├── auth0.py              # Auth0 JWT validation
│   │   └── encryption.py         # API key encryption (Fernet)
│   ├── models/
│   │   ├── user.py               # User (supports Auth0)
│   │   ├── canvas.py             # Canvas (workspace)
│   │   ├── node.py               # Nodes + connections
│   │   ├── okr.py                # Objectives, KeyResults, Metrics
│   │   ├── organization.py       # Multi-tenancy
│   │   ├── template.py           # AI node templates
│   │   ├── settings.py           # AI provider settings
│   │   ├── task.py               # Tasks + InputEvent for webhook processing
│   │   └── skill.py              # External skills + MeetingImport
│   ├── schemas/                  # Pydantic request/response models
│   ├── services/
│   │   ├── template_service.py   # Template resolution
│   │   ├── settings_service.py   # API key management
│   │   ├── indexing_service.py   # Pinecone + embeddings
│   │   ├── input_processor.py    # Job pipeline for skill events
│   │   ├── zoom.py               # Zoom OAuth + API
│   │   ├── transcript_processor.py # AI meeting notes extraction
│   │   ├── confluence.py         # Confluence skill
│   │   └── slack.py              # Slack skill
│   └── api/v1/endpoints/
│       ├── auth.py               # Auth (Auth0 + legacy JWT)
│       ├── canvases.py           # Canvas CRUD
│       ├── nodes.py              # Node CRUD + connections
│       ├── okrs.py               # OKR management
│       ├── metrics.py            # Metrics tracking
│       ├── tasks.py              # Task CRUD + node linking
│       ├── templates.py          # AI templates API
│       ├── settings.py           # AI provider settings API
│       ├── zoom.py               # Zoom skill API + webhooks
│       ├── skills.py             # Confluence/Slack skills
│       └── organizations.py      # Org management
├── mcp_server.py                 # MCP server for Claude
├── alembic/                      # Database migrations
└── requirements.txt
```

## Canvas Hierarchy

```
Objective → Key Result → Metric
                │
                ▼
           Problem(s)  ← "Blockers/gaps to achieving the KR"
                │
                ▼
           Doc (PRD)   ← "Solution spec for problem(s)"
```

Connection rules: `objective→keyresult`, `keyresult→metric`, `keyresult→problem`, `problem→doc`

## API Endpoints

Base URL: `http://localhost:8000/api/v1`

### Auth
- `POST /auth/register` - Create account (legacy)
- `POST /auth/login` - Get JWT token (legacy)
- `GET /auth/me` - Current user (supports Auth0 tokens)

### Templates (AI Node Prompts)
- `GET /templates/` - List available templates
- `GET /templates/resolve?node_type=X` - Get resolved template (User → Org → System)
- `POST /templates/` - Create user template
- `POST /templates/organization/{id}` - Create org template
- `PUT /templates/{id}` - Update template
- `DELETE /templates/{id}` - Delete template

### AI Settings
- `GET /settings/` - Get current settings (masked keys)
- `PUT /settings/` - Update settings (individuals only)
- `PUT /settings/organization/{id}` - Update org settings
- `POST /settings/index/canvas` - Index canvas for search
- `POST /settings/search` - Semantic search across canvases

### Zoom Skill
- `GET /skills/zoom/connect` - Start OAuth
- `GET /skills/zoom/status` - Connection status
- `GET /skills/zoom/recordings` - List available recordings
- `POST /skills/zoom/import` - Import meeting with transcript
- `GET /skills/zoom/import/{id}/status` - Check processing status
- `POST /skills/zoom/webhook` - Receive Zoom webhook events

### Tasks
- `GET /tasks/` - List tasks with filters (status, priority, source, canvas, assignee)
- `GET /tasks/stats` - Get task statistics (counts by status, overdue)
- `POST /tasks/` - Create task manually
- `GET /tasks/{id}` - Get task details
- `PUT /tasks/{id}` - Update task
- `DELETE /tasks/{id}` - Delete task
- `POST /tasks/{id}/link/{node_id}` - Link task to canvas node
- `DELETE /tasks/{id}/link/{node_id}` - Unlink task from node

### Canvases, Nodes, OKRs, Metrics
(Standard CRUD endpoints - see API docs at `/docs`)

## Node Types

```python
DOC = "doc"              # Rich-text document (PRD, Tech Spec, etc.)
PROBLEM = "problem"      # Blocker connected to Key Results
OBJECTIVE = "objective"  # OKR objective
KEYRESULT = "keyresult"  # Measurable outcome
METRIC = "metric"        # Tracked measurement
SKILL = "skill"
WEBHOOK = "webhook"
API = "api"
MCP = "mcp"
CUSTOM = "custom"
```

## AI Template System

Templates provide AI context for each node type. Resolution cascade: **User → Organization → System**

```python
# Example: PRD template
{
    "node_type": "doc",
    "subtype": "prd",
    "name": "Product Requirements Document",
    "system_prompt": "You are helping write a PRD...",
    "generation_prompt": "Based on these problems: {connected_problems}...",
    "allowed_inputs": ["problem"],
    "allowed_outputs": ["doc", "agent"],
}
```

Default templates included:
- Objective, Key Result, Metric (OKR flow)
- Problem (blockers for KRs)
- Doc/PRD, Doc/Tech Spec

## API Key Management

Settings cascade: **Organization → User → System defaults**

- **Org members**: Must use org-level keys (enforced for compliance)
- **Individual users**: Can set their own keys
- **System fallback**: Default keys from `.env`

Keys are encrypted using envelope encryption (Fernet + master key).

## Canvas Indexing (Semantic Search)

Uses HuggingFace `BAAI/bge-large-en-v1.5` embeddings + Pinecone vector storage.

Namespace strategy:
- **Org users**: `org_{org_id}` with `metadata.canvas_id` filter
- **Individual users**: `user_{user_id}` with `metadata.canvas_id` filter

Background indexing triggers on node create/update/delete.

## Zoom Skill

Flow:
1. OAuth connect → stores tokens per organization
2. List recordings (last 30 days with transcripts)
3. Import meeting → fetches transcript
4. AI processing (background):
   - Extracts: summary, key points, action items, decisions
   - Creates Doc node with formatted meeting notes
   - Creates Task entities for action items

Webhook support:
- Configure webhook URL: `https://your-domain/api/v1/skills/zoom/webhook`
- Events: `meeting.ended`, `recording.completed`
- Automatically triggers transcript processing pipeline

## Jira Skill

Bidirectional sync between Jira issues and internal Tasks.

### API Endpoints
- `GET /skills/jira/connect` - Start OAuth
- `GET /skills/jira/status` - Connection status
- `DELETE /skills/jira/disconnect` - Disconnect
- `POST /skills/jira/import` - Bulk import issues via JQL
- `POST /skills/jira/push` - Push internal task to Jira
- `POST /skills/jira/webhook` - Receive Jira webhook events

### Import Example
```bash
POST /api/v1/skills/jira/import
{
  "jql": "project = PROJ AND status != Done",
  "canvas_id": 5
}
```

### Push to Jira Example
```bash
POST /api/v1/skills/jira/push
{
  "task_id": 123,
  "project_key": "PROJ",
  "issue_type": "Task"
}
```

### Status Mapping
| Jira Status | Internal Status |
|-------------|-----------------|
| To Do, Open | pending |
| In Progress, In Review | in_progress |
| Done, Closed, Resolved | completed |
| Cancelled, Won't Do | cancelled |

### Webhook Events
- `jira:issue_created` - Creates new Task
- `jira:issue_updated` - Updates existing Task
- `jira:issue_deleted` - Marks Task as cancelled

## Input Processor Pipeline

Extensible job-based system for processing skill events (webhooks, imports).

```
Webhook/Event → InputEvent → InputProcessor → Jobs → Results
                                   │
                    ┌──────────────┼──────────────┐
                    │              │              │
              TranscriptJob   NotesJob      TaskJob
                    │              │              │
                    ▼              ▼              ▼
               raw_content → summary/points → Task entities
                                              → Node links
```

### Architecture

```python
# JobContext: Shared state passed between jobs
context = JobContext(
    session=db_session,
    user_id=1,
    organization_id=1,
    input_event=event,
    raw_content="transcript...",  # Set by TranscriptExtractionJob
    summary="...",                # Set by MeetingNotesJob
    extracted_tasks=[...],        # Set by MeetingNotesJob
    created_tasks=[...],          # Set by TaskExtractionJob
    canvas_id=5,                  # Target canvas for nodes
)

# Create pipeline with jobs
pipeline = InputProcessor()
pipeline.register_jobs([
    TranscriptExtractionJob(),    # Parse VTT/text transcripts
    MeetingNotesJob(),            # AI extraction → summary, key points, action items
    TaskExtractionJob(),          # Create Task entities from action items
    NodeCreationJob(),            # Create doc nodes on canvas
    NodeLinkingJob(),             # Link tasks to related nodes
])
result = await pipeline.process(context)
```

### Pre-configured Pipelines

- `create_zoom_pipeline()` - Full meeting processing (transcript → notes → tasks → nodes)
- `create_slack_pipeline()` - Task extraction + node linking (no transcript step)

### Task Model

Tasks extracted from meetings/messages with canvas node linking:

```python
class Task:
    title: str                    # "Schedule follow-up meeting"
    description: str
    assignee_name: str            # "John" (from transcript)
    assignee_email: str           # If matched
    due_date: datetime
    due_date_text: str            # "next Friday"
    status: pending|in_progress|completed|cancelled
    priority: low|medium|high|urgent
    source: manual|zoom|slack|calendar|email|ai_extracted
    source_id: str                # External meeting/message ID
    canvas_id: int                # Associated canvas
    linked_nodes: List[Node]      # Many-to-many relationship
    context: str                  # Surrounding transcript context
```

### InputEvent Tracking

Tracks webhook/import events for processing:

```python
class InputEvent:
    source_type: str              # "zoom", "slack", etc.
    event_type: str               # "meeting.ended", "message.created"
    external_id: str              # Zoom meeting UUID, etc.
    payload: dict                 # Raw webhook payload
    status: pending|processing|completed|failed
    created_task_ids: List[int]   # Tasks created by pipeline
    created_node_ids: List[int]   # Nodes created by pipeline
    results: dict                 # Job results summary
```

## Environment Variables

```bash
# Core
APP_NAME=Merlin
DEBUG=True
SECRET_KEY=your-secret-key
DATABASE_URL=sqlite+aiosqlite:///./merlin.db
CORS_ORIGINS_STR=http://localhost:3000

# Auth0
AUTH0_DOMAIN=your-tenant.auth0.com
AUTH0_API_AUDIENCE=https://your-api-identifier
AUTH0_CLIENT_ID=xxx
AUTH0_CLIENT_SECRET=xxx

# JWT (legacy fallback)
JWT_SECRET_KEY=your-jwt-secret
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30

# Encryption (for API key storage)
ENCRYPTION_MASTER_KEY=  # Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Default AI Providers (system fallback)
DEFAULT_ANTHROPIC_API_KEY=
DEFAULT_OPENAI_API_KEY=
DEFAULT_HUGGINGFACE_API_KEY=
DEFAULT_PINECONE_API_KEY=
DEFAULT_PINECONE_ENVIRONMENT=
DEFAULT_PINECONE_INDEX_NAME=merlin-canvas

# Zoom
ZOOM_CLIENT_ID=
ZOOM_CLIENT_SECRET=
ZOOM_REDIRECT_URI=http://localhost:8000/api/v1/skills/zoom/callback
ZOOM_WEBHOOK_SECRET_TOKEN=  # For webhook signature verification

# Jira (Atlassian)
JIRA_CLIENT_ID=
JIRA_CLIENT_SECRET=
JIRA_REDIRECT_URI=http://localhost:8000/api/v1/skills/jira/callback
JIRA_WEBHOOK_SECRET=

# Confluence
CONFLUENCE_CLIENT_ID=
CONFLUENCE_CLIENT_SECRET=
CONFLUENCE_REDIRECT_URI=http://localhost:8000/api/v1/skills/confluence/callback

# Slack
SLACK_CLIENT_ID=
SLACK_CLIENT_SECRET=
SLACK_SIGNING_SECRET=
```

## MCP Servers

### Canvas MCP Server

Exposes canvas context to Claude via MCP protocol:

```bash
python mcp_server.py
```

Tools available:
- `list_templates` - Get all node templates
- `get_template` - Get specific template with AI prompts
- `get_node_context` - Full context for generating content
- `get_connection_rules` - Canvas hierarchy rules

### Skills MCP Server (Jira & Confluence)

Exposes CRUD operations on Jira issues and Confluence pages:

```bash
python skills_mcp_server.py
```

**Jira Tools (9):**
- `jira_search_issues` - Search via JQL
- `jira_get_issue` - Get issue details
- `jira_create_issue` - Create issue
- `jira_update_issue` - Update issue fields
- `jira_delete_issue` - Delete issue
- `jira_transition_issue` - Change status
- `jira_get_transitions` - List available transitions
- `jira_get_comments` - List comments
- `jira_add_comment` - Add comment

**Confluence Tools (7):**
- `confluence_list_spaces` - List spaces
- `confluence_get_space` - Get space details
- `confluence_list_pages` - List pages in space
- `confluence_get_page` - Get page content
- `confluence_create_page` - Create page
- `confluence_update_page` - Update page
- `confluence_delete_page` - Delete page

Requires `MCP_USER_ID` env var for skill lookup and audit logging.

## Key Files Reference

| Feature | Files |
|---------|-------|
| Auth0 | `core/auth0.py`, `endpoints/auth.py` |
| Templates | `models/template.py`, `services/template_service.py`, `endpoints/templates.py` |
| AI Settings | `models/settings.py`, `services/settings_service.py`, `endpoints/settings.py` |
| Encryption | `core/encryption.py` |
| Indexing | `services/indexing_service.py` |
| Tasks | `models/task.py`, `endpoints/tasks.py` |
| Input Processor | `services/input_processor.py`, `models/task.py` (InputEvent) |
| Zoom | `services/zoom.py`, `services/transcript_processor.py`, `endpoints/zoom.py` |
| Jira | `services/jira.py`, `services/jira_processor.py`, `endpoints/jira.py` |
| Skills | `models/skill.py`, `schemas/skill.py`, `endpoints/skills.py` |
| MCP (Canvas) | `mcp_server.py` |
| MCP (Skills) | `skills_mcp_server.py` |
