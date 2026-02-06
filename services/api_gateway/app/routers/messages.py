"""
Messages router for VOS API Gateway.

Handles user-facing messages from agents.
"""

import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel

from app.database import DatabaseClient
from app.schemas import ConversationMessage, ConversationHistoryResponse, SenderType
from app.utils.url_signing import generate_audio_signed_url

logger = logging.getLogger(__name__)

router = APIRouter()


class UserMessage(BaseModel):
    """Schema for messages sent to users from agents."""
    agent_id: str
    content: str
    content_type: str = "text"
    timestamp: str
    session_id: str = "user_session_default"  # Default session ID if not provided
    voice_message_id: Optional[int] = None  # Optional voice message ID for TTS responses
    attachment_ids: Optional[List[str]] = None  # Image attachment IDs
    document_ids: Optional[List[str]] = None  # Document reference IDs


def get_db():
    """Get database client from main application context"""
    from app.main import db_client
    if not db_client:
        raise RuntimeError("Database client not initialized")
    return db_client


@router.post("/messages/user")
async def send_user_message(message: UserMessage, db: DatabaseClient = Depends(get_db)):
    """
    Receive a message from an agent to send to the user.

    Stores the agent message in the conversation_messages table.
    In a full implementation, this would also send to frontend via websockets,
    push notifications, etc.
    """
    try:
        logger.info(f"📤 Message from {message.agent_id} to user: {message.content}")

        # Store agent message in conversation_messages table
        # If voice_message_id is provided, link it and set input_mode to 'voice'
        if message.voice_message_id:
            insert_query = """
            INSERT INTO conversation_messages (session_id, sender_type, sender_id, content, metadata, input_mode, voice_message_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id, timestamp;
            """
            metadata = {
                "content_type": message.content_type,
                "original_timestamp": message.timestamp
            }
            # Include attachment_ids if provided
            if message.attachment_ids:
                metadata["attachment_ids"] = message.attachment_ids
            # Include document_ids if provided
            if message.document_ids:
                metadata["document_ids"] = message.document_ids
            result = db.execute_query(
                insert_query,
                (message.session_id, "agent", message.agent_id, message.content, json.dumps(metadata), "voice", message.voice_message_id)
            )
        else:
            insert_query = """
            INSERT INTO conversation_messages (session_id, sender_type, sender_id, content, metadata)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id, timestamp;
            """
            metadata = {
                "content_type": message.content_type,
                "original_timestamp": message.timestamp
            }
            # Include attachment_ids if provided
            if message.attachment_ids:
                metadata["attachment_ids"] = message.attachment_ids
                logger.info(f"Storing {len(message.attachment_ids)} attachment_ids in message metadata")
            # Include document_ids if provided
            if message.document_ids:
                metadata["document_ids"] = message.document_ids
                logger.info(f"Storing {len(message.document_ids)} document_ids in message metadata")
            result = db.execute_query(
                insert_query,
                (message.session_id, "agent", message.agent_id, message.content, json.dumps(metadata))
            )

        message_id = result[0][0] if result else None
        message_timestamp = result[0][1] if result else datetime.now()

        logger.info(f"Stored agent message {message_id} in conversation_messages for session {message.session_id}")

        # Query voice_messages to get audio metadata (if this is a voice response)
        audio_metadata = {
            "input_mode": "text",
            "voice_message_id": None,
            "audio_file_path": None,
            "audio_url": None,
            "audio_duration_ms": None
        }

        try:
            # Check if this message has associated voice message
            voice_query = """
            SELECT vm.id, vm.audio_file_path, vm.duration_ms
            FROM conversation_messages cm
            LEFT JOIN voice_messages vm ON cm.voice_message_id = vm.id
            WHERE cm.id = %s;
            """
            voice_result = db.execute_query(voice_query, (message_id,))

            if voice_result and voice_result[0][0]:  # Has voice message
                voice_msg_id, audio_path, duration_ms = voice_result[0]
                audio_metadata["input_mode"] = "voice"
                audio_metadata["voice_message_id"] = voice_msg_id
                audio_metadata["audio_file_path"] = audio_path
                audio_metadata["audio_duration_ms"] = duration_ms

                # Generate signed URL if audio file exists
                if audio_path:
                    audio_metadata["audio_url"] = generate_audio_signed_url(audio_path)
                    logger.debug(f"Generated signed URL for message {message_id}: {audio_path}")

        except Exception as e:
            logger.error(f"Error querying voice metadata: {e}")
            # Continue without audio metadata

        # Publish notification to frontend_notifications exchange for real-time delivery
        try:
            from app.notification_publisher import get_notification_publisher

            publisher = get_notification_publisher()
            if publisher:
                success = publisher.publish_new_message(
                    session_id=message.session_id,
                    message_id=message_id,
                    agent_id=message.agent_id,
                    content=message.content,
                    content_type=message.content_type,
                    input_mode=audio_metadata["input_mode"],
                    voice_message_id=audio_metadata["voice_message_id"],
                    audio_file_path=audio_metadata["audio_file_path"],
                    audio_url=audio_metadata["audio_url"],
                    audio_duration_ms=audio_metadata["audio_duration_ms"],
                    attachment_ids=message.attachment_ids,
                    document_ids=message.document_ids
                )

                if success:
                    logger.info(f"✅ Published new_message notification for message {message_id} "
                              f"(input_mode={audio_metadata['input_mode']})")
                else:
                    logger.warning(f"⚠️ Failed to publish notification for message {message_id}")
            else:
                logger.warning("⚠️ Notification publisher not initialized")

        except Exception as e:
            # Don't fail the request if notification publishing fails
            logger.error(f"Error publishing notification: {e}")

        return {
            "status": "success",
            "message": "User message received and stored",
            "message_id": message_id,
            "agent_id": message.agent_id,
            "session_id": message.session_id,
            "timestamp": message_timestamp.isoformat(),
            "content_length": len(message.content)
        }

    except Exception as e:
        logger.error(f"Error processing user message: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to process user message: {str(e)}")


