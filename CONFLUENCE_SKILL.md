# Confluence Skill

Backend-first architecture for syncing Merlin documents with Atlassian Confluence.

## Architecture Overview

```
Frontend                    Backend                      Atlassian
   │                           │                            │
   │──Connect Confluence──────>│                            │
   │<────auth_url + state──────│                            │
   │                           │                            │
   │══════════════════════════>│══════Redirect user════════>│
   │                           │<═══code + state═══════════│
   │                           │──Exchange code for tokens──│
   │                           │<──access_token, refresh────│
   │                           │                            │
   │<══Redirect back═══════════│                            │
   │                           │                            │
   │──List Confluence Spaces──>│──GET /spaces──────────────>│
   │<───────spaces─────────────│<──────spaces───────────────│
```

## Backend Components

### Models (`app/models/skill.py`)

| Model | Purpose |
|-------|---------|
| `Skill` | Org-level OAuth connection (stores tokens securely) |
| `SpaceSkill` | Links Merlin spaces to Confluence spaces |
| `PageSync` | Tracks individual page sync status |

### Schemas (`app/schemas/skill.py`)

Pydantic models for API request/response validation.

### Service (`app/services/confluence.py`)

Confluence API client handling:
- OAuth 2.0 flow (auth URL generation, token exchange, refresh)
- Spaces/pages CRUD via Atlassian REST API v2
- Content conversion (Confluence storage format ↔ Tiptap JSON)

### API Endpoints (`app/api/v1/endpoints/skills.py`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/skills/providers` | List available skill providers |
| `GET` | `/skills/` | List org skills |
| `GET` | `/skills/{provider}` | Get specific skill |
| `DELETE` | `/skills/{provider}` | Disconnect skill |
| `GET` | `/skills/confluence/connect` | Initiate OAuth flow |
| `GET` | `/skills/confluence/callback` | OAuth callback handler |
| `GET` | `/skills/confluence/spaces` | List Confluence spaces |
| `GET` | `/skills/confluence/spaces/{key}/pages` | List pages in space |
| `GET` | `/skills/spaces/{id}` | Get space skill |
| `POST` | `/skills/spaces/{id}/confluence` | Link space to Confluence |
| `DELETE` | `/skills/spaces/{id}/confluence` | Unlink space |
| `POST` | `/skills/spaces/{id}/confluence/import` | Import pages |
| `POST` | `/skills/spaces/{id}/confluence/export` | Export pages |

## Configuration

Add to `.env`:

```bash
# Confluence OAuth 2.0 (Atlassian)
CONFLUENCE_CLIENT_ID=your-client-id
CONFLUENCE_CLIENT_SECRET=your-client-secret
CONFLUENCE_REDIRECT_URI=http://localhost:8000/api/v1/skills/confluence/callback
CONFLUENCE_SCOPES=read:confluence-content.all write:confluence-content read:confluence-space.summary offline_access
```

## Setup Instructions

### 1. Create Atlassian OAuth App

1. Go to https://developer.atlassian.com/console/myapps/
2. Click "Create" → "OAuth 2.0 skill"
3. Name it (e.g., "Merlin Skill")
4. Add callback URL: `http://localhost:8000/api/v1/skills/confluence/callback`
5. Add scopes:
   - `read:confluence-content.all`
   - `write:confluence-content`
   - `read:confluence-space.summary`
   - `offline_access`
6. Copy Client ID and Secret to `.env`

### 2. Run Database Migrations

```bash
cd Merlin
source venv/bin/activate
alembic revision --autogenerate -m "add skills"
alembic upgrade head
```

### 3. Start the Server

```bash
uvicorn app.main:app --reload
```

## Frontend Skill

The frontend (`Merlin-fe`) calls the backend API:

```typescript
// src/lib/skills-api.ts
const { authUrl } = await skillsApi.connectConfluence();
window.location.href = authUrl; // Redirect to Atlassian OAuth
```

After OAuth callback, the backend redirects to:
- Success: `{frontend_url}/settings/skills?connected=confluence`
- Error: `{frontend_url}/settings/skills?error=confluence_connect_failed`

## Content Conversion

### Confluence → Tiptap

```python
ConfluenceService.confluence_to_tiptap(storage_html) -> dict
```

Converts Confluence storage format HTML to Tiptap JSON document structure.

### Tiptap → Confluence

```python
ConfluenceService.tiptap_to_confluence(tiptap_json) -> str
```

Converts Tiptap JSON back to Confluence storage format for export.

## Security Considerations

- OAuth tokens stored in database (encrypt in production with Fernet)
- OAuth state parameter prevents CSRF attacks
- Tokens refreshed automatically when expired
- API keys never exposed to frontend

## Future Enhancements

- [ ] Token encryption at rest
- [ ] Webhook-based real-time sync
- [ ] Conflict resolution UI
- [ ] Batch sync operations
- [ ] Notion skill
- [ ] Google Docs skill
