"""
JIRA Context Service - Index and retrieve Jira issues for AI context.

When generating/refining canvas nodes:
1. Searches for relevant Jira issues using semantic similarity
2. Includes issue content (title, description, comments) in AI prompts
3. Automatically links relevant issues to nodes

Works similar to meeting transcript context, but for Jira data.
"""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.models.task import Task, TaskSource
from app.models.canvas import Canvas
from app.services.indexing_service import CanvasIndexingService
from app.services.settings_service import SettingsService

logger = logging.getLogger(__name__)


class JiraContextService:
    """Manages Jira issues as contextual knowledge for canvas AI."""

    @staticmethod
    async def index_jira_issues(
        session: AsyncSession,
        canvas_id: int,
        user_id: int,
        organization_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Index all Jira issues associated with a canvas into the vector store.

        This makes them searchable for AI context when generating node content.

        Args:
            session: Database session
            canvas_id: Canvas to index issues for
            user_id: User making the request
            organization_id: Organization ID (for namespace)

        Returns:
            {"indexed": int, "status": str}
        """
        # Get all Jira tasks for this canvas
        query = select(Task).where(
            and_(
                Task.canvas_id == canvas_id,
                Task.source == TaskSource.JIRA,
            )
        )
        result = await session.execute(query)
        tasks = result.scalars().all()

        if not tasks:
            return {"indexed": 0, "status": "no_jira_issues"}

        # Get AI settings (embeddings + Pinecone)
        settings = await SettingsService.get_settings(
            session, user_id, organization_id
        )

        if not settings or not settings.pinecone_api_key:
            raise ValueError("Pinecone not configured for indexing")

        # Prepare documents for indexing
        documents = []
        for task in tasks:
            # Combine all text content from the issue
            content_parts = [
                f"Issue: {task.source_id}",
                f"Title: {task.title}",
            ]

            if task.description:
                content_parts.append(f"Description: {task.description}")

            if task.context:
                content_parts.append(f"Additional Context: {task.context}")

            if task.assignee_name:
                content_parts.append(f"Assignee: {task.assignee_name}")

            content = "\n\n".join(content_parts)

            documents.append({
                "id": f"jira_{task.id}",
                "content": content,
                "metadata": {
                    "type": "jira_issue",
                    "task_id": task.id,
                    "canvas_id": canvas_id,
                    "issue_key": task.source_id,
                    "status": task.status,
                    "priority": task.priority,
                    "source_url": task.source_url,
                    "indexed_at": datetime.utcnow().isoformat(),
                }
            })

        # Index documents using existing canvas indexing service
        indexing_service = CanvasIndexingService()

        # Get namespace (org_X or user_X)
        namespace = f"org_{organization_id}" if organization_id else f"user_{user_id}"

        # Use the existing index_nodes method (it works for any documents)
        indexed_count = 0
        try:
            # Get embeddings generator
            embeddings = indexing_service._get_embeddings(settings)
            pinecone = indexing_service._get_pinecone_client(settings)

            # Generate embeddings for all documents
            texts = [doc["content"] for doc in documents]
            vectors = await embeddings.embed(texts)

            # Prepare vectors for Pinecone
            pinecone_vectors = []
            for i, doc in enumerate(documents):
                pinecone_vectors.append({
                    "id": doc["id"],
                    "values": vectors[i],
                    "metadata": doc["metadata"],
                })

            # Upsert to Pinecone
            await pinecone.upsert(pinecone_vectors, namespace)
            indexed_count = len(documents)

            logger.info(
                f"Indexed {indexed_count} Jira issues for canvas {canvas_id}"
            )

        except Exception as e:
            logger.error(f"Failed to index Jira issues: {e}")
            raise

        return {"indexed": indexed_count, "status": "success"}

    @staticmethod
    async def search_relevant_jira_issues(
        session: AsyncSession,
        query_text: str,
        canvas_id: Optional[int] = None,
        user_id: Optional[int] = None,
        organization_id: Optional[int] = None,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Search for Jira issues relevant to a query (e.g., node content being generated).

        This is used to provide AI context when generating/refining canvas nodes.

        Args:
            session: Database session
            query_text: Text to search for (e.g., "authentication slow")
            canvas_id: Optional - filter to specific canvas
            user_id: User ID for settings
            organization_id: Org ID for settings
            top_k: Number of results to return

        Returns:
            List of matching issues with metadata and content
        """
        # Get AI settings
        settings = await SettingsService.get_settings(
            session, user_id, organization_id
        )

        if not settings or not settings.pinecone_api_key:
            return []  # Fail gracefully if not configured

        indexing_service = CanvasIndexingService()
        embeddings = indexing_service._get_embeddings(settings)
        pinecone = indexing_service._get_pinecone_client(settings)

        # Generate embedding for query
        query_vector = await embeddings.embed_single(query_text)

        # Determine namespace
        namespace = f"org_{organization_id}" if organization_id else f"user_{user_id}"

        # Build filter for canvas_id if provided
        metadata_filter = None
        if canvas_id:
            metadata_filter = {
                "type": {"$eq": "jira_issue"},
                "canvas_id": {"$eq": canvas_id},
            }
        else:
            metadata_filter = {"type": {"$eq": "jira_issue"}}

        # Query Pinecone
        try:
            results = await pinecone.query(
                vector=query_vector,
                namespace=namespace,
                top_k=top_k,
                filter=metadata_filter,
                include_metadata=True,
            )

            # Fetch full task details from database
            matches = results.get("matches", [])
            enriched_results = []

            for match in matches:
                metadata = match.get("metadata", {})
                task_id = metadata.get("task_id")

                if not task_id:
                    continue

                # Get full task from database
                task_result = await session.execute(
                    select(Task).where(Task.id == task_id)
                )
                task = task_result.scalar_one_or_none()

                if task:
                    enriched_results.append({
                        "score": match.get("score", 0),
                        "issue_key": task.source_id,
                        "title": task.title,
                        "description": task.description,
                        "status": task.status,
                        "priority": task.priority,
                        "source_url": task.source_url,
                        "task_id": task.id,
                        "context": task.context,
                    })

            return enriched_results

        except Exception as e:
            logger.error(f"Failed to search Jira issues: {e}")
            return []  # Fail gracefully

    @staticmethod
    def format_jira_context_for_ai(issues: List[Dict[str, Any]]) -> str:
        """
        Format Jira issues into a context string for AI prompts.

        Args:
            issues: List of issue dictionaries from search_relevant_jira_issues

        Returns:
            Formatted context string
        """
        if not issues:
            return ""

        context_parts = [
            "## Related Jira Issues",
            "",
            "The following Jira issues are related to this topic and may provide useful context:",
            "",
        ]

        for i, issue in enumerate(issues, 1):
            context_parts.extend([
                f"### {i}. {issue['issue_key']}: {issue['title']}",
                f"**Status**: {issue['status']} | **Priority**: {issue['priority']}",
                "",
            ])

            if issue.get("description"):
                context_parts.extend([
                    f"**Description**: {issue['description'][:500]}...",
                    "",
                ])

            if issue.get("source_url"):
                context_parts.append(f"[View in Jira]({issue['source_url']})")
                context_parts.append("")

        return "\n".join(context_parts)

    @staticmethod
    async def auto_link_relevant_issues(
        session: AsyncSession,
        node_id: int,
        node_content: str,
        canvas_id: int,
        user_id: int,
        organization_id: Optional[int] = None,
        threshold: float = 0.75,
        max_links: int = 3,
    ) -> List[int]:
        """
        Automatically link relevant Jira issues to a node based on semantic similarity.

        Args:
            session: Database session
            node_id: Node to link issues to
            node_content: Content of the node (for similarity search)
            canvas_id: Canvas the node belongs to
            user_id: User ID
            organization_id: Org ID
            threshold: Minimum similarity score (0-1)
            max_links: Maximum number of issues to link

        Returns:
            List of linked task IDs
        """
        from app.models.node import Node

        # Search for relevant issues
        relevant_issues = await JiraContextService.search_relevant_jira_issues(
            session,
            query_text=node_content,
            canvas_id=canvas_id,
            user_id=user_id,
            organization_id=organization_id,
            top_k=max_links * 2,  # Get more candidates
        )

        # Filter by threshold
        high_confidence_issues = [
            issue for issue in relevant_issues
            if issue["score"] >= threshold
        ][:max_links]

        if not high_confidence_issues:
            return []

        # Get the node
        node_result = await session.execute(
            select(Node).where(Node.id == node_id)
        )
        node = node_result.scalar_one_or_none()

        if not node:
            return []

        # Link the tasks
        linked_ids = []
        for issue in high_confidence_issues:
            task_id = issue["task_id"]

            # Get the task
            task_result = await session.execute(
                select(Task).where(Task.id == task_id)
            )
            task = task_result.scalar_one_or_none()

            if task and node not in task.linked_nodes:
                task.linked_nodes.append(node)
                linked_ids.append(task_id)
                logger.info(
                    f"Auto-linked Jira issue {issue['issue_key']} to node {node_id} "
                    f"(score: {issue['score']:.2f})"
                )

        await session.commit()
        return linked_ids
