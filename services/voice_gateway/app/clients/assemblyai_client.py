"""
AssemblyAI Speech-to-Text Client
Handles real-time streaming and batch audio transcription
"""

import asyncio
import logging
import threading
from typing import Callable, Optional, Type, Iterator
import pybreaker

try:
    import assemblyai as aai
    from assemblyai.streaming.v3 import (
        BeginEvent,
        StreamingClient,
        StreamingClientOptions,
        StreamingError,
        StreamingEvents,
        StreamingParameters,
        StreamingSessionParameters,
        TerminationEvent,
        TurnEvent,
    )
except ImportError:
    # Graceful degradation if assemblyai not installed yet
    StreamingClient = None
    StreamingEvents = None
    aai = None
    logger = logging.getLogger(__name__)
    logger.warning("AssemblyAI package not installed. Run: pip install assemblyai")

from ..models import AudioFormat, TranscriptionResult
from ..config import settings

logger = logging.getLogger(__name__)


class AudioStreamBuffer:
    """Thread-safe buffer for audio chunks to feed to AssemblyAI

    Buffers audio to ensure chunks meet AssemblyAI's minimum duration requirement
    of 50-1000ms. We target 100ms chunks for optimal accuracy/latency balance.
    """

    # Minimum bytes to accumulate before sending (100ms at 16kHz, 16-bit mono)
    # 16000 samples/sec * 2 bytes/sample * 0.1 sec = 3200 bytes
    MIN_CHUNK_BYTES = 3200

    def __init__(self):
        self.queue = []
        self.lock = threading.Lock()
        self.condition = threading.Condition(self.lock)
        self.closed = False
        self._accumulator = b""  # Buffer for accumulating small chunks

    def write(self, chunk: bytes):
        """Add audio chunk to buffer, accumulating until minimum size reached"""
        with self.lock:
            if not self.closed:
                # Accumulate chunks
                self._accumulator += chunk

                # When we have enough data, queue it
                while len(self._accumulator) >= self.MIN_CHUNK_BYTES:
                    ready_chunk = self._accumulator[:self.MIN_CHUNK_BYTES]
                    self._accumulator = self._accumulator[self.MIN_CHUNK_BYTES:]
                    self.queue.append(ready_chunk)
                    self.condition.notify()

    def read(self, timeout: float = 0.1) -> Optional[bytes]:
        """Read next audio chunk from buffer"""
        with self.condition:
            if not self.queue and not self.closed:
                self.condition.wait(timeout)

            if self.queue:
                return self.queue.pop(0)
            return None

    def close(self):
        """Close the buffer and flush any remaining audio"""
        with self.lock:
            # Flush any remaining accumulated audio
            if self._accumulator and len(self._accumulator) > 0:
                # Only send if we have at least some meaningful audio (>20ms)
                if len(self._accumulator) >= 640:  # 20ms minimum
                    self.queue.append(self._accumulator)
                self._accumulator = b""

            self.closed = True
            self.condition.notify_all()

    def __iter__(self) -> Iterator[bytes]:
        """Iterate over audio chunks"""
        while not self.closed or self.queue:
            chunk = self.read()
            if chunk:
                yield chunk
            elif self.closed:
                break

# Circuit breaker for AssemblyAI API
assemblyai_breaker = pybreaker.CircuitBreaker(
    fail_max=3,  # Open circuit after 3 failures
    reset_timeout=30,  # Stay open for 30 seconds
    name="assemblyai_api"
)


