# Slack Skill

Backend skill for connecting Merlin to Slack workspaces.

## Features

- **OAuth 2.0 Authentication** - Secure workspace connection
- **Channel Listing** - Browse public and private channels
- **Message History** - Read channel and thread messages
- **Post Messages** - Share content to Slack channels
- **User Directory** - List workspace members
- **Search** - Find messages across the workspace

## Architecture Overview

```
Frontend                    Backend                       Slack
   │                           │                            │
   │──Connect Slack───────────>│                            │
   │<────auth_url + state──────│                            │
   │                           │                            │
   │══════════════════════════>│══════Redirect user════════>│
   │                           │<═══code + state════════════│
   │                           │──Exchange code for token───│
   │                           │<──access_token + team info─│
   │                           │                            │
   │<══Redirect back═══════════│                            │
   │                           │                            │
   │──Get Channels────────────>│──conversations.list───────>│
   │<───────channels───────────│<──────channels─────────────│
   │                           │                            │
   │──Get Messages────────────>│──conversations.history────>│
   │<───────messages───────────│<──────messages─────────────│
```

## API Endpoints

### OAuth Flow

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/skills/slack/connect` | Initiate OAuth flow |
| `GET` | `/skills/slack/callback` | OAuth callback handler |

### Team/Workspace

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/skills/slack/team` | Get workspace info |

### Channels

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/skills/slack/channels` | List all channels |
| `GET` | `/skills/slack/channels/{id}` | Get channel details |
| `GET` | `/skills/slack/channels/{id}/messages` | Get channel history |
| `GET` | `/skills/slack/channels/{id}/threads/{ts}` | Get thread replies |
| `POST` | `/skills/slack/channels/{id}/messages` | Post a message |

### Users

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/skills/slack/users` | List workspace users |
| `GET` | `/skills/slack/users/{id}` | Get user details |

### Search

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/skills/slack/search` | Search messages |

## Configuration

Add to `.env`:

```bash
# Slack OAuth 2.0
SLACK_CLIENT_ID=your-client-id
SLACK_CLIENT_SECRET=your-client-secret
SLACK_SIGNING_SECRET=your-signing-secret
SLACK_REDIRECT_URI=http://localhost:8000/api/v1/skills/slack/callback
SLACK_SCOPES=channels:read,channels:history,chat:write,users:read,team:read,files:read
```

## Setup Instructions

### 1. Create Slack App

1. Go to https://api.slack.com/apps
2. Click "Create New App" → "From scratch"
3. Name it (e.g., "Merlin") and select your workspace
4. Go to "OAuth & Permissions"
5. Add redirect URL: `http://localhost:8000/api/v1/skills/slack/callback`
6. Add Bot Token Scopes:
   - `channels:read` - View basic channel info
   - `channels:history` - View messages in public channels
   - `groups:read` - View private channels (optional)
   - `groups:history` - View messages in private channels (optional)
   - `chat:write` - Send messages
   - `users:read` - View users
   - `team:read` - View workspace info
   - `files:read` - View files
   - `search:read` - Search messages (optional)
7. Install app to workspace
8. Copy "Bot User OAuth Token", Client ID, and Client Secret to `.env`
9. Go to "Basic Information" and copy Signing Secret

### 2. Run Database Migrations

```bash
cd Merlin
source venv/bin/activate
alembic revision --autogenerate -m "add slack skill"
alembic upgrade head
```

### 3. Start the Server

```bash
uvicorn app.main:app --reload
```

## Usage Examples

### List Channels

```bash
curl -X GET "http://localhost:8000/api/v1/skills/slack/channels" \
  -H "Authorization: Bearer $TOKEN"
```

Response:
```json
{
  "channels": [
    {
      "id": "C01234567",
      "name": "general",
      "is_private": false,
      "topic": "Company-wide announcements",
      "num_members": 50
    }
  ],
  "cursor": null
}
```

### Get Channel Messages

```bash
curl -X GET "http://localhost:8000/api/v1/skills/slack/channels/C01234567/messages?limit=20" \
  -H "Authorization: Bearer $TOKEN"
```

Response:
```json
{
  "messages": [
    {
      "ts": "1234567890.123456",
      "text": "Hello world!",
      "user_id": "U01234567",
      "timestamp": "2024-01-15T10:30:00"
    }
  ],
  "has_more": true,
  "cursor": "bmV4dF90czoxMjM0NTY3ODkw"
}
```

### Post a Message

```bash
curl -X POST "http://localhost:8000/api/v1/skills/slack/channels/C01234567/messages" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello from Merlin!"}'
```

### Search Messages

```bash
curl -X POST "http://localhost:8000/api/v1/skills/slack/search" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "project update", "count": 10}'
```

## Scopes Reference

| Scope | Purpose |
|-------|---------|
| `channels:read` | List public channels |
| `channels:history` | Read public channel messages |
| `groups:read` | List private channels |
| `groups:history` | Read private channel messages |
| `im:read` | List direct messages |
| `im:history` | Read direct messages |
| `mpim:read` | List group DMs |
| `mpim:history` | Read group DM messages |
| `chat:write` | Post messages |
| `users:read` | List users and get profiles |
| `users:read.email` | Access user emails |
| `team:read` | Get workspace info |
| `files:read` | View shared files |
| `search:read` | Search messages/files |

## Security Considerations

- OAuth tokens stored in database (encrypt in production)
- Signing secret used to verify Slack webhook requests
- Bot tokens don't expire but can be revoked
- Respect rate limits (Tier 1-4 based on scope)

## Rate Limits

Slack API has tiered rate limits:

| Tier | Requests/min | Endpoints |
|------|-------------|-----------|
| Tier 1 | 1+ | Most read operations |
| Tier 2 | 20+ | conversations.list |
| Tier 3 | 50+ | chat.postMessage |
| Tier 4 | 100+ | users.info |

## Future Enhancements

- [ ] Webhook support for real-time events
- [ ] File upload/download
- [ ] Interactive messages (buttons, modals)
- [ ] Slash command handlers
- [ ] App Home tab
- [ ] Socket mode for events
