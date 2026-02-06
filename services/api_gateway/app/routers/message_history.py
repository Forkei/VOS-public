from datetime import datetime
from typing import List, Optional, Dict, Any
from uuid import UUID
import logging

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field
from enum import Enum

from app.database import DatabaseClient
from app.config import Settings

logger = logging.getLogger(__name__)
router = APIRouter()

# ================================================
# Pydantic Schemas
# ================================================

class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"

class AgentStatus(str, Enum):
    ACTIVE = "active"
    SLEEPING = "sleeping"
    OFF = "off"

class ProcessingState(str, Enum):
    IDLE = "idle"
    THINKING = "thinking"
    EXECUTING_TOOLS = "executing_tools"

class MessageSchema(BaseModel):
    """Schema for a single message"""
    role: MessageRole
    content: Dict[str, Any]  # JSON content
    documents: List[str] = Field(default_factory=list)  # Document IDs
    timestamp: Optional[str] = None  # ISO 8601 timestamp

class TranscriptSubmission(BaseModel):
    """Schema for submitting full conversation transcript"""
    agent_id: str
    messages: List[MessageSchema]

class MessageAppend(BaseModel):
    """Schema for appending a single message"""
    agent_id: str
    message: MessageSchema

class TranscriptResponse(BaseModel):
    """Response for transcript operations"""
    status: str
    agent_id: str
    message_count: int
    timestamp: datetime

class SystemPromptUpdate(BaseModel):
    """Schema for updating the system message in transcript"""
    content: str

class SystemPromptResponse(BaseModel):
    """Response for system prompt operations"""
    status: str
    agent_id: str
    updated: bool
    timestamp: datetime

class MessageHistoryResponse(BaseModel):
    """Response for retrieving message history"""
    agent_id: str
    messages: List[MessageSchema]
    total_messages: int
    timestamp: datetime

class DocumentCreate(BaseModel):
    """Schema for creating a document"""
    creator_agent_id: str
    title: Optional[str] = None
    content: str
    mime_type: str = "text/plain"
    is_shared: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)

class DocumentResponse(BaseModel):
    """Response for document operations"""
    document_id: UUID
    creator_agent_id: str
    title: Optional[str]
    content: str
    mime_type: str
    is_shared: bool
    metadata: Dict[str, Any]
    created_at: datetime

class AgentStateUpdate(BaseModel):
    """Schema for updating agent status"""
    status: AgentStatus

class ProcessingStateUpdate(BaseModel):
    """Schema for updating agent processing state"""
    processing_state: ProcessingState

class AgentStateResponse(BaseModel):
    """Response for agent state"""
    agent_id: str
    status: AgentStatus
    processing_state: ProcessingState
    total_messages: int
    last_updated: datetime

# ================================================
# Database Dependency
# ================================================

def get_db():
    """Get database client from main application context"""
    from app.main import db_client
    if not db_client:
        raise RuntimeError("Database client not initialized")
    return db_client

# ================================================
# Transcript Management Endpoints
# ================================================

