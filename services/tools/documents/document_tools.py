"""
Document management tools for VOS agents.

Tools for creating, reading, listing, and deleting documents.
Documents are lightweight references for efficient data piping between agents.
"""

import os
import json
import uuid
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

from vos_sdk import BaseTool

logger = logging.getLogger(__name__)


def get_db_client():
    """Get database client - import at runtime to avoid circular imports."""
    try:
        # Try to get from API Gateway context
        from app.main import db_client
        return db_client
    except ImportError:
        # Fallback: create new connection
        try:
            from vos_sdk.core.config import AgentConfig
            import psycopg2

            database_url = os.environ.get("DATABASE_URL")
            if database_url:
                conn = psycopg2.connect(database_url)
                return conn
        except Exception as e:
            logger.error(f"Could not get database client: {e}")
            return None
    return None


def generate_document_id() -> str:
    """Generate a unique document ID."""
    return f"doc_{uuid.uuid4().hex[:12]}"


class CreateDocumentTool(BaseTool):
    """
    Create a new document for efficient data sharing.

    Use this to package content (tool results, notes, generated text) into
    a document that can be easily shared with other agents or the user.
    Instead of passing large text blocks, you can create a document and
    share just the document_id.
    """

    def __init__(self):
        super().__init__(
            name="create_document",
            description="Create a document for efficient data sharing between agents"
        )
        self.database_url = os.environ.get("DATABASE_URL")

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate document creation arguments."""
        if "title" not in arguments:
            return False, "Missing required argument: 'title'"

        if "content" not in arguments:
            return False, "Missing required argument: 'content'"

        if not isinstance(arguments["title"], str) or not arguments["title"].strip():
            return False, "'title' must be a non-empty string"

        if not isinstance(arguments["content"], str):
            return False, "'content' must be a string"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "create_document",
            "description": "Create a document to package content for efficient sharing. Returns a document_id that can be passed to other agents or users without sending the full content.",
            "parameters": [
                {
                    "name": "title",
                    "type": "str",
                    "description": "Short, descriptive title for the document",
                    "required": True
                },
                {
                    "name": "content",
                    "type": "str",
                    "description": "The document content (text, JSON, etc.)",
                    "required": True
                },
                {
                    "name": "content_type",
                    "type": "str",
                    "description": "MIME type: 'text/plain', 'application/json', 'text/markdown'. Default: text/plain",
                    "required": False
                },
                {
                    "name": "tags",
                    "type": "list[str]",
                    "description": "Tags for categorization and search",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """Create a new document."""
        try:
            import psycopg2

            document_id = generate_document_id()
            title = arguments["title"]
            content = arguments["content"]
            content_type = arguments.get("content_type", "text/plain")
            tags = arguments.get("tags")
            file_size = len(content.encode('utf-8'))

            # Get session_id from context if available
            session_id = getattr(self, 'session_id', None)

            # Connect to database
            conn = psycopg2.connect(self.database_url)
            cursor = conn.cursor()

            try:
                insert_query = """
                INSERT INTO documents (
                    document_id, title, content, content_type, file_size_bytes,
                    tags, session_id, source_type, source_agent_id, creator_agent_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, created_at;
                """

                cursor.execute(
                    insert_query,
                    (
                        document_id,
                        title,
                        content,
                        content_type,
                        file_size,
                        tags,
                        session_id,
                        "agent",
                        self.agent_name,
                        self.agent_name
                    )
                )

                result = cursor.fetchone()
                conn.commit()

                logger.info(f"Created document: {document_id} ({title}) by {self.agent_name}")

                # Send success notification
                self.send_result_notification(
                    status="SUCCESS",
                    result={
                        "document_id": document_id,
                        "title": title,
                        "content_type": content_type,
                        "file_size_bytes": file_size,
                        "message": f"Document '{title}' created. Use document_id '{document_id}' to reference it."
                    }
                )

            finally:
                cursor.close()
                conn.close()

        except Exception as e:
            logger.error(f"Error creating document: {e}")
            self.send_result_notification(
                status="FAILURE",
                result={"error": str(e)}
            )


class ReadDocumentTool(BaseTool):
    """
    Read the content of a document.

    Use this to retrieve the content of a document by its ID.
    Documents may have been created by you, another agent, or uploaded by the user.
    """

    def __init__(self):
        super().__init__(
            name="read_document",
            description="Read the content of a document by its ID"
        )
        self.database_url = os.environ.get("DATABASE_URL")

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate read arguments."""
        if "document_id" not in arguments:
            return False, "Missing required argument: 'document_id'"

        if not isinstance(arguments["document_id"], str):
            return False, "'document_id' must be a string"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "read_document",
            "description": "Read the content of a document by its ID",
            "parameters": [
                {
                    "name": "document_id",
                    "type": "str",
                    "description": "The document ID (e.g., doc_abc123...)",
                    "required": True
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """Read a document."""
        try:
            import psycopg2

            document_id = arguments["document_id"]

            conn = psycopg2.connect(self.database_url)
            cursor = conn.cursor()

            try:
                query = """
                SELECT document_id, title, content, content_type, file_size_bytes,
                       tags, source_type, source_agent_id, created_at
                FROM documents
                WHERE document_id = %s;
                """

                cursor.execute(query, (document_id,))
                result = cursor.fetchone()

                if not result:
                    self.send_result_notification(
                        status="FAILURE",
                        result={"error": f"Document not found: {document_id}"}
                    )
                    return

                doc_id, title, content, content_type, file_size, tags, source_type, source_agent, created_at = result

                logger.info(f"Read document: {document_id} ({title})")

                self.send_result_notification(
                    status="SUCCESS",
                    result={
                        "document_id": doc_id,
                        "title": title,
                        "content": content,
                        "content_type": content_type,
                        "file_size_bytes": file_size,
                        "tags": tags,
                        "source_type": source_type,
                        "source_agent_id": source_agent,
                        "created_at": created_at.isoformat() if created_at else None
                    }
                )

            finally:
                cursor.close()
                conn.close()

        except Exception as e:
            logger.error(f"Error reading document: {e}")
            self.send_result_notification(
                status="FAILURE",
                result={"error": str(e)}
            )


class ListDocumentsTool(BaseTool):
    """
    List available documents.

    Use this to see what documents are available, optionally filtered by
    session, tags, or source agent.
    """

    def __init__(self):
        super().__init__(
            name="list_documents",
            description="List available documents"
        )
        self.database_url = os.environ.get("DATABASE_URL")

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate list arguments."""
        # All arguments are optional
        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "list_documents",
            "description": "List available documents with optional filters",
            "parameters": [
                {
                    "name": "session_id",
                    "type": "str",
                    "description": "Filter by session ID",
                    "required": False
                },
                {
                    "name": "source_agent_id",
                    "type": "str",
                    "description": "Filter by source agent ID",
                    "required": False
                },
                {
                    "name": "limit",
                    "type": "int",
                    "description": "Maximum number of documents to return (default: 20)",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """List documents."""
        try:
            import psycopg2

            session_id = arguments.get("session_id")
            source_agent_id = arguments.get("source_agent_id")
            limit = arguments.get("limit", 20)

            conn = psycopg2.connect(self.database_url)
            cursor = conn.cursor()

            try:
                # Build query with optional filters
                conditions = []
                params = []

                if session_id:
                    conditions.append("session_id = %s")
                    params.append(session_id)

                if source_agent_id:
                    conditions.append("source_agent_id = %s")
                    params.append(source_agent_id)

                where_clause = " AND ".join(conditions) if conditions else "1=1"

                query = f"""
                SELECT document_id, title, content_type, file_size_bytes,
                       tags, source_type, source_agent_id, created_at
                FROM documents
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT %s;
                """

                params.append(limit)
                cursor.execute(query, tuple(params))
                results = cursor.fetchall()

                documents = []
                for row in results:
                    doc_id, title, content_type, file_size, tags, source_type, source_agent, created_at = row
                    documents.append({
                        "document_id": doc_id,
                        "title": title,
                        "content_type": content_type,
                        "file_size_bytes": file_size,
                        "tags": tags,
                        "source_type": source_type,
                        "source_agent_id": source_agent,
                        "created_at": created_at.isoformat() if created_at else None
                    })

                logger.info(f"Listed {len(documents)} documents")

                self.send_result_notification(
                    status="SUCCESS",
                    result={
                        "documents": documents,
                        "count": len(documents)
                    }
                )

            finally:
                cursor.close()
                conn.close()

        except Exception as e:
            logger.error(f"Error listing documents: {e}")
            self.send_result_notification(
                status="FAILURE",
                result={"error": str(e)}
            )


class DeleteDocumentTool(BaseTool):
    """
    Delete a document.

    Use this to remove a document that is no longer needed.
    """

    def __init__(self):
        super().__init__(
            name="delete_document",
            description="Delete a document by its ID"
        )
        self.database_url = os.environ.get("DATABASE_URL")

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate delete arguments."""
        if "document_id" not in arguments:
            return False, "Missing required argument: 'document_id'"

        if not isinstance(arguments["document_id"], str):
            return False, "'document_id' must be a string"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "delete_document",
            "description": "Delete a document by its ID",
            "parameters": [
                {
                    "name": "document_id",
                    "type": "str",
                    "description": "The document ID to delete",
                    "required": True
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """Delete a document."""
        try:
            import psycopg2

            document_id = arguments["document_id"]

            conn = psycopg2.connect(self.database_url)
            cursor = conn.cursor()

            try:
                # Check if exists
                cursor.execute(
                    "SELECT title FROM documents WHERE document_id = %s;",
                    (document_id,)
                )
                result = cursor.fetchone()

                if not result:
                    self.send_result_notification(
                        status="FAILURE",
                        result={"error": f"Document not found: {document_id}"}
                    )
                    return

                title = result[0]

                # Delete
                cursor.execute(
                    "DELETE FROM documents WHERE document_id = %s;",
                    (document_id,)
                )
                conn.commit()

                logger.info(f"Deleted document: {document_id} ({title})")

                self.send_result_notification(
                    status="SUCCESS",
                    result={
                        "document_id": document_id,
                        "title": title,
                        "message": f"Document '{title}' deleted successfully"
                    }
                )

            finally:
                cursor.close()
                conn.close()

        except Exception as e:
            logger.error(f"Error deleting document: {e}")
            self.send_result_notification(
                status="FAILURE",
                result={"error": str(e)}
            )
