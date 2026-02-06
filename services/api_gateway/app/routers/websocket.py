"""
WebSocket router for real-time frontend notifications.

Provides WebSocket endpoints for live message streaming with JWT authentication.
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.websocket_manager import connection_manager
from app.app_interaction_manager import app_interaction_manager
from app.pending_notifications import deliver_pending_notifications, get_pending_count
from app.database import DatabaseClient
from app.middleware.auth import verify_jwt_token

logger = logging.getLogger(__name__)

router = APIRouter()
security = HTTPBearer()


def get_db():
    """Get database client from main application context"""
    from app.main import db_client
    if not db_client:
        raise RuntimeError("Database client not initialized")
    return db_client


async def handle_user_message(message_data: dict, session_id: str, user_id: str, db: DatabaseClient):
    """
    Handle incoming user message from WebSocket and route to primary_agent.

    Args:
        message_data: Message data including content, inputMode, and voiceMetadata
        session_id: Session ID for the conversation
        user_id: User identifier
        db: Database client
    """
    try:
        content = message_data.get("content", "")
        input_mode = message_data.get("inputMode", "text")
        voice_metadata = message_data.get("voiceMetadata", {})

        logger.info(f"📥 User message ({input_mode}) in session {session_id}: {content[:100]}")

        # Store user message in conversation_messages table
        insert_query = """
        INSERT INTO conversation_messages (
            session_id, sender_type, sender_id, content,
            input_mode, voice_metadata, metadata
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id, timestamp;
        """

        metadata = {
            "content_type": "voice_transcript" if input_mode == "voice" else "text"
        }

        result = db.execute_query(
            insert_query,
            (
                session_id,
                "user",
                user_id,
                content,
                input_mode,
                json.dumps(voice_metadata),
                json.dumps(metadata)
            )
        )

        message_id = result[0][0] if result else None
        logger.info(f"Stored user message {message_id} in conversation_messages")

        # Route to primary_agent via RabbitMQ
        from app.main import rabbitmq_client

        if rabbitmq_client:
            notification_payload = {
                "notification_type": "user_message",
                "session_id": session_id,
                "content": content,
                "content_type": "voice_transcript" if input_mode == "voice" else "text",
                "message_id": str(message_id) if message_id else None,
                "user_id": user_id,
                "voice_mode": input_mode == "voice"  # Flag for primary_agent to respond via voice
            }

            success = rabbitmq_client.publish_message("primary_agent_queue", notification_payload)

            if success:
                logger.info(f"✅ Routed message to primary_agent (voice_mode={input_mode == 'voice'})")
            else:
                logger.error(f"❌ Failed to route message to primary_agent")
        else:
            logger.error("❌ RabbitMQ client not initialized")

    except Exception as e:
        logger.error(f"Error handling user message: {e}")


@router.websocket("/ws/conversations/{session_id}/stream")
async def websocket_conversation_stream(
    websocket: WebSocket,
    session_id: str,
    token: Optional[str] = Query(None, description="JWT authentication token"),
    db: DatabaseClient = Depends(get_db)
):
    """
    WebSocket endpoint for real-time conversation updates.

    Provides:
    - Real-time agent message delivery
    - Agent status updates (thinking, executing tools, etc.)
    - Action status descriptions ("Searching weather...")
    - Timer/alarm notifications
    - Automatic delivery of pending notifications on connect

    Authentication:
    - Requires JWT token in query parameter: ?token=<jwt>
    - Token should contain 'sub' (username) and 'exp' (expiration)

    Args:
        websocket: WebSocket connection
        session_id: Conversation session ID
        token: JWT authentication token
        db: Database client (injected)
    """

    # Authenticate the connection
    if not token:
        logger.warning(f"WebSocket connection attempt without token for session {session_id}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Missing authentication token")
        return

    try:
        # Verify JWT token
        payload = verify_jwt_token(token)

        if not payload:
            logger.warning(f"WebSocket connection with invalid token for session {session_id}")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid authentication token")
            return

        username = payload.get("sub")
        logger.info(f"🔐 WebSocket authentication successful for user '{username}', session {session_id}")

    except Exception as e:
        logger.error(f"WebSocket authentication error: {e}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Authentication failed")
        return

    # Accept the connection
    connected = await connection_manager.connect(websocket, session_id)

    if not connected:
        logger.error(f"Failed to establish WebSocket connection for session {session_id}")
        return

    try:
        # Check for pending notifications
        pending_count = get_pending_count(session_id, db)

        if pending_count > 0:
            logger.info(f"📬 Delivering {pending_count} pending notification(s) to session {session_id}")
            delivered = await deliver_pending_notifications(session_id, db)
            logger.info(f"✅ Delivered {delivered}/{pending_count} pending notifications")

        # Keep connection alive and handle incoming messages
        while True:
            try:
                # Receive messages from client
                data = await websocket.receive_text()

                # Parse incoming message
                try:
                    message_data = json.loads(data)

                    # Check if this is a user message to be sent to agent
                    if message_data.get("type") == "user_message":
                        await handle_user_message(message_data, session_id, username, db)
                    else:
                        # Handle other message types (pings, acknowledgments, etc.)
                        logger.debug(f"Received from client {session_id}: {data[:100]}")

                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON received from {session_id}: {data[:100]}")
                except Exception as e:
                    logger.error(f"Error handling message from {session_id}: {e}")

            except WebSocketDisconnect:
                logger.info(f"🔌 WebSocket disconnected normally for session {session_id}")
                break

    except Exception as e:
        logger.error(f"WebSocket error for session {session_id}: {e}", exc_info=True)

    finally:
        # Clean up connection
        await connection_manager.disconnect(websocket, session_id)


@router.websocket("/ws/notifications/app-interaction")
async def websocket_app_interaction(
    websocket: WebSocket,
    agent_id: Optional[str] = Query(None, description="Agent ID to receive notifications from"),
):
    """
    WebSocket endpoint for app interaction notifications (calendar, reminders, timers, etc.).

    This endpoint provides real-time notifications from agents about app-specific events:
    - Calendar events created/updated/deleted
    - Reminders triggered
    - Timers/alarms
    - Other app interactions

    Args:
        websocket: WebSocket connection
        agent_id: Optional agent ID to filter notifications (e.g., 'calendar_agent')
    """
    # Accept connection without authentication for now (calendar notifications)
    # TODO: Add optional token-based auth in the future
    await websocket.accept()

    # Register connection with manager
    await app_interaction_manager.connect(websocket, agent_id)
    logger.info(f"📱 App interaction WebSocket connected for agent: {agent_id or 'all'}")

    try:
        # Keep connection alive and handle client messages
        while True:
            try:
                # Receive messages from client (mostly pings/keepalives)
                data = await websocket.receive_text()

                # Parse and handle any client messages if needed
                try:
                    message_data = json.loads(data)
                    logger.debug(f"Received from app interaction client: {message_data}")

                    # Handle ping/pong for keepalive
                    if message_data.get("type") == "ping":
                        await websocket.send_text(json.dumps({"type": "pong"}))

                except json.JSONDecodeError:
                    logger.debug(f"Non-JSON message from client: {data[:100]}")

            except WebSocketDisconnect:
                logger.info(f"🔌 App interaction WebSocket disconnected for agent: {agent_id or 'all'}")
                break

    except Exception as e:
        logger.error(f"App interaction WebSocket error: {e}", exc_info=True)
    finally:
        # Unregister connection
        await app_interaction_manager.disconnect(websocket, agent_id)
        try:
            await websocket.close()
        except:
            pass


@router.get("/ws/stats")
async def websocket_stats():
    """
    Get WebSocket connection statistics.

    Returns:
        Statistics about active WebSocket connections
    """
    active_sessions = connection_manager.get_active_sessions()
    total_connections = connection_manager.get_total_connections()

    session_details = []
    for session_id in active_sessions:
        connection_count = connection_manager.get_connection_count(session_id)
        session_details.append({
            "session_id": session_id,
            "connections": connection_count
        })

    return {
        "total_sessions": len(active_sessions),
        "total_connections": total_connections,
        "sessions": session_details
    }
