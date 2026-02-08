from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.core.config import settings
from app.core.database import create_db_and_tables, async_session_maker
from app.api.v1.router import api_router
from app.services import template_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await create_db_and_tables()

    # Seed default templates
    async with async_session_maker() as session:
        count = await template_service.seed_system_templates(session)
        if count > 0:
            print(f"✓ Seeded {count} system default templates")

    yield
    # Shutdown


app = FastAPI(
    title=settings.APP_NAME,
    description="Miro-style product management canvas with nodes, OKRs, and integrations",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(api_router, prefix="/api/v1")


@app.get("/health")
async def health_check():
    return {"status": "healthy", "app": settings.APP_NAME}
