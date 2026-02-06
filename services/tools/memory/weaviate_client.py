"""
Weaviate client for VOS memory management.

This module provides a client for interacting with Weaviate to store and retrieve
agent memories, user preferences, and conversation history.
"""

import os
import logging
from typing import Dict, Any, List, Optional
from enum import Enum
from datetime import datetime

import weaviate
from weaviate.classes.config import Configure, Property, DataType
from weaviate.classes.query import Filter, Sort

logger = logging.getLogger(__name__)


class MemoryType(str, Enum):
    """Types of memories that can be stored."""
    USER_PREFERENCE = "user_preference"
    USER_FACT = "user_fact"
    CONVERSATION_CONTEXT = "conversation_context"
    AGENT_PROCEDURE = "agent_procedure"
    KNOWLEDGE = "knowledge"
    EVENT_PATTERN = "event_pattern"
    ERROR_HANDLING = "error_handling"
    PROACTIVE_ACTION = "proactive_action"


class MemoryScope(str, Enum):
    """Scope of memory access."""
    INDIVIDUAL = "individual"  # Agent's private memory
    SHARED = "shared"          # Accessible to all agents


class MemorySource(str, Enum):
    """How the memory was created."""
    USER_EXPLICIT = "user_explicit"      # User directly told us
    INFERRED = "inferred"                # Inferred from behavior
    PROACTIVE_AGENT = "proactive_agent"  # Background memory agent
    AGENT_LEARNING = "agent_learning"    # Agent learned from experience


