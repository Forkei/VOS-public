"""
Voice Gateway Main Application
FastAPI server with WebSocket endpoint for voice interactions
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import jwt as pyjwt

from .config import settings
from .models import StartSessionPayload, WebSocketMessage
from .voice_session import VoiceSession
from .clients.database_client import DatabaseClient
from .clients.rabbitmq_client import RabbitMQClient
from .utils.session_manager import SessionManager
from .utils.jwt_auth import JWTAuth

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Global clients
db_client: Optional[DatabaseClient] = None
rabbitmq_client: Optional[RabbitMQClient] = None
session_manager: Optional[SessionManager] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifecycle manager for FastAPI application
    Handles startup and shutdown of global resources
    """
    global db_client, rabbitmq_client, session_manager

    logger.info(f"Starting Voice Gateway v{settings.SERVICE_VERSION}")

    # Initialize clients
    db_client = DatabaseClient()
    rabbitmq_client = RabbitMQClient()
    session_manager = SessionManager(session_timeout=settings.SESSION_TIMEOUT)
    
    # Initialize call audio bridge (global)
    global call_audio_bridge
    call_audio_bridge = CallAudioBridge(db_client, rabbitmq_client)

    # Connect to services (including Cartesia pre-connection)
    try:
        await db_client.connect()
        await rabbitmq_client.connect()
        # Pre-connect Cartesia streaming for low-latency TTS
        await call_audio_bridge.initialize()
        logger.info("All services connected successfully")
    except Exception as e:
        logger.error(f"Failed to connect to services: {e}")
        raise

    # Start background tasks
    consumer_task = asyncio.create_task(consume_agent_responses())
    call_audio_task = asyncio.create_task(consume_call_audio())

    yield

    # Shutdown
    logger.info("Shutting down Voice Gateway")

    # Cancel background tasks
    consumer_task.cancel()
    call_audio_task.cancel()
    
    # Cleanup audio bridge
    if call_audio_bridge:
        # pending cleanup if needed
        pass

    # Close clients
    await rabbitmq_client.close()
    await db_client.close()

    logger.info("Voice Gateway shutdown complete")


from .utils.call_audio_bridge import CallAudioBridge

# Global call audio bridge for managing call sessions
call_audio_bridge: Optional[CallAudioBridge] = None


async def consume_call_audio():
    """Consume audio from call_audio_queue for STT processing"""
    global call_audio_bridge
    import aio_pika
    import json
    import base64

    try:
        connection = await aio_pika.connect_robust(settings.RABBITMQ_URL)
        channel = await connection.channel()

        # Declare queue
        queue = await channel.declare_queue("call_audio_queue", durable=True)

        # CallAudioBridge is already initialized in lifespan
        if not call_audio_bridge:
             logger.error("CallAudioBridge not initialized!")
             return

        logger.info("🎙️ Started consuming from call_audio_queue")

        async for message in queue:
            async with message.process():
                try:
                    data = json.loads(message.body.decode())

                    msg_type = data.get("type")
                    notification_type = data.get("notification_type")

                    if msg_type == "stream_started":
                        # Twilio stream started - initialize session with stream_sid
                        # This ensures TTS can be sent before any audio is received
                        session_id = data.get("session_id")
                        call_id = data.get("call_id")
                        twilio_call_sid = data.get("twilio_call_sid")
                        twilio_stream_sid = data.get("stream_sid")

                        if session_id and call_id:
                            logger.info(f"📞 Stream started for call {call_id}, stream_sid={twilio_stream_sid}")
                            # Initialize session with Twilio info (no audio yet)
                            await call_audio_bridge.initialize_twilio_session(
                                session_id=session_id,
                                call_id=call_id,
                                twilio_call_sid=twilio_call_sid,
                                twilio_stream_sid=twilio_stream_sid
                            )

                    elif msg_type == "call_audio":
                        session_id = data.get("session_id")
                        call_id = data.get("call_id")
                        audio_b64 = data.get("audio_data")
                        # Twilio-specific fields
                        source = data.get("source", "web")
                        twilio_call_sid = data.get("twilio_call_sid")
                        twilio_stream_sid = data.get("stream_sid")
                        # Fast mode for low-latency responses
                        fast_mode = data.get("fast_mode", False)

                        if session_id and call_id and audio_b64:
                            # Decode audio
                            audio_data = base64.b64decode(audio_b64)

                            # Process through bridge (with Twilio fields if present)
                            await call_audio_bridge.process_audio(
                                session_id=session_id,
                                call_id=call_id,
                                audio_data=audio_data,
                                source=source,
                                twilio_call_sid=twilio_call_sid,
                                twilio_stream_sid=twilio_stream_sid,
                                fast_mode=fast_mode
                            )

                    elif notification_type == "call_ended":
                        # Cleanup call audio bridge session
                        call_id = data.get("payload", {}).get("call_id")
                        if call_id:
                            logger.info(f"📞 Cleaning up call audio bridge for {call_id}")
                            await call_audio_bridge.end_session(call_id)

                except Exception as e:
                    logger.error(f"Error processing call audio: {e}")

    except Exception as e:
        logger.error(f"Error in call audio consumer: {e}")