@router.post("/transcript/submit", response_model=TranscriptResponse)
async def submit_transcript(submission: TranscriptSubmission, db: DatabaseClient = Depends(get_db)):
    """
    Submit a complete conversation transcript for an agent.
    This replaces the entire message history for the agent.
    """
    try:
        import json as json_lib
        from psycopg2.extras import Json
        
        # Convert messages to PostgreSQL array of composite types
        messages_array = []
        for msg in submission.messages:
            # Properly format documents array
            docs_array = '{' + ','.join(f'"{d}"' for d in msg.documents) + '}'
            # Escape JSON content properly
            json_content = json_lib.dumps(msg.content).replace("'", "''")
            # Create composite type tuple
            messages_array.append(
                f"ROW('{msg.role.value}'::message_role, '{json_content}'::jsonb, '{docs_array}'::text[])"
            )
        
        # Build the array expression
        messages_expression = f"ARRAY[{','.join(messages_array)}]::message[]"
        
        # Upsert message history using raw SQL with proper casting
        query = f"""
        INSERT INTO message_history (agent_id, messages)
        VALUES (%s, {messages_expression})
        ON CONFLICT (agent_id) 
        DO UPDATE SET messages = EXCLUDED.messages, timestamp = NOW()
        RETURNING timestamp;
        """
        
        result = db.execute_query(query, (submission.agent_id,))
        timestamp = result[0][0] if result else datetime.now()
        
        # Update agent state
        agent_query = """
        INSERT INTO agent_state (agent_id, total_messages, status)
        VALUES (%s, %s, 'active')
        ON CONFLICT (agent_id)
        DO UPDATE SET 
            total_messages = %s,
            last_updated = NOW(),
            status = 'active';
        """
        
        db.execute_query(agent_query, (submission.agent_id, len(submission.messages), len(submission.messages)))
        
        return TranscriptResponse(
            status="success",
            agent_id=submission.agent_id,
            message_count=len(submission.messages),
            timestamp=timestamp
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to submit transcript: {str(e)}")

@router.post("/transcript/append", response_model=TranscriptResponse)
async def append_message(append_data: MessageAppend, db: DatabaseClient = Depends(get_db)):
    """
    Append a single message to an agent's message history.
    Creates message history if it doesn't exist.
    """
    try:
        import json as json_lib

        # Insert message into individual columns
        query = """
        INSERT INTO message_history (agent_id, role, content, metadata, message_type, correlation_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING timestamp;
        """

        role_val = append_data.message.role.value
        content_json = json_lib.dumps(append_data.message.content)

        # Extract metadata if available
        metadata = {}
        if hasattr(append_data.message, 'metadata'):
            metadata = append_data.message.metadata or {}

        # Use documents as part of metadata if they exist
        if append_data.message.documents:
            metadata['documents'] = append_data.message.documents

        result = db.execute_query(query, (
            append_data.agent_id,
            role_val,
            content_json,
            json_lib.dumps(metadata),
            'standard',  # message_type
            None  # correlation_id
        ))

        timestamp = result[0][0] if result else datetime.now()

        # Count total messages for this agent
        count_query = """
        SELECT COUNT(*) FROM message_history WHERE agent_id = %s;
        """
        count_result = db.execute_query(count_query, (append_data.agent_id,))
        total_messages = count_result[0][0] if count_result else 1

        # Update agent state
        agent_query = """
        INSERT INTO agent_state (agent_id, total_messages, status)
        VALUES (%s, %s, 'active')
        ON CONFLICT (agent_id)
        DO UPDATE SET
            total_messages = %s,
            last_updated = NOW(),
            status = 'active';
        """

        db.execute_query(agent_query, (append_data.agent_id, total_messages, total_messages))

        return TranscriptResponse(
            status="success",
            agent_id=append_data.agent_id,
            message_count=total_messages,
            timestamp=timestamp
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to append message: {str(e)}")


@router.put("/transcript/{agent_id}/system-prompt", response_model=SystemPromptResponse)
async def update_system_prompt(
    agent_id: str,
    update: SystemPromptUpdate,
    db: DatabaseClient = Depends(get_db)
):
    """
    Update the system message in an agent's transcript.

    This updates the content of the existing system message without
    affecting the rest of the conversation history.
    """
    try:
        import json as json_lib

        # Update the system message content
        # The system message is identified by role = 'system'
        # Keep original timestamp so system message stays first in transcript
        query = """
        UPDATE message_history
        SET content = %s
        WHERE agent_id = %s AND role = 'system'
        RETURNING timestamp;
        """

        # Wrap content in the expected format
        content_json = json_lib.dumps({"text": update.content})

        result = db.execute_query(query, (content_json, agent_id))

        if not result:
            # No system message exists, create one with earliest possible timestamp
            # so it appears first in the transcript
            insert_query = """
            INSERT INTO message_history (agent_id, role, content, metadata, message_type, timestamp)
            VALUES (%s, 'system', %s, '{}', 'system_prompt', '1970-01-01 00:00:00')
            RETURNING timestamp;
            """
            result = db.execute_query(insert_query, (agent_id, content_json))
            updated = False
        else:
            updated = True

        timestamp = result[0][0] if result else datetime.now()

        logger.info(f"Updated system prompt for {agent_id}")

        return SystemPromptResponse(
            status="success",
            agent_id=agent_id,
            updated=updated,
            timestamp=timestamp
        )

    except Exception as e:
        logger.error(f"Failed to update system prompt: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to update system prompt: {str(e)}")


@router.get("/transcript/{agent_id}", response_model=MessageHistoryResponse)
async def get_transcript(
    agent_id: str,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: DatabaseClient = Depends(get_db)
):
    """
    Retrieve message history for an agent with pagination.
    """
    try:
        # Get messages from database
        # Order by: system messages first, then by timestamp
        # This ensures system prompt is always first regardless of its timestamp
        query = """
        SELECT role, content, metadata, timestamp, message_type, correlation_id
        FROM message_history
        WHERE agent_id = %s
        ORDER BY (role = 'system') DESC, timestamp ASC
        LIMIT %s OFFSET %s;
        """

        result = db.execute_query(query, (agent_id, limit, offset))

        if not result:
            # Return empty history if agent doesn't exist yet
            return MessageHistoryResponse(
                agent_id=agent_id,
                messages=[],
                total_messages=0,
                timestamp=datetime.now()
            )

        # Process individual message rows
        messages = []
        latest_timestamp = datetime.now()

        for row in result:
            role, content, metadata, timestamp, message_type, correlation_id = row

            # Convert role string to MessageRole enum
            try:
                message_role = MessageRole(role)
            except ValueError:
                # Default to user if invalid role
                message_role = MessageRole.USER

            # Parse content (it might be JSON string, dict, or plain text)
            # IMPORTANT: Content must always be a dict for MessageSchema validation
            try:
                if isinstance(content, str):
                    # Try to parse as JSON first
                    try:
                        import json as json_lib
                        parsed_content = json_lib.loads(content)
                        # Ensure parsed content is a dict, not a list or other type
                        if isinstance(parsed_content, dict):
                            content_dict = parsed_content
                        else:
                            # Wrap non-dict types (list, str, int, etc.) in a dict
                            content_dict = {"value": parsed_content}
                    except (json_lib.JSONDecodeError, ValueError):
                        # Not valid JSON, treat as plain text
                        content_dict = {"text": content}
                else:
                    # Ensure non-string content is also a dict
                    if isinstance(content, dict):
                        content_dict = content
                    else:
                        # Wrap non-dict types in a dict
                        content_dict = {"value": content}
            except:
                content_dict = {"text": str(content)}

            # Format timestamp as ISO 8601 string
            timestamp_str = timestamp.isoformat() if timestamp else None

            messages.append(MessageSchema(
                role=message_role,
                content=content_dict,
                documents=[],  # Can be extended if needed
                timestamp=timestamp_str
            ))

            if timestamp:
                latest_timestamp = timestamp

        return MessageHistoryResponse(
            agent_id=agent_id,
            messages=messages,
            total_messages=len(messages),
            timestamp=latest_timestamp
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve transcript: {str(e)}")

@router.delete("/transcript/{agent_id}")
async def delete_transcript(
    agent_id: str,
    reset_system_prompt: bool = Query(True, description="If true, deletes ALL messages including system prompt. Agent will regenerate on next message."),
    clear_notifications: bool = Query(True, description="If true, also clears pending notifications from agent's queue"),
    db: DatabaseClient = Depends(get_db)
):
    """
    Delete conversation history for an agent.

    If reset_system_prompt=true (default): Deletes ALL messages including the system prompt.
    The agent will automatically regenerate the system prompt with proper tool/agent info on its next message.

    If reset_system_prompt=false: Keeps system messages, only deletes conversation history.

    If clear_notifications=true (default): Also purges all pending notifications from the agent's RabbitMQ queue.
    This gives the agent a completely fresh start.
    """
    try:
        if reset_system_prompt:
            # Delete ALL messages including system messages
            query = """
            DELETE FROM message_history
            WHERE agent_id = %s;
            """
            logger.info(f"Deleting ALL message history for {agent_id} (including system prompt)")
        else:
            # Delete all messages EXCEPT system messages
            query = """
            DELETE FROM message_history
            WHERE agent_id = %s AND role != 'system';
            """
            logger.info(f"Deleting conversation history for {agent_id} (preserving system prompt)")

        result = db.execute_query(query, (agent_id,))
        deleted_count = result if isinstance(result, int) else 0

        # Clear pending notifications from RabbitMQ queue if requested
        notifications_cleared = 0
        if clear_notifications:
            try:
                from app.main import rabbitmq_client

                if rabbitmq_client:
                    queue_name = f"{agent_id}_queue"

                    # Purge the queue (deletes all messages)
                    try:
                        method_frame = rabbitmq_client.channel.queue_purge(queue_name)
                        notifications_cleared = method_frame.method.message_count
                        logger.info(f"✅ Purged {notifications_cleared} notifications from {queue_name}")
                    except Exception as e:
                        logger.warning(f"⚠️ Failed to purge queue {queue_name}: {e}")
                        notifications_cleared = -1  # Indicate failure
                else:
                    logger.warning("RabbitMQ client not available, cannot clear notifications")
                    notifications_cleared = -1

            except Exception as e:
                logger.error(f"Error clearing notifications: {e}")
                notifications_cleared = -1

        # Count remaining messages
        count_query = """
        SELECT COUNT(*) FROM message_history WHERE agent_id = %s;
        """
        count_result = db.execute_query(count_query, (agent_id,))
        remaining_messages = count_result[0][0] if count_result else 0

        # Update agent state to reflect remaining message count
        update_query = """
        UPDATE agent_state
        SET total_messages = %s, last_updated = NOW()
        WHERE agent_id = %s;
        """
        db.execute_query(update_query, (remaining_messages, agent_id))

        response_data = {
            "status": "success",
            "agent_id": agent_id,
            "deleted_messages": deleted_count,
            "remaining_messages": remaining_messages,
            "notifications_cleared": notifications_cleared if clear_notifications else None,
            "timestamp": datetime.now(),
            "note": "Agent will regenerate system prompt on next message" if reset_system_prompt else None
        }

        return response_data

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete transcript: {str(e)}")

# ================================================
# Document Management Endpoints
# ================================================

@router.post("/documents", response_model=DocumentResponse)
async def create_document(doc_data: DocumentCreate, db: DatabaseClient = Depends(get_db)):
    """Create a new document."""
    try:
        query = """
        INSERT INTO documents (creator_agent_id, title, content, mime_type, is_shared, metadata)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING document_id, created_at;
        """
        
        import json as json_lib
        result = db.execute_query(query, (
            doc_data.creator_agent_id,
            doc_data.title,
            doc_data.content,
            doc_data.mime_type,
            doc_data.is_shared,
            json_lib.dumps(doc_data.metadata)
        ))
        
        document_id = result[0][0]
        created_at = result[0][1]
        
        return DocumentResponse(
            document_id=document_id,
            creator_agent_id=doc_data.creator_agent_id,
            title=doc_data.title,
            content=doc_data.content,
            mime_type=doc_data.mime_type,
            is_shared=doc_data.is_shared,
            metadata=doc_data.metadata,
            created_at=created_at
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create document: {str(e)}")

@router.get("/documents/{document_id}", response_model=DocumentResponse)
async def get_document(document_id: UUID, db: DatabaseClient = Depends(get_db)):
    """
    Retrieve a document by ID.
    Works regardless of is_shared status - any agent with the ID can access it.
    """
    try:
        query = """
        SELECT document_id, creator_agent_id, title, content, mime_type, is_shared, metadata, created_at
        FROM documents 
        WHERE document_id = %s;
        """
        
        result = db.execute_query(query, (str(document_id),))
        
        if not result:
            raise HTTPException(status_code=404, detail="Document not found")
        
        row = result[0]
        return DocumentResponse(
            document_id=row[0],
            creator_agent_id=row[1],
            title=row[2],
            content=row[3],
            mime_type=row[4],
            is_shared=row[5],
            metadata=row[6],
            created_at=row[7]
        )
        
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"Failed to retrieve document: {str(e)}")

@router.get("/documents/by-agent/{agent_id}", response_model=List[DocumentResponse])
async def get_documents_by_agent(
    agent_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: DatabaseClient = Depends(get_db)
):
    """List documents created by an agent."""
    try:
        query = """
        SELECT document_id, creator_agent_id, title, content, mime_type, is_shared, metadata, created_at
        FROM documents 
        WHERE creator_agent_id = %s
        ORDER BY created_at DESC
        LIMIT %s OFFSET %s;
        """
        
        result = db.execute_query(query, (agent_id, limit, offset))
        
        documents = []
        for row in result:
            documents.append(DocumentResponse(
                document_id=row[0],
                creator_agent_id=row[1],
                title=row[2],
                content=row[3],
                mime_type=row[4],
                is_shared=row[5],
                metadata=row[6],
                created_at=row[7]
            ))
        
        return documents
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve documents: {str(e)}")

# ================================================
# Agent State Endpoints
# ================================================

@router.get("/agents/{agent_id}/status", response_model=AgentStateResponse)
async def get_agent_status(agent_id: str, db: DatabaseClient = Depends(get_db)):
    """Get current agent status (alias for /state endpoint)."""
    return await get_agent_state(agent_id, db)

@router.put("/agents/{agent_id}/status", response_model=AgentStateResponse)
async def update_agent_status(
    agent_id: str,
    status_update: AgentStateUpdate,
    db: DatabaseClient = Depends(get_db)
):
    """Update agent status."""
    try:
        query = """
        INSERT INTO agent_state (agent_id, status)
        VALUES (%s, %s)
        ON CONFLICT (agent_id)
        DO UPDATE SET
            status = EXCLUDED.status,
            last_updated = NOW()
        RETURNING agent_id, status, processing_state, total_messages, last_updated;
        """

        result = db.execute_query(query, (agent_id, status_update.status.value))

        if not result:
            raise HTTPException(status_code=500, detail="Failed to update agent status")

        row = result[0]

        # Publish agent_status notification to frontend
        try:
            from app.notification_publisher import get_notification_publisher

            publisher = get_notification_publisher()
            if publisher:
                success = publisher.publish_agent_status(
                    agent_id=row[0],
                    status=row[1],
                    processing_state=row[2],
                    session_id=None  # Broadcast to all sessions
                )

                if success:
                    logger.info(f"✅ Published agent_status notification for {agent_id}: {row[1]}")
                else:
                    logger.warning(f"⚠️ Failed to publish agent_status notification for {agent_id}")

        except Exception as e:
            # Don't fail the request if notification publishing fails
            logger.error(f"Error publishing agent_status notification: {e}")

        return AgentStateResponse(
            agent_id=row[0],
            status=AgentStatus(row[1]),
            processing_state=ProcessingState(row[2]),
            total_messages=row[3],
            last_updated=row[4]
        )

    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"Failed to update agent status: {str(e)}")

@router.get("/agents/{agent_id}/state", response_model=AgentStateResponse)
async def get_agent_state(agent_id: str, db: DatabaseClient = Depends(get_db)):
    """Get current agent state and statistics."""
    try:
        query = """
        SELECT agent_id, status, processing_state, total_messages, last_updated
        FROM agent_state
        WHERE agent_id = %s;
        """

        result = db.execute_query(query, (agent_id,))

        if not result:
            raise HTTPException(status_code=404, detail="Agent not found")

        row = result[0]
        return AgentStateResponse(
            agent_id=row[0],
            status=AgentStatus(row[1]),
            processing_state=ProcessingState(row[2]),
            total_messages=row[3],
            last_updated=row[4]
        )
        
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"Failed to retrieve agent state: {str(e)}")

@router.put("/agents/{agent_id}/processing-state", response_model=AgentStateResponse)
async def update_agent_processing_state(
    agent_id: str,
    processing_state_update: ProcessingStateUpdate,
    db: DatabaseClient = Depends(get_db)
):
    """Update agent processing state (idle, thinking, executing_tools)."""
    try:
        query = """
        INSERT INTO agent_state (agent_id, processing_state)
        VALUES (%s, %s)
        ON CONFLICT (agent_id)
        DO UPDATE SET
            processing_state = EXCLUDED.processing_state,
            last_updated = NOW()
        RETURNING agent_id, status, processing_state, total_messages, last_updated;
        """

        result = db.execute_query(query, (agent_id, processing_state_update.processing_state.value))

        if not result:
            raise HTTPException(status_code=500, detail="Failed to update agent processing state")

        row = result[0]

        # Publish agent_status notification to frontend
        try:
            from app.notification_publisher import get_notification_publisher

            publisher = get_notification_publisher()
            if publisher:
                success = publisher.publish_agent_status(
                    agent_id=row[0],
                    status=row[1],
                    processing_state=row[2],
                    session_id=None  # Broadcast to all sessions
                )

                if success:
                    logger.info(f"✅ Published agent_status notification for {agent_id}: {row[2]}")
                else:
                    logger.warning(f"⚠️ Failed to publish agent_status notification for {agent_id}")

        except Exception as e:
            # Don't fail the request if notification publishing fails
            logger.error(f"Error publishing agent_status notification: {e}")

        return AgentStateResponse(
            agent_id=row[0],
            status=AgentStatus(row[1]),
            processing_state=ProcessingState(row[2]),
            total_messages=row[3],
            last_updated=row[4]
        )

    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"Failed to update agent processing state: {str(e)}")

