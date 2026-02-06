"""
Weaviate client for VOS notes semantic search.

This module provides a client for interacting with Weaviate to enable
semantic search on notes stored in PostgreSQL.
"""

import os
import sys
import logging
import importlib.util
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

import weaviate
from weaviate.classes.config import Configure, Property, DataType
from weaviate.classes.query import Filter, MetadataQuery

logger = logging.getLogger(__name__)

def _get_embedding_service():
    """Get the embedding service, loading it dynamically if needed."""
    # Try to get from already loaded module
    if "memory.embedding_service" in sys.modules:
        return sys.modules["memory.embedding_service"].get_embedding_service()

    # Try direct import first (when running as part of agent)
    try:
        from memory.embedding_service import get_embedding_service
        return get_embedding_service()
    except ImportError:
        pass

    # Load dynamically for api_gateway context
    tools_path = Path(__file__).parent.parent
    spec = importlib.util.spec_from_file_location(
        "embedding_service",
        tools_path / "memory" / "embedding_service.py"
    )
    embedding_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(embedding_module)
    return embedding_module.get_embedding_service()


class NotesWeaviateClient:
    """
    Client for managing notes semantic search in Weaviate.

    Handles schema creation, note indexing, and semantic search operations.
    """

    # Weaviate collection name
    NOTES_COLLECTION = "Note"

    def __init__(self, weaviate_url: Optional[str] = None):
        """
        Initialize Weaviate client for notes.

        Args:
            weaviate_url: Weaviate connection URL (defaults to env var WEAVIATE_URL)
        """
        self.weaviate_url = weaviate_url or os.getenv(
            "WEAVIATE_URL",
            "http://weaviate:8080"
        )
        self.client = None
        self.embedding_service = None

    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()

    def connect(self) -> None:
        """Establish connection to Weaviate."""
        try:
            # Parse host and port from URL
            url_parts = self.weaviate_url.replace("http://", "").replace("https://", "")
            if ":" in url_parts:
                host, port = url_parts.split(":")
                port = int(port)
            else:
                host = url_parts
                port = 8080

            # Connect to Weaviate
            self.client = weaviate.connect_to_custom(
                http_host=host,
                http_port=port,
                http_secure=False,
                grpc_host=host,
                grpc_port=50051,
                grpc_secure=False
            )

            # Initialize embedding service
            self.embedding_service = _get_embedding_service()

            # Initialize schema if needed
            self._ensure_schema()

            logger.info(f"Notes Weaviate client connected to {self.weaviate_url}")

        except Exception as e:
            logger.error(f"Failed to connect to Weaviate: {e}")
            raise

    def close(self) -> None:
        """Close Weaviate connection."""
        if self.client:
            self.client.close()
            logger.debug("Closed Notes Weaviate connection")

    def _ensure_schema(self) -> None:
        """
        Ensure the Note collection exists with proper schema.

        Schema includes:
        - note_id: PostgreSQL note ID (for reference)
        - title: Note title (vectorized)
        - content: Note content (vectorized)
        - tags: Note tags (for filtering)
        - folder: Note folder (for filtering)
        - created_at: Creation timestamp
        - updated_at: Last update timestamp
        """
        try:
            # Check if collection exists
            if self.client.collections.exists(self.NOTES_COLLECTION):
                logger.debug(f"Collection '{self.NOTES_COLLECTION}' already exists")
                return

            # Create Note collection with 768-dimensional vectors
            self.client.collections.create(
                name=self.NOTES_COLLECTION,
                description="VOS notes for semantic search",
                vectorizer_config=Configure.Vectorizer.none(),  # We provide our own vectors
                properties=[
                    # Reference to PostgreSQL
                    Property(
                        name="note_id",
                        data_type=DataType.INT,
                        description="PostgreSQL note ID",
                        skip_vectorization=True
                    ),
                    # Content fields (used for vectorization)
                    Property(
                        name="title",
                        data_type=DataType.TEXT,
                        description="Note title"
                    ),
                    Property(
                        name="content",
                        data_type=DataType.TEXT,
                        description="Note content"
                    ),
                    # Filtering fields
                    Property(
                        name="tags",
                        data_type=DataType.TEXT_ARRAY,
                        description="Note tags for filtering",
                        skip_vectorization=True
                    ),
                    Property(
                        name="folder",
                        data_type=DataType.TEXT,
                        description="Note folder for filtering",
                        skip_vectorization=True
                    ),
                    # Metadata
                    Property(
                        name="created_at",
                        data_type=DataType.DATE,
                        description="Creation timestamp",
                        skip_vectorization=True
                    ),
                    Property(
                        name="updated_at",
                        data_type=DataType.DATE,
                        description="Last update timestamp",
                        skip_vectorization=True
                    ),
                ]
            )

            logger.info(f"Created collection '{self.NOTES_COLLECTION}' with 768-dim vector support")

        except Exception as e:
            logger.error(f"Failed to ensure schema: {e}")
            raise

    def _generate_embedding(self, title: str, content: str) -> List[float]:
        """
        Generate embedding for a note by combining title and content.

        Args:
            title: Note title
            content: Note content

        Returns:
            768-dimensional embedding vector
        """
        # Combine title and content for embedding
        # Title is weighted by appearing first
        combined_text = f"{title}\n\n{content}" if content else title
        return self.embedding_service.embed_memory(combined_text)

    def index_note(
        self,
        note_id: int,
        title: str,
        content: Optional[str] = None,
        tags: Optional[List[str]] = None,
        folder: Optional[str] = None,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None
    ) -> str:
        """
        Index a note in Weaviate for semantic search.

        Args:
            note_id: PostgreSQL note ID
            title: Note title
            content: Note content
            tags: Note tags
            folder: Note folder
            created_at: Creation timestamp
            updated_at: Last update timestamp

        Returns:
            UUID of the indexed note in Weaviate
        """
        try:
            collection = self.client.collections.get(self.NOTES_COLLECTION)

            # Check if note already exists
            existing = self._get_by_note_id(note_id)
            if existing:
                # Update existing entry
                return self.update_note(
                    note_id=note_id,
                    title=title,
                    content=content,
                    tags=tags,
                    folder=folder,
                    updated_at=updated_at
                )

            # Generate embedding
            vector = self._generate_embedding(title, content or "")

            # RFC3339 format with timezone
            now = datetime.now().astimezone().isoformat()

            properties = {
                "note_id": note_id,
                "title": title,
                "content": content or "",
                "tags": tags or [],
                "folder": folder or "",
                "created_at": created_at or now,
                "updated_at": updated_at or now
            }

            # Insert with vector
            uuid = collection.data.insert(
                properties=properties,
                vector=vector
            )

            logger.info(f"Indexed note {note_id} with UUID {uuid}")
            return str(uuid)

        except Exception as e:
            logger.error(f"Failed to index note {note_id}: {e}")
            raise

    def update_note(
        self,
        note_id: int,
        title: Optional[str] = None,
        content: Optional[str] = None,
        tags: Optional[List[str]] = None,
        folder: Optional[str] = None,
        updated_at: Optional[str] = None
    ) -> str:
        """
        Update a note's index in Weaviate.

        Args:
            note_id: PostgreSQL note ID
            title: New title (optional)
            content: New content (optional)
            tags: New tags (optional)
            folder: New folder (optional)
            updated_at: Update timestamp

        Returns:
            UUID of the updated note
        """
        try:
            collection = self.client.collections.get(self.NOTES_COLLECTION)

            # Find existing entry by note_id
            existing = self._get_by_note_id(note_id)
            if not existing:
                logger.warning(f"Note {note_id} not found in Weaviate for update")
                # Index it instead
                if title:
                    return self.index_note(
                        note_id=note_id,
                        title=title,
                        content=content,
                        tags=tags,
                        folder=folder,
                        updated_at=updated_at
                    )
                return ""

            weaviate_uuid = existing["uuid"]
            current_title = existing.get("title", "")
            current_content = existing.get("content", "")

            # Build update properties
            updates = {
                "updated_at": updated_at or datetime.now().astimezone().isoformat()
            }

            new_title = title if title is not None else current_title
            new_content = content if content is not None else current_content

            if title is not None:
                updates["title"] = title
            if content is not None:
                updates["content"] = content
            if tags is not None:
                updates["tags"] = tags
            if folder is not None:
                updates["folder"] = folder

            # Regenerate embedding if title or content changed
            vector = None
            if title is not None or content is not None:
                vector = self._generate_embedding(new_title, new_content)

            # Update object
            collection.data.update(
                uuid=weaviate_uuid,
                properties=updates,
                vector=vector
            )

            logger.info(f"Updated note {note_id} in Weaviate")
            return weaviate_uuid

        except Exception as e:
            logger.error(f"Failed to update note {note_id}: {e}")
            raise

    def delete_note(self, note_id: int) -> bool:
        """
        Delete a note from Weaviate.

        Args:
            note_id: PostgreSQL note ID

        Returns:
            True if deleted successfully
        """
        try:
            collection = self.client.collections.get(self.NOTES_COLLECTION)

            # Find existing entry by note_id
            existing = self._get_by_note_id(note_id)
            if not existing:
                logger.warning(f"Note {note_id} not found in Weaviate for deletion")
                return False

            collection.data.delete_by_id(existing["uuid"])

            logger.info(f"Deleted note {note_id} from Weaviate")
            return True

        except Exception as e:
            logger.error(f"Failed to delete note {note_id}: {e}")
            raise

    def _get_by_note_id(self, note_id: int) -> Optional[Dict[str, Any]]:
        """
        Get a note from Weaviate by its PostgreSQL ID.

        Args:
            note_id: PostgreSQL note ID

        Returns:
            Note data with UUID or None if not found
        """
        try:
            collection = self.client.collections.get(self.NOTES_COLLECTION)

            response = collection.query.fetch_objects(
                filters=Filter.by_property("note_id").equal(note_id),
                limit=1
            )

            if response.objects:
                obj = response.objects[0]
                return {
                    "uuid": str(obj.uuid),
                    "note_id": obj.properties.get("note_id"),
                    "title": obj.properties.get("title"),
                    "content": obj.properties.get("content"),
                    "tags": obj.properties.get("tags", []),
                    "folder": obj.properties.get("folder"),
                    "created_at": obj.properties.get("created_at"),
                    "updated_at": obj.properties.get("updated_at")
                }

            return None

        except Exception as e:
            logger.error(f"Failed to get note {note_id} from Weaviate: {e}")
            return None

    def semantic_search(
        self,
        query: str,
        tags: Optional[List[str]] = None,
        folder: Optional[str] = None,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Perform semantic search on notes.

        Args:
            query: Search query text
            tags: Filter by tags (any match)
            folder: Filter by folder
            limit: Maximum number of results

        Returns:
            List of matching notes with relevance scores
        """
        try:
            collection = self.client.collections.get(self.NOTES_COLLECTION)

            # Generate query embedding
            query_vector = self.embedding_service.embed_query(query)

            # Build filters
            filters = []

            if tags:
                filters.append(Filter.by_property("tags").contains_any(tags))

            if folder:
                filters.append(Filter.by_property("folder").equal(folder))

            # Combine filters
            combined_filter = None
            if filters:
                combined_filter = filters[0]
                for f in filters[1:]:
                    combined_filter = combined_filter & f

            # Perform vector search
            response = collection.query.near_vector(
                near_vector=query_vector,
                limit=limit,
                filters=combined_filter,
                return_metadata=MetadataQuery(distance=True, score=True)
            )

            # Format results
            results = []
            for obj in response.objects:
                result = {
                    "note_id": obj.properties.get("note_id"),
                    "title": obj.properties.get("title"),
                    "content_preview": (obj.properties.get("content", "")[:200] + "...")
                        if len(obj.properties.get("content", "")) > 200
                        else obj.properties.get("content", ""),
                    "tags": obj.properties.get("tags", []),
                    "folder": obj.properties.get("folder"),
                    "created_at": obj.properties.get("created_at"),
                    "updated_at": obj.properties.get("updated_at")
                }

                # Add relevance score
                if hasattr(obj.metadata, "score") and obj.metadata.score is not None:
                    result["relevance_score"] = obj.metadata.score
                elif hasattr(obj.metadata, "distance") and obj.metadata.distance is not None:
                    # Convert distance to similarity (lower distance = higher similarity)
                    result["relevance_score"] = 1.0 / (1.0 + obj.metadata.distance)

                results.append(result)

            logger.info(f"Semantic search found {len(results)} notes for query: {query[:50]}...")
            return results

        except Exception as e:
            logger.error(f"Failed to perform semantic search: {e}")
            raise

    def hybrid_search(
        self,
        query: str,
        tags: Optional[List[str]] = None,
        folder: Optional[str] = None,
        limit: int = 10,
        alpha: float = 0.5
    ) -> List[Dict[str, Any]]:
        """
        Perform hybrid search combining vector and BM25 keyword search.

        Args:
            query: Search query text
            tags: Filter by tags (any match)
            folder: Filter by folder
            limit: Maximum number of results
            alpha: Balance between vector (1.0) and keyword (0.0) search

        Returns:
            List of matching notes with relevance scores
        """
        try:
            collection = self.client.collections.get(self.NOTES_COLLECTION)

            # Build filters
            filters = []

            if tags:
                filters.append(Filter.by_property("tags").contains_any(tags))

            if folder:
                filters.append(Filter.by_property("folder").equal(folder))

            # Combine filters
            combined_filter = None
            if filters:
                combined_filter = filters[0]
                for f in filters[1:]:
                    combined_filter = combined_filter & f

            # Generate query embedding for vector part of hybrid search
            query_vector = self.embedding_service.embed_query(query)

            # Perform hybrid search with external vector
            response = collection.query.hybrid(
                query=query,
                vector=query_vector,
                alpha=alpha,
                limit=limit,
                filters=combined_filter,
                return_metadata=MetadataQuery(score=True)
            )

            # Format results
            results = []
            for obj in response.objects:
                result = {
                    "note_id": obj.properties.get("note_id"),
                    "title": obj.properties.get("title"),
                    "content_preview": (obj.properties.get("content", "")[:200] + "...")
                        if len(obj.properties.get("content", "")) > 200
                        else obj.properties.get("content", ""),
                    "tags": obj.properties.get("tags", []),
                    "folder": obj.properties.get("folder"),
                    "created_at": obj.properties.get("created_at"),
                    "updated_at": obj.properties.get("updated_at")
                }

                # Add relevance score
                if hasattr(obj.metadata, "score") and obj.metadata.score is not None:
                    result["relevance_score"] = obj.metadata.score

                results.append(result)

            logger.info(f"Hybrid search found {len(results)} notes for query: {query[:50]}...")
            return results

        except Exception as e:
            logger.error(f"Failed to perform hybrid search: {e}")
            raise

    def bulk_index(self, notes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Bulk index multiple notes for initial migration.

        Args:
            notes: List of note dictionaries with keys:
                - id: PostgreSQL note ID
                - title: Note title
                - content: Note content
                - tags: Note tags (optional)
                - folder: Note folder (optional)
                - created_at: Creation timestamp (optional)
                - updated_at: Update timestamp (optional)

        Returns:
            Summary of indexing results
        """
        try:
            collection = self.client.collections.get(self.NOTES_COLLECTION)

            # Generate embeddings in batch
            texts = [
                f"{note['title']}\n\n{note.get('content', '')}"
                for note in notes
            ]
            vectors = self.embedding_service.embed_memories_batch(texts)

            now = datetime.now().astimezone().isoformat()

            success_count = 0
            error_count = 0
            errors = []

            # Insert notes with their vectors
            for i, note in enumerate(notes):
                try:
                    properties = {
                        "note_id": note["id"],
                        "title": note["title"],
                        "content": note.get("content", ""),
                        "tags": note.get("tags", []),
                        "folder": note.get("folder", ""),
                        "created_at": note.get("created_at", now),
                        "updated_at": note.get("updated_at", now)
                    }

                    collection.data.insert(
                        properties=properties,
                        vector=vectors[i]
                    )
                    success_count += 1

                except Exception as e:
                    error_count += 1
                    errors.append({
                        "note_id": note.get("id"),
                        "error": str(e)
                    })
                    logger.warning(f"Failed to index note {note.get('id')}: {e}")

            logger.info(f"Bulk indexed {success_count} notes, {error_count} errors")

            return {
                "success_count": success_count,
                "error_count": error_count,
                "errors": errors[:10]  # Return first 10 errors
            }

        except Exception as e:
            logger.error(f"Failed to bulk index notes: {e}")
            raise

    def get_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the notes collection.

        Returns:
            Collection statistics
        """
        try:
            collection = self.client.collections.get(self.NOTES_COLLECTION)

            # Get object count
            response = collection.aggregate.over_all(total_count=True)

            return {
                "collection": self.NOTES_COLLECTION,
                "total_notes": response.total_count if response else 0
            }

        except Exception as e:
            logger.error(f"Failed to get stats: {e}")
            return {
                "collection": self.NOTES_COLLECTION,
                "total_notes": 0,
                "error": str(e)
            }


# Global singleton instance
_notes_weaviate_client = None


def get_notes_weaviate_client() -> NotesWeaviateClient:
    """
    Get the global notes Weaviate client instance.

    Returns:
        NotesWeaviateClient singleton
    """
    global _notes_weaviate_client
    if _notes_weaviate_client is None:
        _notes_weaviate_client = NotesWeaviateClient()
        _notes_weaviate_client.connect()
    return _notes_weaviate_client
