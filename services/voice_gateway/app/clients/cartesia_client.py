"""
Cartesia AI Text-to-Speech Client
Handles TTS generation with emotional control using REST API
"""

import logging
from typing import Optional
import requests
import pybreaker

from ..config import settings

logger = logging.getLogger(__name__)

# Circuit breaker for Cartesia API
cartesia_breaker = pybreaker.CircuitBreaker(
    fail_max=3,  # Open circuit after 3 failures
    reset_timeout=30,  # Stay open for 30 seconds
    name="cartesia_api"
)


class CartesiaTTSClient:
    """Client for Cartesia AI text-to-speech generation"""

    def __init__(self, api_key: str, voice_id: Optional[str] = None):
        """
        Initialize Cartesia client

        Args:
            api_key: Cartesia API key
            voice_id: Voice ID to use (defaults to config)
        """
        self.api_key = api_key
        self.voice_id = voice_id or settings.CARTESIA_VOICE_ID
        self.base_url = "https://api.cartesia.ai"

        logger.info(f"Initialized Cartesia client with voice_id={self.voice_id}")

    async def generate_audio(
        self,
        text: str,
        emotion: str = "neutral"
    ) -> bytes:
        """
        Generate complete TTS audio (buffered mode for Phase 1)

        Args:
            text: Text to convert to speech
            emotion: Emotional tone (ignored - uses CARTESIA_EMOTION from config)

        Returns:
            Complete WAV audio file as bytes
        """
        try:
            # Check circuit breaker state
            if cartesia_breaker.current_state == pybreaker.STATE_OPEN:
                logger.error("❌ Cartesia circuit breaker is OPEN - too many recent failures")
                raise pybreaker.CircuitBreakerError("Cartesia service unavailable")

            # Use configured emotion from settings
            cartesia_emotion = settings.CARTESIA_EMOTION

            logger.info(f"Generating TTS for text: '{text[:50]}...' (emotion={cartesia_emotion})")

            # Get generation config with emotion
            generation_config = self._get_generation_config(cartesia_emotion)

            # Wrap TTS generation in circuit breaker
            @cartesia_breaker
            def _generate_tts():
                # Prepare request
                url = f"{self.base_url}/tts/bytes"
                headers = {
                    "Cartesia-Version": settings.CARTESIA_VERSION,
                    "X-API-Key": self.api_key,
                    "Content-Type": "application/json"
                }
                data = {
                    "model_id": settings.CARTESIA_MODEL,
                    "transcript": text,
                    "voice": {
                        "mode": "id",
                        "id": self.voice_id
                    },
                    "output_format": {
                        "container": settings.CARTESIA_OUTPUT_FORMAT,
                        "encoding": settings.CARTESIA_ENCODING,
                        "sample_rate": settings.CARTESIA_SAMPLE_RATE
                    },
                    "generation_config": generation_config
                }

                # Make request
                response = requests.post(url, json=data, headers=headers, timeout=30)
                response.raise_for_status()

                complete_audio = response.content

                logger.info(
                    f"✅ TTS generation complete: {len(complete_audio)} bytes"
                )

                return complete_audio

            return _generate_tts()

        except pybreaker.CircuitBreakerError:
            logger.error("❌ Cartesia circuit breaker is OPEN - service unavailable")
            raise
        except Exception as e:
            logger.error(f"❌ Error generating TTS: {e}")
            raise

    def _get_generation_config(self, emotion: str) -> dict:
        """
        Get generation config with Cartesia emotion field

        Args:
            emotion: Emotional tone to apply (Cartesia native emotion)

        Returns:
            Dict with generation config including emotion
        """
        # Default settings
        speed = 1.0
        volume = 1.0

        # Cartesia supports native emotion field
        # Primary emotions (best results): neutral, angry, excited, content, sad, scared
        logger.debug(
            f"Generation config: emotion={emotion}, speed={speed}, volume={volume}"
        )

        return {
            "emotion": emotion,
            "speed": speed,
            "volume": volume
        }

    def get_available_voices(self) -> list:
        """
        Get list of available voices from Cartesia

        Returns:
            List of available voices
        """
        try:
            url = f"{self.base_url}/voices"
            headers = {
                "Cartesia-Version": settings.CARTESIA_VERSION,
                "X-API-Key": self.api_key
            }

            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()

            data = response.json()
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"Error fetching voices: {e}")
            return []