class WeaviateClient:
    """
    Client for managing VOS memories in Weaviate.

    Handles schema creation, memory storage, and retrieval operations.
    """

    # Weaviate collection name
    MEMORY_COLLECTION = "Memory"

    def __init__(self, weaviate_url: Optional[str] = None):
        """
        Initialize Weaviate client.

        Args:
            weaviate_url: Weaviate connection URL (defaults to env var WEAVIATE_URL)
        """
        self.weaviate_url = weaviate_url or os.getenv(
            "WEAVIATE_URL",
            "http://weaviate:8080"
        )
        self.client = None

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

            # Connect to Weaviate (anonymous access enabled in docker-compose)
            self.client = weaviate.connect_to_custom(
                http_host=host,
                http_port=port,
                http_secure=False,
                grpc_host=host,
                grpc_port=50051,
                grpc_secure=False
            )

            # Initialize schema if needed
            self._ensure_schema()

            logger.info(f"Connected to Weaviate at {self.weaviate_url}")

        except Exception as e:
            logger.error(f"Failed to connect to Weaviate: {e}")
            raise

    def close(self) -> None:
        """Close Weaviate connection."""
        if self.client:
            self.client.close()
            logger.debug("Closed Weaviate connection")

    def _ensure_schema(self) -> None:
        """
        Ensure the Memory collection exists with proper schema.

        Schema includes all fields from our design:
        - Core: content, memory_type, scope
        - Context: agent_id, session_id, related fields, tags
        - Metadata: importance, confidence, source
        - Temporal: created_at, updated_at, expires_at, access tracking
        - Learning: success_count, failure_count
        """
        try:
            # Check if collection exists
            if self.client.collections.exists(self.MEMORY_COLLECTION):
                logger.debug(f"Collection '{self.MEMORY_COLLECTION}' already exists")
                return

            # Create Memory collection with 768-dimensional vectors (nomic embeddings)
            self.client.collections.create(
                name=self.MEMORY_COLLECTION,
                description="VOS agent memories, user preferences, and conversation history",
                vectorizer_config=Configure.Vectorizer.none(),  # We provide our own vectors
                properties=[
                    # Core fields
                    Property(
                        name="content",
                        data_type=DataType.TEXT,
                        description="The actual memory content"
                    ),
                    Property(
                        name="memory_type",
                        data_type=DataType.TEXT,
                        description="Type of memory",
                        skip_vectorization=True
                    ),
                    Property(
                        name="scope",
                        data_type=DataType.TEXT,
                        description="Individual or shared memory",
                        skip_vectorization=True
                    ),

                    # Scope & Access
                    Property(
                        name="agent_id",
                        data_type=DataType.TEXT,
                        description="Agent that created/owns this memory",
                        skip_vectorization=True
                    ),
                    Property(
                        name="session_id",
                        data_type=DataType.TEXT,
                        description="Session where memory was formed",
                        skip_vectorization=True
                    ),

                    # Context & Relationships
                    Property(
                        name="related_event_types",
                        data_type=DataType.TEXT_ARRAY,
                        description="Event types this memory relates to",
                        skip_vectorization=True
                    ),
                    Property(
                        name="related_tools",
                        data_type=DataType.TEXT_ARRAY,
                        description="Tools this memory involves",
                        skip_vectorization=True
                    ),
                    Property(
                        name="related_memory_ids",
                        data_type=DataType.TEXT_ARRAY,
                        description="Related memory UUIDs for knowledge graphs",
                        skip_vectorization=True
                    ),
                    Property(
                        name="tags",
                        data_type=DataType.TEXT_ARRAY,
                        description="Searchable categorization tags",
                        skip_vectorization=True
                    ),

                    # Metadata
                    Property(
                        name="importance",
                        data_type=DataType.NUMBER,
                        description="Importance score (0.0-1.0)",
                        skip_vectorization=True
                    ),
                    Property(
                        name="confidence",
                        data_type=DataType.NUMBER,
                        description="Confidence score (0.0-1.0)",
                        skip_vectorization=True
                    ),
                    Property(
                        name="source",
                        data_type=DataType.TEXT,
                        description="How memory was created",
                        skip_vectorization=True
                    ),

                    # Temporal & Usage
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
                    Property(
                        name="expires_at",
                        data_type=DataType.DATE,
                        description="Expiration timestamp for temporary memories",
                        skip_vectorization=True
                    ),
                    Property(
                        name="access_count",
                        data_type=DataType.INT,
                        description="Number of times accessed",
                        skip_vectorization=True
                    ),
                    Property(
                        name="last_accessed_at",
                        data_type=DataType.DATE,
                        description="Last access timestamp",
                        skip_vectorization=True
                    ),

                    # Learning metrics
                    Property(
                        name="success_count",
                        data_type=DataType.INT,
                        description="Success count for procedures",
                        skip_vectorization=True
                    ),
                    Property(
                        name="failure_count",
                        data_type=DataType.INT,
                        description="Failure count for procedures",
                        skip_vectorization=True
                    ),
                ]
            )

            logger.info(f"Created collection '{self.MEMORY_COLLECTION}' with 768-dim vector support")

        except Exception as e:
            logger.error(f"Failed to ensure schema: {e}")
            raise

    def create_memory(
        self,
        content: str,
        memory_type: MemoryType,
        scope: MemoryScope,
        vector: List[float],
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        related_event_types: Optional[List[str]] = None,
        related_tools: Optional[List[str]] = None,
        related_memory_ids: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        importance: float = 0.5,
        confidence: float = 1.0,
        source: MemorySource = MemorySource.AGENT_LEARNING,
        expires_at: Optional[str] = None,
        success_count: int = 0,
        failure_count: int = 0
    ) -> str:
        """
        Create a new memory in Weaviate.

        Args:
            content: The memory content
            memory_type: Type of memory
            scope: Individual or shared
            vector: 768-dimensional embedding vector
            agent_id: Agent that created this memory
            session_id: Session ID
            related_event_types: Event types this memory relates to
            related_tools: Tools involved
            related_memory_ids: Related memory UUIDs
            tags: Searchable tags
            importance: Importance score (0.0-1.0)
            confidence: Confidence score (0.0-1.0)
            source: How memory was created
            expires_at: Optional expiration timestamp
            success_count: Success count for procedures
            failure_count: Failure count for procedures

        Returns:
            UUID of the created memory
        """
        try:
            collection = self.client.collections.get(self.MEMORY_COLLECTION)

            # RFC3339 format with timezone
            now = datetime.now().astimezone().isoformat()

            properties = {
                "content": content,
                "memory_type": memory_type.value,
                "scope": scope.value,
                "agent_id": agent_id,
                "session_id": session_id,
                "related_event_types": related_event_types or [],
                "related_tools": related_tools or [],
                "related_memory_ids": related_memory_ids or [],
                "tags": tags or [],
                "importance": max(0.0, min(1.0, importance)),
                "confidence": max(0.0, min(1.0, confidence)),
                "source": source.value,
                "created_at": now,
                "updated_at": now,
                "expires_at": expires_at,
                "access_count": 0,
                "last_accessed_at": now,
                "success_count": success_count,
                "failure_count": failure_count
            }

            # Insert with vector
            uuid = collection.data.insert(
                properties=properties,
                vector=vector
            )

            logger.info(f"Created {scope.value} memory {uuid} of type {memory_type.value}")
            return str(uuid)

        except Exception as e:
            logger.error(f"Failed to create memory: {e}")
            raise

    def search_memories(
        self,
        query_vector: Optional[List[float]] = None,
        memory_type: Optional[MemoryType] = None,
        scope: Optional[MemoryScope] = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        min_importance: Optional[float] = None,
        min_confidence: Optional[float] = None,
        limit: int = 10,
        sort_by_created: bool = False,
        sort_by_accessed: bool = False,
        sort_ascending: bool = False,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        updated_after: Optional[str] = None,
        updated_before: Optional[str] = None,
        include_vectors: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Search for memories using vector similarity and filters.

        Args:
            query_vector: 768-dim embedding for semantic search
            memory_type: Filter by memory type
            scope: Filter by scope (individual/shared)
            agent_id: Filter by agent ID
            session_id: Filter by session ID
            tags: Filter by tags (any match)
            min_importance: Minimum importance score
            min_confidence: Minimum confidence score
            limit: Maximum number of results
            sort_by_created: Sort by created_at timestamp
            sort_by_accessed: Sort by last_accessed_at timestamp
            sort_ascending: If True, sort oldest first; if False (default), sort newest first
            created_after: Filter memories created after this ISO timestamp
            created_before: Filter memories created before this ISO timestamp
            updated_after: Filter memories updated after this ISO timestamp
            updated_before: Filter memories updated before this ISO timestamp
            include_vectors: If True, include embedding vectors in response

        Returns:
            List of matching memories with metadata and scores
        """
        try:
            collection = self.client.collections.get(self.MEMORY_COLLECTION)

            # Build filters
            filters = []

            if memory_type:
                filters.append(Filter.by_property("memory_type").equal(memory_type.value))

            if scope:
                filters.append(Filter.by_property("scope").equal(scope.value))

            if agent_id:
                filters.append(Filter.by_property("agent_id").equal(agent_id))

            if session_id:
                filters.append(Filter.by_property("session_id").equal(session_id))

            if min_importance is not None:
                filters.append(Filter.by_property("importance").greater_or_equal(min_importance))

            if min_confidence is not None:
                filters.append(Filter.by_property("confidence").greater_or_equal(min_confidence))

            if tags:
                filters.append(Filter.by_property("tags").contains_any(tags))

            # Time-based filters
            if created_after:
                filters.append(Filter.by_property("created_at").greater_than(created_after))

            if created_before:
                filters.append(Filter.by_property("created_at").less_than(created_before))

            if updated_after:
                filters.append(Filter.by_property("updated_at").greater_than(updated_after))

            if updated_before:
                filters.append(Filter.by_property("updated_at").less_than(updated_before))

            # Combine filters
            combined_filter = None
            if filters:
                combined_filter = filters[0]
                for f in filters[1:]:
                    combined_filter = combined_filter & f

            # Perform search
            if query_vector:
                # Vector search with filters
                response = collection.query.near_vector(
                    near_vector=query_vector,
                    limit=limit,
                    filters=combined_filter,
                    return_metadata=["distance", "score"],
                    include_vector=include_vectors
                )
            else:
                # Just filter (no semantic search)
                if sort_by_accessed:
                    # Sort by last_accessed_at
                    response = collection.query.fetch_objects(
                        limit=limit,
                        filters=combined_filter,
                        sort=Sort.by_property("last_accessed_at", ascending=sort_ascending),
                        include_vector=include_vectors
                    )
                elif sort_by_created:
                    # Sort by created_at
                    response = collection.query.fetch_objects(
                        limit=limit,
                        filters=combined_filter,
                        sort=Sort.by_property("created_at", ascending=sort_ascending),
                        include_vector=include_vectors
                    )
                else:
                    response = collection.query.fetch_objects(
                        limit=limit,
                        filters=combined_filter,
                        include_vector=include_vectors
                    )

            # Format results
            results = []
            for obj in response.objects:
                memory = self._format_memory_object(obj, include_vector=include_vectors)
                results.append(memory)

            logger.info(f"Found {len(results)} memories")
            return results

        except Exception as e:
            logger.error(f"Failed to search memories: {e}")
            raise

    def get_memory(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a specific memory by ID.

        Args:
            memory_id: UUID of the memory

        Returns:
            Memory data or None if not found
        """
        try:
            collection = self.client.collections.get(self.MEMORY_COLLECTION)
            obj = collection.query.fetch_object_by_id(memory_id)

            if not obj:
                return None

            memory = self._format_memory_object(obj)

            # Increment access count
            self._increment_access_count(memory_id)

            logger.info(f"Retrieved memory {memory_id}")
            return memory

        except Exception as e:
            logger.error(f"Failed to get memory {memory_id}: {e}")
            raise

    def update_memory(
        self,
        memory_id: str,
        content: Optional[str] = None,
        vector: Optional[List[float]] = None,
        tags: Optional[List[str]] = None,
        importance: Optional[float] = None,
        confidence: Optional[float] = None,
        related_memory_ids: Optional[List[str]] = None,
        success_count: Optional[int] = None,
        failure_count: Optional[int] = None
    ) -> bool:
        """
        Update an existing memory.

        Args:
            memory_id: UUID of the memory to update
            content: New content (optional)
            vector: New embedding vector (optional)
            tags: New tags (optional, replaces existing)
            importance: New importance score (optional)
            confidence: New confidence score (optional)
            related_memory_ids: New related memory IDs (optional)
            success_count: New success count (optional)
            failure_count: New failure count (optional)

        Returns:
            True if updated successfully
        """
        try:
            collection = self.client.collections.get(self.MEMORY_COLLECTION)

            # Get existing memory
            existing = collection.query.fetch_object_by_id(memory_id)
            if not existing:
                logger.warning(f"Memory {memory_id} not found")
                return False

            # Build update properties
            updates = {
                "updated_at": datetime.now().astimezone().isoformat()
            }

            if content is not None:
                updates["content"] = content

            if tags is not None:
                updates["tags"] = tags

            if importance is not None:
                updates["importance"] = max(0.0, min(1.0, importance))

            if confidence is not None:
                updates["confidence"] = max(0.0, min(1.0, confidence))

            if related_memory_ids is not None:
                updates["related_memory_ids"] = related_memory_ids

            if success_count is not None:
                updates["success_count"] = success_count

            if failure_count is not None:
                updates["failure_count"] = failure_count

            # Update object
            collection.data.update(
                uuid=memory_id,
                properties=updates,
                vector=vector
            )

            logger.info(f"Updated memory {memory_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to update memory {memory_id}: {e}")
            raise

    def delete_memory(self, memory_id: str) -> bool:
        """
        Delete a memory.

        Args:
            memory_id: UUID of the memory to delete

        Returns:
            True if deleted successfully
        """
        try:
            collection = self.client.collections.get(self.MEMORY_COLLECTION)
            collection.data.delete_by_id(memory_id)

            logger.info(f"Deleted memory {memory_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to delete memory {memory_id}: {e}")
            raise

    def _format_memory_object(self, obj, include_vector: bool = False) -> Dict[str, Any]:
        """Format a Weaviate object into a memory dictionary."""
        memory = {
            "id": str(obj.uuid),
            "content": obj.properties.get("content"),
            "memory_type": obj.properties.get("memory_type"),
            "scope": obj.properties.get("scope"),
            "agent_id": obj.properties.get("agent_id"),
            "session_id": obj.properties.get("session_id"),
            "related_event_types": obj.properties.get("related_event_types", []),
            "related_tools": obj.properties.get("related_tools", []),
            "related_memory_ids": obj.properties.get("related_memory_ids", []),
            "tags": obj.properties.get("tags", []),
            "importance": obj.properties.get("importance"),
            "confidence": obj.properties.get("confidence"),
            "source": obj.properties.get("source"),
            "created_at": obj.properties.get("created_at"),
            "updated_at": obj.properties.get("updated_at"),
            "expires_at": obj.properties.get("expires_at"),
            "access_count": obj.properties.get("access_count", 0),
            "last_accessed_at": obj.properties.get("last_accessed_at"),
            "success_count": obj.properties.get("success_count", 0),
            "failure_count": obj.properties.get("failure_count", 0)
        }

        # Add search score if available
        if hasattr(obj.metadata, "score") and obj.metadata.score is not None:
            memory["search_score"] = obj.metadata.score
        elif hasattr(obj.metadata, "distance") and obj.metadata.distance is not None:
            memory["search_distance"] = obj.metadata.distance

        # Add vector if requested
        if include_vector and hasattr(obj, "vector") and obj.vector is not None:
            if isinstance(obj.vector, dict) and "default" in obj.vector:
                memory["embedding"] = obj.vector["default"]
            else:
                memory["embedding"] = obj.vector

        return memory

    def _increment_access_count(self, memory_id: str) -> None:
        """Increment access count for a memory (non-critical operation)."""
        try:
            collection = self.client.collections.get(self.MEMORY_COLLECTION)
            obj = collection.query.fetch_object_by_id(memory_id)

            if obj:
                access_count = obj.properties.get("access_count", 0) + 1
                collection.data.update(
                    uuid=memory_id,
                    properties={
                        "access_count": access_count,
                        "last_accessed_at": datetime.now().astimezone().isoformat()
                    }
                )

        except Exception as e:
            logger.warning(f"Failed to increment access count for {memory_id}: {e}")
            # Don't raise - this is non-critical

    def mark_memories_provided(self, memory_ids: List[str]) -> None:
        """
        Mark memories as provided to the agent.

        Updates last_accessed_at and access_count for these memories.
        This should be called when memories are actually given to the agent,
        not during search.

        Args:
            memory_ids: List of memory UUIDs that were provided
        """
        for memory_id in memory_ids:
            self._increment_access_count(memory_id)

        if memory_ids:
            logger.info(f"Marked {len(memory_ids)} memories as provided")
