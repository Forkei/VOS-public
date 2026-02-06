"""
Documents router for VOS API Gateway.

Handles document management for efficient data piping between agents.
Documents are lightweight reference-based abstractions for data movement.
"""

import logging
import uuid
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from pathlib import Path

from fastapi import APIRouter, HTTPException, Depends, Query, UploadFile, File, Form
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


# Document storage threshold - content larger than this goes to GCS
CONTENT_SIZE_THRESHOLD = 100 * 1024  # 100KB


class DocumentCreate(BaseModel):
    """Schema for creating a document."""
    title: str
    content: str
    content_type: str = "text/plain"
    tags: Optional[List[str]] = None
    session_id: Optional[str] = None
    source_type: Optional[str] = "manual"  # 'manual', 'agent', 'tool_result', 'note_export'
    source_agent_id: Optional[str] = None
    source_tool: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class DocumentResponse(BaseModel):
    """Schema for document response."""
    document_id: str
    title: Optional[str] = None
    content_type: str
    file_size_bytes: Optional[int] = None
    tags: Optional[List[str]] = None
    session_id: Optional[str] = None
    source_type: Optional[str] = None
    source_agent_id: Optional[str] = None
    creator_agent_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class DocumentWithContent(DocumentResponse):
    """Schema for document with content."""
    content: Optional[str] = None


class DocumentListResponse(BaseModel):
    """Schema for list of documents."""
    documents: List[DocumentResponse]
    total: int
    limit: int
    offset: int


def get_db():
    """Get database client from main application context."""
    from app.main import db_client
    if not db_client:
        raise RuntimeError("Database client not initialized")
    return db_client


def generate_document_id() -> str:
    """Generate a unique document ID."""
    return f"doc_{uuid.uuid4().hex[:12]}"


@router.post("/docs", response_model=DocumentResponse)
async def create_document(document: DocumentCreate):
    """
    Create a new document.

    Documents are lightweight references for efficient data piping between agents.
    Use this to package tool results, notes, or any content that needs to be
    shared between agents without repeatedly passing large text blocks.

    Args:
        document: Document creation data

    Returns:
        DocumentResponse with document_id for referencing

    Example:
        Create a document from search results:
        ```
        POST /api/v1/documents
        {
            "title": "Search results: Python tutorials",
            "content": "[...search results JSON...]",
            "content_type": "application/json",
            "source_type": "tool_result",
            "source_tool": "web_search"
        }
        ```
    """
    try:
        db = get_db()

        document_id = generate_document_id()
        content = document.content
        file_size = len(content.encode('utf-8'))

        # For now, store content inline (GCS integration can be added later)
        # TODO: If content > CONTENT_SIZE_THRESHOLD, store in GCS

        insert_query = """
        INSERT INTO documents (
            document_id, title, content, content_type, file_size_bytes,
            tags, session_id, source_type, source_agent_id, source_tool,
            creator_agent_id, metadata
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, created_at;
        """

        result = db.execute_query(
            insert_query,
            (
                document_id,
                document.title,
                content,
                document.content_type,
                file_size,
                document.tags,
                document.session_id,
                document.source_type,
                document.source_agent_id,
                document.source_tool,
                document.source_agent_id,  # creator = source agent
                json.dumps(document.metadata) if document.metadata else None
            )
        )

        created_at = result[0][1] if result else datetime.utcnow()

        logger.info(f"Created document: {document_id} ({document.title})")

        return DocumentResponse(
            document_id=document_id,
            title=document.title,
            content_type=document.content_type,
            file_size_bytes=file_size,
            tags=document.tags,
            session_id=document.session_id,
            source_type=document.source_type,
            source_agent_id=document.source_agent_id,
            creator_agent_id=document.source_agent_id,
            created_at=created_at.isoformat()
        )

    except Exception as e:
        logger.error(f"Error creating document: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create document: {e}")


@router.get("/docs", response_model=DocumentListResponse)
async def list_documents(
    session_id: Optional[str] = Query(None, description="Filter by session ID"),
    source_agent_id: Optional[str] = Query(None, description="Filter by source agent"),
    source_type: Optional[str] = Query(None, description="Filter by source type"),
    tags: Optional[str] = Query(None, description="Filter by tags (comma-separated)"),
    limit: int = Query(50, ge=1, le=200, description="Maximum documents to return"),
    offset: int = Query(0, ge=0, description="Number of documents to skip")
):
    """
    List documents with optional filters.

    Args:
        session_id: Filter by session ID
        source_agent_id: Filter by source agent
        source_type: Filter by source type ('manual', 'agent', 'tool_result', 'note_export')
        tags: Filter by tags (comma-separated)
        limit: Maximum documents to return
        offset: Number of documents to skip

    Returns:
        DocumentListResponse with paginated results
    """
    try:
        db = get_db()

        # Build query with optional filters
        conditions = []
        params = []

        if session_id:
            conditions.append("session_id = %s")
            params.append(session_id)

        if source_agent_id:
            conditions.append("source_agent_id = %s")
            params.append(source_agent_id)

        if source_type:
            conditions.append("source_type = %s")
            params.append(source_type)

        if tags:
            tag_list = [t.strip() for t in tags.split(',')]
            conditions.append("tags && %s")
            params.append(tag_list)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        # Get documents
        query = f"""
        SELECT document_id, title, content_type, file_size_bytes, tags,
               session_id, source_type, source_agent_id, creator_agent_id,
               created_at, updated_at
        FROM documents
        WHERE {where_clause}
        ORDER BY created_at DESC
        LIMIT %s OFFSET %s;
        """

        params.extend([limit, offset])
        result = db.execute_query(query, tuple(params))

        documents = []
        for row in result or []:
            documents.append(DocumentResponse(
                document_id=row[0],
                title=row[1],
                content_type=row[2],
                file_size_bytes=row[3],
                tags=row[4],
                session_id=row[5],
                source_type=row[6],
                source_agent_id=row[7],
                creator_agent_id=row[8],
                created_at=row[9].isoformat() if row[9] else None,
                updated_at=row[10].isoformat() if row[10] else None
            ))

        # Get total count
        count_query = f"""
        SELECT COUNT(*) FROM documents WHERE {where_clause};
        """
        count_result = db.execute_query(count_query, tuple(params[:-2]))
        total = count_result[0][0] if count_result else 0

        return DocumentListResponse(
            documents=documents,
            total=total,
            limit=limit,
            offset=offset
        )

    except Exception as e:
        logger.error(f"Error listing documents: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list documents: {e}")