async def consume_agent_responses():
    """Consume agent responses from voice_gateway_queue"""
    import aio_pika
    import json

    try:
        connection = await aio_pika.connect_robust(settings.RABBITMQ_URL)
        channel = await connection.channel()

        # Declare queue
        queue = await channel.declare_queue("voice_gateway_queue", durable=True)

        logger.info("Started consuming from voice_gateway_queue")

        async for message in queue:
            async with message.process():
                try:
                    data = json.loads(message.body.decode())

                    logger.info(f"🎤 [VOICE GATEWAY] Received message from queue")
                    logger.info(f"🎤 [VOICE GATEWAY] Full message data: {json.dumps(data, indent=2)}")

                    # Extract session_id and content
                    session_id = data.get("payload", {}).get("session_id")
                    content = data.get("payload", {}).get("content")
                    sender_agent_id = data.get("payload", {}).get("sender_agent_id")

                    logger.info(f"🎤 [VOICE GATEWAY] Extracted - session_id: {session_id}, content_length: {len(content) if content else 0}, sender: {sender_agent_id}")

                    if session_id and content:
                        # Find active voice session
                        voice_session = session_manager.get_session(session_id)

                        logger.info(f"🎤 [VOICE GATEWAY] Looking for active voice session: {session_id}")
                        logger.info(f"🎤 [VOICE GATEWAY] Active sessions: {list(session_manager.sessions.keys())}")

                        # Check if this is a call-mode speak request
                        is_call_mode = data.get("payload", {}).get("is_call_speech", False)
                        call_id = data.get("payload", {}).get("call_id")

                        # IMPORTANT: Check call mode FIRST - it takes priority over voice sessions
                        if is_call_mode and call_id and call_audio_bridge:
                            # Handle call-mode TTS through the bridge (phone calls)
                            logger.info(f"📞 [VOICE GATEWAY] Call mode TTS for call {call_id}")
                            emotion = data.get("payload", {}).get("emotion", "neutral")
                            fast_mode = data.get("payload", {}).get("fast_mode", False)
                            await call_audio_bridge.agent_speak(
                                session_id=session_id,
                                call_id=call_id,
                                text=content,
                                agent_id=sender_agent_id,
                                emotion=emotion,
                                fast_mode=fast_mode
                            )
                            logger.info(f"📞 [VOICE GATEWAY] ✅ Sent TTS audio for call {call_id} (fast_mode={fast_mode})")
                        elif voice_session:
                            # Regular voice session (walkie-talkie mode in chat)
                            logger.info(f"🎤 [VOICE GATEWAY] ✅ Found voice session, routing to TTS")
                            logger.info(f"🎤 [VOICE GATEWAY] Content to speak: '{content}'")
                            await voice_session.handle_agent_response(content)
                            logger.info(f"🎤 [VOICE GATEWAY] ✅ Successfully routed agent response to session {session_id}")
                        else:
                            logger.warning(f"🎤 [VOICE GATEWAY] ❌ No active voice session for {session_id}")
                            logger.warning(f"🎤 [VOICE GATEWAY] ❌ Falling back to text mode - sending via API Gateway")

                            # Fallback: Send as text message via API Gateway
                            try:
                                import requests
                                api_gateway_url = "http://api_gateway:8000"

                                # Read internal API key
                                try:
                                    with open("/shared/internal_api_key", "r") as f:
                                        internal_api_key = f.read().strip()
                                except FileNotFoundError:
                                    logger.error("Internal API key file not found")
                                    internal_api_key = None

                                message_data = {
                                    "agent_id": sender_agent_id or "voice_gateway",
                                    "content": f"[Voice mode unavailable] {content}",
                                    "content_type": "text",
                                    "session_id": session_id,
                                    "timestamp": data.get("timestamp")
                                }

                                headers = {"Content-Type": "application/json"}
                                if internal_api_key:
                                    headers["X-Internal-Key"] = internal_api_key

                                response = requests.post(
                                    f"{api_gateway_url}/api/v1/messages/user",
                                    json=message_data,
                                    headers=headers,
                                    timeout=10
                                )

                                if response.status_code == 200:
                                    logger.info(f"🎤 [VOICE GATEWAY] ✅ Sent text fallback to user via API Gateway")
                                else:
                                    logger.error(f"🎤 [VOICE GATEWAY] ❌ Failed to send text fallback: {response.status_code}")

                            except Exception as e:
                                logger.error(f"🎤 [VOICE GATEWAY] ❌ Error sending text fallback: {e}")
                    else:
                        logger.warning(f"🎤 [VOICE GATEWAY] ❌ Missing session_id or content: session_id={session_id}, content={'present' if content else 'missing'}")

                except Exception as e:
                    logger.error(f"🎤 [VOICE GATEWAY] ❌ Error processing agent response: {e}", exc_info=True)

    except Exception as e:
        logger.error(f"Error in agent response consumer: {e}")


