"""
Call WebSocket Router

Provides WebSocket endpoint for real-time voice calls with agents.
Handles call signaling, audio streaming, and call state management.
"""

import asyncio
import json
import logging
from typing import Optional, Dict, Any
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, Depends, status, HTTPException, Request
from pydantic import BaseModel, Field

from app.middleware.auth import verify_jwt_token, JWT_SECRET, JWT_ALGORITHM
import jwt
from app.services.call_manager import (
    get_call_manager,
    CallManager,
    CallStatus,
    CallEndReason,
    Call
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["calls"])


# =============================================================================
# Authentication Dependencies
# =============================================================================

async def get_current_user(request: Request) -> dict:
    """
    FastAPI dependency to extract and validate JWT from Authorization header.
    Returns the decoded token payload.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header",
            headers={"WWW-Authenticate": "Bearer"}
        )

    token = auth_header.split(" ")[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if not payload.get("sub") and not payload.get("session_id"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token claims"
            )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"}
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"}
        )


# =============================================================================
# Pydantic Models for WebSocket Messages
# =============================================================================

class InitiateCallMessage(BaseModel):
    """Client -> Server: Initiate a call"""
    type: str = "initiate_call"
    target_agent: str = Field(default="primary_agent")
    fast_mode: bool = Field(default=False, description="Enable fast mode with reduced latency model and limited tools")


class AcceptCallMessage(BaseModel):
    """Client -> Server: Accept incoming call"""
    type: str = "accept_call"
    call_id: str


class DeclineCallMessage(BaseModel):
    """Client -> Server: Decline incoming call"""
    type: str = "decline_call"
    call_id: str
    reason: Optional[str] = None


class EndCallMessage(BaseModel):
    """Client -> Server: End the call"""
    type: str = "end_call"


class HoldCallMessage(BaseModel):
    """Client -> Server: Put call on hold"""
    type: str = "hold_call"


class ResumeCallMessage(BaseModel):
    """Client -> Server: Resume held call"""
    type: str = "resume_call"


# =============================================================================
# REST API Request Models
# =============================================================================

class InitiateCallRequest(BaseModel):
    """Request body for initiating a call"""
    session_id: str
    initiated_by: str
    target: str = "user"
    reason: Optional[str] = None
    opening_message: Optional[str] = None


class AnswerCallRequest(BaseModel):
    """Request body for answering a call"""
    answered_by: str = "agent"
    twilio_call_sid: Optional[str] = None  # Used as fallback lookup for Twilio calls


class TransferCallRequest(BaseModel):
    """Request body for transferring a call"""
    from_agent: str
    to_agent: str
    twilio_call_sid: Optional[str] = None  # Used as fallback lookup for Twilio calls


class RecallCallRequest(BaseModel):
    """Request body for recalling a call"""
    by_agent: str = "primary_agent"


class EndCallRequest(BaseModel):
    """Request body for ending a call"""
    ended_by: str = "system"
    twilio_call_sid: Optional[str] = None  # Used as fallback lookup for Twilio calls


class EndActiveCallRequest(BaseModel):
    """Request body for ending active call by session"""
    session_id: str
    ended_by: str = "agent"
    twilio_call_sid: Optional[str] = None  # Fallback for phone calls
    call_id: Optional[str] = None  # Fallback if session_id lookup fails


class TransferActiveCallRequest(BaseModel):
    """Request body for transferring active call by session"""
    session_id: str
    from_agent: str
    to_agent: str
    twilio_call_sid: Optional[str] = None  # Fallback for phone calls
    call_id: Optional[str] = None  # Fallback if session_id lookup fails


# =============================================================================
# Call WebSocket Manager
# =============================================================================

class CallWebSocketManager:
    """Manages WebSocket connections for voice calls"""

    def __init__(self):
        # session_id -> WebSocket
        self._connections: Dict[str, WebSocket] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, session_id: str) -> bool:
        """Register a call WebSocket connection"""
        async with self._lock:
            # Close existing connection if any
            if session_id in self._connections:
                try:
                    await self._connections[session_id].close()
                except Exception:
                    pass

            self._connections[session_id] = websocket
            logger.info(f"Call WebSocket connected for session {session_id}")
            return True

    async def disconnect(self, session_id: str):
        """Remove a call WebSocket connection"""
        async with self._lock:
            if session_id in self._connections:
                del self._connections[session_id]
                logger.info(f"Call WebSocket disconnected for session {session_id}")

    async def send_event(self, session_id: str, event: Dict[str, Any]):
        """Send an event to a session's WebSocket"""
        websocket = self._connections.get(session_id)
        event_type = event.get("type", "unknown")
        if websocket:
            try:
                await websocket.send_json(event)
                logger.info(f"📤 Sent {event_type} event to session {session_id}")
            except Exception as e:
                logger.error(f"Error sending {event_type} event to {session_id}: {e}")
        else:
            logger.warning(f"📤 No WebSocket found for session {session_id} - {event_type} event dropped!")

    async def send_audio(self, session_id: str, audio_data: bytes):
        """Send audio data to a session's WebSocket"""
        websocket = self._connections.get(session_id)
        if websocket:
            try:
                await websocket.send_bytes(audio_data)
                logger.info(f"📤 Sent {len(audio_data)} bytes audio to session {session_id}")
            except Exception as e:
                logger.error(f"Error sending audio to {session_id}: {e}")
        else:
            logger.warning(f"📤 No WebSocket found for session {session_id} - audio dropped!")
            logger.warning(f"📤 Active WebSocket sessions: {list(self._connections.keys())}")

    def get_websocket(self, session_id: str) -> Optional[WebSocket]:
        """Get WebSocket for a session"""
        return self._connections.get(session_id)


