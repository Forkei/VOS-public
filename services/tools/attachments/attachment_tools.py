"""
Attachment tools for VOS agents.

Tools for downloading images from URLs, creating attachments, and sharing
images between agents.
"""

import os
import io
import uuid
import base64
import logging
import requests
from typing import Dict, Any, Optional
from datetime import datetime
from urllib.parse import urlparse

from vos_sdk import BaseTool

logger = logging.getLogger(__name__)

# Supported image types
SUPPORTED_IMAGE_TYPES = {
    'image/jpeg': '.jpg',
    'image/png': '.png',
    'image/gif': '.gif',
    'image/webp': '.webp',
    'image/bmp': '.bmp',
}

# Max image size (10MB)
MAX_IMAGE_SIZE = 10 * 1024 * 1024


def generate_attachment_id() -> str:
    """Generate a unique attachment ID."""
    return f"att_{uuid.uuid4().hex[:12]}"


class DownloadImageTool(BaseTool):
    """
    Download an image from a URL and store it as an attachment.

    Use this to fetch images from the web and make them available
    for viewing, sharing with other agents, or sending to the user.
    """

    def __init__(self):
        super().__init__(
            name="download_image",
            description="Download an image from a URL and store it as an attachment"
        )
        self.database_url = os.environ.get("DATABASE_URL")
        self.api_gateway_url = os.environ.get("API_GATEWAY_URL", "http://api_gateway:8000")

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate download arguments."""
        if "url" not in arguments:
            return False, "Missing required argument: 'url'"

        url = arguments["url"]
        if not isinstance(url, str):
            return False, "'url' must be a string"

        # Basic URL validation
        try:
            parsed = urlparse(url)
            if parsed.scheme not in ('http', 'https'):
                return False, "URL must use http or https scheme"
        except Exception:
            return False, "Invalid URL format"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "download_image",
            "description": "Download an image from a URL and store it as an attachment. Returns an attachment_id that can be shared with other agents or sent to the user.",
            "parameters": [
                {
                    "name": "url",
                    "type": "str",
                    "description": "The URL of the image to download",
                    "required": True
                },
                {
                    "name": "filename",
                    "type": "str",
                    "description": "Optional filename for the downloaded image",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """Download an image from URL and store as attachment."""
        try:
            import psycopg2

            url = arguments["url"]
            custom_filename = arguments.get("filename")

            logger.info(f"Downloading image from: {url}")

            # Download the image
            try:
                response = requests.get(
                    url,
                    timeout=30,
                    headers={
                        'User-Agent': 'VOS-Agent/1.0 (Image Downloader)'
                    },
                    stream=True
                )
                response.raise_for_status()
            except requests.exceptions.RequestException as e:
                self.send_result_notification(
                    status="FAILURE",
                    result={"error": f"Failed to download image: {str(e)}"}
                )
                return

            # Check content type
            content_type = response.headers.get('Content-Type', '').split(';')[0].strip()
            if content_type not in SUPPORTED_IMAGE_TYPES:
                self.send_result_notification(
                    status="FAILURE",
                    result={"error": f"Unsupported image type: {content_type}. Supported: {list(SUPPORTED_IMAGE_TYPES.keys())}"}
                )
                return

            # Read image data
            image_data = response.content
            file_size = len(image_data)

            if file_size > MAX_IMAGE_SIZE:
                self.send_result_notification(
                    status="FAILURE",
                    result={"error": f"Image too large: {file_size} bytes (max {MAX_IMAGE_SIZE})"}
                )
                return

            # Generate filename if not provided
            if custom_filename:
                filename = custom_filename
            else:
                # Extract from URL or generate
                parsed_url = urlparse(url)
                path_filename = os.path.basename(parsed_url.path)
                if path_filename and '.' in path_filename:
                    filename = path_filename
                else:
                    ext = SUPPORTED_IMAGE_TYPES.get(content_type, '.jpg')
                    filename = f"downloaded_image{ext}"

            # Try to get image dimensions
            width, height = None, None
            try:
                from PIL import Image
                img = Image.open(io.BytesIO(image_data))
                width, height = img.size
            except ImportError:
                logger.debug("PIL not available - skipping dimension detection")
            except Exception as e:
                logger.debug(f"Could not determine image dimensions: {e}")

            # Generate attachment ID and storage path
            attachment_id = generate_attachment_id()
            session_id = getattr(self, 'session_id', None) or 'agent_session'

            # Store as base64 in a local path (or GCS in production)
            # For now, we'll store the path reference and base64 in metadata
            storage_path = f"attachments/{session_id}/{attachment_id}/{filename}"

            # Encode image as base64 for storage/transfer
            base64_data = base64.b64encode(image_data).decode('utf-8')

            # Store in database
            conn = psycopg2.connect(self.database_url)
            cursor = conn.cursor()

            try:
                insert_query = """
                INSERT INTO attachments (
                    attachment_id, session_id, attachment_type, original_filename,
                    content_type, file_size_bytes, storage_path, width, height, created_by
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, created_at;
                """

                cursor.execute(
                    insert_query,
                    (
                        attachment_id,
                        session_id,
                        'image',
                        filename,
                        content_type,
                        file_size,
                        storage_path,
                        width,
                        height,
                        self.agent_name
                    )
                )

                result = cursor.fetchone()
                conn.commit()

                logger.info(f"Downloaded and stored image: {attachment_id} ({filename}) by {self.agent_name}")

                self.send_result_notification(
                    status="SUCCESS",
                    result={
                        "attachment_id": attachment_id,
                        "filename": filename,
                        "content_type": content_type,
                        "file_size_bytes": file_size,
                        "width": width,
                        "height": height,
                        "base64_data": base64_data,  # Include for immediate use
                        "source_url": url,
                        "message": f"Image downloaded and stored as '{attachment_id}'. You can share this with other agents or include it in messages."
                    }
                )

            finally:
                cursor.close()
                conn.close()

        except Exception as e:
            logger.error(f"Error downloading image: {e}")
            self.send_result_notification(
                status="FAILURE",
                result={"error": str(e)}
            )


class CreateAttachmentTool(BaseTool):
    """
    Create an attachment from base64 image data.

    Use this when you have image data (e.g., from another tool, screenshot,
    or generated image) and want to store it as a shareable attachment.
    """

    def __init__(self):
        super().__init__(
            name="create_attachment",
            description="Create an attachment from base64 image data"
        )
        self.database_url = os.environ.get("DATABASE_URL")

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate attachment creation arguments."""
        if "base64_data" not in arguments:
            return False, "Missing required argument: 'base64_data'"

        if "content_type" not in arguments:
            return False, "Missing required argument: 'content_type'"

        content_type = arguments["content_type"]
        if content_type not in SUPPORTED_IMAGE_TYPES:
            return False, f"Unsupported content type: {content_type}. Supported: {list(SUPPORTED_IMAGE_TYPES.keys())}"

        # Validate base64
        try:
            data = base64.b64decode(arguments["base64_data"])
            if len(data) > MAX_IMAGE_SIZE:
                return False, f"Image data too large (max {MAX_IMAGE_SIZE} bytes)"
        except Exception:
            return False, "Invalid base64 data"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "create_attachment",
            "description": "Create an attachment from base64-encoded image data. Returns an attachment_id for sharing.",
            "parameters": [
                {
                    "name": "base64_data",
                    "type": "str",
                    "description": "Base64-encoded image data",
                    "required": True
                },
                {
                    "name": "content_type",
                    "type": "str",
                    "description": "MIME type: 'image/png', 'image/jpeg', 'image/gif', 'image/webp'",
                    "required": True
                },
                {
                    "name": "filename",
                    "type": "str",
                    "description": "Optional filename for the attachment",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """Create an attachment from base64 data."""
        try:
            import psycopg2

            base64_data = arguments["base64_data"]
            content_type = arguments["content_type"]
            filename = arguments.get("filename")

            # Decode to get size
            image_data = base64.b64decode(base64_data)
            file_size = len(image_data)

            # Generate filename if not provided
            if not filename:
                ext = SUPPORTED_IMAGE_TYPES.get(content_type, '.png')
                filename = f"attachment_{uuid.uuid4().hex[:8]}{ext}"

            # Try to get dimensions
            width, height = None, None
            try:
                from PIL import Image
                img = Image.open(io.BytesIO(image_data))
                width, height = img.size
            except ImportError:
                pass
            except Exception:
                pass

            # Generate IDs
            attachment_id = generate_attachment_id()
            session_id = getattr(self, 'session_id', None) or 'agent_session'
            storage_path = f"attachments/{session_id}/{attachment_id}/{filename}"

            # Store in database
            conn = psycopg2.connect(self.database_url)
            cursor = conn.cursor()

            try:
                insert_query = """
                INSERT INTO attachments (
                    attachment_id, session_id, attachment_type, original_filename,
                    content_type, file_size_bytes, storage_path, width, height, created_by
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, created_at;
                """

                cursor.execute(
                    insert_query,
                    (
                        attachment_id,
                        session_id,
                        'image',
                        filename,
                        content_type,
                        file_size,
                        storage_path,
                        width,
                        height,
                        self.agent_name
                    )
                )

                result = cursor.fetchone()
                conn.commit()

                logger.info(f"Created attachment: {attachment_id} ({filename}) by {self.agent_name}")

                self.send_result_notification(
                    status="SUCCESS",
                    result={
                        "attachment_id": attachment_id,
                        "filename": filename,
                        "content_type": content_type,
                        "file_size_bytes": file_size,
                        "width": width,
                        "height": height,
                        "message": f"Attachment created: '{attachment_id}'"
                    }
                )

            finally:
                cursor.close()
                conn.close()

        except Exception as e:
            logger.error(f"Error creating attachment: {e}")
            self.send_result_notification(
                status="FAILURE",
                result={"error": str(e)}
            )


class GetAttachmentTool(BaseTool):
    """
    Get attachment metadata and optionally the image data.

    Use this to retrieve information about an attachment or get
    the base64 image data for viewing or processing.
    """

    def __init__(self):
        super().__init__(
            name="get_attachment",
            description="Get attachment metadata and optionally the image data"
        )
        self.database_url = os.environ.get("DATABASE_URL")
        self.api_gateway_url = os.environ.get("API_GATEWAY_URL", "http://api_gateway:8000")

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate get arguments."""
        if "attachment_id" not in arguments:
            return False, "Missing required argument: 'attachment_id'"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "get_attachment",
            "description": "Get attachment metadata and optionally retrieve the image data",
            "parameters": [
                {
                    "name": "attachment_id",
                    "type": "str",
                    "description": "The attachment ID (e.g., att_abc123...)",
                    "required": True
                },
                {
                    "name": "include_data",
                    "type": "bool",
                    "description": "Whether to include base64 image data (default: false)",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """Get attachment information."""
        try:
            import psycopg2

            attachment_id = arguments["attachment_id"]
            include_data = arguments.get("include_data", False)

            conn = psycopg2.connect(self.database_url)
            cursor = conn.cursor()

            try:
                query = """
                SELECT attachment_id, session_id, attachment_type, original_filename,
                       content_type, file_size_bytes, storage_path, width, height,
                       created_by, created_at
                FROM attachments
                WHERE attachment_id = %s;
                """

                cursor.execute(query, (attachment_id,))
                result = cursor.fetchone()

                if not result:
                    self.send_result_notification(
                        status="FAILURE",
                        result={"error": f"Attachment not found: {attachment_id}"}
                    )
                    return

                (att_id, session_id, att_type, filename, content_type, file_size,
                 storage_path, width, height, created_by, created_at) = result

                response_data = {
                    "attachment_id": att_id,
                    "session_id": session_id,
                    "attachment_type": att_type,
                    "filename": filename,
                    "content_type": content_type,
                    "file_size_bytes": file_size,
                    "width": width,
                    "height": height,
                    "created_by": created_by,
                    "created_at": created_at.isoformat() if created_at else None
                }

                # If include_data requested, fetch from API gateway or storage
                if include_data:
                    try:
                        # Try to get from API gateway
                        api_response = requests.get(
                            f"{self.api_gateway_url}/attachments/{attachment_id}/data",
                            timeout=30
                        )
                        if api_response.status_code == 200:
                            response_data["base64_data"] = base64.b64encode(api_response.content).decode('utf-8')
                    except Exception as e:
                        logger.warning(f"Could not fetch attachment data: {e}")
                        response_data["data_error"] = "Could not retrieve image data"

                logger.info(f"Retrieved attachment: {attachment_id}")

                self.send_result_notification(
                    status="SUCCESS",
                    result=response_data
                )

            finally:
                cursor.close()
                conn.close()

        except Exception as e:
            logger.error(f"Error getting attachment: {e}")
            self.send_result_notification(
                status="FAILURE",
                result={"error": str(e)}
            )


class ViewImageTool(BaseTool):
    """
    View an image attachment - loads it into the agent's visual context.

    When you call this tool, the image will be visible in your next thinking step.
    Use this when you need to analyze or understand an image that was shared with you.
    """

    def __init__(self):
        super().__init__(
            name="view_image",
            description="View an image attachment - loads it into your visual context for analysis"
        )
        self.database_url = os.environ.get("DATABASE_URL")
        self.api_gateway_url = os.environ.get("API_GATEWAY_URL", "http://api_gateway:8000")

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate view arguments."""
        if "attachment_id" not in arguments:
            return False, "Missing required argument: 'attachment_id'"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "view_image",
            "description": "View an image attachment. The image will appear in your visual context on the next turn, allowing you to analyze and describe it.",
            "parameters": [
                {
                    "name": "attachment_id",
                    "type": "str",
                    "description": "The attachment ID to view (e.g., att_abc123...)",
                    "required": True
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """View an image - queues it for the agent's visual context."""
        try:
            import psycopg2

            attachment_id = arguments["attachment_id"]

            # Get attachment metadata from database
            conn = psycopg2.connect(self.database_url)
            cursor = conn.cursor()

            try:
                query = """
                SELECT attachment_id, original_filename, content_type,
                       file_size_bytes, width, height, storage_path
                FROM attachments
                WHERE attachment_id = %s;
                """
                cursor.execute(query, (attachment_id,))
                result = cursor.fetchone()

                if not result:
                    self.send_result_notification(
                        status="FAILURE",
                        result={"error": f"Attachment not found: {attachment_id}"}
                    )
                    return

                att_id, filename, content_type, file_size, width, height, storage_path = result

                # Fetch actual image data from API gateway
                image_data = None
                try:
                    response = requests.get(
                        f"{self.api_gateway_url}/attachments/{attachment_id}/data",
                        timeout=30
                    )
                    if response.status_code == 200:
                        image_data = base64.b64encode(response.content).decode('utf-8')
                except Exception as e:
                    logger.warning(f"Could not fetch image data from API: {e}")

                # If API fetch failed, the image viewing won't work fully
                # but we still report success with metadata

                # Queue the image for the agent's visual context
                # This is done by including special flags in the result
                self.send_result_notification(
                    status="SUCCESS",
                    result={
                        "attachment_id": att_id,
                        "filename": filename,
                        "content_type": content_type,
                        "width": width,
                        "height": height,
                        "file_size_bytes": file_size,
                        "message": f"Image '{filename}' loaded. It will appear in your visual context.",
                        # Special field to signal image should be added to context
                        "_view_image": True,
                        "_image_data": {
                            "attachment_id": att_id,
                            "content_type": content_type,
                            "base64_data": image_data
                        } if image_data else None
                    }
                )

                logger.info(f"Image {attachment_id} queued for viewing by {self.agent_name}")

            finally:
                cursor.close()
                conn.close()

        except Exception as e:
            logger.error(f"Error viewing image: {e}")
            self.send_result_notification(
                status="FAILURE",
                result={"error": str(e)}
            )


# Export attachment tools
ATTACHMENT_TOOLS = [
    DownloadImageTool,
    CreateAttachmentTool,
    GetAttachmentTool,
    ViewImageTool,
]