# Create FastAPI app
app = FastAPI(
    title="VOS Voice Gateway",
    version=settings.SERVICE_VERSION,
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": settings.SERVICE_NAME,
        "version": settings.SERVICE_VERSION,
        "active_sessions": session_manager.get_active_session_count() if session_manager else 0
    }


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": settings.SERVICE_NAME,
        "version": settings.SERVICE_VERSION,
        "endpoints": {
            "websocket": "/ws/voice/{session_id}?token={jwt_token}",
            "token": "/voice/token",
            "audio": "/audio/{session_id}/{interaction_id}",
            "health": "/health"
        }
    }


@app.post("/voice/token")
async def generate_voice_token(session_id: str, user_id: Optional[str] = None):
    """
    Generate JWT token for voice WebSocket connection

    Args:
        session_id: Session ID for voice connection
        user_id: Optional user ID

    Returns:
        JWT token and expiration info
    """
    try:
        token = JWTAuth.generate_voice_token(session_id, user_id)

        return {
            "token": token,
            "session_id": session_id,
            "expires_in_minutes": settings.JWT_EXPIRATION_MINUTES,
            "websocket_url": f"wss://api.jarvos.dev/ws/voice/{session_id}?token={token}"
        }

    except Exception as e:
        logger.error(f"Error generating voice token: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate token")


