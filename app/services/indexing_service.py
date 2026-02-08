"""
Canvas indexing service using HuggingFace embeddings and Pinecone vector storage.

Handles:
- Generating embeddings for node content
- Storing/updating vectors in Pinecone
- Semantic search across canvas content
"""
import asyncio
import httpx
from typing import Optional, List, Dict, Any
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.node import Node
from app.models.canvas import Canvas
from app.models.settings import CanvasIndex
from app.services.settings_service import SettingsService


class EmbeddingError(Exception):
    """Raised when embedding generation fails."""
    pass


class VectorStoreError(Exception):
    """Raised when vector store operations fail."""
    pass


class HuggingFaceEmbeddings:
    """Generate embeddings using HuggingFace Inference API."""

    INFERENCE_API_URL = "https://api-inference.huggingface.co/pipeline/feature-extraction/{model}"

    def __init__(self, api_key: str, model: str = "BAAI/bge-large-en-v1.5"):
        self.api_key = api_key
        self.model = model
        self.url = self.INFERENCE_API_URL.format(model=model)
        self.dimension = self._get_dimension(model)

    @staticmethod
    def _get_dimension(model: str) -> int:
        """Get embedding dimension for known models."""
        dimensions = {
            "BAAI/bge-large-en-v1.5": 1024,
            "BAAI/bge-base-en-v1.5": 768,
            "BAAI/bge-small-en-v1.5": 384,
            "sentence-transformers/all-mpnet-base-v2": 768,
            "sentence-transformers/all-MiniLM-L6-v2": 384,
        }
        return dimensions.get(model, 1024)

    async def embed(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for a list of texts.

        Args:
            texts: List of strings to embed

        Returns:
            List of embedding vectors
        """
        if not texts:
            return []

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                self.url,
                headers=headers,
                json={"inputs": texts, "options": {"wait_for_model": True}},
            )

            if response.status_code != 200:
                raise EmbeddingError(
                    f"HuggingFace API error: {response.status_code} - {response.text}"
                )

            embeddings = response.json()

            # Handle nested response format
            if isinstance(embeddings[0], list) and isinstance(embeddings[0][0], list):
                # Mean pooling for token-level embeddings
                embeddings = [
                    [sum(x) / len(x) for x in zip(*emb)]
                    for emb in embeddings
                ]

            return embeddings

    async def embed_single(self, text: str) -> List[float]:
        """Generate embedding for a single text."""
        embeddings = await self.embed([text])
        return embeddings[0] if embeddings else []


class PineconeClient:
    """Client for Pinecone vector database operations."""

    def __init__(
        self,
        api_key: str,
        environment: str,
        index_name: str,
    ):
        self.api_key = api_key
        self.environment = environment
        self.index_name = index_name
        self.host = None  # Will be set after getting index info

    async def _get_index_host(self) -> str:
        """Get the index host URL from Pinecone."""
        if self.host:
            return self.host

        # List indexes to get host
        url = "https://api.pinecone.io/indexes"
        headers = {"Api-Key": self.api_key}

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)

            if response.status_code != 200:
                raise VectorStoreError(
                    f"Failed to list Pinecone indexes: {response.status_code} - {response.text}"
                )

            data = response.json()
            for index in data.get("indexes", []):
                if index["name"] == self.index_name:
                    self.host = index["host"]
                    return self.host

            raise VectorStoreError(f"Index '{self.index_name}' not found in Pinecone")

    async def upsert(
        self,
        vectors: List[Dict[str, Any]],
        namespace: str,
    ) -> Dict[str, Any]:
        """
        Upsert vectors to Pinecone.

        Args:
            vectors: List of {"id": str, "values": List[float], "metadata": dict}
            namespace: Namespace to upsert into (e.g., "org_123" or "user_456")
        """
        host = await self._get_index_host()
        url = f"https://{host}/vectors/upsert"

        headers = {
            "Api-Key": self.api_key,
            "Content-Type": "application/json",
        }

        payload = {
            "vectors": vectors,
            "namespace": namespace,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, headers=headers, json=payload)

            if response.status_code != 200:
                raise VectorStoreError(
                    f"Pinecone upsert failed: {response.status_code} - {response.text}"
                )

            return response.json()

    async def delete(
        self,
        ids: Optional[List[str]] = None,
        namespace: str = "",
        filter: Optional[Dict[str, Any]] = None,
        delete_all: bool = False,
    ) -> Dict[str, Any]:
        """
        Delete vectors from Pinecone.

        Args:
            ids: Specific vector IDs to delete
            namespace: Namespace to delete from
            filter: Metadata filter for deletion
            delete_all: Delete all vectors in namespace
        """
        host = await self._get_index_host()
        url = f"https://{host}/vectors/delete"

        headers = {
            "Api-Key": self.api_key,
            "Content-Type": "application/json",
        }

        payload = {"namespace": namespace}
        if ids:
            payload["ids"] = ids
        if filter:
            payload["filter"] = filter
        if delete_all:
            payload["deleteAll"] = True

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=payload)

            if response.status_code != 200:
                raise VectorStoreError(
                    f"Pinecone delete failed: {response.status_code} - {response.text}"
                )

            return response.json()

    async def query(
        self,
        vector: List[float],
        namespace: str,
        top_k: int = 10,
        filter: Optional[Dict[str, Any]] = None,
        include_metadata: bool = True,
    ) -> Dict[str, Any]:
        """
        Query Pinecone for similar vectors.

        Args:
            vector: Query vector
            namespace: Namespace to search in
            top_k: Number of results to return
            filter: Metadata filter
            include_metadata: Include metadata in results
        """
        host = await self._get_index_host()
        url = f"https://{host}/query"

        headers = {
            "Api-Key": self.api_key,
            "Content-Type": "application/json",
        }

        payload = {
            "vector": vector,
            "namespace": namespace,
            "topK": top_k,
            "includeMetadata": include_metadata,
        }
        if filter:
            payload["filter"] = filter

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=payload)

            if response.status_code != 200:
                raise VectorStoreError(
                    f"Pinecone query failed: {response.status_code} - {response.text}"
                )

            return response.json()


class CanvasIndexingService:
    """Service for indexing canvas content into vector storage."""

    @staticmethod
    async def get_clients(
        session: AsyncSession,
        user_id: int
    ) -> tuple[HuggingFaceEmbeddings, PineconeClient]:
        """Get configured embedding and vector store clients for a user."""
        settings = await SettingsService.get_effective_settings(session, user_id)

        if not settings.get("huggingface_api_key"):
            raise EmbeddingError("HuggingFace API key not configured")

        if not settings.get("pinecone_api_key"):
            raise VectorStoreError("Pinecone API key not configured")

        embeddings = HuggingFaceEmbeddings(
            api_key=settings["huggingface_api_key"],
            model=settings.get("preferred_embedding_model", "BAAI/bge-large-en-v1.5"),
        )

        pinecone = PineconeClient(
            api_key=settings["pinecone_api_key"],
            environment=settings.get("pinecone_environment", ""),
            index_name=settings.get("pinecone_index_name", "merlin-canvas"),
        )

        return embeddings, pinecone

    @staticmethod
    def _prepare_node_text(node: Node) -> str:
        """Prepare node content for embedding."""
        parts = [f"[{node.node_type.upper()}] {node.name}"]

        if node.content:
            # Strip HTML tags if present (simple approach)
            content = node.content
            if "<" in content and ">" in content:
                import re
                content = re.sub(r'<[^>]+>', ' ', content)
            parts.append(content[:2000])  # Limit content length

        return "\n".join(parts)

    @staticmethod
    async def index_canvas(
        session: AsyncSession,
        canvas_id: int,
        user_id: int,
    ) -> Dict[str, Any]:
        """
        Index all nodes in a canvas.

        Args:
            session: Database session
            canvas_id: Canvas to index
            user_id: User performing the indexing (for settings resolution)

        Returns:
            Indexing result with count and status
        """
        # Get canvas and nodes
        result = await session.execute(
            select(Canvas).where(Canvas.id == canvas_id)
        )
        canvas = result.scalar_one_or_none()

        if not canvas:
            raise ValueError(f"Canvas {canvas_id} not found")

        result = await session.execute(
            select(Node).where(Node.canvas_id == canvas_id)
        )
        nodes = list(result.scalars().all())

        if not nodes:
            return {"status": "success", "indexed": 0, "message": "No nodes to index"}

        # Get clients
        embeddings_client, pinecone_client = await CanvasIndexingService.get_clients(
            session, user_id
        )

        # Get namespace
        namespace = await SettingsService.get_pinecone_namespace(session, user_id)

        # Prepare texts for embedding
        node_texts = [CanvasIndexingService._prepare_node_text(node) for node in nodes]

        # Generate embeddings
        embeddings = await embeddings_client.embed(node_texts)

        # Prepare vectors for Pinecone
        vectors = []
        for node, embedding in zip(nodes, embeddings):
            vectors.append({
                "id": f"node_{node.id}",
                "values": embedding,
                "metadata": {
                    "canvas_id": canvas_id,
                    "node_id": node.id,
                    "node_type": node.node_type,
                    "node_name": node.name,
                    "canvas_name": canvas.name,
                },
            })

        # Upsert to Pinecone
        await pinecone_client.upsert(vectors, namespace)

        # Update or create index status
        result = await session.execute(
            select(CanvasIndex).where(CanvasIndex.canvas_id == canvas_id)
        )
        canvas_index = result.scalar_one_or_none()

        if canvas_index:
            canvas_index.is_indexed = True
            canvas_index.last_indexed_at = datetime.utcnow()
            canvas_index.index_version += 1
            canvas_index.node_count = len(nodes)
            canvas_index.last_error = None
        else:
            canvas_index = CanvasIndex(
                canvas_id=canvas_id,
                pinecone_namespace=namespace,
                is_indexed=True,
                last_indexed_at=datetime.utcnow(),
                node_count=len(nodes),
            )
            session.add(canvas_index)

        await session.commit()

        return {
            "status": "success",
            "indexed": len(nodes),
            "namespace": namespace,
            "canvas_id": canvas_id,
        }

    @staticmethod
    async def index_node(
        session: AsyncSession,
        node_id: int,
        user_id: int,
    ) -> Dict[str, Any]:
        """Index a single node (for incremental updates)."""
        result = await session.execute(
            select(Node).where(Node.id == node_id)
        )
        node = result.scalar_one_or_none()

        if not node:
            raise ValueError(f"Node {node_id} not found")

        # Get canvas for metadata
        result = await session.execute(
            select(Canvas).where(Canvas.id == node.canvas_id)
        )
        canvas = result.scalar_one_or_none()

        # Get clients
        embeddings_client, pinecone_client = await CanvasIndexingService.get_clients(
            session, user_id
        )

        # Get namespace
        namespace = await SettingsService.get_pinecone_namespace(session, user_id)

        # Generate embedding
        text = CanvasIndexingService._prepare_node_text(node)
        embedding = await embeddings_client.embed_single(text)

        # Upsert to Pinecone
        vector = {
            "id": f"node_{node.id}",
            "values": embedding,
            "metadata": {
                "canvas_id": node.canvas_id,
                "node_id": node.id,
                "node_type": node.node_type,
                "node_name": node.name,
                "canvas_name": canvas.name if canvas else "",
            },
        }

        await pinecone_client.upsert([vector], namespace)

        return {
            "status": "success",
            "node_id": node_id,
            "namespace": namespace,
        }

    @staticmethod
    async def delete_node_from_index(
        session: AsyncSession,
        node_id: int,
        user_id: int,
    ) -> Dict[str, Any]:
        """Remove a node from the index."""
        _, pinecone_client = await CanvasIndexingService.get_clients(session, user_id)
        namespace = await SettingsService.get_pinecone_namespace(session, user_id)

        await pinecone_client.delete(ids=[f"node_{node_id}"], namespace=namespace)

        return {"status": "success", "deleted": f"node_{node_id}"}

    @staticmethod
    async def delete_canvas_from_index(
        session: AsyncSession,
        canvas_id: int,
        user_id: int,
    ) -> Dict[str, Any]:
        """Remove all nodes of a canvas from the index."""
        _, pinecone_client = await CanvasIndexingService.get_clients(session, user_id)
        namespace = await SettingsService.get_pinecone_namespace(session, user_id)

        # Delete by filter
        await pinecone_client.delete(
            namespace=namespace,
            filter={"canvas_id": {"$eq": canvas_id}},
        )

        # Update index status
        result = await session.execute(
            select(CanvasIndex).where(CanvasIndex.canvas_id == canvas_id)
        )
        canvas_index = result.scalar_one_or_none()

        if canvas_index:
            canvas_index.is_indexed = False
            canvas_index.node_count = 0
            await session.commit()

        return {"status": "success", "canvas_id": canvas_id}

    @staticmethod
    async def search_canvas(
        session: AsyncSession,
        query: str,
        user_id: int,
        canvas_id: Optional[int] = None,
        node_types: Optional[List[str]] = None,
        top_k: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Semantic search across canvas content.

        Args:
            session: Database session
            query: Search query text
            user_id: User performing the search
            canvas_id: Optional - limit search to specific canvas
            node_types: Optional - filter by node types
            top_k: Number of results to return

        Returns:
            List of matching nodes with scores
        """
        embeddings_client, pinecone_client = await CanvasIndexingService.get_clients(
            session, user_id
        )
        namespace = await SettingsService.get_pinecone_namespace(session, user_id)

        # Generate query embedding
        query_embedding = await embeddings_client.embed_single(query)

        # Build filter
        filter_dict = {}
        if canvas_id:
            filter_dict["canvas_id"] = {"$eq": canvas_id}
        if node_types:
            filter_dict["node_type"] = {"$in": node_types}

        # Query Pinecone
        results = await pinecone_client.query(
            vector=query_embedding,
            namespace=namespace,
            top_k=top_k,
            filter=filter_dict if filter_dict else None,
        )

        # Format results
        matches = []
        for match in results.get("matches", []):
            matches.append({
                "node_id": match["metadata"].get("node_id"),
                "canvas_id": match["metadata"].get("canvas_id"),
                "node_type": match["metadata"].get("node_type"),
                "node_name": match["metadata"].get("node_name"),
                "canvas_name": match["metadata"].get("canvas_name"),
                "score": match["score"],
            })

        return matches


# Singleton instance
indexing_service = CanvasIndexingService()