# Global manager instance
call_ws_manager = CallWebSocketManager()


# =============================================================================
# Helper Functions
# =============================================================================

def get_db():
    """Get database client"""
    from app.main import db_client
    if not db_client:
        raise RuntimeError("Database client not initialized")
    return db_client


async def broadcast_call_event(event: Dict[str, Any]):
    """Broadcast call events from CallManager to WebSocket clients"""
    session_id = event.get("call", {}).get("session_id")
    if session_id:
        await call_ws_manager.send_event(session_id, event)


# =============================================================================
# WebSocket Endpoint
# =============================================================================

@router.websocket("/ws/call/{session_id}")
async def call_websocket_endpoint(
    websocket: WebSocket,
    session_id: str,
    token: Optional[str] = Query(None, description="JWT authentication token")
):
    """
    WebSocket endpoint for voice calls.

    Provides:
    - Call initiation and termination
    - Bidirectional audio streaming
    - Call state updates (ringing, connected, hold, etc.)
    - Live transcription
    - Agent speech (TTS audio)

    Authentication:
    - Requires JWT token in query parameter: ?token=<jwt>

    Message Types (Client -> Server):
    - initiate_call: Start a new call
    - accept_call: Accept incoming call
    - decline_call: Decline incoming call
    - end_call: End active call
    - hold_call: Put call on hold
    - resume_call: Resume held call
    - audio: Binary audio data (PCM 16-bit, 16kHz)

    Message Types (Server -> Client):
    - call_ringing: Call is ringing
    - call_connected: Call is connected
    - call_ended: Call has ended
    - call_on_hold: Call is on hold
    - call_transferring: Call is being transferred
    - transcription: Live transcription of user speech
    - agent_speaking: Agent is speaking (includes TTS text)
    - audio: Binary TTS audio data
    - error: Error occurred

    Args:
        websocket: WebSocket connection
        session_id: User session ID
        token: JWT authentication token
    """
    # Authenticate
    if not token:
        logger.warning(f"Call WebSocket attempt without token: {session_id}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Missing token")
        return

    try:
        payload = verify_jwt_token(token)
        if not payload:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid token")
            return
        username = payload.get("sub")
        logger.info(f"Call WebSocket authenticated: {username} ({session_id})")
    except Exception as e:
        logger.error(f"Call WebSocket auth error: {e}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Auth failed")
        return

    # Accept connection
    await websocket.accept()
    await call_ws_manager.connect(websocket, session_id)

    # Get call manager
    call_manager = get_call_manager()
    if not call_manager:
        await websocket.send_json({
            "type": "error",
            "code": "service_unavailable",
            "message": "Call service not available"
        })
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    # Register event callback for this session (keyed by session_id to prevent duplicates)
    async def handle_call_event(event: Dict[str, Any]):
        event_session = event.get("call", {}).get("session_id")
        if event_session == session_id:
            await call_ws_manager.send_event(session_id, event)

    call_manager.register_event_callback(session_id, handle_call_event)

    # Check for existing active call
    active_call = call_manager.get_active_call(session_id)
    if active_call:
        # If call is on hold due to user disconnect, auto-resume it
        if active_call.status == CallStatus.ON_HOLD:
            hold_reason = active_call.metadata.get("hold_reason")
            if hold_reason == "user_disconnected":
                logger.info(f"User reconnected, resuming call {active_call.call_id}")
                await call_manager.resume_call(active_call.call_id)
                # Refresh call state after resume
                active_call = call_manager.get_active_call(session_id)

        await websocket.send_json({
            "type": "call_state",
            "call": active_call.to_dict() if active_call else None
        })

    try:
        while True:
            message = await websocket.receive()

            # Handle binary audio data
            if "bytes" in message:
                audio_data = message["bytes"]
                await handle_audio_chunk(session_id, audio_data, call_manager)

            # Handle JSON messages
            elif "text" in message:
                try:
                    data = json.loads(message["text"])
                    msg_type = data.get("type")

                    if msg_type == "initiate_call":
                        await handle_initiate_call(
                            websocket, session_id, data, call_manager
                        )

                    elif msg_type == "accept_call":
                        await handle_accept_call(
                            websocket, session_id, data, call_manager
                        )

                    elif msg_type == "decline_call":
                        await handle_decline_call(
                            websocket, session_id, data, call_manager
                        )

                    elif msg_type == "end_call":
                        await handle_end_call(
                            websocket, session_id, call_manager
                        )

                    elif msg_type == "hold_call":
                        await handle_hold_call(
                            websocket, session_id, call_manager
                        )

                    elif msg_type == "resume_call":
                        await handle_resume_call(
                            websocket, session_id, call_manager
                        )

                    elif msg_type == "ping":
                        await websocket.send_json({"type": "pong"})

                    else:
                        logger.warning(f"Unknown call message type: {msg_type}")

                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON from call WebSocket: {session_id}")

    except WebSocketDisconnect:
        logger.info(f"Call WebSocket cleanly disconnected: {session_id}")

    except Exception as e:
        logger.error(f"Call WebSocket error for {session_id}: {e}", exc_info=True)

    finally:
        # Handle disconnect - put call on hold if active
        # This runs for both clean disconnects and errors
        try:
            active_call = call_manager.get_active_call(session_id)
            if active_call and active_call.status == CallStatus.CONNECTED:
                logger.info(f"Putting call {active_call.call_id} on hold due to user disconnect")
                await call_manager.hold_call(active_call.call_id, reason="user_disconnected")
        except Exception as hold_error:
            logger.error(f"Error putting call on hold: {hold_error}")

        # Unregister event callback to prevent memory leaks
        call_manager.unregister_event_callback(session_id)
        await call_ws_manager.disconnect(session_id)


