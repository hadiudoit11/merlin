"""
Skills API Endpoints

Handles external service connections (Confluence, Notion, etc.)
including OAuth flows, space linking, and sync operations.
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from datetime import datetime, timedelta

from app.core.database import get_session
from app.core.config import settings
from app.api.deps import get_current_user
from app.models.user import User
from app.models.organization import Organization, OrganizationMember
from app.models.skill import (
    Skill,
    SpaceSkill,
    PageSync,
    SkillProvider as ModelProvider,
    SyncDirection as ModelSyncDirection,
    SyncStatus as ModelSyncStatus,
)
from app.schemas.skill import (
    SkillProvider,
    SyncDirection,
    SyncStatus,
    SkillResponse,
    SkillBrief,
    OAuthInitResponse,
    ConfluenceSpace,
    ConfluencePage,
    ConfluencePageList,
    SpaceSkillCreate,
    SpaceSkillUpdate,
    SpaceSkillResponse,
    ImportRequest,
    ExportRequest,
    SyncResult,
    SyncNowRequest,
    PageSyncResponse,
    ProviderInfo,
    SlackTeam,
    SlackChannel,
    SlackChannelList,
    SlackUser,
    SlackUserList,
    SlackMessage,
    SlackMessageList,
    SlackPostMessageRequest,
    SlackSearchRequest,
    SlackSearchResult,
)
from app.services.confluence import ConfluenceService
from app.services.slack import SlackService


router = APIRouter()

# Store OAuth state temporarily (in production, use Redis or similar)
_oauth_states: dict = {}


# ============ Helper Functions ============

async def get_user_organization(
    session: AsyncSession,
    user_id: int,
    organization_id: Optional[int] = None
) -> Organization:
    """Get user's organization (or a specific one they have access to)."""
    query = (
        select(Organization)
        .join(OrganizationMember)
        .where(OrganizationMember.user_id == user_id, Organization.is_active == True)
    )

    if organization_id:
        query = query.where(Organization.id == organization_id)

    result = await session.execute(query.order_by(Organization.id).limit(1))
    org = result.scalar_one_or_none()

    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found or you don't have access"
        )

    return org


async def get_skill(
    session: AsyncSession,
    organization_id: int,
    provider: ModelProvider
) -> Optional[Skill]:
    """Get skill by provider for an organization."""
    result = await session.execute(
        select(Skill)
        .where(
            Skill.organization_id == organization_id,
            Skill.provider == provider
        )
    )
    return result.scalar_one_or_none()


async def require_skill(
    session: AsyncSession,
    organization_id: int,
    provider: ModelProvider
) -> Skill:
    """Get skill or raise 404."""
    skill = await get_skill(session, organization_id, provider)
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{provider.value} skill not found"
        )
    return skill


# ============ Provider Info ============

@router.get("/providers", response_model=List[ProviderInfo])
async def list_providers() -> List[ProviderInfo]:
    """List all available skill providers."""
    return [
        ProviderInfo(
            id=SkillProvider.CONFLUENCE,
            name="Confluence",
            description="Sync documents with Atlassian Confluence",
            icon="confluence",
            is_configured=settings.CONFLUENCE_CONFIGURED,
            auth_type="oauth",
            scopes=settings.CONFLUENCE_SCOPES.split(" "),
        ),
        ProviderInfo(
            id=SkillProvider.SLACK,
            name="Slack",
            description="Connect to Slack for notifications and sharing",
            icon="slack",
            is_configured=settings.SLACK_CONFIGURED,
            auth_type="oauth",
            scopes=settings.SLACK_SCOPES.split(","),
        ),
        ProviderInfo(
            id=SkillProvider.NOTION,
            name="Notion",
            description="Sync pages with Notion workspaces",
            icon="notion",
            is_configured=False,  # Not implemented yet
            auth_type="oauth",
            scopes=[],
        ),
    ]


# ============ Skill CRUD ============

