# Task Log - Merlin Backend

## Active Tasks

| # | Task | Reason | Status | Notes |
|---|------|--------|--------|-------|
| 1 | Set up CLAUDE.md + TASK_LOG.md | Persistent context across Claude sessions | Completed | Instructions in CLAUDE.md, logs here |
| 2 | Rename "Integrations" to "Skills" | Agent capabilities rebrand | Completed | Full rename across DB, models, schemas, API routes, services, config, docs |
| 3 | Skills MCP Server (Jira & Confluence) | CRUD operations on Jira/Confluence via MCP | Completed | 16 tools (9 Jira + 7 Confluence), new skills_mcp_server.py |
| 4 | Canvas Agent: Jira/Confluence tools, auto-inject state, bump max_tokens | Make Merlin smarter — access Jira/Confluence, save a round-trip, support longer outputs | Completed | 4 new tools added to CANVAS_TOOLS (12 total), canvas state auto-injected in chat() and chat_stream(), max_tokens 4096→8192 |

## Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-02-20 | Keep task logs in TASK_LOG.md, not CLAUDE.md | CLAUDE.md stays clean with project info + instructions; logs live separately |
| 2026-02-20 | Defer renaming `allowed_integrations` JSON key inside `skill_settings` column | Avoid data migration complexity for stored JSON; can be done in follow-up |
| 2026-02-20 | Keep internal variable names (e.g. `get_integration()`, `integration`) in service files | These use `Skill` type hints already; renaming every local var is cosmetic and risks introducing bugs |
| 2026-02-20 | Create separate skills_mcp_server.py instead of extending mcp_server.py | Separation of concerns: canvas MCP vs external skills MCP. Each server has its own auth/connection patterns. |
| 2026-03-02 | Add Jira/Confluence tools directly to canvas agent (not just MCP) | MCP tools are for external Claude integrations; canvas agent needs its own tool definitions to call Jira/Confluence mid-conversation without MCP |
| 2026-03-02 | Auto-inject canvas state instead of relying on agent tool call | Saves one Claude API round-trip per conversation turn; get_canvas_state tool kept for mid-conversation re-reads |
| 2026-03-02 | Bump max_tokens from 4096 to 8192 | 4096 too low for full PRDs and tech specs; 8192 allows long-form artifact generation without truncation |
| 2026-03-02 | Confluence token refresh handled inline in _get_confluence_service | Unlike Jira (which has JiraSkillService.get_or_refresh_token), Confluence has no built-in skill service for token refresh — caller must handle it |

## Blockers & Open Questions

- **Post-deploy**: Update OAuth redirect URIs in Atlassian, Slack, and Zoom developer consoles to use `/api/v1/skills/` paths
