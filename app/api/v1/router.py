from fastapi import APIRouter

from app.api.v1.endpoints import canvases, nodes, okrs, metrics, auth, organizations, integrations, templates, settings, zoom, jira, tasks, mcp, agent, tokens, projects, artifacts, change_proposals

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(tokens.router, prefix="/tokens", tags=["tokens"])
api_router.include_router(organizations.router, prefix="/organizations", tags=["organizations"])
api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
api_router.include_router(artifacts.router, prefix="/artifacts", tags=["artifacts"])
api_router.include_router(change_proposals.router, prefix="/change-proposals", tags=["change-proposals"])
api_router.include_router(canvases.router, prefix="/canvases", tags=["canvases"])
api_router.include_router(nodes.router, prefix="/nodes", tags=["nodes"])
api_router.include_router(okrs.router, prefix="/okrs", tags=["okrs"])
api_router.include_router(metrics.router, prefix="/metrics", tags=["metrics"])
api_router.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
api_router.include_router(integrations.router, prefix="/integrations", tags=["integrations"])
api_router.include_router(zoom.router, prefix="/integrations/zoom", tags=["zoom"])
api_router.include_router(jira.router, prefix="/integrations/jira", tags=["jira"])
api_router.include_router(templates.router, prefix="/templates", tags=["templates"])
api_router.include_router(settings.router, prefix="/settings", tags=["settings"])
api_router.include_router(agent.router, prefix="/agent", tags=["agent"])
api_router.include_router(mcp.router)