# =============================================================================
# Message Handlers
# =============================================================================

async def handle_initiate_call(
    websocket: WebSocket,
    session_id: str,
    data: Dict[str, Any],
    call_manager: CallManager
):
    """Handle call initiation request - agent must answer via answer_call tool"""
    target_agent = data.get("target_agent", "primary_agent")
    fast_mode = data.get("fast_mode", False)

    try:
        call = await call_manager.initiate_call(
            session_id=session_id,
            initiated_by="user",
            target_agent=target_agent,
            fast_mode=fast_mode
        )

        await websocket.send_json({
            "type": "call_ringing",
            "call": call.to_dict()
        })

        logger.info(f"Call initiated: {call.call_id} -> {target_agent} (waiting for agent to answer)")

    except ValueError as e:
        await websocket.send_json({
            "type": "error",
            "code": "call_exists",
            "message": str(e)
        })
    except Exception as e:
        logger.error(f"Error initiating call: {e}")
        await websocket.send_json({
            "type": "error",
            "code": "initiate_failed",
            "message": "Failed to initiate call"
        })


async def handle_accept_call(
    websocket: WebSocket,
    session_id: str,
    data: Dict[str, Any],
    call_manager: CallManager
):
    """Handle call accept (for incoming calls from agent)"""
    call_id = data.get("call_id")
    if not call_id:
        await websocket.send_json({
            "type": "error",
            "code": "missing_call_id",
            "message": "call_id required"
        })
        return

    try:
        call_uuid = UUID(call_id)
        success = await call_manager.answer_call(call_uuid, answered_by="user")

        if success:
            call = call_manager.get_call_by_id(call_uuid)
            await websocket.send_json({
                "type": "call_connected",
                "call": call.to_dict() if call else {"call_id": call_id}
            })
        else:
            await websocket.send_json({
                "type": "error",
                "code": "accept_failed",
                "message": "Could not accept call"
            })

    except Exception as e:
        logger.error(f"Error accepting call: {e}")
        await websocket.send_json({
            "type": "error",
            "code": "accept_error",
            "message": str(e)
        })


async def handle_decline_call(
    websocket: WebSocket,
    session_id: str,
    data: Dict[str, Any],
    call_manager: CallManager
):
    """Handle call decline"""
    call_id = data.get("call_id")
    reason = data.get("reason")

    if not call_id:
        await websocket.send_json({
            "type": "error",
            "code": "missing_call_id",
            "message": "call_id required"
        })
        return

    try:
        call_uuid = UUID(call_id)
        success = await call_manager.decline_call(
            call_uuid, declined_by="user", reason=reason
        )

        if success:
            await websocket.send_json({
                "type": "call_ended",
                "call_id": call_id,
                "reason": "declined"
            })

    except Exception as e:
        logger.error(f"Error declining call: {e}")


async def handle_end_call(
    websocket: WebSocket,
    session_id: str,
    call_manager: CallManager
):
    """Handle call end request"""
    active_call = call_manager.get_active_call(session_id)

    if not active_call:
        await websocket.send_json({
            "type": "error",
            "code": "no_active_call",
            "message": "No active call to end"
        })
        return

    try:
        success = await call_manager.end_call(
            active_call.call_id,
            ended_by="user"
        )

        if success:
            await websocket.send_json({
                "type": "call_ended",
                "call_id": str(active_call.call_id),
                "reason": "user_hangup",
                "duration": active_call.get_duration()
            })

    except Exception as e:
        logger.error(f"Error ending call: {e}")


