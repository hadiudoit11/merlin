# External Skills

Merlin supports skills with external services for document syncing, communication, and collaboration.

## Available Skills

| Skill | Status | Documentation |
|-------------|--------|---------------|
| [Confluence](./CONFLUENCE_INTEGRATION.md) | Implemented | Sync documents with Atlassian Confluence |
| [Slack](./SLACK_INTEGRATION.md) | Implemented | Connect to Slack for notifications and sharing |
| Notion | Planned | Sync pages with Notion workspaces |
| Google Docs | Planned | Sync with Google Workspace |
| GitHub | Planned | Link repos, sync markdown |

## Architecture

All skills follow the same backend-first architecture:

```
┌─────────────────────────────────────────────────────────────────┐
│                         Frontend                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ Skill  │  │   Connect    │  │    Sync      │          │
│  │   Settings   │  │   Dialog     │  │   Status     │          │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘          │
└─────────┼─────────────────┼─────────────────┼───────────────────┘
          │                 │                 │
          ▼                 ▼                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Backend API Layer                             │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              /api/v1/skills/                        │   │
│  │  - OAuth flows                                            │   │
│  │  - Token management                                       │   │
│  │  - API proxying                                           │   │
│  │  - Data transformation                                    │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
          │                 │                 │
          ▼                 ▼                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                    External Services                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐        │
│  │Confluence│  │  Slack   │  │  Notion  │  │  GitHub  │        │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘        │
└─────────────────────────────────────────────────────────────────┘
```

## Common Endpoints

All skills share these base endpoints:

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/skills/providers` | List available providers |
| `GET` | `/skills/` | List connected skills |
| `GET` | `/skills/{provider}` | Get skill details |
| `DELETE` | `/skills/{provider}` | Disconnect skill |
| `GET` | `/skills/{provider}/connect` | Start OAuth flow |
| `GET` | `/skills/{provider}/callback` | OAuth callback |

## Database Models

### Skill

Organization-level connection to an external service:

```python
class Skill:
    id: int
    organization_id: int
    provider: SkillProvider  # confluence, slack, notion, etc.
    access_token: str              # Encrypted OAuth token
    refresh_token: str             # For token refresh
    token_expires_at: datetime
    provider_data: dict            # Provider-specific metadata
    status: SyncStatus             # idle, syncing, error
    connected_by_id: int
    created_at: datetime
    updated_at: datetime
```

### SpaceSkill

Links a Merlin space to an external space:

```python
class SpaceSkill:
    id: int
    skill_id: int
    space_id: str
    external_space_key: str
    external_space_id: str
    external_space_name: str
    sync_enabled: bool
    sync_direction: SyncDirection  # import, export, bidirectional
    auto_sync: bool
    sync_status: SyncStatus
    last_sync_at: datetime
    page_mappings: dict            # Merlin page ID -> External page ID
```

### PageSync

Tracks sync status for individual pages:

```python
class PageSync:
    id: int
    space_skill_id: int
    page_id: str
    external_page_id: str
    external_page_url: str
    local_version: int
    remote_version: int
    sync_status: SyncStatus
    has_conflict: bool
    last_sync_at: datetime
```

## Configuration

Add to `.env`:

```bash
# Confluence
CONFLUENCE_CLIENT_ID=...
CONFLUENCE_CLIENT_SECRET=...
CONFLUENCE_REDIRECT_URI=http://localhost:8000/api/v1/skills/confluence/callback

# Slack
SLACK_CLIENT_ID=...
SLACK_CLIENT_SECRET=...
SLACK_SIGNING_SECRET=...
SLACK_REDIRECT_URI=http://localhost:8000/api/v1/skills/slack/callback
```

## Security

- **Tokens** - OAuth tokens are stored server-side, never exposed to frontend
- **Encryption** - Use Fernet encryption for tokens at rest in production
- **CSRF** - OAuth state parameter prevents cross-site request forgery
- **Refresh** - Tokens are automatically refreshed when expired
- **Scopes** - Minimal scopes requested for each skill

## Adding New Skills

1. Add provider to `SkillProvider` enum in `models/skill.py`
2. Add config settings in `core/config.py`
3. Create service class in `services/{provider}.py`
4. Add schemas in `schemas/skill.py`
5. Add endpoints in `api/v1/endpoints/skills.py`
6. Update `list_providers` endpoint
7. Create documentation in `{PROVIDER}_INTEGRATION.md`
8. Run migrations

## Testing

```bash
# Run skill tests
pytest tests/test_skills.py -v

# Test OAuth flow manually
curl http://localhost:8000/api/v1/skills/confluence/connect
# Follow redirect, authorize, check callback
```