@router.get("/agents/{agent_id}/processing-state")
async def get_agent_processing_state(agent_id: str, db: DatabaseClient = Depends(get_db)):
    """Get just the agent's current processing state."""
    try:
        query = """
        SELECT processing_state
        FROM agent_state
        WHERE agent_id = %s;
        """

        result = db.execute_query(query, (agent_id,))

        if not result:
            # Create agent with default state if it doesn't exist
            create_query = """
            INSERT INTO agent_state (agent_id, processing_state, status)
            VALUES (%s, 'idle', 'off')
            RETURNING processing_state;
            """
            result = db.execute_query(create_query, (agent_id,))

        processing_state = result[0][0]
        return {"agent_id": agent_id, "processing_state": processing_state}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get agent processing state: {str(e)}")

@router.get("/agents/{agent_id}/metadata")
async def get_agent_metadata(agent_id: str, db: DatabaseClient = Depends(get_db)):
    """Get agent metadata (JSONB field for storing arbitrary agent state)."""
    try:
        query = """
        SELECT metadata
        FROM agent_state
        WHERE agent_id = %s;
        """

        result = db.execute_query(query, (agent_id,))

        if not result:
            raise HTTPException(status_code=404, detail="Agent not found")

        metadata = result[0][0] or {}
        return {"agent_id": agent_id, "metadata": metadata}

    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"Failed to get agent metadata: {str(e)}")

