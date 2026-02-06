"""
Attachments router for VOS API Gateway.

Handles image and file uploads for chat messages with vision capabilities.
"""

import logging
import os
import uuid
import json
import base64
from pathlib import Path
from typing import Optional
from datetime import datetime

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()

# Attachment storage path (shared volume)
ATTACHMENTS_BASE_PATH = Path("/shared/attachments")
ATTACHMENTS_BASE_PATH.mkdir(parents=True, exist_ok=True)

# Allowed image types
ALLOWED_IMAGE_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}

# Max file size (20MB)
MAX_FILE_SIZE = 20 * 1024 * 1024


class AttachmentMetadata(BaseModel):
    """Attachment metadata response"""
    attachment_id: str
    session_id: str
    attachment_type: str
    original_filename: Optional[str] = None
    content_type: str
    file_size_bytes: int
    width: Optional[int] = None
    height: Optional[int] = None
    created_at: str


class AttachmentUploadResponse(BaseModel):
    """Response after successful upload"""
    attachment_id: str
    content_type: str
    file_size_bytes: int
    width: Optional[int] = None
    height: Optional[int] = None


class SignedUrlResponse(BaseModel):
    """Signed URL response"""
    url: str
    expires_at: str
    expires_in: int  # Seconds until expiration


# API Base URL for absolute URLs (configurable via environment)
API_BASE_URL = os.environ.get("API_BASE_URL", "https://api.jarvos.dev")