@router.get("/docs/{document_id}", response_model=DocumentWithContent)
async def get_document(document_id: str):
    """
    Get a document by ID with its content.

    Args:
        document_id: Document ID (e.g., doc_abc123...)

    Returns:
        DocumentWithContent including the full content
    """
    try:
        db = get_db()

        query = """
        SELECT document_id, title, content, content_type, file_size_bytes, tags,
               session_id, source_type, source_agent_id, creator_agent_id,
               created_at, updated_at
        FROM documents
        WHERE document_id = %s;
        """

        result = db.execute_query(query, (document_id,))

        if not result:
            raise HTTPException(status_code=404, detail="Document not found")

        row = result[0]

        return DocumentWithContent(
            document_id=row[0],
            title=row[1],
            content=row[2],
            content_type=row[3],
            file_size_bytes=row[4],
            tags=row[5],
            session_id=row[6],
            source_type=row[7],
            source_agent_id=row[8],
            creator_agent_id=row[9],
            created_at=row[10].isoformat() if row[10] else None,
            updated_at=row[11].isoformat() if row[11] else None
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting document: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get document: {e}")


@router.get("/docs/{document_id}/content")
async def get_document_content(document_id: str):
    """
    Get just the raw content of a document.

    Useful for piping document content to other systems without metadata.

    Args:
        document_id: Document ID

    Returns:
        Raw content string with appropriate content-type
    """
    try:
        db = get_db()

        query = """
        SELECT content, content_type FROM documents WHERE document_id = %s;
        """

        result = db.execute_query(query, (document_id,))

        if not result:
            raise HTTPException(status_code=404, detail="Document not found")

        content, content_type = result[0]

        from fastapi.responses import Response
        return Response(
            content=content or "",
            media_type=content_type or "text/plain"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting document content: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get document content: {e}")


@router.delete("/docs/{document_id}")
async def delete_document(document_id: str):
    """
    Delete a document.

    Args:
        document_id: Document ID to delete

    Returns:
        Success message
    """
    try:
        db = get_db()

        # Check if exists
        check_query = "SELECT id FROM documents WHERE document_id = %s;"
        result = db.execute_query(check_query, (document_id,))

        if not result:
            raise HTTPException(status_code=404, detail="Document not found")

        # Delete
        delete_query = "DELETE FROM documents WHERE document_id = %s;"
        db.execute_query(delete_query, (document_id,))

        logger.info(f"Deleted document: {document_id}")

        return {"status": "success", "message": f"Document {document_id} deleted"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting document: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete document: {e}")


@router.get("/sessions/{session_id}/docs", response_model=DocumentListResponse)
async def get_session_documents(
    session_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0)
):
    """
    Get all documents for a session.

    Convenience endpoint to list all documents attached to a conversation session.

    Args:
        session_id: Session ID
        limit: Maximum documents to return
        offset: Number of documents to skip

    Returns:
        DocumentListResponse with session's documents
    """
    return await list_documents(
        session_id=session_id,
        limit=limit,
        offset=offset
    )


# Utility functions for agent tools

async def create_document_from_tool(
    title: str,
    content: str,
    content_type: str = "text/plain",
    source_agent_id: str = None,
    source_tool: str = None,
    session_id: str = None,
    tags: List[str] = None
) -> str:
    """
    Create a document from a tool result.

    This is a utility function for agent tools to easily create documents.

    Args:
        title: Document title
        content: Document content
        content_type: MIME type of content
        source_agent_id: ID of the agent creating the document
        source_tool: Name of the tool creating the document
        session_id: Optional session to attach to
        tags: Optional tags

    Returns:
        document_id string for referencing
    """
    doc = DocumentCreate(
        title=title,
        content=content,
        content_type=content_type,
        source_type="tool_result",
        source_agent_id=source_agent_id,
        source_tool=source_tool,
        session_id=session_id,
        tags=tags
    )

    response = await create_document(doc)
    return response.document_id


async def get_document_content_by_id(document_id: str) -> Optional[str]:
    """
    Get document content by ID.

    Utility function for agent tools to read documents.

    Args:
        document_id: Document ID

    Returns:
        Document content string or None if not found
    """
    try:
        db = get_db()

        query = "SELECT content FROM documents WHERE document_id = %s;"
        result = db.execute_query(query, (document_id,))

        if result:
            return result[0][0]
        return None

    except Exception as e:
        logger.error(f"Error getting document content: {e}")
        return None