@router.get("/", response_model=List[SkillBrief])
async def list_skills(
    organization_id: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> List[SkillBrief]:
    """List all skills for the organization."""
    org = await get_user_organization(session, current_user.id, organization_id)

    result = await session.execute(
        select(Skill).where(Skill.organization_id == org.id)
    )
    skills = result.scalars().all()

    return [
        SkillBrief(
            id=i.id,
            provider=SkillProvider(i.provider.value),
            status=SyncStatus(i.status.value),
            is_connected=i.is_connected,
            site_url=i.provider_data.get("site_url") if i.provider_data else None,
        )
        for i in skills
    ]


@router.get("/{provider}", response_model=SkillResponse)
async def get_skill_by_provider(
    provider: SkillProvider,
    organization_id: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SkillResponse:
    """Get a specific skill by provider."""
    org = await get_user_organization(session, current_user.id, organization_id)
    skill = await require_skill(session, org.id, ModelProvider(provider.value))

    return SkillResponse(
        id=skill.id,
        provider=SkillProvider(skill.provider.value),
        status=SyncStatus(skill.status.value),
        is_connected=skill.is_connected,
        provider_data=skill.provider_data or {},
        connected_by_id=skill.connected_by_id,
        created_at=skill.created_at,
        updated_at=skill.updated_at,
        site_url=skill.provider_data.get("site_url") if skill.provider_data else None,
        cloud_id=skill.provider_data.get("cloud_id") if skill.provider_data else None,
    )


@router.delete("/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_skill(
    provider: SkillProvider,
    organization_id: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Disconnect a skill. This removes all tokens and space links."""
    org = await get_user_organization(session, current_user.id, organization_id)
    skill = await require_skill(session, org.id, ModelProvider(provider.value))

    await session.delete(skill)
    await session.commit()


# ============ Confluence OAuth Flow ============

@router.get("/confluence/connect", response_model=OAuthInitResponse)
async def initiate_confluence_oauth(
    organization_id: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> OAuthInitResponse:
    """Initiate Confluence OAuth flow. Returns URL to redirect user to."""
    if not settings.CONFLUENCE_CONFIGURED:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Confluence skill is not configured"
        )

    org = await get_user_organization(session, current_user.id, organization_id)

    # Check if already connected
    existing = await get_skill(session, org.id, ModelProvider.CONFLUENCE)
    if existing and existing.is_connected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Confluence is already connected. Disconnect first to reconnect."
        )

    # Generate OAuth state
    service = ConfluenceService()
    state = service.generate_state()

    # Store state with org/user info (expires in 10 minutes)
    _oauth_states[state] = {
        "organization_id": org.id,
        "user_id": current_user.id,
        "expires_at": datetime.utcnow() + timedelta(minutes=10),
    }

    auth_url = service.get_authorization_url(state)

    return OAuthInitResponse(auth_url=auth_url, state=state)


@router.get("/confluence/callback")
async def confluence_oauth_callback(
    code: str,
    state: str,
    session: AsyncSession = Depends(get_session),
):
    """
    Handle Confluence OAuth callback.

    This endpoint is called by Atlassian after user authorizes.
    It exchanges the code for tokens and stores the skill.
    """
    # Validate state
    state_data = _oauth_states.pop(state, None)
    if not state_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OAuth state"
        )

    if datetime.utcnow() > state_data["expires_at"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth state has expired"
        )

    organization_id = state_data["organization_id"]
    user_id = state_data["user_id"]

    try:
        # Exchange code for tokens
        service = ConfluenceService()
        tokens = await service.exchange_code_for_tokens(code)

        access_token = tokens["access_token"]
        refresh_token = tokens.get("refresh_token")
        expires_in = tokens.get("expires_in", 3600)

        # Get accessible resources (cloud sites)
        service.access_token = access_token
        resources = await service.get_accessible_resources()

        if not resources:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No accessible Confluence sites found"
            )

        # Use first available site
        site = resources[0]
        cloud_id = site["id"]
        site_url = site.get("url", "")
        site_name = site.get("name", "")

        await service.close()

        # Create or update skill
        skill = await get_skill(session, organization_id, ModelProvider.CONFLUENCE)

        if skill:
            skill.access_token = access_token
            skill.refresh_token = refresh_token
            skill.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
            skill.provider_data = {
                "cloud_id": cloud_id,
                "site_url": site_url,
                "site_name": site_name,
            }
            skill.status = ModelSyncStatus.IDLE
            skill.connected_by_id = user_id
        else:
            skill = Skill(
                organization_id=organization_id,
                provider=ModelProvider.CONFLUENCE,
                access_token=access_token,
                refresh_token=refresh_token,
                token_expires_at=datetime.utcnow() + timedelta(seconds=expires_in),
                provider_data={
                    "cloud_id": cloud_id,
                    "site_url": site_url,
                    "site_name": site_name,
                },
                status=ModelSyncStatus.IDLE,
                connected_by_id=user_id,
            )
            session.add(skill)

        await session.commit()

        # Redirect back to frontend
        frontend_url = settings.CORS_ORIGINS[0] if settings.CORS_ORIGINS else "http://localhost:3000"
        return RedirectResponse(
            url=f"{frontend_url}/settings/skills?connected=confluence",
            status_code=status.HTTP_302_FOUND,
        )

    except Exception as e:
        # Redirect with error
        frontend_url = settings.CORS_ORIGINS[0] if settings.CORS_ORIGINS else "http://localhost:3000"
        return RedirectResponse(
            url=f"{frontend_url}/settings/skills?error=confluence_connect_failed",
            status_code=status.HTTP_302_FOUND,
        )


# ============ Confluence Spaces ============

@router.get("/confluence/spaces", response_model=List[ConfluenceSpace])
async def list_confluence_spaces(
    organization_id: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> List[ConfluenceSpace]:
    """List available Confluence spaces."""
    org = await get_user_organization(session, current_user.id, organization_id)
    skill = await require_skill(session, org.id, ModelProvider.CONFLUENCE)

    if not skill.is_connected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Confluence is not connected"
        )

    service = ConfluenceService(
        access_token=skill.access_token,
        cloud_id=skill.provider_data.get("cloud_id"),
    )

    try:
        spaces = await service.list_spaces()
        return spaces
    finally:
        await service.close()


@router.get("/confluence/spaces/{space_key}/pages", response_model=ConfluencePageList)
async def list_confluence_pages(
    space_key: str,
    limit: int = Query(50, le=100),
    cursor: Optional[str] = None,
    organization_id: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ConfluencePageList:
    """List pages in a Confluence space."""
    org = await get_user_organization(session, current_user.id, organization_id)
    skill = await require_skill(session, org.id, ModelProvider.CONFLUENCE)

    if not skill.is_connected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Confluence is not connected"
        )

    service = ConfluenceService(
        access_token=skill.access_token,
        cloud_id=skill.provider_data.get("cloud_id"),
    )

    try:
        # First get the space to get its ID
        space = await service.get_space(space_key)
        if not space:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Confluence space '{space_key}' not found"
            )

        result = await service.list_pages(space.id, limit=limit, cursor=cursor)

        return ConfluencePageList(
            pages=result["pages"],
            total=result["total"],
            start=0,
            limit=limit,
        )
    finally:
        await service.close()


# ============ Space Skills ============

@router.get("/spaces/{space_id}", response_model=Optional[SpaceSkillResponse])
async def get_space_skill(
    space_id: str,
    provider: SkillProvider = Query(...),
    organization_id: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> Optional[SpaceSkillResponse]:
    """Get space skill for a specific provider."""
    org = await get_user_organization(session, current_user.id, organization_id)
    skill = await get_skill(session, org.id, ModelProvider(provider.value))

    if not skill:
        return None

    result = await session.execute(
        select(SpaceSkill)
        .where(
            SpaceSkill.skill_id == skill.id,
            SpaceSkill.space_id == space_id,
        )
    )
    space_sk = result.scalar_one_or_none()

    if not space_sk:
        return None

    return SpaceSkillResponse(
        id=space_sk.id,
        skill_id=space_sk.skill_id,
        space_id=space_sk.space_id,
        space_type=space_sk.space_type,
        external_space_key=space_sk.external_space_key,
        external_space_id=space_sk.external_space_id,
        external_space_name=space_sk.external_space_name,
        sync_enabled=space_sk.sync_enabled,
        sync_direction=SyncDirection(space_sk.sync_direction.value),
        auto_sync=space_sk.auto_sync,
        sync_status=SyncStatus(space_sk.sync_status.value),
        last_sync_at=space_sk.last_sync_at,
        last_sync_error=space_sk.last_sync_error,
        created_at=space_sk.created_at,
        updated_at=space_sk.updated_at,
    )


@router.post("/spaces/{space_id}/confluence", response_model=SpaceSkillResponse)
async def link_space_to_confluence(
    space_id: str,
    data: SpaceSkillCreate,
    organization_id: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SpaceSkillResponse:
    """Link a Merlin space to a Confluence space."""
    org = await get_user_organization(session, current_user.id, organization_id)
    skill = await require_skill(session, org.id, ModelProvider.CONFLUENCE)

    if not skill.is_connected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Confluence is not connected"
        )

    # Check if already linked
    result = await session.execute(
        select(SpaceSkill)
        .where(
            SpaceSkill.skill_id == skill.id,
            SpaceSkill.space_id == space_id,
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Space is already linked to Confluence"
        )

    # Verify the Confluence space exists
    service = ConfluenceService(
        access_token=skill.access_token,
        cloud_id=skill.provider_data.get("cloud_id"),
    )

    try:
        confluence_space = await service.get_space(data.external_space_key)
        if not confluence_space:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Confluence space '{data.external_space_key}' not found"
            )
    finally:
        await service.close()

    # Create space skill
    space_sk = SpaceSkill(
        skill_id=skill.id,
        space_id=space_id,
        space_type="document",
        external_space_key=confluence_space.key,
        external_space_id=confluence_space.id,
        external_space_name=confluence_space.name,
        sync_enabled=True,
        sync_direction=ModelSyncDirection(data.sync_direction.value),
        auto_sync=data.auto_sync,
        sync_status=ModelSyncStatus.IDLE,
    )
    session.add(space_sk)
    await session.commit()
    await session.refresh(space_sk)

    return SpaceSkillResponse(
        id=space_sk.id,
        skill_id=space_sk.skill_id,
        space_id=space_sk.space_id,
        space_type=space_sk.space_type,
        external_space_key=space_sk.external_space_key,
        external_space_id=space_sk.external_space_id,
        external_space_name=space_sk.external_space_name,
        sync_enabled=space_sk.sync_enabled,
        sync_direction=SyncDirection(space_sk.sync_direction.value),
        auto_sync=space_sk.auto_sync,
        sync_status=SyncStatus(space_sk.sync_status.value),
        last_sync_at=space_sk.last_sync_at,
        last_sync_error=space_sk.last_sync_error,
        created_at=space_sk.created_at,
        updated_at=space_sk.updated_at,
    )


@router.delete("/spaces/{space_id}/confluence", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_space_from_confluence(
    space_id: str,
    organization_id: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Unlink a Merlin space from Confluence."""
    org = await get_user_organization(session, current_user.id, organization_id)
    skill = await get_skill(session, org.id, ModelProvider.CONFLUENCE)

    if not skill:
        return

    result = await session.execute(
        select(SpaceSkill)
        .where(
            SpaceSkill.skill_id == skill.id,
            SpaceSkill.space_id == space_id,
        )
    )
    space_sk = result.scalar_one_or_none()

    if space_sk:
        await session.delete(space_sk)
        await session.commit()


# ============ Import/Export ============

@router.post("/spaces/{space_id}/confluence/import", response_model=SyncResult)
async def import_from_confluence(
    space_id: str,
    data: ImportRequest,
    organization_id: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SyncResult:
    """Import pages from Confluence into Merlin."""
    org = await get_user_organization(session, current_user.id, organization_id)
    skill = await require_skill(session, org.id, ModelProvider.CONFLUENCE)

    if not skill.is_connected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Confluence is not connected"
        )

    # Get space skill
    result = await session.execute(
        select(SpaceSkill)
        .where(
            SpaceSkill.skill_id == skill.id,
            SpaceSkill.space_id == space_id,
        )
    )
    space_sk = result.scalar_one_or_none()

    if not space_sk:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Space is not linked to Confluence"
        )

    service = ConfluenceService(
        access_token=skill.access_token,
        cloud_id=skill.provider_data.get("cloud_id"),
    )

    sync_result = SyncResult(success=True)

    try:
        for page_id in data.page_ids:
            try:
                page = await service.get_page(page_id, include_body=True)
                if not page:
                    sync_result.errors.append(f"Page {page_id} not found")
                    continue

                # Convert content
                tiptap_content = service.confluence_to_tiptap(page.body_html or "")

                # TODO: Create page in Merlin docs system
                # For now, we'll just track the sync status
                # In a real implementation, you'd call your docs API here

                # Create page sync record
                page_sync = PageSync(
                    space_skill_id=space_sk.id,
                    page_id=f"merlin-{page_id}",  # Would be real Merlin page ID
                    external_page_id=page_id,
                    external_page_url=page.web_url,
                    local_version=1,
                    remote_version=page.version,
                    sync_status=ModelSyncStatus.IDLE,
                    last_sync_at=datetime.utcnow(),
                    last_sync_direction="import",
                )
                session.add(page_sync)

                sync_result.imported += 1

            except Exception as e:
                sync_result.errors.append(f"Failed to import {page_id}: {str(e)}")

        # Update space skill
        space_sk.last_sync_at = datetime.utcnow()
        space_sk.sync_status = ModelSyncStatus.IDLE

        await session.commit()

    finally:
        await service.close()

    if sync_result.errors:
        sync_result.success = False

    return sync_result


@router.post("/spaces/{space_id}/confluence/export", response_model=SyncResult)
async def export_to_confluence(
    space_id: str,
    data: ExportRequest,
    organization_id: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SyncResult:
    """Export pages from Merlin to Confluence."""
    org = await get_user_organization(session, current_user.id, organization_id)
    skill = await require_skill(session, org.id, ModelProvider.CONFLUENCE)

    if not skill.is_connected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Confluence is not connected"
        )

    # Get space skill
    result = await session.execute(
        select(SpaceSkill)
        .where(
            SpaceSkill.skill_id == skill.id,
            SpaceSkill.space_id == space_id,
        )
    )
    space_sk = result.scalar_one_or_none()

    if not space_sk:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Space is not linked to Confluence"
        )

    sync_result = SyncResult(success=True)

    # TODO: Implement actual export
    # This would:
    # 1. Get Merlin pages by ID
    # 2. Convert Tiptap to Confluence storage format
    # 3. Create/update pages in Confluence
    # 4. Update page sync records

    sync_result.errors.append("Export not yet implemented")
    sync_result.success = False

    return sync_result


# ============ Slack OAuth Flow ============

@router.get("/slack/connect", response_model=OAuthInitResponse)
async def initiate_slack_oauth(
    organization_id: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> OAuthInitResponse:
    """Initiate Slack OAuth flow. Returns URL to redirect user to."""
    if not settings.SLACK_CONFIGURED:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Slack skill is not configured"
        )

    org = await get_user_organization(session, current_user.id, organization_id)

    # Check if already connected
    existing = await get_skill(session, org.id, ModelProvider.SLACK)
    if existing and existing.is_connected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Slack is already connected. Disconnect first to reconnect."
        )

    # Generate OAuth state
    service = SlackService()
    state = service.generate_state()

    # Store state with org/user info (expires in 10 minutes)
    _oauth_states[state] = {
        "organization_id": org.id,
        "user_id": current_user.id,
        "provider": "slack",
        "expires_at": datetime.utcnow() + timedelta(minutes=10),
    }

    auth_url = service.get_authorization_url(state)

    return OAuthInitResponse(auth_url=auth_url, state=state)


