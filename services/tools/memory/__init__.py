"""
Memory tools for VOS agents.

Tools for creating, retrieving, and managing memories in Weaviate.
"""

# Don't import memory_tools at package level to avoid circular import with vos_sdk
# memory_tools can be imported directly when needed by agents
from .weaviate_client import (
    WeaviateClient,
    MemoryType,
    MemoryScope,
    MemorySource
)
from .embedding_service import EmbeddingService, get_embedding_service

__all__ = [
    "WeaviateClient",
    "MemoryType",
    "MemoryScope",
    "MemorySource",
    "EmbeddingService",
    "get_embedding_service"
]
