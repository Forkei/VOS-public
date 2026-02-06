"""
Twilio Media Stream WebSocket Handler
Handles bidirectional audio streaming between Twilio and VOS
"""

import asyncio
import json
import logging
import base64
import os
from typing import Optional, Dict

import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..config import settings
from ..utils.audio_converter import mulaw_to_pcm, pcm_to_mulaw

logger = logging.getLogger(__name__)

# API Gateway URL for registering calls
API_GATEWAY_URL = os.getenv("API_GATEWAY_URL", "http://api_gateway:8000")

router = APIRouter()


class TwilioMediaStreamSession:
    """Manages a single Twilio Media Stream connection"""

    def __init__(
        self,
        websocket: WebSocket,
        session_id: str,
        call_id: str,
        twilio_call_sid: str
    ):
        self.websocket = websocket
        self.session_id = session_id
        self.call_id = call_id
        self.twilio_call_sid = twilio_call_sid
        self.stream_sid: Optional[str] = None
        self.caller_phone_number: Optional[str] = None
        self.caller_name: Optional[str] = None
        self.is_connected = False
        self.audio_buffer: bytes = b""

    async def handle_start(self, data: dict):
        """Handle stream start event"""
        start_data = data.get("start", {})
        self.stream_sid = start_data.get("streamSid")

        # Get custom parameters
        custom_params = start_data.get("customParameters", {})
        self.caller_phone_number = custom_params.get("caller_phone_number")
        self.caller_name = custom_params.get("caller_name")

        self.is_connected = True
        logger.info(
            f"Twilio stream started: stream_sid={self.stream_sid}, "
            f"session={self.session_id}, caller={self.caller_phone_number}"
        )

    async def handle_media(self, data: dict, rabbitmq_client) -> Optional[bytes]:
        """
        Handle incoming media (audio) from Twilio.

        Returns decoded PCM audio data.
        """
        media = data.get("media", {})
        payload = media.get("payload")

        if not payload:
            return None

        # Decode base64 mulaw audio
        mulaw_audio = base64.b64decode(payload)

        # Convert mulaw 8kHz to PCM 16kHz for VOS pipeline
        pcm_audio = mulaw_to_pcm(mulaw_audio)

        # Buffer audio and send in chunks (avoid sending tiny fragments)
        self.audio_buffer += pcm_audio

        # SECURITY: Prevent unbounded buffer growth (max ~2 seconds at 16kHz 16-bit mono)
        # At 16kHz, 16-bit audio: 16000 * 2 * 2 = 64000 bytes for 2 seconds
        MAX_BUFFER_SIZE = 64000
        if len(self.audio_buffer) > MAX_BUFFER_SIZE:
            logger.warning(f"Audio buffer overflow for {self.session_id}, dropping oldest {len(self.audio_buffer) - MAX_BUFFER_SIZE} bytes")
            self.audio_buffer = self.audio_buffer[-MAX_BUFFER_SIZE:]

        # Send to Voice Gateway when we have enough audio (~100ms of audio)
        # At 16kHz, 16-bit audio: 16000 * 2 * 0.1 = 3200 bytes per 100ms
        if len(self.audio_buffer) >= 3200:
            await rabbitmq_client.publish_audio_to_voice_gateway(
                session_id=self.session_id,
                call_id=self.call_id,
                audio_data=self.audio_buffer,
                twilio_call_sid=self.twilio_call_sid,
                stream_sid=self.stream_sid
            )
            audio_to_return = self.audio_buffer
            self.audio_buffer = b""
            return audio_to_return

        return None

    async def send_audio(self, pcm_audio: bytes):
        """
        Send audio back to Twilio caller.

        Args:
            pcm_audio: PCM 16kHz audio to send
        """
        if not self.is_connected or not self.stream_sid:
            return

        try:
            # Convert PCM to mulaw for Twilio
            mulaw_audio = pcm_to_mulaw(pcm_audio)

            # Encode to base64
            audio_b64 = base64.b64encode(mulaw_audio).decode()

            # Send as Twilio media message
            message = {
                "event": "media",
                "streamSid": self.stream_sid,
                "media": {
                    "payload": audio_b64
                }
            }

            await self.websocket.send_json(message)

        except Exception as e:
            logger.error(f"Error sending audio to Twilio: {e}")

    async def send_mark(self, name: str):
        """
        Send a mark message to Twilio for synchronization.

        Args:
            name: Mark name for tracking
        """
        if not self.is_connected or not self.stream_sid:
            return

        try:
            message = {
                "event": "mark",
                "streamSid": self.stream_sid,
                "mark": {
                    "name": name
                }
            }

            await self.websocket.send_json(message)

        except Exception as e:
            logger.error(f"Error sending mark to Twilio: {e}")

    async def send_clear(self):
        """
        Send clear message to stop current audio playback (for interruption).
        """
        if not self.is_connected or not self.stream_sid:
            return

        try:
            message = {
                "event": "clear",
                "streamSid": self.stream_sid
            }

            await self.websocket.send_json(message)
            logger.debug(f"Sent clear to Twilio stream {self.stream_sid}")

        except Exception as e:
            logger.error(f"Error sending clear to Twilio: {e}")