@router.get("/slack/callback")
async def slack_oauth_callback(
    code: str,
    state: str,
    session: AsyncSession = Depends(get_session),
):
    """
    Handle Slack OAuth callback.

    This endpoint is called by Slack after user authorizes.
    It exchanges the code for tokens and stores the skill.
    """
    # Validate state
    state_data = _oauth_states.pop(state, None)
    if not state_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OAuth state"
        )

    if datetime.utcnow() > state_data["expires_at"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth state has expired"
        )

    organization_id = state_data["organization_id"]
    user_id = state_data["user_id"]

    try:
        # Exchange code for tokens
        service = SlackService()
        tokens = await service.exchange_code_for_tokens(code)

        access_token = tokens["access_token"]
        team = tokens.get("team", {})
        team_id = team.get("id", "")
        team_name = team.get("name", "")

        # Get additional team info
        service.access_token = access_token
        try:
            team_info = await service.get_team_info()
            team_domain = team_info.domain
            team_icon = team_info.icon_url
        except Exception:
            team_domain = ""
            team_icon = None

        await service.close()

        # Create or update skill
        skill = await get_skill(session, organization_id, ModelProvider.SLACK)

        provider_data = {
            "team_id": team_id,
            "team_name": team_name,
            "team_domain": team_domain,
            "team_icon": team_icon,
        }

        if skill:
            skill.access_token = access_token
            skill.refresh_token = None  # Slack doesn't use refresh tokens
            skill.token_expires_at = None  # Slack tokens don't expire
            skill.provider_data = provider_data
            skill.status = ModelSyncStatus.IDLE
            skill.connected_by_id = user_id
        else:
            skill = Skill(
                organization_id=organization_id,
                provider=ModelProvider.SLACK,
                access_token=access_token,
                refresh_token=None,
                token_expires_at=None,
                provider_data=provider_data,
                status=ModelSyncStatus.IDLE,
                connected_by_id=user_id,
            )
            session.add(skill)

        await session.commit()

        # Redirect back to frontend
        frontend_url = settings.CORS_ORIGINS[0] if settings.CORS_ORIGINS else "http://localhost:3000"
        return RedirectResponse(
            url=f"{frontend_url}/settings/skills?connected=slack",
            status_code=status.HTTP_302_FOUND,
        )

    except Exception as e:
        # Redirect with error
        frontend_url = settings.CORS_ORIGINS[0] if settings.CORS_ORIGINS else "http://localhost:3000"
        return RedirectResponse(
            url=f"{frontend_url}/settings/skills?error=slack_connect_failed",
            status_code=status.HTTP_302_FOUND,
        )


