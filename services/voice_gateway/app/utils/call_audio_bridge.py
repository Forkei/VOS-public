"""
Call Audio Bridge

Bridges audio between api_gateway call WebSocket and voice_gateway STT/TTS processing.
This allows call mode to work without a direct WebSocket connection to voice_gateway.
"""

import asyncio
import base64
import json
import logging
import requests
from datetime import datetime
from typing import Dict, Optional, Any, Tuple
from dataclasses import dataclass, field

from ..clients.database_client import DatabaseClient
from ..clients.rabbitmq_client import RabbitMQClient
from ..clients.assemblyai_client import AssemblyAITranscriptionClient
from ..clients.elevenlabs_client import ElevenLabsTTSClient
from ..clients.cartesia_client import CartesiaTTSClient
from ..clients.cartesia_streaming_client import CartesiaStreamingClient
from ..models import AudioFormat
from ..config import settings

# Default audio format for call mode (PCM 16kHz mono - standard for phone calls)
DEFAULT_CALL_AUDIO_FORMAT = AudioFormat(
    codec="pcm",
    container="wav",
    sample_rate=16000,
    channels=1,
    bitrate=16000
)

logger = logging.getLogger(__name__)


def pcm_to_wav(pcm_data: bytes, sample_rate: int = 24000, channels: int = 1, bits_per_sample: int = 16) -> bytes:
    """
    Convert raw PCM audio to WAV format by adding a proper header.

    Args:
        pcm_data: Raw PCM audio bytes
        sample_rate: Sample rate in Hz
        channels: Number of audio channels
        bits_per_sample: Bits per sample (16 for PCM s16le)

    Returns:
        Complete WAV file as bytes
    """
    import struct

    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    data_size = len(pcm_data)

    # WAV header (44 bytes)
    header = struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF',                    # ChunkID
        36 + data_size,             # ChunkSize
        b'WAVE',                    # Format
        b'fmt ',                    # Subchunk1ID
        16,                         # Subchunk1Size (PCM)
        1,                          # AudioFormat (1 = PCM)
        channels,                   # NumChannels
        sample_rate,                # SampleRate
        byte_rate,                  # ByteRate
        block_align,                # BlockAlign
        bits_per_sample,            # BitsPerSample
        b'data',                    # Subchunk2ID
        data_size                   # Subchunk2Size
    )

    return header + pcm_data


@dataclass
class CallBridgeSession:
    """Represents a bridged call session for STT/TTS processing"""
    session_id: str
    call_id: str
    current_agent_id: Optional[str] = "primary_agent"
    stt_client: Optional[AssemblyAITranscriptionClient] = None
    tts_client: Any = None  # Default TTS client (buffered fallback)
    tts_provider: str = "cartesia"  # Default to Cartesia for lower latency
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_audio_at: Optional[datetime] = None
    is_active: bool = True
    # Per-agent TTS clients: agent_id -> (tts_client, voice_id)
    agent_tts_clients: Dict[str, Any] = field(default_factory=dict)
    # Cache of agent voice settings to avoid repeated API calls
    agent_voice_cache: Dict[str, dict] = field(default_factory=dict)
    # Transcription debounce: accumulate text and wait for silence before sending to agent
    pending_transcription: str = ""
    debounce_task: Optional[asyncio.Task] = None
    # TTS playback state for audio ducking
    is_tts_playing: bool = False
    # Twilio-specific fields
    audio_source: str = "web"  # "web" or "twilio"
    # Fast mode for low-latency responses
    fast_mode: bool = False
    twilio_call_sid: Optional[str] = None
    twilio_stream_sid: Optional[str] = None


