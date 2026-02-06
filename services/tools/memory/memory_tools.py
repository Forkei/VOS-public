"""
Memory management tools for VOS agents.

Tools for creating, retrieving, searching, updating, and deleting memories.
"""

import os
import logging
from typing import Dict, Any, Optional

from vos_sdk import BaseTool
from .weaviate_client import (
    WeaviateClient,
    MemoryType,
    MemoryScope,
    MemorySource
)
from .embedding_service import get_embedding_service

logger = logging.getLogger(__name__)


class CreateMemoryTool(BaseTool):
    """Create a new memory in the VOS memory system."""

    def __init__(self):
        super().__init__(
            name="create_memory",
            description="Create a new memory that can be recalled later"
        )
        self.weaviate_url = os.environ.get("WEAVIATE_URL", "http://weaviate:8080")

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate memory creation arguments."""
        if "content" not in arguments:
            return False, "Missing required argument: 'content'"

        if not isinstance(arguments["content"], str) or not arguments["content"].strip():
            return False, "'content' must be a non-empty string"

        if "memory_type" not in arguments:
            return False, "Missing required argument: 'memory_type'"

        # Validate memory_type
        try:
            MemoryType(arguments["memory_type"])
        except ValueError:
            valid_types = [mt.value for mt in MemoryType]
            return False, f"'memory_type' must be one of: {', '.join(valid_types)}"

        # Validate scope if provided
        if "scope" in arguments:
            try:
                MemoryScope(arguments["scope"])
            except ValueError:
                return False, "'scope' must be 'individual' or 'shared'"

        # Validate source if provided
        if "source" in arguments:
            try:
                MemorySource(arguments["source"])
            except ValueError:
                valid_sources = [s.value for s in MemorySource]
                return False, f"'source' must be one of: {', '.join(valid_sources)}"

        # Validate importance and confidence ranges
        for field in ["importance", "confidence"]:
            if field in arguments:
                value = arguments[field]
                if not isinstance(value, (int, float)) or not (0.0 <= value <= 1.0):
                    return False, f"'{field}' must be a number between 0.0 and 1.0"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "create_memory",
            "description": "Create a new memory that can be recalled later using semantic search",
            "parameters": [
                {
                    "name": "content",
                    "type": "str",
                    "description": "The memory content (what to remember)",
                    "required": True
                },
                {
                    "name": "memory_type",
                    "type": "str",
                    "description": f"Type: {', '.join([mt.value for mt in MemoryType])}",
                    "required": True
                },
                {
                    "name": "scope",
                    "type": "str",
                    "description": "'individual' (private) or 'shared' (all agents). Default: shared",
                    "required": False
                },
                {
                    "name": "tags",
                    "type": "list[str]",
                    "description": "Searchable tags for categorization",
                    "required": False
                },
                {
                    "name": "importance",
                    "type": "float",
                    "description": "Importance score 0.0-1.0 (default: 0.5)",
                    "required": False
                },
                {
                    "name": "confidence",
                    "type": "float",
                    "description": "Confidence score 0.0-1.0 (default: 1.0)",
                    "required": False
                },
                {
                    "name": "source",
                    "type": "str",
                    "description": f"How created: {', '.join([s.value for s in MemorySource])}",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """Create a new memory."""
        try:
            # Get embedding service
            embedding_service = get_embedding_service()

            # Generate embedding for the memory content
            vector = embedding_service.embed_memory(arguments["content"])

            # Parse arguments
            memory_type = MemoryType(arguments["memory_type"])
            scope = MemoryScope(arguments.get("scope", "shared"))
            source = MemorySource(arguments.get("source", "agent_learning"))

            # Create memory (no session_id - memories are session-agnostic)
            with WeaviateClient(self.weaviate_url) as client:
                memory_id = client.create_memory(
                    content=arguments["content"],
                    memory_type=memory_type,
                    scope=scope,
                    vector=vector,
                    agent_id=self.agent_name,
                    related_event_types=arguments.get("related_event_types"),
                    related_tools=arguments.get("related_tools"),
                    tags=arguments.get("tags"),
                    importance=arguments.get("importance", 0.5),
                    confidence=arguments.get("confidence", 1.0),
                    source=source,
                    expires_at=arguments.get("expires_at")
                )

            self.send_result_notification(
                status="SUCCESS",
                result={
                    "memory_id": memory_id
                }
            )

        except Exception as e:
            logger.error(f"Failed to create memory: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to create memory: {str(e)}"
            )


class SearchMemoryTool(BaseTool):
    """Search for memories using semantic similarity and filters."""

    def __init__(self):
        super().__init__(
            name="search_memory",
            description="Search for relevant memories using semantic similarity"
        )
        self.weaviate_url = os.environ.get("WEAVIATE_URL", "http://weaviate:8080")

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate search arguments."""
        # At least query or some filter must be provided
        has_query = "query" in arguments and arguments["query"]
        has_filter = any(k in arguments for k in ["memory_type", "scope", "tags"])

        if not has_query and not has_filter:
            return False, "Must provide 'query' and/or filter parameters (memory_type, scope, tags)"

        # Validate limit
        if "limit" in arguments:
            limit = arguments["limit"]
            if not isinstance(limit, int) or limit < 1 or limit > 100:
                return False, "'limit' must be an integer between 1 and 100"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "search_memory",
            "description": "Search for relevant memories using semantic similarity and filters",
            "parameters": [
                {
                    "name": "query",
                    "type": "str",
                    "description": "Search query for semantic similarity search",
                    "required": False
                },
                {
                    "name": "memory_type",
                    "type": "str",
                    "description": f"Filter by type: {', '.join([mt.value for mt in MemoryType])}",
                    "required": False
                },
                {
                    "name": "scope",
                    "type": "str",
                    "description": "Filter by scope: 'individual' or 'shared'",
                    "required": False
                },
                {
                    "name": "tags",
                    "type": "list[str]",
                    "description": "Filter by tags (any match)",
                    "required": False
                },
                {
                    "name": "min_importance",
                    "type": "float",
                    "description": "Minimum importance score (0.0-1.0)",
                    "required": False
                },
                {
                    "name": "limit",
                    "type": "int",
                    "description": "Maximum number of results (default: 10, max: 100)",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """Search for memories."""
        try:
            # Generate query embedding if query provided
            query_vector = None
            if "query" in arguments and arguments["query"]:
                embedding_service = get_embedding_service()
                query_vector = embedding_service.embed_query(arguments["query"])

            # Parse filters
            memory_type = None
            if "memory_type" in arguments:
                memory_type = MemoryType(arguments["memory_type"])

            scope = None
            if "scope" in arguments:
                scope = MemoryScope(arguments["scope"])

            # Search memories (no session_id - memories are session-agnostic)
            with WeaviateClient(self.weaviate_url) as client:
                memories = client.search_memories(
                    query_vector=query_vector,
                    memory_type=memory_type,
                    scope=scope,
                    agent_id=arguments.get("agent_id"),
                    tags=arguments.get("tags"),
                    min_importance=arguments.get("min_importance"),
                    min_confidence=arguments.get("min_confidence"),
                    limit=arguments.get("limit", 10)
                )

            # Format results for agent
            formatted_memories = []
            for mem in memories:
                formatted_memory = {
                    "id": mem["id"],
                    "content": mem["content"],
                    "memory_type": mem["memory_type"],
                    "scope": mem["scope"],
                    "tags": mem["tags"],
                    "importance": mem["importance"],
                    "confidence": mem["confidence"]
                }
                formatted_memories.append(formatted_memory)

            self.send_result_notification(
                status="SUCCESS",
                result={
                    "count": len(memories),
                    "memories": formatted_memories
                }
            )

        except Exception as e:
            logger.error(f"Failed to search memories: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to search memories: {str(e)}"
            )


class GetMemoryTool(BaseTool):
    """Retrieve a specific memory by ID."""

    def __init__(self):
        super().__init__(
            name="get_memory",
            description="Retrieve a specific memory by its ID"
        )
        self.weaviate_url = os.environ.get("WEAVIATE_URL", "http://weaviate:8080")

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate get memory arguments."""
        if "memory_id" not in arguments:
            return False, "Missing required argument: 'memory_id'"

        if not isinstance(arguments["memory_id"], str):
            return False, "'memory_id' must be a string"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "get_memory",
            "description": "Retrieve a specific memory by its UUID",
            "parameters": [
                {
                    "name": "memory_id",
                    "type": "str",
                    "description": "UUID of the memory to retrieve",
                    "required": True
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """Retrieve a memory by ID."""
        try:
            with WeaviateClient(self.weaviate_url) as client:
                memory = client.get_memory(arguments["memory_id"])

            if memory:
                self.send_result_notification(
                    status="SUCCESS",
                    result={
                        "memory": memory
                    }
                )
            else:
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Memory {arguments['memory_id']} not found"
                )

        except Exception as e:
            logger.error(f"Failed to get memory: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to get memory: {str(e)}"
            )


class UpdateMemoryTool(BaseTool):
    """Update an existing memory."""

    def __init__(self):
        super().__init__(
            name="update_memory",
            description="Update an existing memory's content or metadata"
        )
        self.weaviate_url = os.environ.get("WEAVIATE_URL", "http://weaviate:8080")

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate update arguments."""
        if "memory_id" not in arguments:
            return False, "Missing required argument: 'memory_id'"

        # Must have at least one field to update
        update_fields = ["content", "tags", "importance", "confidence", "success_count", "failure_count"]
        has_update = any(field in arguments for field in update_fields)

        if not has_update:
            return False, f"Must provide at least one field to update: {', '.join(update_fields)}"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "update_memory",
            "description": "Update an existing memory's content, tags, or metadata",
            "parameters": [
                {
                    "name": "memory_id",
                    "type": "str",
                    "description": "UUID of the memory to update",
                    "required": True
                },
                {
                    "name": "content",
                    "type": "str",
                    "description": "New content (will regenerate embedding)",
                    "required": False
                },
                {
                    "name": "tags",
                    "type": "list[str]",
                    "description": "New tags (replaces existing)",
                    "required": False
                },
                {
                    "name": "importance",
                    "type": "float",
                    "description": "New importance score (0.0-1.0)",
                    "required": False
                },
                {
                    "name": "confidence",
                    "type": "float",
                    "description": "New confidence score (0.0-1.0)",
                    "required": False
                },
                {
                    "name": "success_count",
                    "type": "int",
                    "description": "New success count (for procedures)",
                    "required": False
                },
                {
                    "name": "failure_count",
                    "type": "int",
                    "description": "New failure count (for procedures)",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """Update a memory."""
        try:
            # If updating content, generate new embedding
            vector = None
            if "content" in arguments:
                embedding_service = get_embedding_service()
                vector = embedding_service.embed_memory(arguments["content"])

            with WeaviateClient(self.weaviate_url) as client:
                success = client.update_memory(
                    memory_id=arguments["memory_id"],
                    content=arguments.get("content"),
                    vector=vector,
                    tags=arguments.get("tags"),
                    importance=arguments.get("importance"),
                    confidence=arguments.get("confidence"),
                    related_memory_ids=arguments.get("related_memory_ids"),
                    success_count=arguments.get("success_count"),
                    failure_count=arguments.get("failure_count")
                )

            if success:
                self.send_result_notification(
                    status="SUCCESS",
                    result={
                        "updated": True
                    }
                )
            else:
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Memory {arguments['memory_id']} not found"
                )

        except Exception as e:
            logger.error(f"Failed to update memory: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to update memory: {str(e)}"
            )


class DeleteMemoryTool(BaseTool):
    """Delete a memory from the system."""

    def __init__(self):
        super().__init__(
            name="delete_memory",
            description="Delete a memory from the system"
        )
        self.weaviate_url = os.environ.get("WEAVIATE_URL", "http://weaviate:8080")

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate delete arguments."""
        if "memory_id" not in arguments:
            return False, "Missing required argument: 'memory_id'"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "delete_memory",
            "description": "Permanently delete a memory from the system",
            "parameters": [
                {
                    "name": "memory_id",
                    "type": "str",
                    "description": "UUID of the memory to delete",
                    "required": True
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """Delete a memory."""
        try:
            with WeaviateClient(self.weaviate_url) as client:
                success = client.delete_memory(arguments["memory_id"])

            if success:
                self.send_result_notification(
                    status="SUCCESS",
                    result={
                        "deleted": True
                    }
                )
            else:
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Failed to delete memory {arguments['memory_id']}"
                )

        except Exception as e:
            logger.error(f"Failed to delete memory: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to delete memory: {str(e)}"
            )


# Export memory tools
MEMORY_TOOLS = [
    CreateMemoryTool,
    SearchMemoryTool,
    GetMemoryTool,
    UpdateMemoryTool,
    DeleteMemoryTool
]
