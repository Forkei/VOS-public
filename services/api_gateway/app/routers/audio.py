"""
Audio router for VOS API Gateway.

Handles serving audio files from voice messages with signed URL support.
"""

import logging
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import FileResponse

from app.utils.url_signing import verify_audio_url_signature

logger = logging.getLogger(__name__)

router = APIRouter()

# Audio files base path (shared volume)
AUDIO_BASE_PATH = Path("/shared/audio_files")


@router.get("/audio/signed/{signature}")
async def get_signed_audio_file(
    signature: str,
    file: str = Query(..., description="Relative path to audio file"),
    expires: int = Query(..., description="Unix timestamp when URL expires")
):
    """
    Serve audio files using signed URLs (no authentication required).

    This endpoint allows web audio players to access audio files without
    sending custom HTTP headers. The signature ensures the request is valid
    and time-limited.

    Args:
        signature: HMAC-SHA256 signature of "file:expires"
        file: Relative path like "agent_responses/session_123/vm_456.mp3"
        expires: Unix timestamp when this URL expires

    Returns:
        Audio file with public caching headers

    Raises:
        HTTPException: 403 if signature invalid or expired, 404 if file not found
    """
    try:
        # Verify signature
        is_valid, error_message = verify_audio_url_signature(signature, file, expires)

        if not is_valid:
            logger.warning(f"🚨 Invalid signed URL: {error_message} - file: {file}")
            raise HTTPException(status_code=403, detail=error_message or "Invalid signature")

        # Construct full path
        file_path = AUDIO_BASE_PATH / file

        # Security: Prevent directory traversal attacks
        if not file_path.resolve().is_relative_to(AUDIO_BASE_PATH.resolve()):
            logger.warning(f"🚨 Directory traversal attempt blocked: {file}")
            raise HTTPException(status_code=403, detail="Access denied")

        # Check if file exists
        if not file_path.exists():
            logger.warning(f"Audio file not found: {file}")
            raise HTTPException(status_code=404, detail="Audio file not found")

        # Determine media type based on extension
        suffix = file_path.suffix.lower()
        media_type_map = {
            ".mp3": "audio/mpeg",
            ".webm": "audio/webm",
            ".wav": "audio/wav",
            ".ogg": "audio/ogg",
            ".m4a": "audio/mp4"
        }

        media_type = media_type_map.get(suffix, "application/octet-stream")

        logger.info(f"🎵 Serving signed audio: {file} ({media_type})")

        # Return audio file with complete CORS headers and public caching
        return FileResponse(
            path=file_path,
            media_type=media_type,
            filename=file_path.name,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "*",
                "Access-Control-Expose-Headers": "Content-Length, Content-Range",
                "Cache-Control": "public, max-age=86400",
                "Accept-Ranges": "bytes"
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving signed audio file {file}: {e}")
        raise HTTPException(status_code=500, detail="Failed to serve audio file")


@router.options("/audio/signed/{signature}")
async def audio_signed_options(signature: str):
    """
    Handle CORS preflight requests for signed audio endpoint.

    Browsers send OPTIONS requests before actual GET requests to check CORS permissions.
    """
    return Response(
        status_code=204,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "*",
            "Access-Control-Max-Age": "86400"
        }
    )


@router.get("/audio/{audio_path:path}")
async def get_audio_file(audio_path: str):
    """
    Serve audio files from voice messages.

    Args:
        audio_path: Relative path like "agent_responses/session_123/vm_456.mp3"
                    or "user_recordings/session_123/vm_789.webm"

    Returns:
        Audio file with appropriate Content-Type header

    Raises:
        HTTPException: 403 if path is invalid, 404 if file not found
    """
    try:
        # Construct full path
        file_path = AUDIO_BASE_PATH / audio_path

        # Security: Prevent directory traversal attacks
        if not file_path.resolve().is_relative_to(AUDIO_BASE_PATH.resolve()):
            logger.warning(f"🚨 Directory traversal attempt blocked: {audio_path}")
            raise HTTPException(status_code=403, detail="Access denied")

        # Check if file exists
        if not file_path.exists():
            logger.warning(f"Audio file not found: {audio_path}")
            raise HTTPException(status_code=404, detail="Audio file not found")

        # Determine media type based on extension
        suffix = file_path.suffix.lower()
        media_type_map = {
            ".mp3": "audio/mpeg",
            ".webm": "audio/webm",
            ".wav": "audio/wav",
            ".ogg": "audio/ogg",
            ".m4a": "audio/mp4"
        }

        media_type = media_type_map.get(suffix, "application/octet-stream")

        logger.info(f"🎵 Serving audio file: {audio_path} ({media_type})")

        # Return audio file with CORS headers
        return FileResponse(
            path=file_path,
            media_type=media_type,
            filename=file_path.name,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "*",
                "Access-Control-Expose-Headers": "Content-Length, Content-Range",
                "Accept-Ranges": "bytes"
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving audio file {audio_path}: {e}")
        raise HTTPException(status_code=500, detail="Failed to serve audio file")
