"""
Dimensionality reduction service for memory visualization.

Reduces 768-dimensional embeddings to 2D/3D coordinates using UMAP, PCA, or t-SNE.
"""

import logging
from typing import List, Dict, Any, Literal
import numpy as np
from sklearn.decomposition import PCA
from sklearn.manifold import TSNE

logger = logging.getLogger(__name__)

# Try to import UMAP (optional dependency)
try:
    from umap import UMAP
    UMAP_AVAILABLE = True
except ImportError:
    UMAP_AVAILABLE = False
    logger.warning("UMAP not available. Install with: pip install umap-learn")


class DimensionalityReductionService:
    """
    Service for reducing high-dimensional embeddings to 2D/3D for visualization.

    Supports:
    - UMAP (Uniform Manifold Approximation and Projection) - best for preserving structure
    - PCA (Principal Component Analysis) - fast, linear
    - t-SNE (t-Distributed Stochastic Neighbor Embedding) - good for local structure
    """

    @staticmethod
    def reduce_dimensions(
        embeddings: List[List[float]],
        method: Literal["umap", "pca", "tsne"] = "umap",
        dimensions: int = 2,
        random_state: int = 42
    ) -> np.ndarray:
        """
        Reduce embeddings to 2D or 3D coordinates.

        Args:
            embeddings: List of 768-dimensional embedding vectors
            method: Reduction method (umap, pca, tsne)
            dimensions: Target dimensions (2 or 3)
            random_state: Random seed for reproducibility

        Returns:
            numpy array of shape (n_samples, dimensions)

        Raises:
            ValueError: If invalid method or dimensions
            RuntimeError: If reduction fails
        """
        if dimensions not in [2, 3]:
            raise ValueError("Dimensions must be 2 or 3")

        if len(embeddings) == 0:
            raise ValueError("No embeddings provided")

        # Convert to numpy array
        X = np.array(embeddings)

        if X.shape[0] == 1:
            # Single point - just return it centered at origin
            return np.zeros((1, dimensions))

        logger.info(f"Reducing {X.shape[0]} embeddings from {X.shape[1]}D to {dimensions}D using {method}")

        try:
            if method == "umap":
                if not UMAP_AVAILABLE:
                    raise ValueError("UMAP not available. Use 'pca' or 'tsne' instead, or install umap-learn")

                # UMAP parameters optimized for visualization
                reducer = UMAP(
                    n_components=dimensions,
                    n_neighbors=min(15, X.shape[0] - 1),  # Adjust for small datasets
                    min_dist=0.1,
                    metric='cosine',  # Good for embeddings
                    random_state=random_state
                )
                reduced = reducer.fit_transform(X)

            elif method == "pca":
                # PCA - fast and deterministic
                reducer = PCA(
                    n_components=dimensions,
                    random_state=random_state
                )
                reduced = reducer.fit_transform(X)

            elif method == "tsne":
                # t-SNE - slower but good for local structure
                # Perplexity must be less than n_samples
                perplexity = min(30, X.shape[0] - 1)

                reducer = TSNE(
                    n_components=dimensions,
                    perplexity=max(2, perplexity),  # At least 2
                    random_state=random_state,
                    max_iter=1000,  # Changed from n_iter to max_iter for compatibility
                    metric='cosine'
                )
                reduced = reducer.fit_transform(X)

            else:
                raise ValueError(f"Unknown reduction method: {method}")

            logger.info(f"Successfully reduced to {dimensions}D using {method}")
            return reduced

        except Exception as e:
            logger.error(f"Dimensionality reduction failed: {e}")
            raise RuntimeError(f"Failed to reduce dimensions with {method}: {str(e)}")

    @staticmethod
    def create_visualization_points(
        memories: List[Dict[str, Any]],
        reduced_coords: np.ndarray
    ) -> List[Dict[str, Any]]:
        """
        Combine memory metadata with reduced coordinates.

        Args:
            memories: List of memory dictionaries from Weaviate
            reduced_coords: numpy array of shape (n_samples, 2 or 3)

        Returns:
            List of visualization point dictionaries
        """
        if len(memories) != reduced_coords.shape[0]:
            raise ValueError("Number of memories must match number of coordinates")

        dimensions = reduced_coords.shape[1]
        points = []

        for i, memory in enumerate(memories):
            # Convert datetime to ISO string if present
            created_at = memory.get("created_at")
            if created_at is not None and hasattr(created_at, 'isoformat'):
                created_at = created_at.isoformat()

            point = {
                "id": memory["id"],
                "x": float(reduced_coords[i, 0]),
                "y": float(reduced_coords[i, 1]),
                "z": float(reduced_coords[i, 2]) if dimensions == 3 else None,
                "content": memory["content"],
                "memory_type": memory["memory_type"],
                "importance": memory.get("importance", 0.5),
                "confidence": memory.get("confidence", 1.0),
                "tags": memory.get("tags", []),
                "created_at": created_at,
                "access_count": memory.get("access_count", 0),
                "search_score": memory.get("search_score"),
                "agent_id": memory.get("agent_id"),
                "scope": memory.get("scope")
            }
            points.append(point)

        return points