async def handle_hold_call(
    websocket: WebSocket,
    session_id: str,
    call_manager: CallManager
):
    """Handle hold request"""
    active_call = call_manager.get_active_call(session_id)

    if not active_call:
        return

    try:
        success = await call_manager.hold_call(active_call.call_id)
        if success:
            await websocket.send_json({
                "type": "call_on_hold",
                "call_id": str(active_call.call_id)
            })
    except Exception as e:
        logger.error(f"Error holding call: {e}")


async def handle_resume_call(
    websocket: WebSocket,
    session_id: str,
    call_manager: CallManager
):
    """Handle resume request"""
    active_call = call_manager.get_active_call(session_id)

    if not active_call or active_call.status != CallStatus.ON_HOLD:
        return

    try:
        success = await call_manager.resume_call(active_call.call_id)
        if success:
            call = call_manager.get_call_by_id(active_call.call_id)
            await websocket.send_json({
                "type": "call_connected",
                "call": call.to_dict() if call else {"call_id": str(active_call.call_id)}
            })
    except Exception as e:
        logger.error(f"Error resuming call: {e}")


async def handle_audio_chunk(
    session_id: str,
    audio_data: bytes,
    call_manager: CallManager
):
    """
    Handle incoming audio chunk from user.

    Routes audio to the voice gateway for STT processing.
    """
    active_call = call_manager.get_active_call(session_id)

    if not active_call or active_call.status != CallStatus.CONNECTED:
        return

    # Route audio to voice gateway for STT
    # This will be implemented when we extend the voice gateway
    # For now, we'll forward via RabbitMQ
    try:
        from app.main import rabbitmq_client

        if rabbitmq_client:
            # Get fast_mode from call metadata
            fast_mode = active_call.metadata.get("fast_mode", False)

            # Send audio chunk to voice gateway queue
            await forward_audio_to_voice_gateway(
                session_id,
                active_call.call_id,
                audio_data,
                fast_mode=fast_mode
            )

    except Exception as e:
        logger.error(f"Error forwarding audio: {e}")


# Global aio_pika connection for async audio forwarding
_aio_pika_connection = None
_aio_pika_channel = None


async def get_aio_pika_channel():
    """Get or create async RabbitMQ channel for audio forwarding"""
    global _aio_pika_connection, _aio_pika_channel
    import aio_pika
    import os

    try:
        # Check if existing connection is still valid
        if _aio_pika_connection and not _aio_pika_connection.is_closed:
            if _aio_pika_channel and not _aio_pika_channel.is_closed:
                return _aio_pika_channel

        # Get RabbitMQ URL from environment
        rabbitmq_url = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/vos_vhost")

        # Create new connection
        _aio_pika_connection = await aio_pika.connect_robust(rabbitmq_url)
        _aio_pika_channel = await _aio_pika_connection.channel()

        # Declare the call audio queue
        await _aio_pika_channel.declare_queue("call_audio_queue", durable=True)

        logger.info("Created async RabbitMQ connection for audio forwarding")
        return _aio_pika_channel

    except Exception as e:
        logger.error(f"Failed to create async RabbitMQ connection: {e}")
        return None


async def forward_audio_to_voice_gateway(
    session_id: str,
    call_id: UUID,
    audio_data: bytes,
    fast_mode: bool = False
):
    """Forward audio to voice gateway for STT processing via RabbitMQ"""
    import base64
    import json
    import aio_pika

    try:
        channel = await get_aio_pika_channel()
        if not channel:
            logger.warning("RabbitMQ channel not available for audio forwarding")
            return

        # Encode audio as base64 for JSON transport
        audio_b64 = base64.b64encode(audio_data).decode('utf-8')

        message = {
            "type": "call_audio",
            "session_id": session_id,
            "call_id": str(call_id),
            "audio_data": audio_b64,
            "audio_size": len(audio_data),
            "fast_mode": fast_mode
        }

        await channel.default_exchange.publish(
            aio_pika.Message(
                body=json.dumps(message).encode(),
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT
            ),
            routing_key="call_audio_queue"
        )

        logger.debug(f"Forwarded {len(audio_data)} bytes audio for call {call_id} (fast_mode={fast_mode})")

    except Exception as e:
        logger.error(f"Error forwarding audio to voice gateway: {e}")


async def send_tts_to_user(
    session_id: str,
    audio_data: bytes,
    text: str = None,
    audio_type: str = "call_speech"
):
    """
    Send TTS audio from voice_gateway back to user's call WebSocket.

    Args:
        session_id: User session ID
        audio_data: Binary audio data (MP3)
        text: Text that was converted to speech (for transcript display)
        audio_type: Type of audio - "call_speech" (auto-play in call) or "chat_voice" (manual play in chat)
    """
    try:
        # Send metadata event first (with audio_type for frontend handling)
        if text:
            await call_ws_manager.send_event(session_id, {
                "type": "agent_speaking",
                "text": text,
                "audio_type": audio_type,
                "auto_play": audio_type == "call_speech"
            })

        # Then send audio data
        await call_ws_manager.send_audio(session_id, audio_data)
        logger.info(f"Sent {len(audio_data)} bytes TTS audio to {session_id} (type={audio_type})")

    except Exception as e:
        logger.error(f"Error sending TTS to user: {e}")