@router.get("/conversations/{session_id}", response_model=ConversationHistoryResponse)
async def get_conversation_history(
    session_id: str,
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of messages to return"),
    offset: int = Query(0, ge=0, description="Number of messages to skip"),
    db: DatabaseClient = Depends(get_db)
):
    """
    Retrieve conversation history for a specific session.

    Returns all messages (both user and agent) for the given session_id,
    ordered chronologically with pagination support.
    """
    try:
        # Get messages from database with voice message data
        query = """
        SELECT
            cm.id,
            cm.session_id,
            cm.sender_type,
            cm.sender_id,
            cm.content,
            cm.timestamp,
            cm.metadata,
            cm.input_mode,
            cm.voice_message_id,
            vm.audio_file_path,
            vm.duration_ms
        FROM conversation_messages cm
        LEFT JOIN voice_messages vm ON cm.voice_message_id = vm.id
        WHERE cm.session_id = %s
        ORDER BY cm.timestamp ASC
        LIMIT %s OFFSET %s;
        """

        result = db.execute_query(query, (session_id, limit, offset))

        if not result:
            # Return empty history if session doesn't exist yet
            return ConversationHistoryResponse(
                session_id=session_id,
                messages=[],
                total_messages=0,
                timestamp=datetime.now()
            )

        # Process message rows
        messages = []
        latest_timestamp = datetime.now()

        for row in result:
            msg_id, sess_id, sender_type, sender_id, content, timestamp, metadata, input_mode, voice_message_id, audio_file_path, audio_duration_ms = row

            # Convert sender_type string to SenderType enum
            try:
                sender_type_enum = SenderType(sender_type)
            except ValueError:
                # Default to user if invalid sender type
                sender_type_enum = SenderType.USER

            # Generate signed URL if audio file exists
            audio_url = None
            if audio_file_path:
                try:
                    audio_url = generate_audio_signed_url(audio_file_path)
                    logger.debug(f"Generated signed URL for message {msg_id}: {audio_file_path}")
                except Exception as e:
                    logger.error(f"Failed to generate signed URL for {audio_file_path}: {e}")

            messages.append(ConversationMessage(
                id=msg_id,
                session_id=sess_id,
                sender_type=sender_type_enum,
                sender_id=sender_id,
                content=content,
                timestamp=timestamp,
                metadata=metadata or {},
                input_mode=input_mode or 'text',
                voice_message_id=voice_message_id,
                audio_file_path=audio_file_path,
                audio_url=audio_url,
                audio_duration_ms=audio_duration_ms
            ))

            if timestamp:
                latest_timestamp = timestamp

        # Get total count for this session
        count_query = """
        SELECT COUNT(*) FROM conversation_messages WHERE session_id = %s;
        """
        count_result = db.execute_query(count_query, (session_id,))
        total_messages = count_result[0][0] if count_result else len(messages)

        logger.info(f"Retrieved {len(messages)} messages for session {session_id} (total: {total_messages})")

        return ConversationHistoryResponse(
            session_id=session_id,
            messages=messages,
            total_messages=total_messages,
            timestamp=latest_timestamp
        )

    except Exception as e:
        logger.error(f"Error retrieving conversation history: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve conversation history: {str(e)}")


@router.delete("/conversations/{session_id}")
async def delete_conversation(
    session_id: str,
    db: DatabaseClient = Depends(get_db)
):
    """
    Delete all messages for a specific conversation session.

    This will permanently remove all user and agent messages
    associated with the given session_id.
    """
    try:
        # First, count how many messages exist for this session
        count_query = """
        SELECT COUNT(*) FROM conversation_messages WHERE session_id = %s;
        """
        count_result = db.execute_query(count_query, (session_id,))
        message_count = count_result[0][0] if count_result else 0

        if message_count == 0:
            logger.info(f"No messages found for session {session_id}")
            return {
                "status": "success",
                "message": "No messages found for this session",
                "session_id": session_id,
                "deleted_count": 0,
                "timestamp": datetime.now().isoformat()
            }

        # Delete all messages for this session
        delete_query = """
        DELETE FROM conversation_messages WHERE session_id = %s;
        """
        db.execute_query(delete_query, (session_id,))

        logger.info(f"Deleted {message_count} messages for session {session_id}")

        return {
            "status": "success",
            "message": "Conversation deleted successfully",
            "session_id": session_id,
            "deleted_count": message_count,
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"Error deleting conversation: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete conversation: {str(e)}")