# ============ Slack Team Info ============

@router.get("/slack/team", response_model=SlackTeam)
async def get_slack_team(
    organization_id: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SlackTeam:
    """Get information about the connected Slack workspace."""
    org = await get_user_organization(session, current_user.id, organization_id)
    skill = await require_skill(session, org.id, ModelProvider.SLACK)

    if not skill.is_connected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Slack is not connected"
        )

    service = SlackService(access_token=skill.access_token)

    try:
        team = await service.get_team_info()
        return SlackTeam(
            id=team.id,
            name=team.name,
            domain=team.domain,
            icon_url=team.icon_url,
        )
    finally:
        await service.close()


# ============ Slack Channels ============

@router.get("/slack/channels", response_model=SlackChannelList)
async def list_slack_channels(
    include_private: bool = False,
    exclude_archived: bool = True,
    limit: int = Query(100, le=1000),
    cursor: Optional[str] = None,
    organization_id: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SlackChannelList:
    """List channels in the Slack workspace."""
    org = await get_user_organization(session, current_user.id, organization_id)
    skill = await require_skill(session, org.id, ModelProvider.SLACK)

    if not skill.is_connected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Slack is not connected"
        )

    service = SlackService(access_token=skill.access_token)

    try:
        result = await service.list_channels(
            include_private=include_private,
            exclude_archived=exclude_archived,
            limit=limit,
            cursor=cursor,
        )

        return SlackChannelList(
            channels=[
                SlackChannel(
                    id=ch.id,
                    name=ch.name,
                    is_private=ch.is_private,
                    is_archived=ch.is_archived,
                    topic=ch.topic,
                    purpose=ch.purpose,
                    num_members=ch.num_members,
                )
                for ch in result["channels"]
            ],
            cursor=result.get("cursor"),
        )
    finally:
        await service.close()