async def send_transcription_to_user(session_id: str, text: str, is_final: bool, confidence: float = None):
    """Send transcription to user's call WebSocket"""
    try:
        await call_ws_manager.send_event(session_id, {
            "type": "transcription",
            "text": text,
            "is_final": is_final,
            "confidence": confidence
        })
        logger.debug(f"Sent transcription to {session_id}: '{text[:50]}...' (final={is_final})")
    except Exception as e:
        logger.error(f"Error sending transcription to user: {e}")


# =============================================================================
# REST Endpoints for Call Management
# =============================================================================

@router.get("/calls/active")
async def get_active_call(session_id: str):
    """Get active call for a session"""
    call_manager = get_call_manager()
    if not call_manager:
        return {"error": "Call service not available"}

    call = call_manager.get_active_call(session_id)
    if call:
        return {"call": call.to_dict()}
    return {"call": None}


@router.get("/calls/{call_id}")
async def get_call_details(call_id: str):
    """Get call details by ID"""
    call_manager = get_call_manager()
    if not call_manager:
        return {"error": "Call service not available"}

    try:
        call_uuid = UUID(call_id)
        call = call_manager.get_call_by_id(call_uuid)
        if call:
            return {"call": call.to_dict()}
        return {"error": "Call not found"}
    except Exception as e:
        return {"error": str(e)}


@router.post("/calls/{call_id}/end")
async def api_end_call(call_id: str, request: EndCallRequest):
    """End a call via REST API (for internal use and agent hang_up tool)"""
    call_manager = get_call_manager()
    if not call_manager:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Call service not available")

    try:
        # Try to find the call by ID first
        existing_call = None
        try:
            call_uuid = UUID(call_id)
            existing_call = call_manager.get_call_by_id(call_uuid)
        except ValueError:
            pass  # Invalid UUID, try fallback

        # Fallback: look up by twilio_call_sid if provided
        if not existing_call and request.twilio_call_sid:
            existing_call = call_manager.get_call_by_twilio_sid(request.twilio_call_sid)
            if existing_call:
                logger.info(f"Found call by twilio_call_sid fallback for end_call: {request.twilio_call_sid} -> {existing_call.call_id}")

        if not existing_call:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Call not found")

        success = await call_manager.end_call(existing_call.call_id, ended_by=request.ended_by)
        return {"success": success, "call_id": str(existing_call.call_id)}
    except HTTPException:
        raise
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/calls/initiate")
async def api_initiate_call(request: InitiateCallRequest):
    """
    Initiate an outbound call (agent calling user).

    Used by Primary Agent's call_user tool.
    """
    call_manager = get_call_manager()
    if not call_manager:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Call service not available")

    try:
        call = await call_manager.initiate_call(
            session_id=request.session_id,
            initiated_by=request.initiated_by,
            target_agent=request.target
        )

        # Notify user's WebSocket about incoming call (via call WebSocket)
        await call_ws_manager.send_event(request.session_id, {
            "type": "incoming_call",
            "call_id": str(call.call_id),
            "caller_agent_id": request.initiated_by,
            "reason": request.reason or "incoming_call",
            "call": call.to_dict()
        })

        # Also notify via main WebSocket (so user gets notified even if not on phone page)
        try:
            from app.websocket_manager import connection_manager
            # Send in WebSocketMessage format that frontend expects
            await connection_manager.send_raw_to_session(request.session_id, {
                "type": "incoming_call",
                "data": {
                    "call_id": str(call.call_id),
                    "caller_agent_id": request.initiated_by,
                    "reason": request.reason or "incoming_call",
                    "call": call.to_dict()
                }
            })
            logger.info(f"Sent incoming_call notification to session {request.session_id}")
        except Exception as e:
            logger.warning(f"Could not send incoming call to main WebSocket: {e}")

        logger.info(f"Agent {request.initiated_by} initiated call to user: {call.call_id}")

        return {
            "success": True,
            "call_id": str(call.call_id),
            "status": call.status.value
        }

    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error initiating call: {e}")
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/calls/{call_id}/answer")
async def api_answer_call(call_id: str, request: AnswerCallRequest):
    """
    Answer an incoming call.

    Used by agent's answer_call tool and twilio_gateway for call answered notifications.
    """
    call_manager = get_call_manager()
    if not call_manager:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Call service not available")

    try:
        # Try to find the call by ID first
        existing_call = None
        try:
            call_uuid = UUID(call_id)
            existing_call = call_manager.get_call_by_id(call_uuid)
        except ValueError:
            pass  # Invalid UUID, try fallback

        # Fallback: look up by twilio_call_sid if provided
        # This handles the case where twilio_gateway uses a different call_id than CallManager
        if not existing_call and request.twilio_call_sid:
            existing_call = call_manager.get_call_by_twilio_sid(request.twilio_call_sid)
            if existing_call:
                logger.info(f"Found call by twilio_call_sid fallback: {request.twilio_call_sid} -> {existing_call.call_id}")

        if not existing_call:
            raise HTTPException(status_code=404, detail="Call not found or already ended")

        success = await call_manager.answer_call(existing_call.call_id, answered_by=request.answered_by)

        if success:
            call = call_manager.get_call_by_id(existing_call.call_id)
            # Notify the user's WebSocket that call is connected
            if call:
                await call_ws_manager.send_event(call.session_id, {
                    "type": "call_connected",
                    "call": call.to_dict()
                })

            return {"success": True, "call_id": str(existing_call.call_id)}
        else:
            raise HTTPException(status_code=400, detail="Could not answer call - call may have ended")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error answering call: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/calls/{call_id}/transfer")