class AssemblyAITranscriptionClient:
    """Client for AssemblyAI real-time and batch transcription"""

    # Codec mapping: client codec → AssemblyAI encoding (if needed)
    ENCODING_MAP = {
        "opus": "opus",
        "aac": "aac",
        "pcm": "pcm_s16le",
        "wav": "pcm_s16le"
    }

    def __init__(
        self,
        api_key: str,
        on_transcript: Callable,
        audio_format: AudioFormat,
        endpointing_ms: Optional[int] = None
    ):
        """
        Initialize AssemblyAI client for streaming transcription

        Args:
            api_key: AssemblyAI API key
            on_transcript: Async callback for transcription results
            audio_format: Audio format specification from client
            endpointing_ms: Silence detection timeout in ms (None = disabled)
        """
        self.api_key = api_key
        self.on_transcript = on_transcript
        self.audio_format = audio_format
        self.endpointing_ms = endpointing_ms

        self.client: Optional[StreamingClient] = None
        self.is_connected = False
        self.event_loop = None

        # Speaker detection: track first speaker ID
        self.first_speaker_id: Optional[str] = None
        self.speaker_detection_enabled = True  # Enable speaker detection

        # Audio streaming buffer
        self._audio_buffer: Optional[AudioStreamBuffer] = None
        self._streaming_task: Optional[asyncio.Task] = None

    async def start(self) -> bool:
        """
        Start AssemblyAI streaming transcription connection

        Returns:
            bool: True if connection started successfully
        """
        try:
            # Store the event loop for callbacks
            self.event_loop = asyncio.get_running_loop()

            # Check circuit breaker state
            if assemblyai_breaker.current_state == pybreaker.STATE_OPEN:
                logger.error("❌ AssemblyAI circuit breaker is OPEN - too many recent failures")
                return False

            if StreamingClient is None:
                logger.error("❌ AssemblyAI package not installed")
                return False

            logger.info(
                f"Starting AssemblyAI with sample_rate={self.audio_format.sample_rate}, "
                f"channels={self.audio_format.channels}, "
                f"speaker_detection={'enabled (first speaker only)' if self.speaker_detection_enabled else 'disabled'}"
            )

            # Create streaming client with circuit breaker
            @assemblyai_breaker
            def _create_client():
                return StreamingClient(
                    StreamingClientOptions(
                        api_key=self.api_key,
                        api_host="streaming.assemblyai.com",
                    )
                )

            self.client = _create_client()

            # Register event handlers
            self.client.on(StreamingEvents.Begin, self._on_begin)
            self.client.on(StreamingEvents.Turn, self._on_turn)
            self.client.on(StreamingEvents.Termination, self._on_terminated)
            self.client.on(StreamingEvents.Error, self._on_error)

            # Get encoding from codec mapping
            encoding = self.ENCODING_MAP.get(self.audio_format.codec.lower(), "pcm_s16le")
            logger.info(f"Using encoding: {encoding} for codec: {self.audio_format.codec}")

            # Connect with streaming parameters
            streaming_params = StreamingParameters(
                sample_rate=self.audio_format.sample_rate,
                encoding=encoding,  # Specify encoding for PCM audio
                format_turns=True,  # Enable turn formatting
                enable_interim_results=True,  # Enable interim results
            )

            self.client.connect(streaming_params)
            self.is_connected = True

            # Initialize audio buffer
            self._audio_buffer = AudioStreamBuffer()

            # Start streaming in background thread
            self._streaming_task = asyncio.create_task(self._run_streaming())

            logger.info("✅ AssemblyAI connection started successfully")
            return True

        except pybreaker.CircuitBreakerError:
            logger.error("❌ AssemblyAI circuit breaker is OPEN - service unavailable")
            return False
        except Exception as e:
            logger.error(f"❌ Error starting AssemblyAI: {e}")
            return False

    async def send_audio(self, audio_data: bytes):
        """
        Send audio chunk to AssemblyAI for transcription

        Args:
            audio_data: Raw audio bytes
        """
        if not self.is_connected or not self.client:
            logger.warning("Cannot send audio: AssemblyAI not connected")
            return

        try:
            # Write to buffer for streaming
            if self._audio_buffer:
                self._audio_buffer.write(audio_data)

        except Exception as e:
            logger.error(f"Error sending audio to AssemblyAI: {e}")

    async def _run_streaming(self):
        """Run the streaming process in executor (client.stream is blocking)"""
        try:
            if self.client and self._audio_buffer:
                # Run in executor since client.stream is blocking
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    self.client.stream,
                    self._audio_buffer
                )
        except Exception as e:
            logger.error(f"Error in streaming task: {e}")
            self.is_connected = False

    def _on_begin(self, client: Type[StreamingClient], event: BeginEvent):
        """Handle session start event"""
        try:
            logger.info(f"AssemblyAI session started: {event.id}")
            # Reset first speaker tracking for new session
            self.first_speaker_id = None
        except Exception as e:
            logger.error(f"Error in AssemblyAI begin handler: {e}")

    def _on_turn(self, client: Type[StreamingClient], event: TurnEvent):
        """Handle turn/transcript events"""
        try:
            transcript_text = event.transcript
            is_final = event.end_of_turn
            is_formatted = getattr(event, 'turn_is_formatted', True)  # Default to True if not present

            # Ignore empty transcripts
            if not transcript_text or not transcript_text.strip():
                return

            # Skip unformatted final turns - wait for the formatted version
            # When format_turns=True, AssemblyAI sends both unformatted and formatted versions
            # We only want to process the formatted one to avoid duplicates
            if is_final and not is_formatted:
                logger.debug(f"Skipping unformatted final turn, waiting for formatted version: '{transcript_text[:30]}...'")
                return

            # Speaker detection: only process first speaker
            if self.speaker_detection_enabled and hasattr(event, 'speaker_id'):
                speaker_id = getattr(event, 'speaker_id', None)

                if speaker_id is not None:
                    if self.first_speaker_id is None:
                        # First speaker detected - lock onto this speaker
                        self.first_speaker_id = speaker_id
                        logger.info(f"🔒 Locked onto first speaker: {speaker_id}")
                    elif speaker_id != self.first_speaker_id:
                        # Different speaker - ignore this transcript
                        logger.debug(f"Ignoring speaker {speaker_id} (not first speaker {self.first_speaker_id})")
                        return

            # Default confidence (AssemblyAI doesn't provide per-turn confidence in streaming)
            confidence = 0.9 if is_final else 0.7

            logger.debug(
                f"Transcription: '{transcript_text}' "
                f"(final={is_final}, formatted={is_formatted}, confidence={confidence:.2f})"
            )

            # Create transcription result
            transcription_result = TranscriptionResult(
                text=transcript_text,
                is_final=is_final,
                confidence=confidence
            )

            # Schedule the callback on the event loop
            if self.event_loop:
                asyncio.run_coroutine_threadsafe(
                    self.on_transcript(transcription_result),
                    self.event_loop
                )

        except Exception as e:
            logger.error(f"Error processing AssemblyAI transcript: {e}")

    def _on_terminated(self, client: Type[StreamingClient], event: TerminationEvent):
        """Handle session termination"""
        try:
            logger.info(
                f"AssemblyAI session terminated: {event.audio_duration_seconds} "
                f"seconds of audio processed"
            )
            self.is_connected = False
        except Exception as e:
            logger.error(f"Error in AssemblyAI termination handler: {e}")

    def _on_error(self, client: Type[StreamingClient], error: StreamingError):
        """Handle AssemblyAI errors"""
        try:
            logger.error(f"AssemblyAI error: {error}")
            # Don't automatically disconnect on error - let the session decide
        except Exception as e:
            logger.error(f"Error in AssemblyAI error handler: {e}")

    async def close(self):
        """Close AssemblyAI connection"""
        self.is_connected = False

        # Close audio buffer
        if self._audio_buffer:
            self._audio_buffer.close()

        # Wait for streaming task to complete
        if self._streaming_task and not self._streaming_task.done():
            try:
                await asyncio.wait_for(self._streaming_task, timeout=2.0)
            except asyncio.TimeoutError:
                logger.warning("Streaming task did not complete in time")
                self._streaming_task.cancel()

        # Disconnect client
        if self.client:
            try:
                self.client.disconnect(terminate=True)
                logger.info("AssemblyAI connection closed gracefully")
            except Exception as e:
                logger.error(f"Error closing AssemblyAI connection: {e}")


