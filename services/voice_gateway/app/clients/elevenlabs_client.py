"""
ElevenLabs Text-to-Speech Client
Handles TTS generation with emotional control using REST API
"""

import logging
from typing import Optional
import requests
import pybreaker

from ..config import settings

logger = logging.getLogger(__name__)

# Circuit breaker for ElevenLabs API
elevenlabs_breaker = pybreaker.CircuitBreaker(
    fail_max=3,  # Open circuit after 3 failures
    reset_timeout=30,  # Stay open for 30 seconds
    name="elevenlabs_api"
)


class ElevenLabsTTSClient:
    """Client for ElevenLabs text-to-speech generation"""

    def __init__(self, api_key: str, voice_id: Optional[str] = None):
        """
        Initialize ElevenLabs client

        Args:
            api_key: ElevenLabs API key
            voice_id: Voice ID to use (defaults to config)
        """
        self.api_key = api_key
        self.voice_id = voice_id or settings.ELEVENLABS_VOICE_ID
        self.base_url = "https://api.elevenlabs.io/v1"

        logger.info(f"Initialized ElevenLabs client with voice_id={self.voice_id}")

    async def generate_audio(
        self,
        text: str,
        emotion: str = "neutral"
    ) -> bytes:
        """
        Generate complete TTS audio (buffered mode for Phase 1)

        Args:
            text: Text to convert to speech
            emotion: Emotional tone (neutral, excited, calm, serious)

        Returns:
            Complete MP3 audio file as bytes
        """
        try:
            # Check circuit breaker state
            if elevenlabs_breaker.current_state == pybreaker.STATE_OPEN:
                logger.error("❌ ElevenLabs circuit breaker is OPEN - too many recent failures")
                raise pybreaker.CircuitBreakerError("ElevenLabs service unavailable")

            logger.info(f"Generating TTS for text: '{text[:50]}...' (emotion={emotion})")

            # Get voice settings based on emotion
            voice_settings = self._get_voice_settings(emotion)

            # Wrap TTS generation in circuit breaker
            @elevenlabs_breaker
            def _generate_tts():
                # Prepare request
                url = f"{self.base_url}/text-to-speech/{self.voice_id}"
                headers = {
                    "Accept": "audio/mpeg",
                    "Content-Type": "application/json",
                    "xi-api-key": self.api_key
                }
                data = {
                    "text": text,
                    "model_id": settings.ELEVENLABS_MODEL,
                    "voice_settings": voice_settings
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
            logger.error("❌ ElevenLabs circuit breaker is OPEN - service unavailable")
            raise
        except Exception as e:
            logger.error(f"❌ Error generating TTS: {e}")
            raise

    def _get_voice_settings(self, emotion: str) -> dict:
        """
        Get voice settings based on desired emotion

        Args:
            emotion: Emotional tone to apply

        Returns:
            Dict with voice settings for the emotion
        """
        # Default settings from config
        stability = settings.ELEVENLABS_STABILITY
        similarity_boost = settings.ELEVENLABS_SIMILARITY_BOOST
        style = settings.ELEVENLABS_STYLE
        use_speaker_boost = True

        # Adjust based on emotion
        # Lower stability = more expressive/variable
        # Higher style = stronger emotional expression
        if emotion == "excited":
            stability = 0.3
            style = 0.6
        elif emotion == "calm":
            stability = 0.8
            style = 0.0
        elif emotion == "serious":
            stability = 0.6
            style = 0.3
        elif emotion == "happy":
            stability = 0.4
            style = 0.5
        elif emotion == "concerned":
            stability = 0.5
            style = 0.4
        # "neutral" uses default settings

        logger.debug(
            f"Voice settings for {emotion}: "
            f"stability={stability}, style={style}"
        )

        return {
            "stability": stability,
            "similarity_boost": similarity_boost,
            "style": style,
            "use_speaker_boost": use_speaker_boost
        }

    def get_available_voices(self) -> list:
        """
        Get list of available voices from ElevenLabs

        Returns:
            List of available voices
        """
        try:
            url = f"{self.base_url}/voices"
            headers = {"xi-api-key": self.api_key}

            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()

            data = response.json()
            return data.get("voices", [])
        except Exception as e:
            logger.error(f"Error fetching voices: {e}")
            return []