@router.post("/attachments/upload", response_model=AttachmentUploadResponse)
async def upload_attachment(
    file: UploadFile = File(..., description="Image file to upload"),
    session_id: str = Form(..., description="Session ID for this upload")
):
    """
    Upload an image attachment for use with chat messages.

    Supports: PNG, JPEG, GIF, WebP images up to 20MB.

    Args:
        file: Image file to upload
        session_id: Session ID to associate with this attachment

    Returns:
        AttachmentUploadResponse with attachment_id and metadata

    Raises:
        HTTPException: 400 if invalid file type/size, 500 if upload fails
    """
    try:
        # Validate content type
        content_type = file.content_type or "application/octet-stream"

        if content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type: {content_type}. Allowed: {list(ALLOWED_IMAGE_TYPES.keys())}"
            )

        # Read file content
        contents = await file.read()

        if len(contents) == 0:
            raise HTTPException(status_code=400, detail="Empty file")

        if len(contents) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"File too large (max {MAX_FILE_SIZE // (1024*1024)}MB)"
            )

        # Generate attachment ID
        attachment_id = f"att_{uuid.uuid4().hex[:16]}"

        # Determine file extension
        extension = ALLOWED_IMAGE_TYPES.get(content_type, ".bin")

        # Create storage path: /shared/attachments/{session_id}/{attachment_id}.ext
        session_path = ATTACHMENTS_BASE_PATH / session_id
        session_path.mkdir(parents=True, exist_ok=True)

        file_path = session_path / f"{attachment_id}{extension}"
        storage_path = f"{session_id}/{attachment_id}{extension}"

        # Write file
        file_path.write_bytes(contents)

        logger.info(f"Uploaded attachment: {attachment_id} ({content_type}, {len(contents)} bytes)")

        # Try to get image dimensions
        width, height = None, None
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(contents))
            width, height = img.size
        except ImportError:
            logger.debug("PIL not available for image dimension extraction")
        except Exception as e:
            logger.debug(f"Could not extract image dimensions: {e}")

        # Store in database
        try:
            from app.main import db_client

            insert_query = """
            INSERT INTO attachments (
                attachment_id, session_id, attachment_type, original_filename,
                content_type, file_size_bytes, storage_path, width, height, created_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id;
            """

            db_client.execute_query(
                insert_query,
                (
                    attachment_id,
                    session_id,
                    "image",
                    file.filename,
                    content_type,
                    len(contents),
                    storage_path,
                    width,
                    height,
                    "user"
                )
            )

            logger.info(f"Stored attachment metadata in database: {attachment_id}")

        except Exception as e:
            logger.error(f"Error storing attachment in database: {e}")
            # Continue anyway - file is stored

        return AttachmentUploadResponse(
            attachment_id=attachment_id,
            content_type=content_type,
            file_size_bytes=len(contents),
            width=width,
            height=height
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading attachment: {e}")
        raise HTTPException(status_code=500, detail="Failed to upload attachment")


@router.get("/attachments/{attachment_id}", response_model=AttachmentMetadata)
async def get_attachment_metadata(attachment_id: str):
    """
    Get attachment metadata.

    Args:
        attachment_id: Attachment ID (e.g., att_abc123...)

    Returns:
        AttachmentMetadata with file information

    Raises:
        HTTPException: 404 if attachment not found
    """
    try:
        from app.main import db_client

        query = """
        SELECT attachment_id, session_id, attachment_type, original_filename,
               content_type, file_size_bytes, width, height, created_at
        FROM attachments
        WHERE attachment_id = %s;
        """

        result = db_client.execute_query(query, (attachment_id,))

        if not result:
            raise HTTPException(status_code=404, detail="Attachment not found")

        row = result[0]

        return AttachmentMetadata(
            attachment_id=row[0],
            session_id=row[1],
            attachment_type=row[2],
            original_filename=row[3],
            content_type=row[4],
            file_size_bytes=row[5],
            width=row[6],
            height=row[7],
            created_at=row[8].isoformat() if row[8] else None
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting attachment metadata: {e}")
        raise HTTPException(status_code=500, detail="Failed to get attachment metadata")


@router.get("/attachments/{attachment_id}/url", response_model=SignedUrlResponse)
async def get_attachment_signed_url(
    attachment_id: str,
    expires_hours: int = Query(default=24, ge=1, le=168, description="Hours until URL expires")
):
    """
    Get a signed URL for downloading an attachment.

    Args:
        attachment_id: Attachment ID
        expires_hours: Hours until URL expires (default: 24, max: 168)

    Returns:
        SignedUrlResponse with signed URL

    Raises:
        HTTPException: 404 if attachment not found
    """
    try:
        from app.main import db_client
        from app.utils.url_signing import URLSigner
        import time

        # Get storage path from database
        query = """
        SELECT storage_path FROM attachments WHERE attachment_id = %s;
        """

        result = db_client.execute_query(query, (attachment_id,))

        if not result:
            raise HTTPException(status_code=404, detail="Attachment not found")

        storage_path = result[0][0]

        # Generate signed URL
        signer = URLSigner()
        expires = int(time.time()) + (expires_hours * 3600)

        # Create signature
        import hmac
        import hashlib
        from urllib.parse import urlencode

        message = f"{storage_path}:{expires}"
        signature = hmac.new(
            signer.secret_key.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()

        params = urlencode({
            'file': storage_path,
            'expires': expires
        })

        # Build absolute URL
        signed_url = f"{API_BASE_URL}/api/v1/attachments/signed/{signature}?{params}"

        expires_at = datetime.fromtimestamp(expires).isoformat()
        expires_in = expires_hours * 3600  # Convert hours to seconds

        return SignedUrlResponse(url=signed_url, expires_at=expires_at, expires_in=expires_in)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating signed URL: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate signed URL")


@router.get("/attachments/signed/{signature}")
async def get_signed_attachment(
    signature: str,
    file: str = Query(..., description="Relative path to attachment"),
    expires: int = Query(..., description="Unix timestamp when URL expires")
):
    """
    Serve attachment files using signed URLs.

    Args:
        signature: HMAC-SHA256 signature
        file: Relative path to attachment
        expires: Unix timestamp when URL expires

    Returns:
        File content with appropriate headers

    Raises:
        HTTPException: 403 if invalid/expired, 404 if not found
    """
    try:
        from app.utils.url_signing import URLSigner
        import time

        # Verify signature
        signer = URLSigner()
        current_time = int(time.time())

        if current_time > expires:
            raise HTTPException(status_code=403, detail="URL has expired")

        # Verify signature
        import hmac
        import hashlib

        message = f"{file}:{expires}"
        expected_signature = hmac.new(
            signer.secret_key.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(signature, expected_signature):
            raise HTTPException(status_code=403, detail="Invalid signature")

        # Construct full path
        file_path = ATTACHMENTS_BASE_PATH / file

        # Security: Prevent directory traversal
        if not file_path.resolve().is_relative_to(ATTACHMENTS_BASE_PATH.resolve()):
            raise HTTPException(status_code=403, detail="Access denied")

        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Attachment not found")

        # Determine media type
        suffix = file_path.suffix.lower()
        media_type_map = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }

        media_type = media_type_map.get(suffix, "application/octet-stream")

        logger.info(f"Serving signed attachment: {file} ({media_type})")

        return FileResponse(
            path=file_path,
            media_type=media_type,
            filename=file_path.name,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "*",
                "Cache-Control": "public, max-age=86400"
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving signed attachment: {e}")
        raise HTTPException(status_code=500, detail="Failed to serve attachment")


@router.options("/attachments/signed/{signature}")
async def attachments_signed_options(signature: str):
    """Handle CORS preflight for signed attachment endpoint."""
    return Response(
        status_code=204,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "*",
            "Access-Control-Max-Age": "86400"
        }
    )


@router.get("/attachments/{attachment_id}/data")
async def get_attachment_data(attachment_id: str):
    """
    Get raw attachment data (for internal agent use).

    Args:
        attachment_id: Attachment ID

    Returns:
        Raw file content with appropriate Content-Type

    Raises:
        HTTPException: 404 if not found
    """
    try:
        from app.main import db_client

        query = """
        SELECT storage_path, content_type FROM attachments WHERE attachment_id = %s;
        """

        result = db_client.execute_query(query, (attachment_id,))

        if not result:
            raise HTTPException(status_code=404, detail="Attachment not found")

        storage_path, content_type = result[0]

        file_path = ATTACHMENTS_BASE_PATH / storage_path

        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Attachment file not found")

        logger.debug(f"Serving attachment data: {attachment_id} ({content_type})")

        return FileResponse(
            path=file_path,
            media_type=content_type,
            filename=file_path.name
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting attachment data: {e}")
        raise HTTPException(status_code=500, detail="Failed to get attachment data")


@router.delete("/attachments/{attachment_id}")
async def delete_attachment(attachment_id: str):
    """
    Delete an attachment.

    Args:
        attachment_id: Attachment ID to delete

    Returns:
        Success message

    Raises:
        HTTPException: 404 if not found, 500 if delete fails
    """
    try:
        from app.main import db_client

        # Get storage path
        query = """
        SELECT storage_path FROM attachments WHERE attachment_id = %s;
        """

        result = db_client.execute_query(query, (attachment_id,))

        if not result:
            raise HTTPException(status_code=404, detail="Attachment not found")

        storage_path = result[0][0]

        # Delete from database
        delete_query = """
        DELETE FROM attachments WHERE attachment_id = %s;
        """

        db_client.execute_query(delete_query, (attachment_id,))

        # Delete file
        file_path = ATTACHMENTS_BASE_PATH / storage_path
        if file_path.exists():
            file_path.unlink()

        logger.info(f"Deleted attachment: {attachment_id}")

        return {"status": "success", "message": f"Attachment {attachment_id} deleted"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting attachment: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete attachment")


# Utility functions for internal use

async def get_attachment_as_base64(attachment_id: str) -> dict:
    """
    Get attachment content as base64 for embedding in agent notifications.

    Args:
        attachment_id: Attachment ID

    Returns:
        Dict with content_type and base64_data

    Raises:
        ValueError: If attachment not found or read fails
    """
    try:
        from app.main import db_client

        query = """
        SELECT storage_path, content_type FROM attachments WHERE attachment_id = %s;
        """

        result = db_client.execute_query(query, (attachment_id,))

        if not result:
            raise ValueError(f"Attachment not found: {attachment_id}")

        storage_path, content_type = result[0]

        file_path = ATTACHMENTS_BASE_PATH / storage_path

        if not file_path.exists():
            raise ValueError(f"Attachment file not found: {attachment_id}")

        # Read and encode
        content = file_path.read_bytes()
        base64_data = base64.b64encode(content).decode('utf-8')

        return {
            "attachment_id": attachment_id,
            "content_type": content_type,
            "base64_data": base64_data
        }

    except Exception as e:
        logger.error(f"Error getting attachment as base64: {e}")
        raise ValueError(f"Failed to get attachment: {e}")


async def get_attachments_for_message(attachment_ids: list) -> list:
    """
    Get multiple attachments as base64 for a message.

    Args:
        attachment_ids: List of attachment IDs

    Returns:
        List of dicts with content_type and base64_data
    """
    attachments = []

    for att_id in attachment_ids:
        try:
            att_data = await get_attachment_as_base64(att_id)
            attachments.append(att_data)
        except ValueError as e:
            logger.warning(f"Skipping invalid attachment {att_id}: {e}")

    return attachments
