"""
Embedding service for VOS memory system.

Uses Google Gemini API text-embedding-004 for semantic memory search.
Generates 768-dimensional embeddings compatible with the existing Weaviate schema.
"""

import logging
import os
from typing import List
from enum import Enum

from google import genai
from google.genai.types import EmbedContentConfig

logger = logging.getLogger(__name__)


class EmbeddingTask(str, Enum):
    """Task types for embeddings with proper prefixes."""
    SEARCH_DOCUMENT = "search_document"  # For storing memories
    SEARCH_QUERY = "search_query"        # For querying memories
    CLUSTERING = "clustering"             # For grouping memories
    CLASSIFICATION = "classification"     # For classification tasks


class EmbeddingService:
    """
    Service for generating embeddings using Google Gemini API.

    This is a singleton service that initializes the Gemini client once and reuses it.
    Uses text-embedding-004 model with 768-dimensional output.
    """

    _instance = None
    _client = None
    _model_name = "models/text-embedding-004"

    def __new__(cls):
        """Ensure singleton pattern."""
        if cls._instance is None:
            cls._instance = super(EmbeddingService, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize the embedding service."""
        if self._client is None:
            self._initialize_client()

    def _initialize_client(self) -> None:
        """Initialize the Gemini API client."""
        try:
            # Get API key from environment
            api_key = os.environ.get("GEMINI_API_KEY")

            if not api_key:
                logger.error("GEMINI_API_KEY not found in environment variables")
                raise ValueError("GEMINI_API_KEY environment variable is required")

            # Initialize Gemini client
            self._client = genai.Client(api_key=api_key)

            logger.info(f"Embedding service initialized with {self._model_name} (768 dimensions)")

        except Exception as e:
            logger.error(f"Failed to initialize Gemini embedding client: {e}")
            raise

    def embed_memory(self, text: str) -> List[float]:
        """
        Generate embedding for storing a memory.

        Uses 'search_document:' prefix for optimal retrieval.

        Args:
            text: Memory content to embed

        Returns:
            768-dimensional embedding vector
        """
        prefixed_text = f"{EmbeddingTask.SEARCH_DOCUMENT.value}: {text}"
        return self._encode_single(prefixed_text, task_type="RETRIEVAL_DOCUMENT")

    def embed_query(self, query: str) -> List[float]:
        """
        Generate embedding for searching memories.

        Uses 'search_query:' prefix for optimal retrieval.

        Args:
            query: Search query text

        Returns:
            768-dimensional embedding vector
        """
        prefixed_text = f"{EmbeddingTask.SEARCH_QUERY.value}: {query}"
        return self._encode_single(prefixed_text, task_type="RETRIEVAL_QUERY")

    def embed_memories_batch(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for multiple memories at once.

        More efficient than calling embed_memory() repeatedly.

        Args:
            texts: List of memory contents

        Returns:
            List of 768-dimensional embedding vectors
        """
        prefixed_texts = [
            f"{EmbeddingTask.SEARCH_DOCUMENT.value}: {text}"
            for text in texts
        ]
        return self._encode_batch(prefixed_texts, task_type="RETRIEVAL_DOCUMENT")

    def embed_queries_batch(self, queries: List[str]) -> List[List[float]]:
        """
        Generate embeddings for multiple queries at once.

        Args:
            queries: List of search queries

        Returns:
            List of 768-dimensional embedding vectors
        """
        prefixed_queries = [
            f"{EmbeddingTask.SEARCH_QUERY.value}: {query}"
            for query in queries
        ]
        return self._encode_batch(prefixed_queries, task_type="RETRIEVAL_QUERY")

    def _encode_single(self, text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> List[float]:
        """
        Encode a single text string using Gemini API.

        Args:
            text: Text to encode (already prefixed)
            task_type: Gemini task type (RETRIEVAL_DOCUMENT or RETRIEVAL_QUERY)

        Returns:
            768-dimensional embedding vector as list
        """
        try:
            # Call Gemini embedding API with 768-dimensional output
            response = self._client.models.embed_content(
                model=self._model_name,
                contents=text,
                config=EmbedContentConfig(
                    task_type=task_type,
                    output_dimensionality=768
                )
            )

            # Extract embedding from response
            if not response or not response.embeddings:
                raise RuntimeError("Empty response from Gemini embedding API")

            embedding = response.embeddings[0].values

            # Validate dimension
            if len(embedding) != 768:
                raise ValueError(f"Expected 768 dimensions, got {len(embedding)}")

            return list(embedding)

        except Exception as e:
            logger.error(f"Failed to generate embedding: {e}")
            raise

    def _encode_batch(self, texts: List[str], task_type: str = "RETRIEVAL_DOCUMENT") -> List[List[float]]:
        """
        Encode multiple text strings efficiently using Gemini API.

        Args:
            texts: List of texts to encode (already prefixed)
            task_type: Gemini task type (RETRIEVAL_DOCUMENT or RETRIEVAL_QUERY)

        Returns:
            List of 768-dimensional embedding vectors
        """
        try:
            # Gemini API supports batch embedding
            response = self._client.models.embed_content(
                model=self._model_name,
                contents=texts,
                config=EmbedContentConfig(
                    task_type=task_type,
                    output_dimensionality=768
                )
            )

            # Extract embeddings from response
            if not response or not response.embeddings:
                raise RuntimeError("Empty response from Gemini embedding API")

            embeddings = []
            for emb in response.embeddings:
                embedding = list(emb.values)

                # Validate dimension
                if len(embedding) != 768:
                    raise ValueError(f"Expected 768 dimensions, got {len(embedding)}")

                embeddings.append(embedding)

            return embeddings

        except Exception as e:
            logger.error(f"Failed to generate batch embeddings: {e}")
            raise

    @property
    def embedding_dimension(self) -> int:
        """Get the dimensionality of the embeddings."""
        return 768


# Global singleton instance
_embedding_service = None


def get_embedding_service() -> EmbeddingService:
    """
    Get the global embedding service instance.

    Returns:
        EmbeddingService singleton
    """
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = EmbeddingService()
    return _embedding_service