@router.put("/agents/{agent_id}/metadata")
async def update_agent_metadata(agent_id: str, metadata: Dict[str, Any], db: DatabaseClient = Depends(get_db)):
    """Update agent metadata (JSONB field for storing arbitrary agent state)."""
    try:
        import json as json_lib

        query = """
        INSERT INTO agent_state (agent_id, metadata)
        VALUES (%s, %s)
        ON CONFLICT (agent_id)
        DO UPDATE SET
            metadata = EXCLUDED.metadata,
            last_updated = NOW()
        RETURNING metadata;
        """

        result = db.execute_query(query, (agent_id, json_lib.dumps(metadata)))

        if not result:
            raise HTTPException(status_code=500, detail="Failed to update agent metadata")

        updated_metadata = result[0][0]
        return {"agent_id": agent_id, "metadata": updated_metadata, "status": "success"}

    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"Failed to update agent metadata: {str(e)}")


@router.get("/agents")
async def get_all_agents(db: DatabaseClient = Depends(get_db)):
    """
    Fetch all agents from the database.

    Returns:
        List of agents with their current status and metadata.
    """
    try:
        query = """
            SELECT
                agent_id,
                status,
                processing_state,
                total_messages,
                last_updated,
                created_at,
                last_error,
                metadata
            FROM agent_state
            ORDER BY agent_id
        """

        results = db.execute_query(query)

        if not results:
            return []

        agents = []
        for row in results:
            agent = {
                "agent_id": row[0],
                "status": row[1],
                "processing_state": row[2],
                "total_messages": row[3],
                "last_updated": row[4].isoformat() if row[4] else None,
                "created_at": row[5].isoformat() if row[5] else None,
                "last_error": row[6],
                "metadata": row[7] if row[7] else {}
            }
            agents.append(agent)

        logger.info(f"Fetched {len(agents)} agents from database")
        return agents

    except Exception as e:
        logger.error(f"Error fetching agents: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch agents: {str(e)}"
        )