class UserMessageInput(BaseModel):
    """Schema for user messages with voice support."""
    session_id: str
    content: str
    user_id: str = "default_user"
    input_mode: str = "text"  # "text" or "voice"
    voice_metadata: Dict[str, Any] = {}


@router.post("/messages/from-user")
async def receive_user_message(
    message: UserMessageInput,
    db: DatabaseClient = Depends(get_db)
):
    """
    Store a message sent by the user.

    This should be called when a user sends a message through the frontend,
    before sending it to the agent via RabbitMQ.

    Supports both text and voice input modes with metadata.
    """
    try:
        logger.info(f"📥 User message ({message.input_mode}) in session {message.session_id}: {message.content}")

        # Store user message in conversation_messages table with voice support
        insert_query = """
        INSERT INTO conversation_messages (
            session_id, sender_type, sender_id, content,
            input_mode, voice_metadata, metadata
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id, timestamp;
        """

        metadata = {
            "content_type": "voice_transcript" if message.input_mode == "voice" else "text"
        }

        result = db.execute_query(
            insert_query,
            (
                message.session_id,
                "user",
                message.user_id,
                message.content,
                message.input_mode,
                json.dumps(message.voice_metadata),
                json.dumps(metadata)
            )
        )

        message_id = result[0][0] if result else None
        message_timestamp = result[0][1] if result else datetime.now()

        logger.info(f"Stored user message {message_id} in conversation_messages for session {message.session_id}")

        return {
            "status": "success",
            "message": "User message stored",
            "message_id": message_id,
            "session_id": message.session_id,
            "timestamp": message_timestamp.isoformat(),
            "input_mode": message.input_mode
        }

    except Exception as e:
        logger.error(f"Error storing user message: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to store user message: {str(e)}")


@router.get("/conversations")
async def list_conversations(
    limit: int = Query(50, ge=1, le=200, description="Maximum number of sessions to return"),
    offset: int = Query(0, ge=0, description="Number of sessions to skip"),
    db: DatabaseClient = Depends(get_db)
):
    """
    List all conversation sessions with metadata.

    Returns a list of all active conversation sessions, ordered by most recent activity.
    Includes message count, last message time, and a preview of the last message.
    """
    try:
        # Query to get all sessions with their metadata
        # Using a subquery to get the last message for each session
        query = """
        WITH session_stats AS (
            SELECT
                session_id,
                COUNT(*) as message_count,
                MAX(timestamp) as last_message_time,
                MIN(timestamp) as first_message_time
            FROM conversation_messages
            GROUP BY session_id
        ),
        last_messages AS (
            SELECT DISTINCT ON (session_id)
                session_id,
                content as last_message_preview,
                sender_type,
                sender_id
            FROM conversation_messages
            ORDER BY session_id, timestamp DESC
        )
        SELECT
            ss.session_id,
            ss.message_count,
            ss.last_message_time,
            ss.first_message_time,
            lm.last_message_preview,
            lm.sender_type,
            lm.sender_id
        FROM session_stats ss
        LEFT JOIN last_messages lm ON ss.session_id = lm.session_id
        ORDER BY ss.last_message_time DESC
        LIMIT %s OFFSET %s;
        """

        result = db.execute_query(query, (limit, offset))

        # Handle case where result is not a list (should not happen with SELECT, but defensive)
        if not isinstance(result, list):
            logger.warning(f"Unexpected result type from query: {type(result)}")
            return {
                "sessions": [],
                "total_sessions": 0,
                "limit": limit,
                "offset": offset,
                "timestamp": datetime.now().isoformat()
            }

        if not result:
            return {
                "sessions": [],
                "total_sessions": 0,
                "limit": limit,
                "offset": offset,
                "timestamp": datetime.now().isoformat()
            }

        # Process results
        sessions = []
        for row in result:
            session_id, message_count, last_message_time, first_message_time, last_message_preview, sender_type, sender_id = row

            # Truncate preview to 100 characters
            preview = last_message_preview[:100] + "..." if len(last_message_preview) > 100 else last_message_preview

            sessions.append({
                "session_id": session_id,
                "message_count": message_count,
                "last_message_time": last_message_time.isoformat() if last_message_time else None,
                "first_message_time": first_message_time.isoformat() if first_message_time else None,
                "last_message_preview": preview,
                "last_message_sender_type": sender_type,
                "last_message_sender_id": sender_id
            })

        # Get total count of sessions
        count_query = """
        SELECT COUNT(DISTINCT session_id) FROM conversation_messages;
        """
        count_result = db.execute_query(count_query, ())
        total_sessions = count_result[0][0] if count_result else 0

        logger.info(f"Retrieved {len(sessions)} conversation sessions (total: {total_sessions})")

        return {
            "sessions": sessions,
            "total_sessions": total_sessions,
            "limit": limit,
            "offset": offset,
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"Error listing conversations: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list conversations: {str(e)}")