# Store active sessions (session_id -> TwilioMediaStreamSession)
active_sessions: Dict[str, TwilioMediaStreamSession] = {}


def get_rabbitmq_client():
    """Get RabbitMQ client from main module"""
    from ..main import rabbitmq_client
    return rabbitmq_client


def get_db_client():
    """Get database client from main module"""
    from ..main import db_client
    return db_client


async def notify_call_answered(call_id: str, twilio_call_sid: str):
    """
    Notify api_gateway that the Twilio call was answered.
    This transitions the call from RINGING to CONNECTED state.
    """
    try:
        # Read internal API key
        try:
            with open("/shared/internal_api_key", "r") as f:
                internal_key = f.read().strip()
        except FileNotFoundError:
            logger.warning("Internal API key not found for call answered notification")
            internal_key = ""

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{API_GATEWAY_URL}/api/v1/calls/{call_id}/answer",
                json={
                    "answered_by": "user",  # The called party answered
                    "twilio_call_sid": twilio_call_sid
                },
                headers={"X-Internal-Key": internal_key},
                timeout=10.0
            )

            if response.status_code == 200:
                logger.info(f"Notified api_gateway that call {call_id} was answered")
            else:
                logger.warning(f"Failed to notify call answered: HTTP {response.status_code} - {response.text}")

    except Exception as e:
        logger.error(f"Error notifying call answered: {e}")


async def register_inbound_call_with_api_gateway(
    session_id: str,
    call_id: str,
    twilio_call_sid: str,
    caller_phone_number: Optional[str],
    caller_name: Optional[str]
):
    """
    Register an inbound Twilio call with the API Gateway's CallManager.

    This ensures the Call object exists so hang_up and other call management
    functions work properly for inbound Twilio calls.
    """
    try:
        # Read internal API key
        try:
            with open("/shared/internal_api_key", "r") as f:
                internal_key = f.read().strip()
        except FileNotFoundError:
            logger.warning("Internal API key not found for inbound call registration")
            internal_key = ""

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{API_GATEWAY_URL}/api/v1/twilio/call/register-inbound",
                json={
                    "session_id": session_id,
                    "call_id": call_id,
                    "twilio_call_sid": twilio_call_sid,
                    "caller_phone_number": caller_phone_number,
                    "caller_name": caller_name
                },
                headers={"X-Internal-Key": internal_key},
                timeout=10.0
            )

            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    logger.info(f"Registered inbound call with API Gateway: {twilio_call_sid}")
                    return result  # Return result containing call_id
                else:
                    logger.warning(f"Failed to register inbound call: {result.get('error')}")
            else:
                logger.warning(f"Failed to register inbound call: HTTP {response.status_code}")

    except Exception as e:
        logger.error(f"Error registering inbound call with API Gateway: {e}")
        # Don't fail the call - registration is non-critical

    return None


