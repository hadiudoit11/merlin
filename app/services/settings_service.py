"""
Settings resolution and management service.

Handles:
- API key storage with encryption
- Settings resolution (Org → User → System)
- Key verification
"""
from typing import Optional, Dict, Any, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime

from app.core.config import settings as app_settings
from app.core.encryption import encrypt_value, decrypt_value, mask_key, EncryptionError
from app.models.settings import (
    AIProviderSettings, SettingsScope, LLMProvider, EmbeddingProvider
)
from app.models.organization import OrganizationMember
from app.models.user import User


class SettingsService:
    """Service for managing AI provider settings."""

    @staticmethod
    async def get_user_organization_id(
        session: AsyncSession,
        user_id: int
    ) -> Optional[int]:
        """Get the organization ID for a user, if any."""
        result = await session.execute(
            select(OrganizationMember.organization_id)
            .where(OrganizationMember.user_id == user_id)
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def is_user_in_org(
        session: AsyncSession,
        user_id: int
    ) -> bool:
        """Check if a user belongs to an organization."""
        org_id = await SettingsService.get_user_organization_id(session, user_id)
        return org_id is not None

    @staticmethod
    async def get_org_settings(
        session: AsyncSession,
        organization_id: int
    ) -> Optional[AIProviderSettings]:
        """Get settings for an organization."""
        result = await session.execute(
            select(AIProviderSettings).where(
                AIProviderSettings.organization_id == organization_id,
                AIProviderSettings.scope == SettingsScope.ORGANIZATION.value,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_user_settings(
        session: AsyncSession,
        user_id: int
    ) -> Optional[AIProviderSettings]:
        """Get settings for an individual user (non-org users only)."""
        result = await session.execute(
            select(AIProviderSettings).where(
                AIProviderSettings.user_id == user_id,
                AIProviderSettings.scope == SettingsScope.USER.value,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def resolve_settings(
        session: AsyncSession,
        user_id: int
    ) -> Tuple[Optional[AIProviderSettings], str]:
        """
        Resolve the effective settings for a user.

        Resolution order:
        1. If user is in org → Use org settings (required)
        2. If user is individual → Use user settings
        3. Fall back to system defaults

        Returns:
            Tuple of (settings, source) where source is "organization", "user", or "system"
        """
        # Check if user is in an organization
        org_id = await SettingsService.get_user_organization_id(session, user_id)

        if org_id:
            # User is in org - MUST use org settings
            settings = await SettingsService.get_org_settings(session, org_id)
            if settings:
                return settings, "organization"
            # Org has no settings configured - fall through to system
            return None, "system"

        # Individual user - can use personal settings
        settings = await SettingsService.get_user_settings(session, user_id)
        if settings:
            return settings, "user"

        return None, "system"

    @staticmethod
    def get_system_defaults() -> Dict[str, Any]:
        """Get system default settings from environment."""
        return {
            "anthropic_api_key": app_settings.DEFAULT_ANTHROPIC_API_KEY or None,
            "openai_api_key": app_settings.DEFAULT_OPENAI_API_KEY or None,
            "huggingface_api_key": app_settings.DEFAULT_HUGGINGFACE_API_KEY or None,
            "pinecone_api_key": app_settings.DEFAULT_PINECONE_API_KEY or None,
            "pinecone_environment": app_settings.DEFAULT_PINECONE_ENVIRONMENT or None,
            "pinecone_index_name": app_settings.DEFAULT_PINECONE_INDEX_NAME or "merlin-canvas",
            "preferred_llm_provider": LLMProvider.ANTHROPIC.value,
            "preferred_llm_model": "claude-sonnet-4-20250514",
            "preferred_embedding_provider": EmbeddingProvider.HUGGINGFACE.value,
            "preferred_embedding_model": "BAAI/bge-large-en-v1.5",
        }

    @staticmethod
    async def get_effective_settings(
        session: AsyncSession,
        user_id: int
    ) -> Dict[str, Any]:
        """
        Get the effective settings for a user, combining DB settings with defaults.

        Returns a dictionary with all settings, decrypted keys, and source info.
        """
        db_settings, source = await SettingsService.resolve_settings(session, user_id)
        defaults = SettingsService.get_system_defaults()

        if db_settings is None:
            return {
                **defaults,
                "source": source,
                "has_custom_settings": False,
            }

        # Decrypt keys from DB settings
        result = {
            "source": source,
            "has_custom_settings": True,
            "settings_id": db_settings.id,
            "preferred_llm_provider": db_settings.preferred_llm_provider,
            "preferred_llm_model": db_settings.preferred_llm_model,
            "preferred_embedding_provider": db_settings.preferred_embedding_provider,
            "preferred_embedding_model": db_settings.preferred_embedding_model,
            "pinecone_environment": db_settings.pinecone_environment or defaults["pinecone_environment"],
            "pinecone_index_name": db_settings.pinecone_index_name or defaults["pinecone_index_name"],
        }

        # Decrypt API keys (with fallback to defaults)
        try:
            result["anthropic_api_key"] = (
                decrypt_value(
                    db_settings.anthropic_api_key_encrypted,
                    db_settings.anthropic_api_key_dek
                ) if db_settings.anthropic_api_key_encrypted else defaults["anthropic_api_key"]
            )
        except EncryptionError:
            result["anthropic_api_key"] = defaults["anthropic_api_key"]

        try:
            result["openai_api_key"] = (
                decrypt_value(
                    db_settings.openai_api_key_encrypted,
                    db_settings.openai_api_key_dek
                ) if db_settings.openai_api_key_encrypted else defaults["openai_api_key"]
            )
        except EncryptionError:
            result["openai_api_key"] = defaults["openai_api_key"]

        try:
            result["huggingface_api_key"] = (
                decrypt_value(
                    db_settings.huggingface_api_key_encrypted,
                    db_settings.huggingface_api_key_dek
                ) if db_settings.huggingface_api_key_encrypted else defaults["huggingface_api_key"]
            )
        except EncryptionError:
            result["huggingface_api_key"] = defaults["huggingface_api_key"]

        try:
            result["pinecone_api_key"] = (
                decrypt_value(
                    db_settings.pinecone_api_key_encrypted,
                    db_settings.pinecone_api_key_dek
                ) if db_settings.pinecone_api_key_encrypted else defaults["pinecone_api_key"]
            )
        except EncryptionError:
            result["pinecone_api_key"] = defaults["pinecone_api_key"]

        return result

    @staticmethod
    async def get_masked_settings(
        session: AsyncSession,
        user_id: int
    ) -> Dict[str, Any]:
        """
        Get settings with masked API keys (for display in UI).

        Org members see org settings (read-only).
        Individual users see their own settings (editable).
        """
        db_settings, source = await SettingsService.resolve_settings(session, user_id)
        defaults = SettingsService.get_system_defaults()

        is_org_member = await SettingsService.is_user_in_org(session, user_id)

        result = {
            "source": source,
            "is_editable": not is_org_member,  # Org members cannot edit
            "has_custom_settings": db_settings is not None,
            "preferred_llm_provider": LLMProvider.ANTHROPIC.value,
            "preferred_llm_model": "claude-sonnet-4-20250514",
            "preferred_embedding_provider": EmbeddingProvider.HUGGINGFACE.value,
            "preferred_embedding_model": "BAAI/bge-large-en-v1.5",
            "pinecone_environment": defaults["pinecone_environment"],
            "pinecone_index_name": defaults["pinecone_index_name"],
            "anthropic_api_key_masked": None,
            "openai_api_key_masked": None,
            "huggingface_api_key_masked": None,
            "pinecone_api_key_masked": None,
            "has_anthropic_key": False,
            "has_openai_key": False,
            "has_huggingface_key": False,
            "has_pinecone_key": False,
        }

        if db_settings:
            result.update({
                "settings_id": db_settings.id,
                "preferred_llm_provider": db_settings.preferred_llm_provider,
                "preferred_llm_model": db_settings.preferred_llm_model,
                "preferred_embedding_provider": db_settings.preferred_embedding_provider,
                "preferred_embedding_model": db_settings.preferred_embedding_model,
                "pinecone_environment": db_settings.pinecone_environment or defaults["pinecone_environment"],
                "pinecone_index_name": db_settings.pinecone_index_name or defaults["pinecone_index_name"],
                "is_verified": db_settings.is_verified,
                "last_verified_at": db_settings.last_verified_at.isoformat() if db_settings.last_verified_at else None,
            })

            # Mask keys for display
            if db_settings.anthropic_api_key_encrypted:
                try:
                    key = decrypt_value(
                        db_settings.anthropic_api_key_encrypted,
                        db_settings.anthropic_api_key_dek
                    )
                    result["anthropic_api_key_masked"] = mask_key(key)
                    result["has_anthropic_key"] = True
                except EncryptionError:
                    pass

            if db_settings.openai_api_key_encrypted:
                try:
                    key = decrypt_value(
                        db_settings.openai_api_key_encrypted,
                        db_settings.openai_api_key_dek
                    )
                    result["openai_api_key_masked"] = mask_key(key)
                    result["has_openai_key"] = True
                except EncryptionError:
                    pass

            if db_settings.huggingface_api_key_encrypted:
                try:
                    key = decrypt_value(
                        db_settings.huggingface_api_key_encrypted,
                        db_settings.huggingface_api_key_dek
                    )
                    result["huggingface_api_key_masked"] = mask_key(key)
                    result["has_huggingface_key"] = True
                except EncryptionError:
                    pass

            if db_settings.pinecone_api_key_encrypted:
                try:
                    key = decrypt_value(
                        db_settings.pinecone_api_key_encrypted,
                        db_settings.pinecone_api_key_dek
                    )
                    result["pinecone_api_key_masked"] = mask_key(key)
                    result["has_pinecone_key"] = True
                except EncryptionError:
                    pass

        return result

    @staticmethod
    async def create_or_update_user_settings(
        session: AsyncSession,
        user_id: int,
        settings_data: Dict[str, Any]
    ) -> AIProviderSettings:
        """
        Create or update settings for an individual user.

        Raises ValueError if user is in an organization (must use org settings).
        """
        # Check if user is in an org
        if await SettingsService.is_user_in_org(session, user_id):
            raise ValueError("Organization members cannot set personal API keys. Contact your org admin.")

        # Get or create settings
        existing = await SettingsService.get_user_settings(session, user_id)

        if existing:
            settings = existing
        else:
            settings = AIProviderSettings(
                scope=SettingsScope.USER.value,
                user_id=user_id,
            )
            session.add(settings)

        # Update fields
        await SettingsService._apply_settings_update(settings, settings_data)

        await session.commit()
        await session.refresh(settings)
        return settings

    @staticmethod
    async def create_or_update_org_settings(
        session: AsyncSession,
        organization_id: int,
        settings_data: Dict[str, Any]
    ) -> AIProviderSettings:
        """Create or update settings for an organization."""
        existing = await SettingsService.get_org_settings(session, organization_id)

        if existing:
            settings = existing
        else:
            settings = AIProviderSettings(
                scope=SettingsScope.ORGANIZATION.value,
                organization_id=organization_id,
            )
            session.add(settings)

        await SettingsService._apply_settings_update(settings, settings_data)

        await session.commit()
        await session.refresh(settings)
        return settings

    @staticmethod
    async def _apply_settings_update(
        settings: AIProviderSettings,
        data: Dict[str, Any]
    ) -> None:
        """Apply updates to a settings object, encrypting keys."""
        # Encrypt and set API keys if provided
        if "anthropic_api_key" in data and data["anthropic_api_key"]:
            encrypted, dek = encrypt_value(data["anthropic_api_key"])
            settings.anthropic_api_key_encrypted = encrypted
            settings.anthropic_api_key_dek = dek

        if "openai_api_key" in data and data["openai_api_key"]:
            encrypted, dek = encrypt_value(data["openai_api_key"])
            settings.openai_api_key_encrypted = encrypted
            settings.openai_api_key_dek = dek

        if "huggingface_api_key" in data and data["huggingface_api_key"]:
            encrypted, dek = encrypt_value(data["huggingface_api_key"])
            settings.huggingface_api_key_encrypted = encrypted
            settings.huggingface_api_key_dek = dek

        if "pinecone_api_key" in data and data["pinecone_api_key"]:
            encrypted, dek = encrypt_value(data["pinecone_api_key"])
            settings.pinecone_api_key_encrypted = encrypted
            settings.pinecone_api_key_dek = dek

        # Set non-encrypted fields
        if "pinecone_environment" in data:
            settings.pinecone_environment = data["pinecone_environment"]

        if "pinecone_index_name" in data:
            settings.pinecone_index_name = data["pinecone_index_name"]

        if "preferred_llm_provider" in data:
            settings.preferred_llm_provider = data["preferred_llm_provider"]

        if "preferred_llm_model" in data:
            settings.preferred_llm_model = data["preferred_llm_model"]

        if "preferred_embedding_provider" in data:
            settings.preferred_embedding_provider = data["preferred_embedding_provider"]

        if "preferred_embedding_model" in data:
            settings.preferred_embedding_model = data["preferred_embedding_model"]

        settings.updated_at = datetime.utcnow()

    @staticmethod
    async def delete_user_settings(
        session: AsyncSession,
        user_id: int
    ) -> bool:
        """Delete a user's personal settings."""
        settings = await SettingsService.get_user_settings(session, user_id)
        if settings:
            await session.delete(settings)
            await session.commit()
            return True
        return False

    @staticmethod
    async def get_pinecone_namespace(
        session: AsyncSession,
        user_id: int
    ) -> str:
        """
        Get the Pinecone namespace for a user's canvases.

        - Org users: org_{org_id}
        - Individual users: user_{user_id}
        """
        org_id = await SettingsService.get_user_organization_id(session, user_id)

        if org_id:
            return f"org_{org_id}"
        return f"user_{user_id}"


# Singleton instance
settings_service = SettingsService()
