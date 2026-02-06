"""
Call Session

Extends VoiceSession to support continuous voice calls with:
- Bidirectional audio streaming (not request-response)
- Agent-initiated TTS at any time
- Call state awareness
- Hold/resume support
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional
from uuid import UUID
from enum import Enum

from fastapi import WebSocket

from .models import VoiceState, AudioFormat, TranscriptionResult, StatusMessage
from .voice_session import VoiceSession
from .clients.database_client import DatabaseClient
from .clients.rabbitmq_client import RabbitMQClient
from .config import settings

logger = logging.getLogger(__name__)


class CallState(str, Enum):
    """Call-specific states"""
    IDLE = "idle"
    RINGING = "ringing"
    CONNECTED = "connected"
    ON_HOLD = "on_hold"
    ENDING = "ending"
    ENDED = "ended"


class CallSession(VoiceSession):
    """
    Extended voice session for continuous call mode.

    Key differences from VoiceSession:
    1. Continuous audio streaming (doesn't stop after each utterance)
    2. Agent can speak at any time (not just in response to user)
    3. Maintains call state alongside voice state
    4. Supports hold/resume
    """

    def __init__(
        self,
        websocket: WebSocket,
        session_id: str,
        call_id: str,
        db_client: DatabaseClient,
        rabbitmq_client: RabbitMQClient
    ):
        """
        Initialize call session.

        Args:
            websocket: FastAPI WebSocket connection
            session_id: User session ID
            call_id: Call ID from call manager
            db_client: Database client
            rabbitmq_client: RabbitMQ client
        """
        super().__init__(websocket, session_id, db_client, rabbitmq_client)

        # Call-specific state
        self.call_id = call_id
        self.call_state = CallState.RINGING
        self.current_agent_id: Optional[str] = None

        # Continuous mode flag
        self.continuous_transcription = True

        # Queue for agent speech requests
        self._speak_queue: asyncio.Queue = asyncio.Queue()
        self._speak_task: Optional[asyncio.Task] = None

        # Track if user is currently speaking (for interruption)
        self._user_speaking = False

        logger.info(f"CallSession created for call {call_id}")

    async def start(self, start_payload: dict):
        """
        Start the call session.

        Extends parent start() with call-specific initialization.
        """
        # Add call mode flags to payload
        start_payload["is_call_mode"] = True
        start_payload["continuous_mode"] = True

        # Call parent start
        await super().start(start_payload)

        # Start the speak queue processor
        self._speak_task = asyncio.create_task(self._process_speak_queue())

        # Update call state
        self.call_state = CallState.CONNECTED

        # Send call_connected event
        await self._send_status("call_connected", {
            "call_id": self.call_id,
            "session_id": self.session_id
        })

        logger.info(f"Call session {self.call_id} started and connected")

    async def _on_transcript(self, result: TranscriptionResult):
        """
        Handle transcription during call.

        In call mode, we:
        1. Track when user is speaking (for potential interruption)
        2. Send interim transcriptions to UI
        3. On final transcription, send to agent but DON'T stop listening
        """
        try:
            # Track user speaking state
            self._user_speaking = not result.is_final

            if not result.is_final:
                # Send interim transcription to UI
                await self._send_status("transcription_interim", {
                    "text": result.text,
                    "confidence": result.confidence,
                    "is_final": False,
                    "call_id": self.call_id
                })
            else:
                # Final transcription
                await self._send_status("transcription_final", {
                    "text": result.text,
                    "confidence": result.confidence,
                    "is_final": True,
                    "call_id": self.call_id
                })

                # Store transcript
                await self._store_transcript("user", None, result.text, result.confidence)

                # Send to agent - but DON'T change state to PROCESSING
                # In call mode, we keep listening while agent thinks
                await self._send_to_agent_call_mode(result.text)

        except Exception as e:
            logger.error(f"Error handling call transcript: {e}")

    async def _send_to_agent_call_mode(self, text: str):
        """
        Send transcription to agent in call mode.

        Unlike regular voice mode, we don't transition to PROCESSING state
        and we keep the audio stream open.
        """
        try:
            # Notify UI that agent is thinking (but call continues)
            await self._send_status("agent_thinking", {
                "agent_id": self.current_agent_id or "primary_agent",
                "status": "Processing...",
                "call_id": self.call_id
            })

            # Publish user message to agent with call context
            await self.rabbitmq.publish_user_message(
                session_id=self.session_id,
                content=text,
                voice_metadata={
                    "call_id": self.call_id,
                    "is_call_mode": True,
                    "current_agent": self.current_agent_id or "primary_agent",
                    "platform": self.platform
                },
                user_timezone=self.user_timezone
            )

            logger.info(f"Sent call transcription to agent: '{text[:50]}...'")

        except Exception as e:
            logger.error(f"Error sending to agent in call mode: {e}")

    async def agent_speak(self, text: str, emotion: str = "neutral"):
        """
        Queue agent speech (called when agent uses speak tool).

        Args:
            text: Text for TTS
            emotion: Emotional tone
        """
        await self._speak_queue.put({
            "text": text,
            "emotion": emotion,
            "timestamp": datetime.utcnow()
        })
        logger.info(f"Queued agent speech: '{text[:50]}...'")

    async def _process_speak_queue(self):
        """
        Process queued speech requests from agents.

        Runs continuously while call is active.
        """
        while self.call_state in (CallState.CONNECTED, CallState.RINGING):
            try:
                # Wait for speech request with timeout
                try:
                    speech = await asyncio.wait_for(
                        self._speak_queue.get(),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                if self.call_state != CallState.CONNECTED:
                    break

                # Generate and send TTS
                await self._speak_immediate(speech["text"], speech["emotion"])

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in speak queue processor: {e}")

        logger.info("Speak queue processor stopped")

    async def _speak_immediate(self, text: str, emotion: str = "neutral"):
        """
        Generate and send TTS immediately.

        Args:
            text: Text to speak
            emotion: Emotional tone
        """
        try:
            # Notify speaking started
            await self._send_status("agent_speaking", {
                "text": text,
                "call_id": self.call_id,
                "agent_id": self.current_agent_id
            })

            # Generate TTS
            complete_audio = await self.tts_client.generate_audio(text, emotion)

            # Store transcript
            await self._store_transcript(
                "agent",
                self.current_agent_id,
                text,
                audio_size=len(complete_audio)
            )

            # Send audio to client
            await self.websocket.send_bytes(complete_audio)

            # Notify speaking completed
            await self._send_status("speaking_completed", {
                "audio_size_bytes": len(complete_audio),
                "call_id": self.call_id
            })

            logger.info(f"Agent spoke: '{text[:50]}...' ({len(complete_audio)} bytes)")

        except Exception as e:
            logger.error(f"Error in immediate speak: {e}")

    async def _store_transcript(
        self,
        speaker_type: str,
        speaker_id: Optional[str],
        content: str,
        confidence: float = None,
        audio_size: int = None
    ):
        """Store call transcript entry."""
        try:
            query = """
            INSERT INTO call_transcripts
                (call_id, speaker_type, speaker_id, content, stt_confidence)
            VALUES (%s, %s, %s, %s, %s)
            """
            await self.db.execute_query(query, (
                self.call_id,
                speaker_type,
                speaker_id,
                content,
                confidence
            ))
        except Exception as e:
            logger.error(f"Failed to store transcript: {e}")

    async def hold(self):
        """Put call on hold."""
        if self.call_state != CallState.CONNECTED:
            return False

        self.call_state = CallState.ON_HOLD

        # Stop transcription
        if self.assemblyai:
            await self.assemblyai.close()

        await self._send_status("call_on_hold", {
            "call_id": self.call_id
        })

        logger.info(f"Call {self.call_id} on hold")
        return True

    async def resume(self):
        """Resume call from hold."""
        if self.call_state != CallState.ON_HOLD:
            return False

        # Restart transcription
        if self.assemblyai:
            await self.assemblyai.start()

        self.call_state = CallState.CONNECTED

        await self._send_status("call_resumed", {
            "call_id": self.call_id
        })

        logger.info(f"Call {self.call_id} resumed")
        return True

    async def transfer(self, to_agent: str, from_agent: str):
        """
        Handle call transfer to another agent.

        Args:
            to_agent: Agent receiving the call
            from_agent: Agent transferring the call
        """
        self.current_agent_id = to_agent

        await self._send_status("call_transferred", {
            "call_id": self.call_id,
            "from_agent": from_agent,
            "to_agent": to_agent
        })

        logger.info(f"Call {self.call_id} transferred from {from_agent} to {to_agent}")

    async def handle_agent_response(self, response_text: str):
        """
        Handle agent response during call.

        In call mode, this queues the speech instead of
        blocking the voice session.
        """
        await self.agent_speak(response_text)

    async def close(self, status: str = "completed"):
        """
        Close the call session.

        Args:
            status: Closing status
        """
        self.call_state = CallState.ENDING

        # Cancel speak task
        if self._speak_task:
            self._speak_task.cancel()
            try:
                await self._speak_task
            except asyncio.CancelledError:
                pass

        # Send call ended event
        await self._send_status("call_ended", {
            "call_id": self.call_id,
            "status": status
        })

        self.call_state = CallState.ENDED

        # Call parent close
        await super().close(status)

        logger.info(f"Call session {self.call_id} closed ({status})")


class CallSessionManager:
    """Manages active call sessions."""

    def __init__(self):
        # call_id -> CallSession
        self._sessions: dict = {}
        self._lock = asyncio.Lock()

    async def create_session(
        self,
        websocket: WebSocket,
        session_id: str,
        call_id: str,
        db_client: DatabaseClient,
        rabbitmq_client: RabbitMQClient
    ) -> CallSession:
        """Create and register a new call session."""
        async with self._lock:
            session = CallSession(
                websocket=websocket,
                session_id=session_id,
                call_id=call_id,
                db_client=db_client,
                rabbitmq_client=rabbitmq_client
            )
            self._sessions[call_id] = session
            return session

    async def get_session(self, call_id: str) -> Optional[CallSession]:
        """Get call session by call ID."""
        return self._sessions.get(call_id)

    async def get_session_by_user(self, session_id: str) -> Optional[CallSession]:
        """Get call session by user session ID."""
        for session in self._sessions.values():
            if session.session_id == session_id:
                return session
        return None

    async def remove_session(self, call_id: str):
        """Remove a call session."""
        async with self._lock:
            if call_id in self._sessions:
                del self._sessions[call_id]

    def get_active_count(self) -> int:
        """Get number of active call sessions."""
        return len(self._sessions)


# Global call session manager
call_session_manager = CallSessionManager()
