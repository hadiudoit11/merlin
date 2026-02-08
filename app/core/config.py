from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import computed_field
from typing import List, Optional


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True, extra="ignore")

    APP_NAME: str = "Merlin"
    APP_BASE_URL: str = "http://localhost:8000"  # Backend base URL
    DEBUG: bool = True
    SECRET_KEY: str = "dev-secret-key-change-in-production"

    # Database - defaults to SQLite for backwards compatibility
    # Use PostgreSQL in Docker: postgresql+asyncpg://merlin:merlin_dev@localhost:5432/merlin
    DATABASE_URL: str = "sqlite+aiosqlite:///./merlin.db"

    # CORS - stored as comma-separated string
    CORS_ORIGINS_STR: str = "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://localhost:3002"

    @computed_field
    @property
    def CORS_ORIGINS(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS_STR.split(",")]

    # JWT (legacy - kept for backwards compatibility during migration)
    JWT_SECRET_KEY: str = "jwt-secret-key"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # Encryption (for storing API keys securely)
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    ENCRYPTION_MASTER_KEY: str = ""

    # Default AI Provider Keys (system fallback)
    DEFAULT_ANTHROPIC_API_KEY: str = ""
    DEFAULT_OPENAI_API_KEY: str = ""
    DEFAULT_HUGGINGFACE_API_KEY: str = ""
    DEFAULT_PINECONE_API_KEY: str = ""
    DEFAULT_PINECONE_ENVIRONMENT: str = ""
    DEFAULT_PINECONE_INDEX_NAME: str = "merlin-canvas"

    # Auth0 Configuration
    AUTH0_DOMAIN: str = ""
    AUTH0_API_AUDIENCE: str = ""
    AUTH0_CLIENT_ID: str = ""
    AUTH0_CLIENT_SECRET: str = ""
    AUTH0_SECRET: str = ""  # Session encryption secret

    @computed_field
    @property
    def AUTH0_ISSUER(self) -> str:
        """Auth0 issuer URL derived from domain."""
        if self.AUTH0_DOMAIN:
            return f"https://{self.AUTH0_DOMAIN}/"
        return ""

    @computed_field
    @property
    def AUTH0_JWKS_URL(self) -> str:
        """Auth0 JWKS URL for fetching public keys."""
        if self.AUTH0_DOMAIN:
            return f"https://{self.AUTH0_DOMAIN}/.well-known/jwks.json"
        return ""

    @computed_field
    @property
    def USE_AUTH0(self) -> bool:
        """Check if Auth0 is configured."""
        return bool(self.AUTH0_DOMAIN and self.AUTH0_API_AUDIENCE)

    # External APIs
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""

    # Confluence Integration (Atlassian OAuth 2.0)
    CONFLUENCE_CLIENT_ID: str = ""
    CONFLUENCE_CLIENT_SECRET: str = ""
    CONFLUENCE_REDIRECT_URI: str = "http://localhost:8000/api/v1/integrations/confluence/callback"
    CONFLUENCE_SCOPES: str = "read:confluence-content.all write:confluence-content read:confluence-space.summary offline_access"

    @computed_field
    @property
    def CONFLUENCE_CONFIGURED(self) -> bool:
        """Check if Confluence OAuth is configured."""
        return bool(self.CONFLUENCE_CLIENT_ID and self.CONFLUENCE_CLIENT_SECRET)

    # Slack Integration (OAuth 2.0)
    SLACK_CLIENT_ID: str = ""
    SLACK_CLIENT_SECRET: str = ""
    SLACK_SIGNING_SECRET: str = ""
    SLACK_REDIRECT_URI: str = "http://localhost:8000/api/v1/integrations/slack/callback"
    SLACK_SCOPES: str = "channels:read,channels:history,chat:write,users:read,team:read,files:read"

    @computed_field
    @property
    def SLACK_CONFIGURED(self) -> bool:
        """Check if Slack OAuth is configured."""
        return bool(self.SLACK_CLIENT_ID and self.SLACK_CLIENT_SECRET)

    # Zoom Integration (OAuth 2.0)
    ZOOM_CLIENT_ID: str = ""
    ZOOM_CLIENT_SECRET: str = ""
    ZOOM_REDIRECT_URI: str = "http://localhost:8000/api/v1/integrations/zoom/callback"
    ZOOM_SCOPES: str = "cloud_recording:read:list_user_recordings cloud_recording:read:recording_token meeting:read:list_meetings meeting:read:meeting user:read:user"
    ZOOM_WEBHOOK_SECRET_TOKEN: str = ""  # For verifying webhook signatures

    @computed_field
    @property
    def ZOOM_CONFIGURED(self) -> bool:
        """Check if Zoom OAuth is configured."""
        return bool(self.ZOOM_CLIENT_ID and self.ZOOM_CLIENT_SECRET)

    # Jira Integration (Atlassian OAuth 2.0)
    JIRA_CLIENT_ID: str = ""
    JIRA_CLIENT_SECRET: str = ""
    JIRA_REDIRECT_URI: str = "http://localhost:8000/api/v1/integrations/jira/callback"
    JIRA_SCOPES: str = "read:jira-work read:jira-user write:jira-work offline_access"
    JIRA_WEBHOOK_SECRET: str = ""  # For webhook signature verification

    @computed_field
    @property
    def JIRA_CONFIGURED(self) -> bool:
        """Check if Jira OAuth is configured."""
        return bool(self.JIRA_CLIENT_ID and self.JIRA_CLIENT_SECRET)

    # Email (for invitations)
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    FROM_EMAIL: str = "noreply@merlin.local"


settings = Settings()