@app.get("/audio/{session_id}/{interaction_id}")
async def get_audio(session_id: str, interaction_id: str):
    """
    Serve TTS audio file for replay

    Args:
        session_id: Session ID
        interaction_id: Interaction ID (UUID)

    Returns:
        Audio file (MP3)
    """
    try:
        # Construct file path
        audio_path = Path(f"/shared/voice_audio/{session_id}/{interaction_id}.mp3")

        if not audio_path.exists():
            raise HTTPException(status_code=404, detail="Audio file not found")

        return FileResponse(
            path=audio_path,
            media_type="audio/mpeg",
            filename=f"{interaction_id}.mp3"
        )

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Audio file not found")
    except Exception as e:
        logger.error(f"Error serving audio file: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.websocket("/ws/voice/{session_id}")
async def voice_websocket(
    websocket: WebSocket,
    session_id: str,
    token: str = Query(...)
):
    """
    WebSocket endpoint for voice interactions

    Args:
        websocket: WebSocket connection
        session_id: User session ID
        token: JWT authentication token (query parameter)
    """
    # Verify JWT token BEFORE accepting WebSocket
    try:
        payload = JWTAuth.verify_voice_token(token)

        # Verify session_id matches token
        if payload.get("session_id") != session_id:
            logger.warning(f"Session ID mismatch: URL={session_id}, token={payload.get('session_id')}")
            await websocket.close(code=1008, reason="Session ID mismatch")
            return

        logger.info(f"✅ JWT verified for session {session_id}")

    except pyjwt.ExpiredSignatureError:
        logger.warning(f"❌ Expired token for session {session_id}")
        await websocket.close(code=1008, reason="Token expired")
        return
    except pyjwt.InvalidTokenError as e:
        logger.warning(f"❌ Invalid token for session {session_id}: {e}")
        await websocket.close(code=1008, reason="Invalid token")
        return
    except Exception as e:
        logger.error(f"❌ Token verification error: {e}")
        await websocket.close(code=1011, reason="Authentication error")
        return

    # Token verified - accept connection
    await websocket.accept()
    logger.info(f"WebSocket connection accepted for session {session_id}")

    voice_session: Optional[VoiceSession] = None

    try:
        # Create voice session
        voice_session = VoiceSession(
            websocket=websocket,
            session_id=session_id,
            db_client=db_client,
            rabbitmq_client=rabbitmq_client
        )

        # Add to session manager
        session_manager.add_session(session_id, voice_session)

        # Wait for start_session message
        session_started = False

        while True:
            message = await websocket.receive()
            logger.debug(f"📩 Raw message keys: {list(message.keys())}")

            # Handle binary audio data
            if "bytes" in message:
                audio_data = message["bytes"]
                logger.debug(f"📨 Received audio chunk: {len(audio_data)} bytes")

                if not session_started:
                    logger.warning("⚠️ Received audio before session started")
                    continue

                # Process audio chunk
                await voice_session.process_audio_chunk(audio_data)
                logger.debug(f"✅ Processed audio chunk: {len(audio_data)} bytes")

            # Handle text (JSON) messages
            elif "text" in message:
                try:
                    data = json.loads(message["text"])
                    message_type = data.get("type")
                    payload = data.get("payload", {})

                    logger.debug(f"Received message: type={message_type}")

                    if message_type == "start_session":
                        if not session_started:
                            await voice_session.start(payload)
                            session_started = True
                        else:
                            logger.warning("Session already started")

                    elif message_type == "end_session":
                        logger.info(f"Session end requested for {session_id}")
                        break

                    else:
                        logger.warning(f"Unknown message type: {message_type}")

                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON message: {e}")
                except Exception as e:
                    logger.error(f"Error processing message: {e}")

    except WebSocketDisconnect as e:
        logger.info(f"WebSocket disconnected for session {session_id}")
        disconnect_reason = "disconnected"

    except Exception as e:
        logger.error(f"Error in voice WebSocket: {e}")
        disconnect_reason = "error"

    else:
        # Normal completion (no exception)
        disconnect_reason = "completed"

    finally:
        # Cleanup
        if voice_session:
            try:
                await voice_session.close(status=disconnect_reason if 'disconnect_reason' in locals() else "error")
            except Exception as cleanup_error:
                logger.error(f"Error closing voice session: {cleanup_error}")

        # Remove from session manager
        session_manager.remove_session(session_id)

        logger.info(f"Voice session {session_id} cleaned up")


# For development/testing
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8100,
        reload=True,
        log_level=settings.LOG_LEVEL.lower()
    )
