# Task Log - Merlin Backend

## Active Tasks

| # | Task | Reason | Status | Notes |
|---|------|--------|--------|-------|
| 1 | Set up CLAUDE.md + TASK_LOG.md | Persistent context across Claude sessions | Completed | Instructions in CLAUDE.md, logs here |
| 2 | Rename "Integrations" to "Skills" | Agent capabilities rebrand | Completed | Full rename across DB, models, schemas, API routes, services, config, docs |
| 3 | Skills MCP Server (Jira & Confluence) | CRUD operations on Jira/Confluence via MCP | Completed | 16 tools (9 Jira + 7 Confluence), new skills_mcp_server.py |

## Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-02-20 | Keep task logs in TASK_LOG.md, not CLAUDE.md | CLAUDE.md stays clean with project info + instructions; logs live separately |
| 2026-02-20 | Defer renaming `allowed_integrations` JSON key inside `skill_settings` column | Avoid data migration complexity for stored JSON; can be done in follow-up |
| 2026-02-20 | Keep internal variable names (e.g. `get_integration()`, `integration`) in service files | These use `Skill` type hints already; renaming every local var is cosmetic and risks introducing bugs |
| 2026-02-20 | Create separate skills_mcp_server.py instead of extending mcp_server.py | Separation of concerns: canvas MCP vs external skills MCP. Each server has its own auth/connection patterns. |

## Blockers & Open Questions

- **Post-deploy**: Update OAuth redirect URIs in Atlassian, Slack, and Zoom developer consoles to use `/api/v1/skills/` paths
