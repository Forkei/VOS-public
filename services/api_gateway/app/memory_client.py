"""
Memory client for API Gateway.

Direct access to Weaviate for memory management without VOS SDK dependency.
"""

import sys
from pathlib import Path
import importlib.util

# Load modules directly to avoid __init__.py which imports vos_sdk
tools_path = Path("/app/tools")

# Load weaviate_client module directly
spec = importlib.util.spec_from_file_location(
    "weaviate_client",
    tools_path / "memory" / "weaviate_client.py"
)
weaviate_client_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(weaviate_client_module)

# Load embedding_service module directly
spec = importlib.util.spec_from_file_location(
    "embedding_service",
    tools_path / "memory" / "embedding_service.py"
)
embedding_service_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(embedding_service_module)

# Export the classes and functions
WeaviateClient = weaviate_client_module.WeaviateClient
MemoryType = weaviate_client_module.MemoryType
MemoryScope = weaviate_client_module.MemoryScope
MemorySource = weaviate_client_module.MemorySource
get_embedding_service = embedding_service_module.get_embedding_service

__all__ = [
    "WeaviateClient",
    "MemoryType",
    "MemoryScope",
    "MemorySource",
    "get_embedding_service"
]
