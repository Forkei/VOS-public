"""
Memory visualization API endpoints.

Provides endpoints for visualizing memories in 2D/3D latent space and statistics.
"""

import os
import logging
from typing import List, Optional, Dict, Any
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.memory_client import (
    WeaviateClient,
    MemoryType,
    MemoryScope,
    get_embedding_service
)
from app.services.dimensionality_reduction import DimensionalityReductionService

logger = logging.getLogger(__name__)

# Create router for memory visualization endpoints
router = APIRouter(prefix="/memories/visualization", tags=["memory-visualization"])


# Request/Response Models
class VisualizationRequest(BaseModel):
    """Request model for dimensionality reduction visualization."""
    method: str = Field(default="umap", description="Reduction method: umap, pca, or tsne")
    dimensions: int = Field(default=2, ge=2, le=3, description="Target dimensions (2 or 3)")
    memory_type: Optional[str] = Field(None, description="Filter by memory type")
    scope: Optional[str] = Field(None, description="Filter by scope (individual/shared)")
    agent_id: Optional[str] = Field(None, description="Filter by agent ID")
    session_id: Optional[str] = Field(None, description="Filter by session ID")
    tags: Optional[List[str]] = Field(None, description="Filter by tags")
    min_importance: Optional[float] = Field(None, ge=0.0, le=1.0, description="Minimum importance")
    min_confidence: Optional[float] = Field(None, ge=0.0, le=1.0, description="Minimum confidence")
    limit: int = Field(default=500, ge=1, le=1000, description="Maximum memories to visualize")


class VisualizationPoint(BaseModel):
    """A single point in the visualization."""
    id: str
    x: float
    y: float
    z: Optional[float] = None
    content: str
    memory_type: str
    importance: float
    confidence: float
    tags: List[str] = Field(default_factory=list)
    created_at: Optional[str] = None
    access_count: int = 0
    search_score: Optional[float] = None
    agent_id: Optional[str] = None
    scope: Optional[str] = None


class VisualizationResponse(BaseModel):
    """Response model for visualization data."""
    status: str
    count: int
    method: str
    dimensions: int
    points: List[VisualizationPoint]
    filters: Dict[str, Any]


class StatisticsByType(BaseModel):
    """Statistics grouped by type."""
    memory_type: str
    count: int
    avg_importance: float
    avg_confidence: float


class StatisticsResponse(BaseModel):
    """Response model for memory statistics."""
    status: str
    total_memories: int
    by_type: List[StatisticsByType]
    by_scope: Dict[str, int]
    top_tags: List[Dict[str, Any]]
    importance_distribution: Dict[str, int]
    confidence_distribution: Dict[str, int]
    date_range: Dict[str, Optional[str]]


def get_weaviate_client():
    """Get Weaviate client instance."""
    weaviate_url = os.environ.get("WEAVIATE_URL", "http://weaviate:8080")
    return WeaviateClient(weaviate_url)