async def api_transfer_call(call_id: str, request: TransferCallRequest):
    """
    Transfer a call to another agent.

    Used by agent's transfer_call tool.
    """
    call_manager = get_call_manager()
    if not call_manager:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Call service not available")

    try:
        # Try to find the call by ID first
        existing_call = None
        try:
            call_uuid = UUID(call_id)
            existing_call = call_manager.get_call_by_id(call_uuid)
        except ValueError:
            pass  # Invalid UUID, try fallback

        # Fallback: look up by twilio_call_sid if provided
        if not existing_call and request.twilio_call_sid:
            existing_call = call_manager.get_call_by_twilio_sid(request.twilio_call_sid)
            if existing_call:
                logger.info(f"Found call by twilio_call_sid fallback for transfer: {request.twilio_call_sid} -> {existing_call.call_id}")

        if not existing_call:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Call not found")

        success = await call_manager.transfer_call(
            existing_call.call_id,
            from_agent=request.from_agent,
            to_agent=request.to_agent
        )

        if success:
            call = call_manager.get_call_by_id(existing_call.call_id)
            # Notify user about transfer
            if call:
                await call_ws_manager.send_event(call.session_id, {
                    "type": "call_transferring",
                    "from_agent": request.from_agent,
                    "to_agent": request.to_agent,
                    "call": call.to_dict()
                })

            return {"success": True, "transferred_to": request.to_agent}
        else:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="Could not transfer call")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error transferring call: {e}")
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/calls/{call_id}/recall")
async def api_recall_call(call_id: str, request: RecallCallRequest):
    """
    Recall a call back to the primary agent.

    Used by Primary Agent's recall_phone tool.
    """
    call_manager = get_call_manager()
    if not call_manager:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Call service not available")

    try:
        call_uuid = UUID(call_id)
        # Transfer back to primary agent
        success = await call_manager.transfer_call(
            call_uuid,
            from_agent="current",
            to_agent=request.by_agent
        )

        if success:
            call = call_manager.get_call_by_id(call_uuid)
            if call:
                await call_ws_manager.send_event(call.session_id, {
                    "type": "call_recalled",
                    "by_agent": request.by_agent,
                    "call": call.to_dict()
                })

            return {"success": True, "recalled_by": request.by_agent}
        else:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="Could not recall call")

    except Exception as e:
        logger.error(f"Error recalling call: {e}")
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/calls/active/end")
async def api_end_active_call(request: EndActiveCallRequest):
    """
    End the active call for a session.

    Alternative to /calls/{call_id}/end when call_id isn't known.
    This endpoint is resilient - it will try multiple lookup methods and
    will return success if the call is already ended (idempotent).
    """
    call_manager = get_call_manager()
    if not call_manager:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Call service not available")

    try:
        # Try multiple lookup methods
        call = call_manager.get_active_call(request.session_id)

        # Fallback 1: Try by call_id if provided
        if not call and request.call_id:
            try:
                call_uuid = UUID(request.call_id)
                call = call_manager.get_call_by_id(call_uuid)
            except ValueError:
                pass

        # Fallback 2: Try by twilio_call_sid if provided
        if not call and request.twilio_call_sid:
            call = call_manager.get_call_by_twilio_sid(request.twilio_call_sid)

        # If no call found, still try to terminate Twilio call if we have the SID
        # This handles the case where the call was already removed from CallManager
        if not call:
            if request.twilio_call_sid:
                # Try to terminate the Twilio call even if we don't have it tracked
                logger.info(f"Call not found in CallManager, but attempting to terminate Twilio call: {request.twilio_call_sid}")
                await call_manager._terminate_twilio_call(request.twilio_call_sid)
                return {"success": True, "message": "Twilio call termination requested"}
            else:
                # No call found and no Twilio SID - could be already ended
                logger.warning(f"No call found for session {request.session_id} - may already be ended")
                return {"success": True, "message": "No active call found (may already be ended)"}

        # Call the call already ended? Still success (idempotent)
        if call.status == CallStatus.ENDED:
            logger.info(f"Call {call.call_id} already ended")
            return {"success": True, "message": "Call already ended"}

        success = await call_manager.end_call(call.call_id, ended_by=request.ended_by)

        if success:
            await call_ws_manager.send_event(request.session_id, {
                "type": "call_ended",
                "call_id": str(call.call_id),
                "reason": f"{request.ended_by}_hangup",
                "duration": call.get_duration()
            })

        return {"success": success}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error ending active call: {e}")
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/calls/active/transfer")
async def api_transfer_active_call(request: TransferActiveCallRequest):
    """
    Transfer the active call for a session to another agent.

    Alternative to /calls/{call_id}/transfer when call_id isn't known.
    Uses fallback lookups if session_id lookup fails.
    """
    call_manager = get_call_manager()
    if not call_manager:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Call service not available")

    try:
        # Try multiple lookup methods
        call = call_manager.get_active_call(request.session_id)

        # Fallback 1: Try by call_id if provided
        if not call and request.call_id:
            try:
                call_uuid = UUID(request.call_id)
                call = call_manager.get_call_by_id(call_uuid)
                # Only use if call is still active
                if call and call.status == CallStatus.ENDED:
                    call = None
            except ValueError:
                pass

        # Fallback 2: Try by twilio_call_sid if provided
        if not call and request.twilio_call_sid:
            call = call_manager.get_call_by_twilio_sid(request.twilio_call_sid)
            # Only use if call is still active
            if call and call.status == CallStatus.ENDED:
                call = None

        if not call:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="No active call found")

        success = await call_manager.transfer_call(
            call.call_id,
            from_agent=request.from_agent,
            to_agent=request.to_agent
        )

        if success:
            updated_call = call_manager.get_call_by_id(call.call_id)
            if updated_call:
                await call_ws_manager.send_event(request.session_id, {
                    "type": "call_transferring",
                    "from_agent": request.from_agent,
                    "to_agent": request.to_agent,
                    "call": updated_call.to_dict()
                })

            return {"success": True, "transferred_to": request.to_agent}
        else:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="Could not transfer call")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error transferring active call: {e}")
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Internal Endpoints for Voice Gateway Callbacks
# =============================================================================

