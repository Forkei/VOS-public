"""
Memory management API endpoints.

Provides REST API for VOS memory system (CRUD operations).
"""

import os
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.memory_client import (
    WeaviateClient,
    MemoryType,
    MemoryScope,
    MemorySource,
    get_embedding_service
)

# Create router for memory endpoints
router = APIRouter(prefix="/memories", tags=["memories"])

# Request/Response Models
class MemoryCreate(BaseModel):
    """Request model for creating a memory."""
    content: str = Field(..., description="The memory content")
    memory_type: str = Field(..., description="Type of memory (user_preference, user_fact, etc.)")
    scope: str = Field(default="shared", description="'individual' or 'shared'")
    agent_id: Optional[str] = Field(None, description="Agent that created this memory")
    session_id: Optional[str] = Field(None, description="Session ID")
    related_event_types: Optional[List[str]] = Field(None, description="Related event types")
    related_tools: Optional[List[str]] = Field(None, description="Related tools")
    related_memory_ids: Optional[List[str]] = Field(None, description="Related memory IDs")
    tags: Optional[List[str]] = Field(None, description="Searchable tags")
    importance: float = Field(default=0.5, ge=0.0, le=1.0, description="Importance score 0.0-1.0")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence score 0.0-1.0")
    source: str = Field(default="agent_learning", description="How memory was created")
    expires_at: Optional[str] = Field(None, description="Optional expiration timestamp")


class MemoryUpdate(BaseModel):
    """Request model for updating a memory."""
    content: Optional[str] = Field(None, description="New content")
    tags: Optional[List[str]] = Field(None, description="New tags (replaces existing)")
    importance: Optional[float] = Field(None, ge=0.0, le=1.0, description="New importance score")
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0, description="New confidence score")
    related_memory_ids: Optional[List[str]] = Field(None, description="New related memory IDs")
    success_count: Optional[int] = Field(None, description="New success count")
    failure_count: Optional[int] = Field(None, description="New failure count")


class MemorySearchRequest(BaseModel):
    """Request model for searching memories."""
    query: Optional[str] = Field(None, description="Search query for semantic search")
    memory_type: Optional[str] = Field(None, description="Filter by memory type")
    scope: Optional[str] = Field(None, description="Filter by scope")
    agent_id: Optional[str] = Field(None, description="Filter by agent ID")
    session_id: Optional[str] = Field(None, description="Filter by session ID")
    tags: Optional[List[str]] = Field(None, description="Filter by tags")
    min_importance: Optional[float] = Field(None, ge=0.0, le=1.0, description="Minimum importance")
    min_confidence: Optional[float] = Field(None, ge=0.0, le=1.0, description="Minimum confidence")
    limit: int = Field(default=10, ge=1, le=100, description="Maximum results")


def get_weaviate_client():
    """Get Weaviate client instance."""
    weaviate_url = os.environ.get("WEAVIATE_URL", "http://weaviate:8080")
    return WeaviateClient(weaviate_url)


@router.post("/", status_code=201)
async def create_memory(memory_data: MemoryCreate):
    """
    Create a new memory.

    Args:
        memory_data: Memory creation data

    Returns:
        Created memory ID and details
    """
    try:
        # Validate memory_type
        try:
            memory_type = MemoryType(memory_data.memory_type)
        except ValueError:
            valid_types = [mt.value for mt in MemoryType]
            raise HTTPException(
                status_code=400,
                detail=f"Invalid memory_type. Must be one of: {', '.join(valid_types)}"
            )

        # Validate scope
        try:
            scope = MemoryScope(memory_data.scope)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid scope. Must be 'individual' or 'shared'"
            )

        # Validate source
        try:
            source = MemorySource(memory_data.source)
        except ValueError:
            valid_sources = [s.value for s in MemorySource]
            raise HTTPException(
                status_code=400,
                detail=f"Invalid source. Must be one of: {', '.join(valid_sources)}"
            )

        # Generate embedding
        embedding_service = get_embedding_service()
        vector = embedding_service.embed_memory(memory_data.content)

        # Create memory
        with get_weaviate_client() as client:
            memory_id = client.create_memory(
                content=memory_data.content,
                memory_type=memory_type,
                scope=scope,
                vector=vector,
                agent_id=memory_data.agent_id,
                session_id=memory_data.session_id,
                related_event_types=memory_data.related_event_types,
                related_tools=memory_data.related_tools,
                related_memory_ids=memory_data.related_memory_ids,
                tags=memory_data.tags,
                importance=memory_data.importance,
                confidence=memory_data.confidence,
                source=source,
                expires_at=memory_data.expires_at
            )

        return {
            "status": "success",
            "memory_id": memory_id,
            "content": memory_data.content,
            "memory_type": memory_type.value,
            "scope": scope.value,
            "message": "Memory created successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create memory: {str(e)}"
        )