@router.get("/slack/channels/{channel_id}", response_model=SlackChannel)
async def get_slack_channel(
    channel_id: str,
    organization_id: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SlackChannel:
    """Get information about a specific Slack channel."""
    org = await get_user_organization(session, current_user.id, organization_id)
    skill = await require_skill(session, org.id, ModelProvider.SLACK)

    if not skill.is_connected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Slack is not connected"
        )

    service = SlackService(access_token=skill.access_token)

    try:
        channel = await service.get_channel_info(channel_id)
        return SlackChannel(
            id=channel.id,
            name=channel.name,
            is_private=channel.is_private,
            is_archived=channel.is_archived,
            topic=channel.topic,
            purpose=channel.purpose,
            num_members=channel.num_members,
        )
    finally:
        await service.close()


# ============ Slack Messages ============

@router.get("/slack/channels/{channel_id}/messages", response_model=SlackMessageList)
async def get_slack_channel_messages(
    channel_id: str,
    limit: int = Query(100, le=1000),
    oldest: Optional[str] = None,
    latest: Optional[str] = None,
    cursor: Optional[str] = None,
    organization_id: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SlackMessageList:
    """Get message history for a Slack channel."""
    org = await get_user_organization(session, current_user.id, organization_id)
    skill = await require_skill(session, org.id, ModelProvider.SLACK)

    if not skill.is_connected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Slack is not connected"
        )

    service = SlackService(access_token=skill.access_token)

    try:
        result = await service.get_channel_history(
            channel_id=channel_id,
            limit=limit,
            oldest=oldest,
            latest=latest,
            cursor=cursor,
        )

        return SlackMessageList(
            messages=[
                SlackMessage(
                    ts=msg.ts,
                    text=msg.text,
                    user_id=msg.user_id,
                    channel_id=msg.channel_id,
                    thread_ts=msg.thread_ts,
                    reply_count=msg.reply_count,
                    timestamp=msg.timestamp,
                )
                for msg in result["messages"]
            ],
            has_more=result.get("has_more", False),
            cursor=result.get("cursor"),
        )
    finally:
        await service.close()


