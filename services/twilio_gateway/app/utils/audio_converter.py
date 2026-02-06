"""
Audio Converter Utilities
Handles conversion between Twilio's mulaw format and VOS's PCM format
"""

import io
import logging
from typing import Optional

# audioop is deprecated in Python 3.11, removed in 3.13
# Use audioop-lts as fallback for Python 3.13+
try:
    import audioop
except ImportError:
    # Python 3.13+ - use audioop-lts package
    import audioop_lts as audioop

from pydub import AudioSegment

logger = logging.getLogger(__name__)

# Twilio uses 8kHz mulaw, VOS uses 16kHz PCM
TWILIO_SAMPLE_RATE = 8000
VOS_SAMPLE_RATE = 16000


def mulaw_to_pcm(mulaw_data: bytes) -> bytes:
    """
    Convert mulaw 8kHz audio to PCM 16-bit 16kHz.

    Twilio sends audio as:
    - 8-bit mulaw encoding
    - 8000 Hz sample rate
    - Mono

    VOS expects:
    - 16-bit PCM (signed, little-endian)
    - 16000 Hz sample rate
    - Mono

    Args:
        mulaw_data: Raw mulaw audio bytes from Twilio

    Returns:
        PCM 16-bit audio bytes at 16kHz
    """
    try:
        # Step 1: Convert mulaw to linear PCM (16-bit)
        # mulaw2lin returns 16-bit samples (2 bytes per sample)
        pcm_8khz = audioop.ulaw2lin(mulaw_data, 2)

        # Step 2: Upsample from 8kHz to 16kHz
        # ratecv returns (fragment, newstate)
        pcm_16khz, _ = audioop.ratecv(
            pcm_8khz,
            2,  # sample width in bytes (16-bit = 2)
            1,  # number of channels (mono)
            TWILIO_SAMPLE_RATE,  # input rate
            VOS_SAMPLE_RATE,  # output rate
            None  # state (None for new conversion)
        )

        return pcm_16khz

    except Exception as e:
        logger.error(f"Error converting mulaw to PCM: {e}")
        return b""


def pcm_to_mulaw(pcm_data: bytes, input_rate: int = VOS_SAMPLE_RATE) -> bytes:
    """
    Convert PCM 16-bit audio to mulaw 8kHz for Twilio.

    Args:
        pcm_data: PCM 16-bit audio bytes
        input_rate: Input sample rate (default 16kHz)

    Returns:
        Mulaw audio bytes at 8kHz
    """
    try:
        # Step 1: Downsample to 8kHz if needed
        if input_rate != TWILIO_SAMPLE_RATE:
            pcm_8khz, _ = audioop.ratecv(
                pcm_data,
                2,  # sample width in bytes
                1,  # channels
                input_rate,  # input rate
                TWILIO_SAMPLE_RATE,  # output rate
                None
            )
        else:
            pcm_8khz = pcm_data

        # Step 2: Convert to mulaw
        mulaw_data = audioop.lin2ulaw(pcm_8khz, 2)

        return mulaw_data

    except Exception as e:
        logger.error(f"Error converting PCM to mulaw: {e}")
        return b""


def mp3_to_mulaw(mp3_data: bytes) -> bytes:
    """
    Convert MP3 audio (TTS output) to mulaw 8kHz for Twilio.

    ElevenLabs and other TTS services typically output MP3.
    This function converts it to Twilio's required format.

    Args:
        mp3_data: MP3 audio bytes

    Returns:
        Mulaw audio bytes at 8kHz
    """
    try:
        # Load MP3 using pydub
        audio = AudioSegment.from_mp3(io.BytesIO(mp3_data))

        # Convert to mono if stereo
        if audio.channels > 1:
            audio = audio.set_channels(1)

        # Resample to 8kHz
        audio = audio.set_frame_rate(TWILIO_SAMPLE_RATE)

        # Get raw PCM samples (16-bit)
        audio = audio.set_sample_width(2)
        pcm_data = audio.raw_data

        # Convert to mulaw
        mulaw_data = audioop.lin2ulaw(pcm_data, 2)

        return mulaw_data

    except Exception as e:
        logger.error(f"Error converting MP3 to mulaw: {e}")
        return b""


def wav_to_mulaw(wav_data: bytes) -> bytes:
    """
    Convert WAV audio to mulaw 8kHz for Twilio.

    Args:
        wav_data: WAV audio bytes

    Returns:
        Mulaw audio bytes at 8kHz
    """
    try:
        # Load WAV using pydub
        audio = AudioSegment.from_wav(io.BytesIO(wav_data))

        # Convert to mono if stereo
        if audio.channels > 1:
            audio = audio.set_channels(1)

        # Resample to 8kHz
        audio = audio.set_frame_rate(TWILIO_SAMPLE_RATE)

        # Get raw PCM samples (16-bit)
        audio = audio.set_sample_width(2)
        pcm_data = audio.raw_data

        # Convert to mulaw
        mulaw_data = audioop.lin2ulaw(pcm_data, 2)

        return mulaw_data

    except Exception as e:
        logger.error(f"Error converting WAV to mulaw: {e}")
        return b""


def get_audio_duration_ms(audio_data: bytes, sample_rate: int, sample_width: int = 2) -> int:
    """
    Calculate audio duration in milliseconds.

    Args:
        audio_data: Raw audio bytes
        sample_rate: Sample rate in Hz
        sample_width: Bytes per sample (2 for 16-bit)

    Returns:
        Duration in milliseconds
    """
    num_samples = len(audio_data) // sample_width
    duration_seconds = num_samples / sample_rate
    return int(duration_seconds * 1000)
