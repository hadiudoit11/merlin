"""API endpoints for AI provider settings and canvas indexing."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.core.database import get_session
from app.api.v1.endpoints.auth import get_current_user
from app.models.user import User
from app.schemas.settings import (
    SettingsUpdate, SettingsResponse,
    IndexCanvasRequest, IndexCanvasResponse,
    SearchRequest, SearchResult, SearchResponse,
)
from app.services.settings_service import SettingsService
from app.services.indexing_service import (
    CanvasIndexingService, EmbeddingError, VectorStoreError
)

router = APIRouter()


# ============ Settings Endpoints ============

@router.get("/", response_model=SettingsResponse)
async def get_settings(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Get current AI provider settings.

    - Org members see org settings (read-only)
    - Individual users see their own settings (editable)
    - Falls back to system defaults if no custom settings
    """
    settings = await SettingsService.get_masked_settings(session, current_user.id)
    return SettingsResponse(**settings)


@router.put("/", response_model=SettingsResponse)
async def update_settings(
    settings_data: SettingsUpdate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Update AI provider settings.

    - Only individual users (not in an org) can update settings
    - Org members must contact their admin to change settings
    """
    # Check if user can edit settings
    is_org_member = await SettingsService.is_user_in_org(session, current_user.id)

    if is_org_member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization members cannot modify settings. Contact your org admin."
        )

    # Update settings
    try:
        await SettingsService.create_or_update_user_settings(
            session,
            user_id=current_user.id,
            settings_data=settings_data.model_dump(exclude_unset=True),
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    # Return updated settings
    settings = await SettingsService.get_masked_settings(session, current_user.id)
    return SettingsResponse(**settings)


@router.delete("/")
async def delete_settings(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Delete personal settings and revert to system defaults.

    Only available for individual users (not org members).
    """
    is_org_member = await SettingsService.is_user_in_org(session, current_user.id)

    if is_org_member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization members cannot modify settings."
        )

    deleted = await SettingsService.delete_user_settings(session, current_user.id)

    if deleted:
        return {"status": "deleted", "message": "Settings deleted, using system defaults"}
    else:
        return {"status": "not_found", "message": "No personal settings to delete"}


# ============ Organization Settings Endpoints ============

@router.get("/organization/{organization_id}", response_model=SettingsResponse)
async def get_org_settings(
    organization_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Get organization AI provider settings.

    Only org members can view their org's settings.
    """
    # Verify user is in the org
    user_org_id = await SettingsService.get_user_organization_id(session, current_user.id)

    if user_org_id != organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must be a member of the organization to view settings"
        )

    settings = await SettingsService.get_masked_settings(session, current_user.id)
    return SettingsResponse(**settings)


@router.put("/organization/{organization_id}", response_model=SettingsResponse)
async def update_org_settings(
    organization_id: int,
    settings_data: SettingsUpdate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Update organization AI provider settings.

    Only org admins can update settings.
    TODO: Add role check for admin
    """
    # Verify user is in the org
    user_org_id = await SettingsService.get_user_organization_id(session, current_user.id)

    if user_org_id != organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must be an admin of the organization to update settings"
        )

    # Update org settings
    await SettingsService.create_or_update_org_settings(
        session,
        organization_id=organization_id,
        settings_data=settings_data.model_dump(exclude_unset=True),
    )

    # Return updated settings
    settings = await SettingsService.get_masked_settings(session, current_user.id)
    return SettingsResponse(**settings)


# ============ Indexing Endpoints ============

@router.post("/index/canvas", response_model=IndexCanvasResponse)
async def index_canvas(
    request: IndexCanvasRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Index a canvas for semantic search.

    Generates embeddings for all nodes and stores in Pinecone.
    """
    try:
        result = await CanvasIndexingService.index_canvas(
            session,
            canvas_id=request.canvas_id,
            user_id=current_user.id,
        )
        return IndexCanvasResponse(**result)

    except EmbeddingError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Embedding service error: {str(e)}"
        )
    except VectorStoreError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Vector store error: {str(e)}"
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )


@router.delete("/index/canvas/{canvas_id}")
async def delete_canvas_index(
    canvas_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Remove a canvas from the search index."""
    try:
        result = await CanvasIndexingService.delete_canvas_from_index(
            session,
            canvas_id=canvas_id,
            user_id=current_user.id,
        )
        return result

    except VectorStoreError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Vector store error: {str(e)}"
        )


@router.post("/search", response_model=SearchResponse)
async def search_canvases(
    request: SearchRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Semantic search across canvas content.

    Search all indexed nodes or filter by canvas/node type.
    """
    try:
        results = await CanvasIndexingService.search_canvas(
            session,
            query=request.query,
            user_id=current_user.id,
            canvas_id=request.canvas_id,
            node_types=request.node_types,
            top_k=request.top_k,
        )

        return SearchResponse(
            query=request.query,
            results=[SearchResult(**r) for r in results],
            total=len(results),
        )

    except EmbeddingError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Embedding service error: {str(e)}"
        )
    except VectorStoreError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Vector store error: {str(e)}"
        )
