# Local Development Setup

This guide covers setting up the Merlin backend and frontend for local development with PostgreSQL.

## Shared Environment

Both `Merlin` (backend) and `Merlin-fe` (frontend) share common environment variables stored in `../.env.shared`:

- Auth0 credentials
- PostgreSQL connection
- Encryption keys
- Application URLs

## Quick Start

```bash
# 1. Start PostgreSQL
docker compose -f docker-compose.db.yml up -d

# 2. Run migrations
source venv/bin/activate
alembic upgrade head

# 3. Start backend
uvicorn app.main:app --reload

# 4. Start frontend (new terminal)
cd ../Merlin-fe
npm run dev
```

## PostgreSQL Credentials

| Setting | Value |
|---------|-------|
| Host | `localhost` |
| Port | `5432` |
| User | `merlin` |
| Password | `merlin_dev_password` |
| Database | `merlin` |
| Connection URL | `postgresql+asyncpg://merlin:merlin_dev_password@localhost:5432/merlin` |

## Docker Commands

```bash
# Start PostgreSQL
docker compose -f docker-compose.db.yml up -d

# Stop PostgreSQL
docker compose -f docker-compose.db.yml down

# View logs
docker compose -f docker-compose.db.yml logs -f

# Reset database (deletes all data!)
docker compose -f docker-compose.db.yml down -v

# Connect via psql
psql postgresql://merlin:merlin_dev_password@localhost:5432/merlin
```

## Syncing Environment Files

A helper script is available to sync both projects from the shared config:

```bash
cd /Users/banegryphon/Documents/GitHub
./sync-env.sh
```

This will:
1. Backup existing `.env` / `.env.local` files
2. Copy `.env.shared` as the base
3. Append project-specific `.env.example` values

## File Structure

```
Documents/GitHub/
├── .env.shared              # Shared variables (Auth0, DB, secrets)
├── sync-env.sh              # Helper script to sync both projects
├── Merlin/
│   ├── .env                 # Backend environment (git-ignored)
│   ├── .env.example         # Template with defaults
│   ├── docker-compose.db.yml # PostgreSQL only
│   └── docker-compose.yml   # Full stack (optional)
└── Merlin-fe/
    ├── .env.local           # Frontend environment (git-ignored)
    └── .env.example         # Template with defaults
```

## Switching Between SQLite and PostgreSQL

### SQLite (no Docker needed)
```bash
# In .env
DATABASE_URL=sqlite+aiosqlite:///./merlin.db
```

### PostgreSQL (recommended)
```bash
# In .env
DATABASE_URL=postgresql+asyncpg://merlin:merlin_dev_password@localhost:5432/merlin
```

## Troubleshooting

### Port 5432 already in use
```bash
# Check what's using the port
lsof -i :5432

# Or use a different port in docker-compose.db.yml
ports:
  - "5433:5432"

# Then update DATABASE_URL
DATABASE_URL=postgresql+asyncpg://merlin:merlin_dev_password@localhost:5433/merlin
```

### Database connection refused
```bash
# Check if container is running
docker ps | grep merlin-db

# Check container logs
docker logs merlin-db
```

### Reset and start fresh
```bash
# Remove container and volume
docker compose -f docker-compose.db.yml down -v

# Remove local SQLite (if switching)
rm -f merlin.db

# Start fresh
docker compose -f docker-compose.db.yml up -d
alembic upgrade head
```