class CallAudioBridge:
    """
    Bridges call audio between api_gateway and voice_gateway.

    Handles:
    - STT processing of user audio received via RabbitMQ
    - TTS generation for agent speech
    - Sending results back to api_gateway via HTTP
    """

    # Debounce delay: wait this long after last transcription before sending to agent
    # This allows user to finish speaking before agent responds
    # 1.2s gives slower speakers time to complete their thoughts
    TRANSCRIPTION_DEBOUNCE_SECONDS = 1.2

    def __init__(self, db_client: DatabaseClient, rabbitmq_client: RabbitMQClient):
        self.db = db_client
        self.rabbitmq = rabbitmq_client

        # Active bridged sessions: call_id -> CallBridgeSession
        self._sessions: Dict[str, CallBridgeSession] = {}
        self._lock = asyncio.Lock()

        # API Gateway URL for callbacks
        self.api_gateway_url = "http://api_gateway:8000"

        # Internal API key for authentication
        self._internal_api_key = self._load_internal_api_key()

        # Shared Cartesia streaming client for low-latency TTS
        # Pre-connect to save ~200ms per request
        self._cartesia_streaming: Optional[CartesiaStreamingClient] = None
        if settings.CARTESIA_API_KEY:
            self._cartesia_streaming = CartesiaStreamingClient(
                api_key=settings.CARTESIA_API_KEY,
                voice_id=settings.CARTESIA_VOICE_ID,
                model="sonic-3",
                sample_rate=24000,  # Good balance of quality/speed
            )

        logger.info("CallAudioBridge initialized (with Cartesia streaming support)")

    async def initialize(self):
        """
        Async initialization - pre-connect Cartesia WebSocket.
        Call this after creating the bridge to ensure Cartesia is ready.
        """
        if self._cartesia_streaming:
            try:
                connected = await self._cartesia_streaming.connect()
                if connected:
                    logger.info("✅ Cartesia streaming pre-connected and ready")
                else:
                    logger.warning("⚠️ Cartesia streaming failed to pre-connect, will retry on first use")
            except Exception as e:
                logger.warning(f"⚠️ Cartesia streaming pre-connect error: {e}, will retry on first use")

    def _load_internal_api_key(self) -> Optional[str]:
        """Load internal API key from shared volume"""
        try:
            with open("/shared/internal_api_key", "r") as f:
                return f.read().strip()
        except Exception as e:
            logger.warning(f"Could not load internal API key: {e}")
            return None

    async def _get_agent_voice(self, session_id: str, agent_id: str) -> Optional[dict]:
        """
        Get voice settings for an agent from the API.

        Returns dict with: tts_provider, voice_id, voice_name
        Falls back to default voice if API call fails.
        """
        try:
            # Look up user_id from session
            # For now, we'll use session_id as user_id since they're the same in VOS
            user_id = session_id

            headers = {"Content-Type": "application/json"}
            if self._internal_api_key:
                headers["X-Internal-Key"] = self._internal_api_key

            response = requests.get(
                f"{self.api_gateway_url}/api/v1/agent-voices/effective/{user_id}/{agent_id}",
                headers=headers,
                timeout=5
            )

            if response.status_code == 200:
                voice_data = response.json()
                logger.info(f"📞 Got voice for agent {agent_id}: {voice_data.get('voice_name')} ({voice_data.get('voice_id')[:8]}...)")
                return voice_data
            else:
                logger.warning(f"📞 Failed to get agent voice (status {response.status_code}), using default")
                return None

        except Exception as e:
            logger.warning(f"📞 Error fetching agent voice: {e}, using default")
            return None

    def _get_tts_client_for_voice(self, tts_provider: str, voice_id: str) -> Any:
        """Create a TTS client for a specific voice."""
        if tts_provider == "cartesia":
            return CartesiaTTSClient(
                api_key=settings.CARTESIA_API_KEY,
                voice_id=voice_id
            )
        else:
            return ElevenLabsTTSClient(
                api_key=settings.ELEVENLABS_API_KEY,
                voice_id=voice_id
            )

    async def _get_or_create_session(
        self,
        session_id: str,
        call_id: str,
        fast_mode: bool = False
    ) -> CallBridgeSession:
        """Get existing session or create a new one"""
        async with self._lock:
            if call_id not in self._sessions:
                logger.info(f"📞 Creating new bridge session for call {call_id} (fast_mode={fast_mode})")

                # Create STT client with default call audio format
                # Note: The callback must return a coroutine (not a Task) because
                # asyncio.run_coroutine_threadsafe is used to schedule it from a background thread
                stt_client = AssemblyAITranscriptionClient(
                    api_key=settings.ASSEMBLYAI_API_KEY,
                    on_transcript=lambda result, sid=session_id, cid=call_id: self._on_transcript(sid, cid, result),
                    audio_format=DEFAULT_CALL_AUDIO_FORMAT
                )

                # Create TTS client based on settings
                tts_provider = settings.TTS_PROVIDER.lower()
                if tts_provider == "cartesia":
                    tts_client = CartesiaTTSClient(
                        api_key=settings.CARTESIA_API_KEY,
                        voice_id=settings.CARTESIA_VOICE_ID
                    )
                else:
                    tts_client = ElevenLabsTTSClient(
                        api_key=settings.ELEVENLABS_API_KEY,
                        voice_id=settings.ELEVENLABS_VOICE_ID
                    )

                session = CallBridgeSession(
                    session_id=session_id,
                    call_id=call_id,
                    stt_client=stt_client,
                    tts_client=tts_client,
                    tts_provider=tts_provider,
                    fast_mode=fast_mode
                )

                # Start STT client
                try:
                    await stt_client.start()
                    logger.info(f"📞 STT client started for call {call_id}")
                except Exception as e:
                    logger.error(f"Failed to start STT client for {call_id}: {e}")

                self._sessions[call_id] = session

            return self._sessions[call_id]

    async def initialize_twilio_session(
        self,
        session_id: str,
        call_id: str,
        twilio_call_sid: Optional[str] = None,
        twilio_stream_sid: Optional[str] = None
    ):
        """
        Initialize a Twilio call session before any audio is received.

        This ensures the stream_sid is available for TTS before the caller speaks.
        Critical for greeting messages that need to play immediately.

        Args:
            session_id: User session ID
            call_id: Call ID
            twilio_call_sid: Twilio call SID
            twilio_stream_sid: Twilio stream SID
        """
        try:
            session = await self._get_or_create_session(session_id, call_id)

            # Set Twilio-specific fields
            session.audio_source = "twilio"
            if twilio_call_sid:
                session.twilio_call_sid = twilio_call_sid
            if twilio_stream_sid:
                session.twilio_stream_sid = twilio_stream_sid

            logger.info(f"📞 Initialized Twilio session for call {call_id}: stream_sid={twilio_stream_sid}")

        except Exception as e:
            logger.error(f"Error initializing Twilio session for call {call_id}: {e}")

    async def process_audio(
        self,
        session_id: str,
        call_id: str,
        audio_data: bytes,
        source: str = "web",
        twilio_call_sid: Optional[str] = None,
        twilio_stream_sid: Optional[str] = None,
        fast_mode: bool = False
    ):
        """
        Process incoming audio chunk from user.

        Args:
            session_id: User session ID
            call_id: Call ID
            audio_data: Raw audio bytes
            source: Audio source ("web" or "twilio")
            twilio_call_sid: Twilio call SID (if source is twilio)
            twilio_stream_sid: Twilio stream SID (if source is twilio)
            fast_mode: Enable fast mode for low-latency responses
        """
        try:
            session = await self._get_or_create_session(session_id, call_id, fast_mode=fast_mode)
            session.last_audio_at = datetime.utcnow()

            # Update Twilio fields if this is a Twilio call
            if source == "twilio":
                session.audio_source = "twilio"
                if twilio_call_sid:
                    session.twilio_call_sid = twilio_call_sid
                if twilio_stream_sid:
                    session.twilio_stream_sid = twilio_stream_sid

            # Send to STT client
            if session.stt_client:
                await session.stt_client.send_audio(audio_data)
                logger.debug(f"📞 Sent {len(audio_data)} bytes to STT for call {call_id}")

        except Exception as e:
            logger.error(f"Error processing audio for call {call_id}: {e}")

    async def _on_transcript(
        self,
        session_id: str,
        call_id: str,
        result: Any
    ):
        """
        Handle transcription result from STT.

        Sends transcription to:
        1. api_gateway (to forward to user's WebSocket) - immediately for display
        2. Agent (if final transcription) - debounced to wait for complete utterance

        Uses debouncing to accumulate final transcriptions before sending to agent.
        This prevents the agent from responding to partial sentences.
        """
        try:
            text = result.text if hasattr(result, 'text') else str(result)
            is_final = result.is_final if hasattr(result, 'is_final') else True
            confidence = result.confidence if hasattr(result, 'confidence') else None

            session = self._sessions.get(call_id)

            # Audio ducking: ignore transcriptions while TTS is playing
            # This prevents echo from agent speech being transcribed
            if session and session.is_tts_playing:
                logger.debug(f"📞 Ignoring transcription during TTS playback: '{text[:30]}...'")
                return

            logger.info(f"📞 Transcription for call {call_id}: '{text}' (final={is_final})")

            # Send to api_gateway to forward to user's WebSocket (immediate display)
            await self._send_transcription_to_gateway(
                session_id=session_id,
                call_id=call_id,
                text=text,
                is_final=is_final,
                confidence=confidence
            )

            # If final, add to debounce buffer and schedule send to agent
            if is_final and text.strip() and session:
                await self._debounced_send_to_agent(session, session_id, call_id, text)

        except Exception as e:
            logger.error(f"Error handling transcript for call {call_id}: {e}")

    async def _debounced_send_to_agent(
        self,
        session: CallBridgeSession,
        session_id: str,
        call_id: str,
        text: str
    ):
        """
        Accumulate transcription and send to agent after debounce delay.

        This allows the user to finish speaking before the agent responds,
        preventing responses to partial sentences.
        """
        # Cancel any existing debounce task
        if session.debounce_task and not session.debounce_task.done():
            session.debounce_task.cancel()
            try:
                await session.debounce_task
            except asyncio.CancelledError:
                pass

        # Accumulate text (add space if there's already pending text)
        if session.pending_transcription:
            session.pending_transcription += " " + text
        else:
            session.pending_transcription = text

        logger.debug(f"📞 Accumulated transcription: '{session.pending_transcription}'")

        # Start new debounce task
        async def send_after_delay():
            try:
                await asyncio.sleep(self.TRANSCRIPTION_DEBOUNCE_SECONDS)

                # Send accumulated text to agent
                if session.pending_transcription.strip():
                    accumulated_text = session.pending_transcription
                    session.pending_transcription = ""  # Clear before sending

                    logger.info(f"📞 Debounce complete, sending to agent: '{accumulated_text}'")
                    await self._send_to_agent(
                        session_id=session_id,
                        call_id=call_id,
                        text=accumulated_text
                    )
            except asyncio.CancelledError:
                # Task was cancelled because more text arrived
                logger.debug(f"📞 Debounce cancelled, more text arriving")
                raise

        session.debounce_task = asyncio.create_task(send_after_delay())

    async def _send_transcription_to_gateway(
        self,
        session_id: str,
        call_id: str,
        text: str,
        is_final: bool,
        confidence: Optional[float]
    ):
        """Send transcription to api_gateway via HTTP callback"""
        try:
            headers = {"Content-Type": "application/json"}
            if self._internal_api_key:
                headers["X-Internal-Key"] = self._internal_api_key

            response = requests.post(
                f"{self.api_gateway_url}/api/v1/calls/internal/transcription",
                json={
                    "session_id": session_id,
                    "call_id": call_id,
                    "text": text,
                    "is_final": is_final,
                    "confidence": confidence
                },
                headers=headers,
                timeout=5
            )

            if response.status_code == 200:
                logger.debug(f"📞 Sent transcription to gateway for {session_id}")
            else:
                logger.warning(f"📞 Failed to send transcription: {response.status_code}")

        except Exception as e:
            logger.error(f"Error sending transcription to gateway: {e}")

    async def _send_to_agent(
        self,
        session_id: str,
        call_id: str,
        text: str
    ):
        """Send user's transcribed speech to agent via RabbitMQ"""
        try:
            session = self._sessions.get(call_id)
            current_agent = session.current_agent_id if session else "primary_agent"
            fast_mode = session.fast_mode if session else False

            await self.rabbitmq.publish_user_message(
                session_id=session_id,
                content=text,
                voice_metadata={
                    "call_id": call_id,
                    "is_call_mode": True,
                    "fast_mode": fast_mode,
                    "current_agent": current_agent,
                    "content_type": "call_transcript"
                }
            )

            logger.info(f"📞 Sent transcription to agent for call {call_id}: '{text[:50]}...' (fast_mode={fast_mode})")

        except Exception as e:
            logger.error(f"Error sending to agent: {e}")

    async def agent_speak(
        self,
        session_id: str,
        call_id: str,
        text: str,
        agent_id: Optional[str] = None,
        emotion: str = "neutral",
        fast_mode: bool = False
    ):
        """
        Generate TTS for agent speech and send to user.

        Uses Cartesia streaming for low-latency generation when available.
        Implements audio ducking by setting is_tts_playing flag.

        Args:
            session_id: User session ID
            call_id: Call ID
            text: Text to speak
            agent_id: Speaking agent ID
            emotion: Emotional tone
            fast_mode: Enable fast mode for low-latency responses
        """
        try:
            session = await self._get_or_create_session(session_id, call_id, fast_mode=fast_mode)

            # Update fast_mode if it changed
            if fast_mode and not session.fast_mode:
                session.fast_mode = fast_mode
                logger.info(f"⚡ Fast mode enabled for call {call_id}")

            if agent_id:
                session.current_agent_id = agent_id

            logger.info(f"📞 Generating TTS for call {call_id}, agent {agent_id}: '{text[:50]}...'")

            # Audio ducking: set flag to ignore transcriptions during TTS playback
            session.is_tts_playing = True

            audio_data = None

            # Try streaming Cartesia first (lowest latency)
            if self._cartesia_streaming:
                try:
                    # Ensure WebSocket is connected
                    if not self._cartesia_streaming.is_connected:
                        await self._cartesia_streaming.connect()

                    # Get agent voice settings if available
                    voice_settings = session.agent_voice_cache.get(agent_id) if agent_id else None
                    if agent_id and not voice_settings:
                        voice_settings = await self._get_agent_voice(session_id, agent_id)
                        if voice_settings:
                            session.agent_voice_cache[agent_id] = voice_settings

                    # Use agent's voice or default
                    voice_id = voice_settings.get("voice_id") if voice_settings else None

                    # If agent has a different voice, we need a separate streaming client
                    # For now, use the shared client with default voice for speed
                    # TODO: Support per-agent streaming clients

                    # Stream and collect audio chunks
                    pcm_chunks = []
                    async for chunk in self._cartesia_streaming.generate_audio_stream(
                        text=text,
                        emotion=emotion,
                        speed=1.0,
                    ):
                        pcm_chunks.append(chunk)

                    if pcm_chunks:
                        # Combine chunks and convert to WAV
                        pcm_data = b"".join(pcm_chunks)
                        audio_data = pcm_to_wav(pcm_data, sample_rate=24000)
                        logger.info(f"📞 Streamed {len(pcm_chunks)} chunks, {len(audio_data)} bytes WAV")

                except Exception as e:
                    logger.warning(f"📞 Cartesia streaming failed, falling back to buffered: {e}")
                    audio_data = None

            # Fallback to buffered TTS if streaming failed or unavailable
            if not audio_data:
                tts_client = await self._get_agent_tts_client(session, session_id, agent_id)
                audio_data = await tts_client.generate_audio(text, emotion)

            if audio_data:
                logger.info(f"📞 Generated {len(audio_data)} bytes of TTS audio")

                # Send to api_gateway to forward to user's WebSocket
                await self._send_tts_to_gateway(
                    session_id=session_id,
                    call_id=call_id,
                    audio_data=audio_data,
                    text=text,
                    agent_id=agent_id
                )

                # Schedule reset of is_tts_playing after estimated playback duration
                # Estimate: ~3 words per second at normal speaking pace, + 1s buffer
                word_count = len(text.split())
                estimated_duration = max(2.0, (word_count / 3.0) + 1.0)
                asyncio.create_task(self._reset_tts_playing(session, estimated_duration))
            else:
                logger.warning(f"📞 TTS returned no audio for call {call_id}")
                session.is_tts_playing = False

        except Exception as e:
            logger.error(f"Error in agent_speak for call {call_id}: {e}")
            # Ensure flag is reset on error
            if call_id in self._sessions:
                self._sessions[call_id].is_tts_playing = False

    async def _reset_tts_playing(self, session: CallBridgeSession, delay: float):
        """Reset is_tts_playing flag after estimated playback duration."""
        try:
            await asyncio.sleep(delay)
            session.is_tts_playing = False
            logger.debug(f"📞 TTS playback flag reset after {delay:.1f}s")
        except asyncio.CancelledError:
            session.is_tts_playing = False

    async def _get_agent_tts_client(
        self,
        session: CallBridgeSession,
        session_id: str,
        agent_id: Optional[str]
    ) -> Any:
        """
        Get or create a TTS client for a specific agent.

        Caches TTS clients per agent to avoid recreating them.

        Args:
            session: The call bridge session
            session_id: User session ID (used to look up voice preferences)
            agent_id: Agent ID to get voice for

        Returns:
            TTS client configured with the agent's voice
        """
        # Use default client if no agent specified
        if not agent_id:
            return session.tts_client

        # Check if we already have a cached client for this agent
        if agent_id in session.agent_tts_clients:
            return session.agent_tts_clients[agent_id]

        # Try to get agent-specific voice settings
        voice_settings = await self._get_agent_voice(session_id, agent_id)

        if voice_settings:
            # Create a new TTS client with the agent's voice
            tts_provider = voice_settings.get("tts_provider", "elevenlabs")
            voice_id = voice_settings.get("voice_id")
            voice_name = voice_settings.get("voice_name", "Unknown")

            if voice_id:
                logger.info(f"📞 Creating TTS client for agent {agent_id} with voice '{voice_name}'")
                tts_client = self._get_tts_client_for_voice(tts_provider, voice_id)
                session.agent_tts_clients[agent_id] = tts_client
                session.agent_voice_cache[agent_id] = voice_settings
                return tts_client

        # Fall back to session default
        logger.info(f"📞 Using default TTS client for agent {agent_id}")
        return session.tts_client

    async def _send_tts_to_gateway(
        self,
        session_id: str,
        call_id: str,
        audio_data: bytes,
        text: Optional[str] = None,
        agent_id: Optional[str] = None
    ):
        """Send TTS audio to api_gateway or Twilio depending on audio source"""
        try:
            session = self._sessions.get(call_id)

            # Check if this is a Twilio call
            if session and session.audio_source == "twilio":
                await self._send_tts_to_twilio(
                    call_id=call_id,
                    audio_data=audio_data,
                    twilio_call_sid=session.twilio_call_sid,
                    twilio_stream_sid=session.twilio_stream_sid
                )
                return

            # Regular web call - send to API Gateway
            headers = {"Content-Type": "application/json"}
            if self._internal_api_key:
                headers["X-Internal-Key"] = self._internal_api_key

            # Encode audio as base64
            audio_b64 = base64.b64encode(audio_data).decode('utf-8')

            response = requests.post(
                f"{self.api_gateway_url}/api/v1/calls/internal/tts-audio",
                json={
                    "session_id": session_id,
                    "call_id": call_id,
                    "audio_b64": audio_b64,
                    "text": text,
                    "agent_id": agent_id
                },
                headers=headers,
                timeout=30  # Longer timeout for audio
            )

            if response.status_code == 200:
                logger.info(f"📞 Sent {len(audio_data)} bytes TTS audio to gateway for {session_id}")
            else:
                logger.warning(f"📞 Failed to send TTS audio: {response.status_code}")

        except Exception as e:
            logger.error(f"Error sending TTS to gateway: {e}")

    async def _send_tts_to_twilio(
        self,
        call_id: str,
        audio_data: bytes,
        twilio_call_sid: Optional[str] = None,
        twilio_stream_sid: Optional[str] = None
    ):
        """
        Send TTS audio to Twilio Gateway via RabbitMQ.

        The Twilio Gateway will convert the audio to mulaw and stream
        it to the caller via the Twilio Media Streams WebSocket.

        Args:
            call_id: VOS call ID
            audio_data: Audio data (WAV or MP3 format)
            twilio_call_sid: Twilio's call SID
            twilio_stream_sid: Twilio's stream SID
        """
        try:
            import aio_pika
            import io
            from pydub import AudioSegment

            # Validate we have the required Twilio identifiers
            if not twilio_call_sid:
                logger.error(f"📞❌ Cannot send TTS to Twilio: missing twilio_call_sid for call {call_id}")
                return
            if not twilio_stream_sid:
                logger.error(f"📞❌ Cannot send TTS to Twilio: missing twilio_stream_sid for call {call_id}")
                return

            logger.info(f"📞🔊 Preparing TTS for Twilio: call={call_id}, call_sid={twilio_call_sid}, stream_sid={twilio_stream_sid}, audio_size={len(audio_data)} bytes")

            # Detect audio format and convert to PCM
            # WAV starts with "RIFF", MP3 starts with 0xFF 0xFB or "ID3"
            is_wav = audio_data[:4] == b'RIFF'
            is_mp3 = audio_data[:3] == b'ID3' or (len(audio_data) > 1 and audio_data[0] == 0xFF and (audio_data[1] & 0xE0) == 0xE0)

            try:
                import audioop
            except ImportError:
                import audioop_lts as audioop

            if is_mp3:
                # Convert MP3 to PCM using pydub
                logger.debug(f"📞🔊 Detected MP3 format, converting to PCM")
                audio = AudioSegment.from_mp3(io.BytesIO(audio_data))
                # Convert to mono 8kHz for Twilio
                audio = audio.set_channels(1).set_frame_rate(8000).set_sample_width(2)
                pcm_8khz = audio.raw_data
                logger.debug(f"📞🔊 Converted MP3 to {len(pcm_8khz)} bytes PCM at 8kHz")
            elif is_wav:
                # Extract PCM from WAV (skip 44-byte header) and downsample
                logger.debug(f"📞🔊 Detected WAV format, extracting PCM")
                pcm_data = audio_data[44:] if len(audio_data) > 44 else audio_data
                # Ensure we have complete frames (2 bytes per sample for 16-bit)
                if len(pcm_data) % 2 != 0:
                    pcm_data = pcm_data[:-1]
                logger.debug(f"📞🔊 Extracted {len(pcm_data)} bytes PCM from {len(audio_data)} bytes WAV")
                # Downsample from 24kHz to 8kHz
                pcm_8khz, _ = audioop.ratecv(pcm_data, 2, 1, 24000, 8000, None)
            else:
                # Unknown format - try to use pydub to auto-detect
                logger.warning(f"📞🔊 Unknown audio format, attempting auto-detection")
                try:
                    audio = AudioSegment.from_file(io.BytesIO(audio_data))
                    audio = audio.set_channels(1).set_frame_rate(8000).set_sample_width(2)
                    pcm_8khz = audio.raw_data
                except Exception as e:
                    logger.error(f"📞❌ Failed to decode audio: {e}")
                    return

            # Convert PCM to mulaw for Twilio
            mulaw_data = audioop.lin2ulaw(pcm_8khz, 2)

            # Encode to base64
            audio_b64 = base64.b64encode(mulaw_data).decode('utf-8')

            # Publish to twilio_tts_queue
            message = {
                "call_sid": twilio_call_sid,
                "stream_sid": twilio_stream_sid,
                "audio_data": audio_b64,
                "call_id": call_id
            }

            # Use RabbitMQ to send to Twilio Gateway
            if self.rabbitmq.channel:
                await self.rabbitmq.channel.default_exchange.publish(
                    aio_pika.Message(
                        body=json.dumps(message).encode(),
                        content_type="application/json",
                        delivery_mode=aio_pika.DeliveryMode.PERSISTENT
                    ),
                    routing_key="twilio_tts_queue"
                )
                logger.info(f"📞🔊 Sent {len(mulaw_data)} bytes TTS audio to Twilio for call {twilio_call_sid}")
            else:
                logger.error("RabbitMQ channel not available for Twilio TTS")

        except Exception as e:
            logger.error(f"Error sending TTS to Twilio: {e}", exc_info=True)

    async def end_session(self, call_id: str):
        """End a bridged call session"""
        async with self._lock:
            if call_id in self._sessions:
                session = self._sessions[call_id]

                # Cancel any pending debounce task
                if session.debounce_task and not session.debounce_task.done():
                    session.debounce_task.cancel()
                    try:
                        await session.debounce_task
                    except asyncio.CancelledError:
                        pass

                # Close STT client
                if session.stt_client:
                    try:
                        await session.stt_client.close()
                    except Exception as e:
                        logger.error(f"Error closing STT client for {call_id}: {e}")

                session.is_active = False
                session.is_tts_playing = False
                del self._sessions[call_id]

                logger.info(f"📞 Ended bridge session for call {call_id}")

    def get_active_sessions(self) -> int:
        """Get count of active bridged sessions"""
        return len(self._sessions)
