# Merlin Backend - FastAPI
# Multi-stage build for development and production

# ==================== Base Stage ====================
FROM python:3.13-slim AS base

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 appuser

# ==================== Dependencies Stage ====================
FROM base AS dependencies

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# ==================== Development Stage ====================
FROM dependencies AS development

# Copy application code
COPY --chown=appuser:appuser . .

USER appuser

EXPOSE 8000

# Development command with hot reload
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ==================== Production Stage ====================
FROM dependencies AS production

# Copy application code
COPY --chown=appuser:appuser . .

# Remove development files
RUN rm -rf tests/ .git/ .gitignore .env.example alembic/ *.md

USER appuser

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Production command
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