@router.get("/slack/channels/{channel_id}/threads/{thread_ts}", response_model=SlackMessageList)
async def get_slack_thread_replies(
    channel_id: str,
    thread_ts: str,
    limit: int = Query(100, le=1000),
    cursor: Optional[str] = None,
    organization_id: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SlackMessageList:
    """Get replies in a Slack thread."""
    org = await get_user_organization(session, current_user.id, organization_id)
    skill = await require_skill(session, org.id, ModelProvider.SLACK)

    if not skill.is_connected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Slack is not connected"
        )

    service = SlackService(access_token=skill.access_token)

    try:
        result = await service.get_thread_replies(
            channel_id=channel_id,
            thread_ts=thread_ts,
            limit=limit,
            cursor=cursor,
        )

        return SlackMessageList(
            messages=[
                SlackMessage(
                    ts=msg.ts,
                    text=msg.text,
                    user_id=msg.user_id,
                    channel_id=msg.channel_id,
                    thread_ts=msg.thread_ts,
                    timestamp=msg.timestamp,
                )
                for msg in result["messages"]
            ],
            has_more=result.get("has_more", False),
            cursor=result.get("cursor"),
        )
    finally:
        await service.close()


@router.post("/slack/channels/{channel_id}/messages", response_model=SlackMessage)
async def post_slack_message(
    channel_id: str,
    data: SlackPostMessageRequest,
    organization_id: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SlackMessage:
    """Post a message to a Slack channel."""
    org = await get_user_organization(session, current_user.id, organization_id)
    skill = await require_skill(session, org.id, ModelProvider.SLACK)

    if not skill.is_connected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Slack is not connected"
        )

    service = SlackService(access_token=skill.access_token)

    try:
        message = await service.post_message(
            channel_id=channel_id,
            text=data.text,
            thread_ts=data.thread_ts,
            unfurl_links=data.unfurl_links,
        )

        return SlackMessage(
            ts=message.ts,
            text=message.text,
            channel_id=message.channel_id,
            thread_ts=message.thread_ts,
            timestamp=message.timestamp,
        )
    finally:
        await service.close()


