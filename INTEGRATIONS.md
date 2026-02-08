# External Integrations

Merlin supports integrations with external services for document syncing, communication, and collaboration.

## Available Integrations

| Integration | Status | Documentation |
|-------------|--------|---------------|
| [Confluence](./CONFLUENCE_INTEGRATION.md) | Implemented | Sync documents with Atlassian Confluence |
| [Slack](./SLACK_INTEGRATION.md) | Implemented | Connect to Slack for notifications and sharing |
| Notion | Planned | Sync pages with Notion workspaces |
| Google Docs | Planned | Sync with Google Workspace |
| GitHub | Planned | Link repos, sync markdown |

## Architecture

All integrations follow the same backend-first architecture:

```
┌─────────────────────────────────────────────────────────────────┐
│                         Frontend                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ Integration  │  │   Connect    │  │    Sync      │          │
│  │   Settings   │  │   Dialog     │  │   Status     │          │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘          │
└─────────┼─────────────────┼─────────────────┼───────────────────┘
          │                 │                 │
          ▼                 ▼                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Backend API Layer                             │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              /api/v1/integrations/                        │   │
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

All integrations share these base endpoints:

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/integrations/providers` | List available providers |
| `GET` | `/integrations/` | List connected integrations |
| `GET` | `/integrations/{provider}` | Get integration details |
| `DELETE` | `/integrations/{provider}` | Disconnect integration |
| `GET` | `/integrations/{provider}/connect` | Start OAuth flow |
| `GET` | `/integrations/{provider}/callback` | OAuth callback |

## Database Models

### Integration

Organization-level connection to an external service:

```python
class Integration:
    id: int
    organization_id: int
    provider: IntegrationProvider  # confluence, slack, notion, etc.
    access_token: str              # Encrypted OAuth token
    refresh_token: str             # For token refresh
    token_expires_at: datetime
    provider_data: dict            # Provider-specific metadata
    status: SyncStatus             # idle, syncing, error
    connected_by_id: int
    created_at: datetime
    updated_at: datetime
```

### SpaceIntegration

Links a Merlin space to an external space:

```python
class SpaceIntegration:
    id: int
    integration_id: int
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
    space_integration_id: int
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
CONFLUENCE_REDIRECT_URI=http://localhost:8000/api/v1/integrations/confluence/callback

# Slack
SLACK_CLIENT_ID=...
SLACK_CLIENT_SECRET=...
SLACK_SIGNING_SECRET=...
SLACK_REDIRECT_URI=http://localhost:8000/api/v1/integrations/slack/callback
```

## Security

- **Tokens** - OAuth tokens are stored server-side, never exposed to frontend
- **Encryption** - Use Fernet encryption for tokens at rest in production
- **CSRF** - OAuth state parameter prevents cross-site request forgery
- **Refresh** - Tokens are automatically refreshed when expired
- **Scopes** - Minimal scopes requested for each integration

## Adding New Integrations

1. Add provider to `IntegrationProvider` enum in `models/integration.py`
2. Add config settings in `core/config.py`
3. Create service class in `services/{provider}.py`
4. Add schemas in `schemas/integration.py`
5. Add endpoints in `api/v1/endpoints/integrations.py`
6. Update `list_providers` endpoint
7. Create documentation in `{PROVIDER}_INTEGRATION.md`
8. Run migrations

## Testing

```bash
# Run integration tests
pytest tests/test_integrations.py -v

# Test OAuth flow manually
curl http://localhost:8000/api/v1/integrations/confluence/connect
# Follow redirect, authorize, check callback
```
