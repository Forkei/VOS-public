"""
URL Signing Utility for Secure Audio File Access

Provides signed URLs with time-limited access to audio files without requiring
authentication headers (needed for web audio players).
"""

import hmac
import hashlib
import time
import logging
from typing import Optional
from urllib.parse import urlencode
import os

logger = logging.getLogger(__name__)


class URLSigner:
    """
    Generate and verify signed URLs for secure file access.

    Uses HMAC-SHA256 to sign URLs with expiration timestamps.
    """

    def __init__(self, secret_key: Optional[str] = None):
        """
        Initialize URL signer with secret key.

        Args:
            secret_key: Secret key for signing. If None, reads from JWT_SECRET env var.
        """
        self.secret_key = secret_key or os.getenv("JWT_SECRET", "")

        if not self.secret_key:
            raise ValueError("URL signing requires JWT_SECRET to be set")

    def generate_signed_url(
        self,
        file_path: str,
        base_url: str = "",
        expires_hours: int = 24
    ) -> str:
        """
        Generate a signed URL for a file.

        Args:
            file_path: Relative path to the file (e.g., "agent_responses/session/vm_123.mp3")
            base_url: Base URL (e.g., "https://api.jarvos.dev"). If empty, returns relative URL.
            expires_hours: Hours until URL expires (default: 24)

        Returns:
            Signed URL string

        Example:
            >>> signer = URLSigner("secret")
            >>> url = signer.generate_signed_url("agent_responses/session/vm_123.mp3")
            >>> print(url)
            /api/v1/audio/signed/abc123...?file=agent_responses/session/vm_123.mp3&expires=1729756800
        """
        # Calculate expiration timestamp
        expires = int(time.time()) + (expires_hours * 3600)

        # Create signature
        message = f"{file_path}:{expires}"
        signature = hmac.new(
            self.secret_key.encode(),
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

    def verify_signature(
        self,
        signature: str,
        file_path: str,
        expires: int
    ) -> tuple[bool, Optional[str]]:
        """
        Verify a signed URL signature.

        Args:
            signature: The signature token from the URL
            file_path: The file path from query params
            expires: The expiration timestamp from query params

        Returns:
            Tuple of (is_valid, error_message)
            - (True, None) if valid
            - (False, "error message") if invalid

        Example:
            >>> signer = URLSigner("secret")
            >>> valid, error = signer.verify_signature("abc123", "file.mp3", 1729756800)
            >>> if valid:
            ...     print("Signature is valid!")
        """
        # Check expiration
        current_time = int(time.time())

        if current_time > expires:
            time_expired = current_time - expires
            logger.warning(f"Signed URL expired {time_expired} seconds ago")
            return False, "URL has expired"

        # Verify signature
        message = f"{file_path}:{expires}"
        expected_signature = hmac.new(
            self.secret_key.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()

        # Use constant-time comparison to prevent timing attacks
        if not hmac.compare_digest(signature, expected_signature):
            logger.warning(f"Invalid signature for file: {file_path}")
            return False, "Invalid signature"

        logger.debug(f"Signature verified for file: {file_path}")
        return True, None


# Singleton instance
_url_signer: Optional[URLSigner] = None


def get_url_signer() -> URLSigner:
    """
    Get or create the singleton URL signer instance.

    Returns:
        URLSigner instance
    """
    global _url_signer

    if _url_signer is None:
        _url_signer = URLSigner()
        logger.info("Initialized URL signer")

    return _url_signer


def generate_audio_signed_url(file_path: str, base_url: str = "") -> str:
    """
    Convenience function to generate a signed URL for an audio file.

    Args:
        file_path: Relative path to audio file
        base_url: Optional base URL

    Returns:
        Signed URL string
    """
    signer = get_url_signer()
    return signer.generate_signed_url(file_path, base_url)


def verify_audio_url_signature(signature: str, file_path: str, expires: int) -> tuple[bool, Optional[str]]:
    """
    Convenience function to verify an audio URL signature.

    Args:
        signature: Signature token
        file_path: File path
        expires: Expiration timestamp

    Returns:
        Tuple of (is_valid, error_message)
    """
    signer = get_url_signer()
    return signer.verify_signature(signature, file_path, expires)