class TranscriptionCallback(BaseModel):
    """Transcription callback from voice gateway"""
    session_id: str
    call_id: str
    text: str
    is_final: bool
    confidence: Optional[float] = None


class TTSAudioCallback(BaseModel):
    """TTS audio callback from voice gateway"""
    session_id: str
    call_id: str
    audio_b64: str  # Base64 encoded audio
    text: Optional[str] = None
    agent_id: Optional[str] = None
    audio_type: str = "call_speech"  # "call_speech" for call audio, "chat_voice" for chat messages


@router.post("/calls/internal/transcription")
async def internal_transcription_callback(request: TranscriptionCallback):
    """
    Receive transcription from voice gateway and forward to:
    1. User's WebSocket (for display)
    2. Agent via RabbitMQ (for processing final transcriptions)

    Internal endpoint - should be called by voice_gateway only.
    """
    try:
        # Send to user's WebSocket for display
        await send_transcription_to_user(
            session_id=request.session_id,
            text=request.text,
            is_final=request.is_final,
            confidence=request.confidence
        )

        # If this is a final transcription with content, send to agent
        if request.is_final and request.text.strip():
            await send_transcription_to_agent(
                session_id=request.session_id,
                call_id=request.call_id,
                text=request.text
            )

        return {"success": True}
    except Exception as e:
        logger.error(f"Error forwarding transcription: {e}")
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))


async def send_transcription_to_agent(session_id: str, call_id: str, text: str):
    """Send user's transcribed speech to the agent handling the call."""
    import aio_pika
    from datetime import datetime
    import uuid

    try:
        from app.main import rabbitmq_client

        if not rabbitmq_client or not rabbitmq_client.connection:
            logger.warning("RabbitMQ not available for agent transcription")
            return

        # Get the call to determine which agent is handling it and fast_mode setting
        call_manager = get_call_manager()
        call = call_manager.get_active_call(session_id) if call_manager else None

        # Determine target agent (default to primary_agent)
        target_agent = "primary_agent"
        if call and call.current_agent_id:
            target_agent = call.current_agent_id

        # Check if fast_mode is enabled for this call
        fast_mode = call.metadata.get("fast_mode", False) if call else False

        # Create user message notification for the agent
        # IMPORTANT: Include explicit call mode instructions for the LLM
        notification = {
            "notification_id": str(uuid.uuid4()),
            "timestamp": datetime.utcnow().isoformat(),
            "notification_type": "user_message",
            "source": "call_transcription",
            "recipient_agent_id": target_agent,
            "payload": {
                "session_id": session_id,
                "content": text,
                "content_type": "call_transcript",
                "call_id": call_id,
                "is_call_mode": True,
                "fast_mode": fast_mode,
                "_call_instruction": "YOU ARE ON AN ACTIVE VOICE CALL. Use the 'speak' tool to respond, NOT send_user_message. The speak tool will convert your text to speech and play it directly in the call."
            }
        }

        channel = await rabbitmq_client.connection.channel()
        target_queue = f"{target_agent}_queue"

        await channel.declare_queue(target_queue, durable=True)
        await channel.default_exchange.publish(
            aio_pika.Message(
                body=json.dumps(notification).encode(),
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT
            ),
            routing_key=target_queue
        )

        logger.info(f"📞 Sent call transcription to {target_agent}: '{text[:50]}...'")

    except Exception as e:
        logger.error(f"Error sending transcription to agent: {e}")