@router.post("/reduce", response_model=VisualizationResponse)
async def reduce_dimensions(request: VisualizationRequest):
    """
    Reduce memory embeddings to 2D/3D coordinates for visualization.

    This endpoint:
    1. Fetches memories with their 768D embeddings
    2. Applies dimensionality reduction (UMAP/PCA/t-SNE)
    3. Returns coordinates with memory metadata

    Example request:
    ```json
    {
        "method": "umap",
        "dimensions": 2,
        "memory_type": "user_preference",
        "min_importance": 0.5,
        "limit": 500
    }
    ```
    """
    try:
        # Validate method
        if request.method not in ["umap", "pca", "tsne"]:
            raise HTTPException(
                status_code=400,
                detail="Invalid method. Must be one of: umap, pca, tsne"
            )

        # Parse memory_type
        parsed_memory_type = None
        if request.memory_type:
            try:
                parsed_memory_type = MemoryType(request.memory_type)
            except ValueError:
                valid_types = [mt.value for mt in MemoryType]
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid memory_type. Must be one of: {', '.join(valid_types)}"
                )

        # Parse scope
        parsed_scope = None
        if request.scope:
            try:
                parsed_scope = MemoryScope(request.scope)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid scope. Must be 'individual' or 'shared'"
                )

        # Fetch memories WITH embeddings
        with get_weaviate_client() as client:
            memories = client.search_memories(
                query_vector=None,  # No semantic search, just filter
                memory_type=parsed_memory_type,
                scope=parsed_scope,
                agent_id=request.agent_id,
                session_id=request.session_id,
                tags=request.tags,
                min_importance=request.min_importance,
                min_confidence=request.min_confidence,
                limit=request.limit,
                include_vectors=True  # CRITICAL: Include embeddings
            )

        if len(memories) == 0:
            return VisualizationResponse(
                status="success",
                count=0,
                method=request.method,
                dimensions=request.dimensions,
                points=[],
                filters={
                    "memory_type": request.memory_type,
                    "scope": request.scope,
                    "agent_id": request.agent_id,
                    "session_id": request.session_id,
                    "tags": request.tags,
                    "min_importance": request.min_importance,
                    "min_confidence": request.min_confidence
                }
            )

        # Extract embeddings
        embeddings = []
        for memory in memories:
            if "embedding" not in memory or memory["embedding"] is None:
                raise HTTPException(
                    status_code=500,
                    detail="Memory missing embedding vector. Database may be corrupted."
                )
            embeddings.append(memory["embedding"])

        logger.info(f"Reducing {len(embeddings)} memories using {request.method}")

        # Apply dimensionality reduction
        reduction_service = DimensionalityReductionService()
        reduced_coords = reduction_service.reduce_dimensions(
            embeddings=embeddings,
            method=request.method,
            dimensions=request.dimensions
        )

        # Create visualization points
        points = reduction_service.create_visualization_points(
            memories=memories,
            reduced_coords=reduced_coords
        )

        return VisualizationResponse(
            status="success",
            count=len(points),
            method=request.method,
            dimensions=request.dimensions,
            points=points,
            filters={
                "memory_type": request.memory_type,
                "scope": request.scope,
                "agent_id": request.agent_id,
                "session_id": request.session_id,
                "tags": request.tags,
                "min_importance": request.min_importance,
                "min_confidence": request.min_confidence
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to reduce dimensions: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to reduce dimensions: {str(e)}"
        )


@router.get("/statistics", response_model=StatisticsResponse)
async def get_statistics():
    """
    Get aggregated statistics about memories.

    Returns counts, distributions, and metadata for all memories in the system.
    Useful for dashboard widgets and filtering UI.
    """
    try:
        # Fetch all memories (without vectors for speed)
        with get_weaviate_client() as client:
            all_memories = client.search_memories(
                query_vector=None,
                limit=10000,  # Get all memories
                include_vectors=False
            )

        if len(all_memories) == 0:
            return StatisticsResponse(
                status="success",
                total_memories=0,
                by_type=[],
                by_scope={},
                top_tags=[],
                importance_distribution={},
                confidence_distribution={},
                date_range={"earliest": None, "latest": None}
            )

        # Aggregate by type
        type_stats = defaultdict(lambda: {"count": 0, "importance_sum": 0.0, "confidence_sum": 0.0})
        scope_counts = defaultdict(int)
        tag_counts = defaultdict(int)
        importance_buckets = defaultdict(int)
        confidence_buckets = defaultdict(int)
        dates = []

        for memory in all_memories:
            # Type stats
            mem_type = memory.get("memory_type", "unknown")
            type_stats[mem_type]["count"] += 1
            type_stats[mem_type]["importance_sum"] += memory.get("importance", 0.5)
            type_stats[mem_type]["confidence_sum"] += memory.get("confidence", 1.0)

            # Scope counts
            scope = memory.get("scope", "unknown")
            scope_counts[scope] += 1

            # Tag counts
            for tag in memory.get("tags", []):
                tag_counts[tag] += 1

            # Importance distribution (buckets of 0.2)
            importance = memory.get("importance", 0.5)
            bucket = f"{int(importance * 5) * 0.2:.1f}-{(int(importance * 5) + 1) * 0.2:.1f}"
            importance_buckets[bucket] += 1

            # Confidence distribution
            confidence = memory.get("confidence", 1.0)
            bucket = f"{int(confidence * 5) * 0.2:.1f}-{(int(confidence * 5) + 1) * 0.2:.1f}"
            confidence_buckets[bucket] += 1

            # Dates
            if memory.get("created_at"):
                dates.append(memory["created_at"])

        # Format type statistics
        by_type = [
            StatisticsByType(
                memory_type=mem_type,
                count=stats["count"],
                avg_importance=stats["importance_sum"] / stats["count"],
                avg_confidence=stats["confidence_sum"] / stats["count"]
            )
            for mem_type, stats in type_stats.items()
        ]
        by_type.sort(key=lambda x: x.count, reverse=True)

        # Top tags (top 20)
        top_tags = [
            {"tag": tag, "count": count}
            for tag, count in sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:20]
        ]

        # Date range
        date_range = {
            "earliest": min(dates) if dates else None,
            "latest": max(dates) if dates else None
        }

        return StatisticsResponse(
            status="success",
            total_memories=len(all_memories),
            by_type=by_type,
            by_scope=dict(scope_counts),
            top_tags=top_tags,
            importance_distribution=dict(importance_buckets),
            confidence_distribution=dict(confidence_buckets),
            date_range=date_range
        )

    except Exception as e:
        logger.error(f"Failed to get statistics: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get statistics: {str(e)}"
        )


@router.get("/search-for-viz")
async def search_for_visualization(
    query: str = Query(..., description="Search query for semantic similarity"),
    limit: int = Query(50, ge=1, le=500, description="Maximum results")
):
    """
    Search memories for visualization highlighting.

    This is a simplified search endpoint that returns just IDs and scores,
    optimized for highlighting search results in the visualization.
    """
    try:
        if not query or query.strip() == "":
            return {
                "status": "success",
                "count": 0,
                "query": query,
                "results": []
            }

        # Generate query embedding
        embedding_service = get_embedding_service()
        query_vector = embedding_service.embed_query(query)

        # Search memories
        with get_weaviate_client() as client:
            memories = client.search_memories(
                query_vector=query_vector,
                limit=limit,
                include_vectors=False
            )

        # Return simplified results (just IDs and scores)
        results = [
            {
                "id": memory["id"],
                "content": memory["content"][:200],  # Preview
                "search_score": memory.get("search_score", 0.0),
                "memory_type": memory.get("memory_type"),
                "importance": memory.get("importance")
            }
            for memory in memories
        ]

        return {
            "status": "success",
            "count": len(results),
            "query": query,
            "results": results
        }

    except Exception as e:
        logger.error(f"Failed to search for visualization: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to search: {str(e)}"
        )
