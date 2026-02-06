"""
URL Signing Utility for Voice Gateway

Generates signed URLs for audio files to be sent via WebSocket events.
"""

import hmac
import hashlib
import time
import os
import logging
from urllib.parse import urlencode

logger = logging.getLogger(__name__)


def generate_audio_signed_url(
    file_path: str,
    base_url: str = "",
    expires_hours: int = 24,
    secret_key: str = None
) -> str:
    """
    Generate a signed URL for an audio file.

    Args:
        file_path: Relative path to the file (e.g., "agent_responses/session/vm_123.mp3")
        base_url: Base URL (e.g., "https://api.jarvos.dev"). If empty, returns relative URL.
        expires_hours: Hours until URL expires (default: 24)
        secret_key: Secret key for signing. If None, reads from JWT_SECRET env var.

    Returns:
        Signed URL string

    Example:
        >>> url = generate_audio_signed_url("agent_responses/session/vm_123.mp3")
        >>> print(url)
        /api/v1/audio/signed/abc123...?file=agent_responses/session/vm_123.mp3&expires=1729756800
    """
    if secret_key is None:
        secret_key = os.getenv("JWT_SECRET", "")

    if not secret_key:
        logger.error("JWT_SECRET not set - cannot generate signed URLs")
        return ""

    # Calculate expiration timestamp
    expires = int(time.time()) + (expires_hours * 3600)

    # Create signature
    message = f"{file_path}:{expires}"
    signature = hmac.new(
        secret_key.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()

    # Build query parameters
    params = urlencode({
        'file': file_path,
        'expires': expires
    })

    # Build URL
    signed_path = f"/api/v1/audio/signed/{signature}?{params}"

    if base_url:
        return f"{base_url}{signed_path}"

    return signed_path