@router.post("/calls/internal/tts-audio")
async def internal_tts_audio_callback(request: TTSAudioCallback):
    """
    Receive TTS audio from voice gateway and forward to user's WebSocket.

    Internal endpoint - should be called by voice_gateway only.

    Audio types:
    - "call_speech": Audio from a voice call, auto-plays immediately
    - "chat_voice": Audio message in chat, queued for manual play
    """
    import base64

    try:
        # Decode base64 audio
        audio_data = base64.b64decode(request.audio_b64)

        await send_tts_to_user(
            session_id=request.session_id,
            audio_data=audio_data,
            text=request.text,
            audio_type=request.audio_type
        )
        return {"success": True, "audio_size": len(audio_data), "audio_type": request.audio_type}
    except Exception as e:
        logger.error(f"Error forwarding TTS audio: {e}")
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Call History REST Endpoints
# =============================================================================

class CallHistoryItem(BaseModel):
    """Call history item"""
    call_id: str
    session_id: str
    initiated_by: str
    initial_target: str
    current_agent_id: str
    call_status: str
    started_at: str
    connected_at: Optional[str] = None
    ended_at: Optional[str] = None
    end_reason: Optional[str] = None
    duration_seconds: Optional[int] = None


class CallHistoryResponse(BaseModel):
    """Response for call history endpoint"""
    calls: list[CallHistoryItem]
    total: int
    page: int
    page_size: int


@router.get("/calls/history/{session_id}")
async def get_call_history(
    session_id: str,
    page: int = 1,
    page_size: int = 20,
    current_user: dict = Depends(get_current_user)
) -> CallHistoryResponse:
    """
    Get call history for a session.

    Args:
        session_id: Session ID to get history for
        page: Page number (1-indexed)
        page_size: Number of items per page

    Returns:
        Paginated list of calls
    """
    try:
        db = get_db()
        offset = (page - 1) * page_size

        # Check if calls table exists first
        table_check = """
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_name = 'calls'
        ) as table_exists
        """
        try:
            check_result = db.execute_query_dict(table_check)
            if not check_result or not check_result[0].get('table_exists', False):
                # Table doesn't exist yet - return empty result
                logger.info("Calls table not found - returning empty history")
                return CallHistoryResponse(calls=[], total=0, page=page, page_size=page_size)
        except Exception as e:
            logger.warning(f"Error checking calls table: {e}")
            return CallHistoryResponse(calls=[], total=0, page=page, page_size=page_size)

        # Get total count
        count_query = "SELECT COUNT(*) as total FROM calls WHERE session_id = %s"
        count_result = db.execute_query_dict(count_query, (session_id,))
        total = count_result[0]['total'] if count_result else 0

        # Get paginated calls (PostgreSQL syntax)
        query = """
        SELECT
            call_id::text,
            session_id,
            initiated_by,
            initial_target,
            current_agent_id,
            call_status::text,
            started_at,
            connected_at,
            ended_at,
            end_reason::text,
            EXTRACT(EPOCH FROM (COALESCE(ended_at, NOW()) - connected_at))::integer as duration_seconds
        FROM calls
        WHERE session_id = %s
        ORDER BY started_at DESC
        LIMIT %s OFFSET %s
        """

        rows = db.execute_query_dict(query, (session_id, page_size, offset))

        calls = []
        for row in rows or []:
            calls.append(CallHistoryItem(
                call_id=row['call_id'],
                session_id=row['session_id'],
                initiated_by=row['initiated_by'],
                initial_target=row['initial_target'],
                current_agent_id=row['current_agent_id'],
                call_status=row['call_status'],
                started_at=row['started_at'].isoformat() if row['started_at'] else None,
                connected_at=row['connected_at'].isoformat() if row['connected_at'] else None,
                ended_at=row['ended_at'].isoformat() if row['ended_at'] else None,
                end_reason=row['end_reason'],
                duration_seconds=row['duration_seconds']
            ))

        return CallHistoryResponse(
            calls=calls,
            total=total,
            page=page,
            page_size=page_size
        )

    except Exception as e:
        logger.error(f"Error getting call history: {e}")
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))
