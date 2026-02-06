"""
Voice Session
Manages a single voice conversation session with audio streaming,
transcription, agent interaction, and TTS generation
"""

import asyncio
import json
import logging
import requests
from datetime import datetime
from typing import Optional
from uuid import UUID
from fastapi import WebSocket
import pybreaker

from .models import (
    VoiceState,
    AudioFormat,
    TranscriptionResult,
    StatusMessage
)
from .clients.assemblyai_client import AssemblyAITranscriptionClient
from .clients.elevenlabs_client import ElevenLabsTTSClient
from .clients.cartesia_client import CartesiaTTSClient
from .clients.rabbitmq_client import RabbitMQClient
from .clients.database_client import DatabaseClient
from .utils.audio_storage import AudioStorage
from .utils.url_signing import generate_audio_signed_url
from .config import settings

logger = logging.getLogger(__name__)


class VoiceSession:
    """Manages a single voice conversation session"""

    def __init__(
        self,
        websocket: WebSocket,
        session_id: str,
        db_client: DatabaseClient,
        rabbitmq_client: RabbitMQClient
    ):
        """
        Initialize voice session

        Args:
            websocket: FastAPI WebSocket connection
            session_id: User session ID
            db_client: Database client instance
            rabbitmq_client: RabbitMQ client instance
        """
        self.websocket = websocket
        self.session_id = session_id
        self.db = db_client
        self.rabbitmq = rabbitmq_client

        # Session state
        self.state = VoiceState.IDLE
        self.voice_session_id: Optional[UUID] = None
        self.current_interaction_id: Optional[UUID] = None  # Deprecated - kept for compatibility
        self.current_voice_message_id: Optional[int] = None
        self.last_activity = datetime.utcnow()

        # Audio format (will be set on start_session)
        self.audio_format: Optional[AudioFormat] = None
        self.platform: str = "web"
        self.user_timezone: Optional[str] = None  # IANA timezone (e.g., "America/New_York")
        self.endpointing_ms: Optional[int] = None  # Silence detection timeout (null = disabled)

        # Clients (initialized on start)
        self.assemblyai: Optional[AssemblyAITranscriptionClient] = None

        # TTS client (initialized in start() based on session settings)
        self.tts_client = None
        self.tts_provider = None
        self.tts_audio_format = None
        self.tts_voice_id = None
        self.audio_storage = AudioStorage()

        # Transcription tracking
        self.interaction_start_time: Optional[datetime] = None

    async def start(self, start_payload: dict):
        """
        Initialize voice session with client parameters

        Args:
            start_payload: Start session payload with platform, audio_format, and TTS settings
        """
        try:
            # Extract session parameters
            self.platform = start_payload.get("platform", "web")
            self.user_timezone = start_payload.get("user_timezone")  # Optional timezone from frontend
            self.endpointing_ms = start_payload.get("endpointing_ms")  # Optional silence detection timeout
            audio_format_dict = start_payload.get("audio_format", {})

            # Create AudioFormat model
            self.audio_format = AudioFormat(**audio_format_dict)

            # Extract TTS settings from payload (fall back to global settings)
            tts_provider_raw = start_payload.get("tts_provider")
            self.tts_provider = (tts_provider_raw or settings.TTS_PROVIDER).lower()

            # Initialize TTS client based on provider selection
            tts_voice_id_raw = start_payload.get("tts_voice_id")

            if self.tts_provider == "cartesia":
                self.tts_voice_id = tts_voice_id_raw or settings.CARTESIA_VOICE_ID
                self.tts_client = CartesiaTTSClient(
                    api_key=settings.CARTESIA_API_KEY,
                    voice_id=self.tts_voice_id
                )
                self.tts_audio_format = settings.CARTESIA_OUTPUT_FORMAT
            else:  # Default to elevenlabs
                self.tts_provider = "elevenlabs"  # Normalize
                self.tts_voice_id = tts_voice_id_raw or settings.ELEVENLABS_VOICE_ID
                self.tts_client = ElevenLabsTTSClient(
                    api_key=settings.ELEVENLABS_API_KEY,
                    voice_id=self.tts_voice_id
                )
                self.tts_audio_format = "mp3"

            logger.info(
                f"Starting voice session for {self.session_id}: "
                f"platform={self.platform}, codec={self.audio_format.codec}, "
                f"tts_provider={self.tts_provider}, voice_id={self.tts_voice_id}"
            )

            # Note: voice_sessions table removed - we now use voice_messages table instead
            # No need to track session metadata separately

            # Initialize AssemblyAI with audio format and endpointing settings
            self.assemblyai = AssemblyAITranscriptionClient(
                api_key=settings.ASSEMBLYAI_API_KEY,
                on_transcript=self._on_transcript,
                audio_format=self.audio_format,
                endpointing_ms=self.endpointing_ms
            )

            if not await self.assemblyai.start():
                # Notify agent that STT failed to start
                await self.rabbitmq.publish_failure_notification(
                    session_id=self.session_id,
                    error_type="stt_failed",
                    error_message="AssemblyAI STT service unavailable (failed to start connection)"
                )
                raise Exception("Failed to start AssemblyAI")

            # Send session_started event
            await self._send_status("session_started", {
                "session_id": self.session_id,
                "voice_id": self.tts_voice_id,
                "tts_provider": self.tts_provider
            })

            logger.info(f"Voice session {self.session_id} started successfully")

        except Exception as e:
            logger.error(f"Error starting voice session: {e}")
            await self._send_error("session_start_failed", str(e))
            raise

    async def process_audio_chunk(self, audio_data: bytes):
        """
        Process incoming audio chunk from client

        Args:
            audio_data: Raw audio bytes
        """
        self.last_activity = datetime.utcnow()

        try:
            # Transition to LISTENING if idle
            if self.state == VoiceState.IDLE:
                self.state = VoiceState.LISTENING
                self.interaction_start_time = datetime.utcnow()
                await self._send_status("listening_started", {})

            # Restart AssemblyAI connection if it closed (e.g., due to inactivity timeout)
            if self.state == VoiceState.LISTENING and self.assemblyai:
                if not self.assemblyai.is_connected:
                    logger.info("🔄 AssemblyAI connection closed, restarting...")
                    if not await self.assemblyai.start():
                        # Failed to restart - notify user
                        await self.rabbitmq.publish_failure_notification(
                            session_id=self.session_id,
                            error_type="stt_failed",
                            error_message="AssemblyAI STT service unavailable (failed to restart connection)"
                        )
                        logger.error("❌ Failed to restart AssemblyAI connection")
                        self.state = VoiceState.IDLE
                        return
                    logger.info("✅ AssemblyAI connection restarted successfully")

                # Send audio to AssemblyAI
                await self.assemblyai.send_audio(audio_data)

        except Exception as e:
            logger.error(f"Error processing audio chunk: {e}")
            await self._send_error("audio_processing_failed", str(e))

    async def _on_transcript(self, result: TranscriptionResult):
        """
        Handle transcription result from AssemblyAI

        Args:
            result: Transcription result
        """
        try:
            if not result.is_final:
                # Send interim transcription to UI
                await self._send_status("transcription_interim", {
                    "text": result.text,
                    "confidence": result.confidence,
                    "is_final": False
                })
            else:
                # Final transcription - send to agent
                await self._send_status("transcription_final", {
                    "text": result.text,
                    "confidence": result.confidence,
                    "is_final": True
                })

                # Create voice message record for user
                voice_message_id = await self.db.create_voice_message(
                    session_id=self.session_id,
                    transcript=result.text,
                    role='user',
                    audio_format='webm',
                    metadata={
                        "confidence": result.confidence,
                        "platform": self.platform
                    }
                )

                # Store conversation message with voice_message_id reference
                await self.db.store_conversation_message(
                    session_id=self.session_id,
                    sender_type="user",
                    sender_id=None,
                    content=result.text,
                    voice_metadata={
                        "confidence": result.confidence
                    },
                    voice_message_id=voice_message_id
                )

                # Store voice_message_id for this interaction
                self.current_voice_message_id = voice_message_id

                # Send to primary agent
                await self._send_to_agent(result.text)

        except Exception as e:
            logger.error(f"Error handling transcript: {e}")
            await self._send_error("transcription_failed", str(e))

    async def _send_to_agent(self, text: str):
        """
        Send transcription to primary agent via RabbitMQ

        Args:
            text: Transcribed text
        """
        try:
            self.state = VoiceState.PROCESSING

            await self._send_status("agent_thinking", {
                "agent_id": "primary_agent",
                "status": "Processing your request..."
            })

            # Publish user message to primary agent
            await self.rabbitmq.publish_user_message(
                session_id=self.session_id,
                content=text,
                voice_metadata={
                    "voice_message_id": self.current_voice_message_id,
                    "platform": self.platform
                },
                user_timezone=self.user_timezone
            )

            logger.info(f"Sent transcription to primary_agent: '{text[:50]}...'")

            # Wait for agent response
            # Note: Agent response will come through handle_agent_response

        except Exception as e:
            logger.error(f"Error sending to agent: {e}")
            await self._send_error("agent_communication_failed", str(e))
            self.state = VoiceState.IDLE

    async def _publish_agent_message_notification(self, content: str, voice_message_id: int):
        """
        Publish agent message to API Gateway to trigger newMessage WebSocket notification

        Args:
            content: Agent message content
            voice_message_id: Voice message ID for audio metadata
        """
        try:
            # Load internal API key
            try:
                with open("/shared/internal_api_key", "r") as f:
                    internal_api_key = f.read().strip()
            except Exception as e:
                logger.error(f"Failed to load internal API key: {e}")
                return

            # Call API Gateway /messages/user endpoint with voice_message_id
            message_data = {
                "agent_id": "primary_agent",
                "content": content,
                "content_type": "text",
                "timestamp": datetime.utcnow().isoformat(),
                "session_id": self.session_id,
                "voice_message_id": voice_message_id
            }

            headers = {
                "Content-Type": "application/json",
                "X-Internal-Key": internal_api_key
            }

            response = requests.post(
                f"{settings.API_GATEWAY_URL}/api/v1/messages/user",
                json=message_data,
                headers=headers,
                timeout=5
            )

            if response.status_code == 200:
                logger.info(f"✅ Published agent message notification for voice_message_id {voice_message_id}")
            else:
                logger.warning(f"⚠️ Failed to publish agent message notification: {response.status_code}")

        except Exception as e:
            logger.error(f"Error publishing agent message notification: {e}")
            # Don't fail the voice response if notification fails

    async def handle_agent_response(self, response_text: str):
        """
        Handle response from agent and generate TTS

        Args:
            response_text: Agent's response text
        """
        try:
            agent_response_time = None
            if self.interaction_start_time:
                agent_response_time = int(
                    (datetime.utcnow() - self.interaction_start_time).total_seconds() * 1000
                )

            # Generate TTS
            await self._speak(response_text, agent_response_time)

        except Exception as e:
            logger.error(f"Error handling agent response: {e}")
            await self._send_error("tts_failed", str(e))
            self.state = VoiceState.IDLE

    async def _speak(self, text: str, agent_response_time: int = None, emotion: str = "neutral"):
        """
        Generate and send TTS audio

        Args:
            text: Text to speak
            agent_response_time: Time agent took to respond (ms)
            emotion: Emotional tone
        """
        try:
            self.state = VoiceState.SPEAKING

            # Notify speaking started
            await self._send_status("speaking_started", {
                "text": text,
                "estimated_duration_ms": len(text) * 50  # Rough estimate
            })

            tts_start_time = datetime.utcnow()

            # Generate complete audio (buffered for Phase 1)
            try:
                complete_audio = await self.tts_client.generate_audio(text, emotion)
            except pybreaker.CircuitBreakerError as e:
                # TTS service is down - notify primary agent to use text mode
                logger.error(f"❌ TTS circuit breaker open for session {self.session_id}")

                await self.rabbitmq.publish_failure_notification(
                    session_id=self.session_id,
                    error_type="tts_failed",
                    error_message=f"{self.tts_provider.capitalize()} TTS service unavailable (circuit breaker open)",
                    original_text=text
                )

                # Notify client of TTS failure
                await self._send_error(
                    "tts_service_unavailable",
                    "Voice synthesis temporarily unavailable. Agent will respond via text.",
                    severity="warning"
                )

                self.state = VoiceState.IDLE
                return  # Exit without speaking

            tts_generation_time = int((datetime.utcnow() - tts_start_time).total_seconds() * 1000)

            # Create voice message record for agent response
            voice_message_id = await self.db.create_voice_message(
                session_id=self.session_id,
                transcript=text,
                role='agent',
                audio_format=self.tts_audio_format,
                audio_size_bytes=len(complete_audio),
                metadata={
                    "tts_generation_time_ms": tts_generation_time,
                    "tts_provider": self.tts_provider,
                    "voice_id": self.tts_voice_id,
                    "emotion": emotion
                }
            )

            # Save audio file using voice_message_id
            audio_file_path = self.audio_storage.save_agent_response(
                audio_data=complete_audio,
                session_id=self.session_id,
                voice_message_id=voice_message_id,
                format=self.tts_audio_format
            )

            # Update voice_message with audio file path
            await self.db.update_voice_message_audio(
                voice_message_id=voice_message_id,
                audio_file_path=audio_file_path,
                audio_size_bytes=len(complete_audio)
            )

            logger.info(f"Saved agent audio to: {audio_file_path}")

            # Generate signed URL for audio playback
            audio_url = generate_audio_signed_url(audio_file_path)
            logger.info(f"🔗 Generated signed audio URL: {audio_url}")

            # Send as single binary frame
            await self.websocket.send_bytes(complete_audio)

            # Notify speaking completed with signed URL
            speaking_completed_payload = {
                "audio_size_bytes": len(complete_audio),
                "audio_file_path": audio_file_path,
                "audio_url": audio_url,
                "audio_duration_ms": None,  # Could calculate from audio if needed
                "voice_message_id": voice_message_id,
                "timestamp": datetime.utcnow().isoformat()
            }
            logger.info(f"📤 Sending speaking_completed event with audio_url: {audio_url}")

            await self._send_status("speaking_completed", speaking_completed_payload)

            # Publish newMessage notification to API Gateway for WebSocket delivery
            # API Gateway will store the conversation_message with voice_message_id reference
            await self._publish_agent_message_notification(text, voice_message_id)

            # Return to idle
            self.state = VoiceState.IDLE
            self.current_voice_message_id = None
            self.interaction_start_time = None

            logger.info(f"Completed speaking: {len(complete_audio)} bytes in {tts_generation_time}ms")

        except Exception as e:
            logger.error(f"Error generating TTS: {e}")
            await self._send_error("tts_generation_failed", str(e))
            self.state = VoiceState.IDLE

    async def _send_status(self, event_type: str, payload: dict):
        """
        Send JSON status message to client

        Args:
            event_type: Event type
            payload: Event payload
        """
        try:
            message = StatusMessage.create(event_type, **payload)
            await self.websocket.send_text(message.json())
        except Exception as e:
            logger.error(f"Error sending status: {e}")

    async def _send_error(self, code: str, message: str, severity: str = "error"):
        """
        Send error message to client

        Args:
            code: Error code
            message: Error message
            severity: Error severity
        """
        await self._send_status("error", {
            "code": code,
            "message": message,
            "severity": severity,
            "retry_possible": True
        })

    async def close(self, status: str = "completed"):
        """
        Clean up session resources

        Args:
            status: Final session status
        """
        try:
            logger.info(f"Closing voice session {self.session_id}")

            # Close AssemblyAI connection
            if self.assemblyai:
                await self.assemblyai.close()

            # Note: voice_sessions table removed - session tracking no longer needed

            logger.info(f"Voice session {self.session_id} closed")

        except Exception as e:
            logger.error(f"Error closing voice session: {e}")