@router.websocket("/media-stream/{session_id}")
async def twilio_media_stream(websocket: WebSocket, session_id: str):
    """
    WebSocket endpoint for Twilio Media Streams.

    Handles bidirectional audio between Twilio and VOS voice pipeline.
    """
    await websocket.accept()
    logger.info(f"Twilio Media Stream WebSocket accepted for session: {session_id}")

    rabbitmq_client = get_rabbitmq_client()
    db_client = get_db_client()

    session: Optional[TwilioMediaStreamSession] = None

    try:
        # Initial parameters will come from the 'start' event
        call_id = ""
        twilio_call_sid = ""

        while True:
            message = await websocket.receive_text()
            data = json.loads(message)

            event = data.get("event")

            if event == "connected":
                logger.info(f"Twilio stream connected for session: {session_id}")

            elif event == "start":
                # Extract parameters from start event
                start_data = data.get("start", {})
                custom_params = start_data.get("customParameters", {})

                call_id = custom_params.get("call_id", "")
                twilio_call_sid = custom_params.get("twilio_call_sid", "")

                # Create session handler
                session = TwilioMediaStreamSession(
                    websocket=websocket,
                    session_id=session_id,
                    call_id=call_id,
                    twilio_call_sid=twilio_call_sid
                )

                await session.handle_start(data)

                # Store in active sessions
                active_sessions[session_id] = session

                # Also store in main module's active_streams for TTS routing
                from ..main import active_streams
                active_streams[twilio_call_sid] = websocket

                # Update database with call info
                if call_id and twilio_call_sid:
                    await db_client.update_call_twilio_info(
                        call_id=call_id,
                        twilio_call_sid=twilio_call_sid,
                        caller_phone_number=session.caller_phone_number,
                        call_source="twilio_inbound"
                    )

                # Notify voice_gateway about stream start so stream_sid is available for TTS
                # This ensures TTS can be sent before any audio is received from the caller
                await rabbitmq_client.publish_stream_started(
                    session_id=session_id,
                    call_id=call_id,
                    twilio_call_sid=twilio_call_sid,
                    stream_sid=session.stream_sid
                )

                # Register the call with api_gateway's CallManager and transition to CONNECTED
                # The media stream only starts after the call is connected (both directions)
                direction = custom_params.get("direction", "inbound")
                if direction != "outbound":  # Inbound calls
                    # Register creates the call, then we immediately transition to CONNECTED
                    # because the media stream being active means the caller is on the line
                    result = await register_inbound_call_with_api_gateway(
                        session_id=session_id,
                        call_id=call_id,
                        twilio_call_sid=twilio_call_sid,
                        caller_phone_number=session.caller_phone_number,
                        caller_name=session.caller_name
                    )
                    # Transition to CONNECTED - the caller is already on the line
                    if result and result.get("call_id"):
                        logger.info(f"Inbound call connected (stream started): call_id={result.get('call_id')}")
                        await notify_call_answered(result.get("call_id"), twilio_call_sid)
                else:
                    # For outbound calls, notify api_gateway that call was answered
                    # The media stream only starts after the callee picks up
                    if call_id:
                        logger.info(f"Outbound call answered (stream started): call_id={call_id}")
                        await notify_call_answered(call_id, twilio_call_sid)

            elif event == "media":
                if session:
                    # Process incoming audio
                    await session.handle_media(data, rabbitmq_client)

            elif event == "mark":
                # Mark received - can be used for synchronization
                mark_name = data.get("mark", {}).get("name")
                logger.debug(f"Received mark: {mark_name}")

            elif event == "stop":
                logger.info(f"Twilio stream stopped for session: {session_id}")
                break

            else:
                logger.debug(f"Unknown Twilio event: {event}")

    except WebSocketDisconnect:
        logger.info(f"Twilio Media Stream disconnected: {session_id}")

    except Exception as e:
        logger.error(f"Error in Twilio Media Stream: {e}", exc_info=True)

    finally:
        # Cleanup - ensure all steps complete even if some fail
        if session_id in active_sessions:
            del active_sessions[session_id]

        if session and session.twilio_call_sid:
            from ..main import active_streams
            if session.twilio_call_sid in active_streams:
                del active_streams[session.twilio_call_sid]

        # Flush any remaining audio buffer (with error handling)
        if session and session.audio_buffer:
            try:
                await rabbitmq_client.publish_audio_to_voice_gateway(
                    session_id=session_id,
                    call_id=session.call_id,
                    audio_data=session.audio_buffer,
                    twilio_call_sid=session.twilio_call_sid,
                    stream_sid=session.stream_sid or ""
                )
                logger.debug(f"Flushed {len(session.audio_buffer)} bytes of remaining audio")
            except Exception as e:
                logger.error(f"Error flushing audio buffer: {e}")

        # Notify that call ended (always try, even if flush failed)
        if session:
            try:
                await rabbitmq_client.publish_call_event(
                    event_type="call_ended",
                    session_id=session_id,
                    call_id=session.call_id,
                    twilio_call_sid=session.twilio_call_sid,
                    phone_number=session.caller_phone_number,
                    metadata={"reason": "stream_closed"}
                )
            except Exception as e:
                logger.error(f"Error publishing call_ended event: {e}")

        logger.info(f"Twilio Media Stream cleanup complete: {session_id}")


def get_session(session_id: str) -> Optional[TwilioMediaStreamSession]:
    """Get an active media stream session by ID"""
    return active_sessions.get(session_id)


async def send_audio_to_session(session_id: str, pcm_audio: bytes):
    """
    Send audio to a specific Twilio session.

    Args:
        session_id: VOS session ID
        pcm_audio: PCM 16kHz audio data
    """
    session = active_sessions.get(session_id)
    if session:
        await session.send_audio(pcm_audio)