# ============ Slack Users ============

@router.get("/slack/users", response_model=SlackUserList)
async def list_slack_users(
    limit: int = Query(100, le=1000),
    cursor: Optional[str] = None,
    organization_id: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SlackUserList:
    """List users in the Slack workspace."""
    org = await get_user_organization(session, current_user.id, organization_id)
    skill = await require_skill(session, org.id, ModelProvider.SLACK)

    if not skill.is_connected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Slack is not connected"
        )

    service = SlackService(access_token=skill.access_token)

    try:
        result = await service.list_users(limit=limit, cursor=cursor)

        return SlackUserList(
            users=[
                SlackUser(
                    id=user.id,
                    name=user.name,
                    real_name=user.real_name,
                    display_name=user.display_name,
                    email=user.email,
                    avatar_url=user.avatar_url,
                    is_bot=user.is_bot,
                    is_admin=user.is_admin,
                )
                for user in result["users"]
            ],
            cursor=result.get("cursor"),
        )
    finally:
        await service.close()


@router.get("/slack/users/{user_id}", response_model=SlackUser)
async def get_slack_user(
    user_id: str,
    organization_id: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SlackUser:
    """Get information about a specific Slack user."""
    org = await get_user_organization(session, current_user.id, organization_id)
    skill = await require_skill(session, org.id, ModelProvider.SLACK)

    if not skill.is_connected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Slack is not connected"
        )

    service = SlackService(access_token=skill.access_token)

    try:
        user = await service.get_user_info(user_id)
        return SlackUser(
            id=user.id,
            name=user.name,
            real_name=user.real_name,
            display_name=user.display_name,
            email=user.email,
            avatar_url=user.avatar_url,
            is_bot=user.is_bot,
            is_admin=user.is_admin,
        )
    finally:
        await service.close()


# ============ Slack Search ============

@router.post("/slack/search", response_model=SlackSearchResult)
async def search_slack_messages(
    data: SlackSearchRequest,
    organization_id: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> SlackSearchResult:
    """Search for messages in Slack (requires search:read scope)."""
    org = await get_user_organization(session, current_user.id, organization_id)
    skill = await require_skill(session, org.id, ModelProvider.SLACK)

    if not skill.is_connected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Slack is not connected"
        )

    service = SlackService(access_token=skill.access_token)

    try:
        result = await service.search_messages(
            query=data.query,
            sort=data.sort,
            sort_dir=data.sort_dir,
            count=data.count,
            page=data.page,
        )

        return SlackSearchResult(
            messages=[
                SlackMessage(
                    ts=msg.ts,
                    text=msg.text,
                    user_id=msg.user_id,
                    channel_id=msg.channel_id,
                    timestamp=msg.timestamp,
                )
                for msg in result["messages"]
            ],
            total=result["total"],
            page=result["page"],
            pages=result["pages"],
        )
    finally:
        await service.close()