class AssemblyAIBatchClient:
    """Client for batch/file-based transcription with AssemblyAI"""

    def __init__(self, api_key: str):
        """
        Initialize AssemblyAI batch client

        Args:
            api_key: AssemblyAI API key
        """
        self.api_key = api_key
        aai.settings.api_key = api_key

    async def transcribe_file(
        self,
        audio_file_url: str,
        speech_model: str = "universal",
        speaker_labels: bool = False
    ) -> dict:
        """
        Transcribe an audio file (URL or local path)

        Args:
            audio_file_url: URL or local file path to audio
            speech_model: AssemblyAI speech model to use
            speaker_labels: Enable speaker diarization

        Returns:
            dict: Transcription result with text, status, and metadata
        """
        try:
            config = aai.TranscriptionConfig(
                speech_model=getattr(aai.SpeechModel, speech_model, aai.SpeechModel.universal),
                speaker_labels=speaker_labels
            )

            # Run in executor since transcribe is synchronous
            transcriber = aai.Transcriber(config=config)
            transcript = await asyncio.get_event_loop().run_in_executor(
                None,
                transcriber.transcribe,
                audio_file_url
            )

            if transcript.status == "error":
                raise RuntimeError(f"Transcription failed: {transcript.error}")

            result = {
                "status": transcript.status,
                "text": transcript.text,
                "confidence": getattr(transcript, "confidence", None),
                "audio_duration": getattr(transcript, "audio_duration", None),
            }

            # Add speaker labels if requested
            if speaker_labels and hasattr(transcript, "utterances"):
                result["utterances"] = [
                    {
                        "speaker": u.speaker,
                        "text": u.text,
                        "start": u.start,
                        "end": u.end,
                        "confidence": u.confidence
                    }
                    for u in transcript.utterances
                ]

            return result

        except Exception as e:
            logger.error(f"Error transcribing file with AssemblyAI: {e}")
            raise
