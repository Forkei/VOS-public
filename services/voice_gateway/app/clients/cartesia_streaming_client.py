"""
Cartesia AI Streaming Text-to-Speech Client
Uses WebSocket for low-latency streaming TTS generation
"""

import asyncio
import base64
import json
import logging
import uuid
from typing import AsyncGenerator, Callable, Optional
import websockets
from websockets.exceptions import ConnectionClosed

from ..config import settings

logger = logging.getLogger(__name__)


class CartesiaStreamingClient:
    """
    WebSocket-based streaming TTS client for Cartesia AI.

    Provides low-latency audio generation by:
    - Maintaining persistent WebSocket connection
    - Streaming audio chunks as they're generated
    - Supporting context continuations for natural prosody
    """

    WS_URL = "wss://api.cartesia.ai/tts/websocket"
    API_VERSION = "2024-11-13"  # Latest stable version

    def __init__(
        self,
        api_key: str,
        voice_id: Optional[str] = None,
        model: str = "sonic-3",
        sample_rate: int = 24000,  # 24kHz is good balance of quality/speed
    ):
        """
        Initialize Cartesia streaming client.

        Args:
            api_key: Cartesia API key
            voice_id: Voice ID to use (defaults to config)
            model: Model to use (sonic-3 is fastest)
            sample_rate: Output sample rate (24000 recommended for streaming)
        """
        self.api_key = api_key
        self.voice_id = voice_id or settings.CARTESIA_VOICE_ID
        self.model = model
        self.sample_rate = sample_rate

        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._connected = False
        self._lock = asyncio.Lock()
        self._response_handlers: dict = {}  # context_id -> queue
        self._listener_task: Optional[asyncio.Task] = None

        logger.info(
            f"Initialized Cartesia streaming client: "
            f"voice={self.voice_id}, model={self.model}, sample_rate={self.sample_rate}"
        )

    async def connect(self) -> bool:
        """
        Establish WebSocket connection to Cartesia.
        Call this once at startup to pre-warm the connection.

        Returns:
            bool: True if connected successfully
        """
        async with self._lock:
            if self._connected and self._ws:
                return True

            try:
                url = f"{self.WS_URL}?api_key={self.api_key}&cartesia_version={self.API_VERSION}"

                self._ws = await websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                )

                self._connected = True

                # Start listener task
                self._listener_task = asyncio.create_task(self._listen_for_responses())

                logger.info("✅ Connected to Cartesia WebSocket")
                return True

            except Exception as e:
                logger.error(f"❌ Failed to connect to Cartesia WebSocket: {e}")
                self._connected = False
                return False

    async def _listen_for_responses(self):
        """Background task to listen for WebSocket responses."""
        try:
            while self._connected and self._ws:
                try:
                    message = await self._ws.recv()
                    data = json.loads(message)

                    context_id = data.get("context_id")
                    if context_id and context_id in self._response_handlers:
                        await self._response_handlers[context_id].put(data)

                except ConnectionClosed:
                    logger.warning("Cartesia WebSocket connection closed, will reconnect on next use")
                    self._connected = False
                    self._ws = None  # Clear so reconnect works
                    break
                except Exception as e:
                    logger.error(f"Error receiving Cartesia response: {e}")
                    # On error, mark as disconnected to trigger reconnect
                    self._connected = False
                    self._ws = None
                    break

        except asyncio.CancelledError:
            pass

    async def generate_audio_stream(
        self,
        text: str,
        emotion: str = "neutral",
        speed: float = 1.0,
    ) -> AsyncGenerator[bytes, None]:
        """
        Generate TTS audio as a stream of chunks.

        Args:
            text: Text to convert to speech
            emotion: Emotional tone (neutral, excited, sad, angry, etc.)
            speed: Speech speed multiplier (0.5-2.0)

        Yields:
            bytes: Raw PCM audio chunks (16-bit, mono)
        """
        # Ensure connected with retry
        max_retries = 2
        for attempt in range(max_retries):
            if not self._connected or not self._ws:
                logger.info(f"Cartesia not connected, attempting connection (attempt {attempt + 1}/{max_retries})")
                connected = await self.connect()
                if not connected:
                    if attempt < max_retries - 1:
                        await asyncio.sleep(0.5)  # Brief delay before retry
                        continue
                    logger.error("Cannot generate audio: failed to connect to Cartesia after retries")
                    return
            break

        if not self._ws or not self._connected:
            logger.error("Cannot generate audio: not connected to Cartesia")
            return

        context_id = str(uuid.uuid4())
        response_queue: asyncio.Queue = asyncio.Queue()
        self._response_handlers[context_id] = response_queue

        try:
            # Build generation config
            generation_config = {
                "speed": speed,
            }

            # Add emotion if not neutral (Cartesia supports: neutral, angry, excited, content, sad, scared)
            if emotion and emotion != "neutral":
                generation_config["emotion"] = emotion

            # Send generation request
            request = {
                "model_id": self.model,
                "transcript": text,
                "voice": {
                    "mode": "id",
                    "id": self.voice_id,
                },
                "output_format": {
                    "container": "raw",
                    "encoding": "pcm_s16le",  # 16-bit PCM for broad compatibility
                    "sample_rate": self.sample_rate,
                },
                "context_id": context_id,
                "generation_config": generation_config,
            }

            logger.debug(f"Sending Cartesia request: context={context_id}, text='{text[:50]}...'")
            await self._ws.send(json.dumps(request))

            # Yield audio chunks as they arrive
            chunks_received = 0
            while True:
                try:
                    # Wait for response with timeout
                    response = await asyncio.wait_for(
                        response_queue.get(),
                        timeout=30.0
                    )

                    # Check for errors
                    if response.get("type") == "error":
                        error_msg = response.get("error", "Unknown error")
                        logger.error(f"Cartesia error: {error_msg}")
                        break

                    # Check for audio chunk
                    if response.get("type") == "chunk":
                        audio_b64 = response.get("data")
                        if audio_b64:
                            audio_bytes = base64.b64decode(audio_b64)
                            chunks_received += 1
                            yield audio_bytes

                    # Check for completion
                    if response.get("done", False):
                        logger.debug(f"Cartesia generation complete: {chunks_received} chunks")
                        break

                except asyncio.TimeoutError:
                    logger.warning("Timeout waiting for Cartesia response")
                    break

        except Exception as e:
            logger.error(f"Error in Cartesia stream generation: {e}")

        finally:
            # Cleanup
            if context_id in self._response_handlers:
                del self._response_handlers[context_id]

    async def generate_audio(
        self,
        text: str,
        emotion: str = "neutral",
        speed: float = 1.0,
    ) -> bytes:
        """
        Generate complete TTS audio (for compatibility with existing code).

        Args:
            text: Text to convert to speech
            emotion: Emotional tone
            speed: Speech speed multiplier

        Returns:
            Complete audio as bytes
        """
        chunks = []
        async for chunk in self.generate_audio_stream(text, emotion, speed):
            chunks.append(chunk)

        return b"".join(chunks)

    async def close(self):
        """Close the WebSocket connection."""
        self._connected = False

        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass

        if self._ws:
            await self._ws.close()
            self._ws = None

        logger.info("Cartesia WebSocket connection closed")

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._connected and self._ws is not None