@router.get("/search")
async def search_memories(
    query: Optional[str] = Query(None, description="Search query for semantic similarity"),
    memory_type: Optional[str] = Query(None, description="Filter by memory type"),
    scope: Optional[str] = Query(None, description="Filter by scope (individual/shared)"),
    agent_id: Optional[str] = Query(None, description="Filter by agent ID"),
    session_id: Optional[str] = Query(None, description="Filter by session ID"),
    tags: Optional[str] = Query(None, description="Comma-separated tags to filter by"),
    min_importance: Optional[float] = Query(None, ge=0.0, le=1.0, description="Minimum importance"),
    min_confidence: Optional[float] = Query(None, ge=0.0, le=1.0, description="Minimum confidence"),
    limit: int = Query(10, ge=1, le=100, description="Maximum results")
):
    """
    Search for memories using semantic similarity and filters.

    This GET-based endpoint makes it easy to test in Swagger UI by filling in parameter boxes.

    Examples:
    - Search by query: GET /memories/search?query=purple+color
    - Filter by type: GET /memories/search?memory_type=user_preference
    - Combine: GET /memories/search?query=coding&memory_type=user_preference&min_importance=0.7
    """
    try:
        # Generate query embedding if query provided
        query_vector = None
        if query:
            embedding_service = get_embedding_service()
            query_vector = embedding_service.embed_query(query)

        # Parse memory_type
        parsed_memory_type = None
        if memory_type:
            try:
                parsed_memory_type = MemoryType(memory_type)
            except ValueError:
                valid_types = [mt.value for mt in MemoryType]
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid memory_type. Must be one of: {', '.join(valid_types)}"
                )

        # Parse scope
        parsed_scope = None
        if scope:
            try:
                parsed_scope = MemoryScope(scope)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid scope. Must be 'individual' or 'shared'"
                )

        # Parse tags (comma-separated)
        parsed_tags = None
        if tags:
            parsed_tags = [t.strip() for t in tags.split(",") if t.strip()]

        # Search memories
        with get_weaviate_client() as client:
            memories = client.search_memories(
                query_vector=query_vector,
                memory_type=parsed_memory_type,
                scope=parsed_scope,
                agent_id=agent_id,
                session_id=session_id,
                tags=parsed_tags,
                min_importance=min_importance,
                min_confidence=min_confidence,
                limit=limit
            )

        return {
            "status": "success",
            "count": len(memories),
            "memories": memories,
            "query": query,
            "filters": {
                "memory_type": memory_type,
                "scope": scope,
                "agent_id": agent_id,
                "session_id": session_id,
                "tags": parsed_tags,
                "min_importance": min_importance,
                "min_confidence": min_confidence
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to search memories: {str(e)}"
        )


@router.get("/{memory_id}")
async def get_memory(memory_id: str):
    """
    Get a specific memory by ID.

    Args:
        memory_id: UUID of the memory

    Returns:
        Memory data

    Raises:
        404: If memory not found
    """
    try:
        with get_weaviate_client() as client:
            memory = client.get_memory(memory_id)

        if not memory:
            raise HTTPException(
                status_code=404,
                detail=f"Memory with ID {memory_id} not found"
            )

        return {
            "status": "success",
            "memory": memory
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve memory: {str(e)}"
        )


@router.patch("/{memory_id}")
async def update_memory(memory_id: str, update_data: MemoryUpdate):
    """
    Update an existing memory.

    Args:
        memory_id: UUID of the memory to update
        update_data: Fields to update

    Returns:
        Update status
    """
    try:
        # If updating content, generate new embedding
        vector = None
        if update_data.content:
            embedding_service = get_embedding_service()
            vector = embedding_service.embed_memory(update_data.content)

        with get_weaviate_client() as client:
            success = client.update_memory(
                memory_id=memory_id,
                content=update_data.content,
                vector=vector,
                tags=update_data.tags,
                importance=update_data.importance,
                confidence=update_data.confidence,
                related_memory_ids=update_data.related_memory_ids,
                success_count=update_data.success_count,
                failure_count=update_data.failure_count
            )

        if not success:
            raise HTTPException(
                status_code=404,
                detail=f"Memory with ID {memory_id} not found"
            )

        return {
            "status": "success",
            "memory_id": memory_id,
            "updated": True,
            "message": "Memory updated successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update memory: {str(e)}"
        )


@router.get("/")
async def list_memories(
    query: Optional[str] = Query(None, description="Text query for similarity search"),
    memory_type: Optional[str] = Query(None, description="Filter by memory type"),
    scope: Optional[str] = Query(None, description="Filter by scope (individual/shared)"),
    agent_id: Optional[str] = Query(None, description="Filter by agent ID"),
    session_id: Optional[str] = Query(None, description="Filter by session ID"),
    tags: Optional[str] = Query(None, description="Comma-separated tags to filter by"),
    min_importance: Optional[float] = Query(None, ge=0.0, le=1.0, description="Minimum importance (0.0-1.0)"),
    max_importance: Optional[float] = Query(None, ge=0.0, le=1.0, description="Maximum importance (0.0-1.0)"),
    min_confidence: Optional[float] = Query(None, ge=0.0, le=1.0, description="Minimum confidence (0.0-1.0)"),
    limit: int = Query(50, ge=1, le=500, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip (pagination)")
):
    """
    List/query memories with flexible filtering.

    This is a GET-based endpoint for easy testing and viewing.
    Supports both semantic search (with query) and filtered listing (without query).

    Examples:
    - View all memories: GET /memories/
    - Search by text: GET /memories/?query=purple%20color
    - Filter by type: GET /memories/?memory_type=user_preference
    - Filter by agent: GET /memories/?agent_id=primary_agent
    - Combine filters: GET /memories/?agent_id=primary_agent&min_importance=0.7
    - Paginate: GET /memories/?limit=10&offset=20
    """
    try:
        # Generate query embedding if query provided
        query_vector = None
        if query:
            embedding_service = get_embedding_service()
            query_vector = embedding_service.embed_query(query)

        # Parse memory_type
        parsed_memory_type = None
        if memory_type:
            try:
                parsed_memory_type = MemoryType(memory_type)
            except ValueError:
                valid_types = [mt.value for mt in MemoryType]
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid memory_type. Must be one of: {', '.join(valid_types)}"
                )

        # Parse scope
        parsed_scope = None
        if scope:
            try:
                parsed_scope = MemoryScope(scope)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid scope. Must be 'individual' or 'shared'"
                )

        # Parse tags (comma-separated)
        parsed_tags = None
        if tags:
            parsed_tags = [t.strip() for t in tags.split(",") if t.strip()]

        # Search/list memories
        with get_weaviate_client() as client:
            all_memories = client.search_memories(
                query_vector=query_vector,
                memory_type=parsed_memory_type,
                scope=parsed_scope,
                agent_id=agent_id,
                session_id=session_id,
                tags=parsed_tags,
                min_importance=min_importance,
                min_confidence=min_confidence,
                limit=limit + offset  # Get extra to handle offset
            )

        # Apply max_importance filter (not supported by Weaviate directly)
        if max_importance is not None:
            all_memories = [m for m in all_memories if m['importance'] <= max_importance]

        # Apply offset/pagination
        paginated_memories = all_memories[offset:offset + limit]

        return {
            "status": "success",
            "count": len(paginated_memories),
            "total_before_pagination": len(all_memories),
            "memories": paginated_memories,
            "query": query,
            "filters": {
                "memory_type": memory_type,
                "scope": scope,
                "agent_id": agent_id,
                "session_id": session_id,
                "tags": parsed_tags,
                "min_importance": min_importance,
                "max_importance": max_importance,
                "min_confidence": min_confidence
            },
            "pagination": {
                "limit": limit,
                "offset": offset
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list memories: {str(e)}"
        )


@router.delete("/{memory_id}")
async def delete_memory(memory_id: str):
    """
    Delete a memory.

    Args:
        memory_id: UUID of the memory to delete

    Returns:
        Deletion status
    """
    try:
        with get_weaviate_client() as client:
            success = client.delete_memory(memory_id)

        if not success:
            raise HTTPException(
                status_code=404,
                detail=f"Memory with ID {memory_id} not found"
            )

        return {
            "status": "success",
            "memory_id": memory_id,
            "deleted": True,
            "message": "Memory deleted successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete memory: {str(e)}"
        )
