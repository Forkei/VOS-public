"""
Messaging tools for agent communication.

Tools for sending messages to users and other agents.
"""

import os
import json
import logging
import time
import uuid
import asyncio
import requests
from typing import Dict, Any, Optional
from datetime import datetime

from vos_sdk import BaseTool
from vos_sdk.tools.base import ToolAvailabilityContext
import aio_pika
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

# Thread pool for running async code from sync context
_executor = ThreadPoolExecutor(max_workers=4)


def _run_in_thread(coro):
    """Run coroutine in a new event loop in a separate thread."""
    return asyncio.run(coro)


def _run_async(coro):
    """
    Run an async coroutine from sync code safely.

    Handles three cases:
    1. No running loop: Use asyncio.run() directly
    2. Running loop in different thread: Use run_coroutine_threadsafe
    3. Running loop in same thread: Run in thread pool to avoid deadlock
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop - safe to create a new one
        return asyncio.run(coro)

    # There's a running loop - to avoid deadlock, always run in a separate thread
    future = _executor.submit(_run_in_thread, coro)
    try:
        return future.result(timeout=30)
    except Exception as e:
        logger.error(f"Error running async code: {e}")
        raise


async def _publish_to_queue_async(rabbitmq_url: str, queue_name: str, message: dict):
    """Async helper to publish message to RabbitMQ queue."""
    connection = await aio_pika.connect_robust(rabbitmq_url)
    try:
        channel = await connection.channel()
        await channel.declare_queue(queue_name, durable=True)

        await channel.default_exchange.publish(
            aio_pika.Message(
                body=json.dumps(message).encode(),
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT
            ),
            routing_key=queue_name
        )
        logger.debug(f"Published message to {queue_name}")
    finally:
        await connection.close()


class SendUserMessageTool(BaseTool):
    """
    Sends a message to the user via the API Gateway.
    Only the primary_agent should typically use this tool.
    """

    def __init__(self):
        super().__init__(
            name="send_user_message",
            description=(
                "Sends a message to the user. YOU control whether to respond with voice (TTS) or text. "
                "Set audio_message=true to generate speech output, or audio_message=false for text-only response. "
                "This choice is independent of how the user sent their message - you can respond with voice "
                "even if they sent text, and vice versa."
            )
        )
        self.api_gateway_url = os.environ.get("API_GATEWAY_URL", "http://localhost:8000")
        self.internal_api_key = self._load_internal_api_key()

    def _load_internal_api_key(self, max_retries: int = 10, initial_delay: float = 0.5) -> Optional[str]:
        """
        Load internal API key from shared volume with retry logic.

        Retries with exponential backoff to handle cases where the API gateway
        hasn't finished writing the key file yet.

        Args:
            max_retries: Maximum number of retry attempts (default: 10)
            initial_delay: Initial delay in seconds between retries (default: 0.5)

        Returns:
            The internal API key string, or None if loading fails after all retries
        """
        delay = initial_delay

        for attempt in range(max_retries):
            try:
                with open("/shared/internal_api_key", "r") as f:
                    key = f.read().strip()
                    if key:  # Ensure key is not empty
                        logger.info(f"✅ SendUserMessageTool loaded internal API key: {key[:8]}... (attempt {attempt + 1})")
                        return key
                    else:
                        logger.warning(f"⚠️ Internal API key file is empty (attempt {attempt + 1}/{max_retries})")
            except FileNotFoundError:
                logger.warning(f"⚠️ Internal API key file not found (attempt {attempt + 1}/{max_retries})")
            except Exception as e:
                logger.error(f"❌ Failed to load internal API key: {e} (attempt {attempt + 1}/{max_retries})")

            # If not the last attempt, wait before retrying
            if attempt < max_retries - 1:
                logger.info(f"Retrying in {delay:.1f} seconds...")
                time.sleep(delay)
                delay = min(delay * 2, 30)  # Exponential backoff, max 30 seconds

        logger.error("❌ SendUserMessageTool failed to load internal API key after all retry attempts")
        return None

    def reload_internal_api_key(self) -> bool:
        """
        Reload the internal API key from disk.

        Useful when the API gateway restarts and generates a new key.

        Returns:
            True if key was successfully reloaded, False otherwise
        """
        logger.info("SendUserMessageTool reloading internal API key...")
        new_key = self._load_internal_api_key(max_retries=3, initial_delay=1.0)
        if new_key:
            self.internal_api_key = new_key
            logger.info("✅ SendUserMessageTool internal API key reloaded successfully")
            return True
        else:
            logger.error("❌ SendUserMessageTool failed to reload internal API key")
            return False

    def is_available(self, context: ToolAvailabilityContext) -> bool:
        """
        Send user message tool is always available.

        During calls, the agent can still send text messages to the chat UI
        while also using the 'speak' tool for voice responses.
        """
        return True

    def _send_to_voice_gateway(self, content: str, session_id: str) -> None:
        """
        Send message to voice_gateway for TTS generation

        Args:
            content: Message content to convert to speech
            session_id: Session ID for routing
        """
        try:
            logger.info(f"🎤 [SEND_USER_MESSAGE] Preparing to send voice response")
            logger.info(f"🎤 [SEND_USER_MESSAGE] session_id: {session_id}")
            logger.info(f"🎤 [SEND_USER_MESSAGE] content: '{content}'")
            logger.info(f"🎤 [SEND_USER_MESSAGE] agent: {self.agent_name}")

            # Create notification for voice_gateway
            notification = {
                "notification_id": str(uuid.uuid4()),
                "timestamp": datetime.utcnow().isoformat(),
                "recipient_agent_id": "voice_gateway",
                "notification_type": "agent_response",
                "source": f"agent_{self.agent_name}",
                "payload": {
                    "sender_agent_id": self.agent_name,
                    "content": content,
                    "session_id": session_id
                }
            }

            logger.info(f"🎤 [SEND_USER_MESSAGE] Notification payload: {json.dumps(notification, indent=2)}")
            logger.info(f"🎤 [SEND_USER_MESSAGE] Publishing to voice_gateway_queue...")

            # Send to voice_gateway queue using async helper (non-blocking)
            _run_async(_publish_to_queue_async(
                self.rabbitmq_url,
                "voice_gateway_queue",
                notification
            ))

            logger.info(f"🎤 [SEND_USER_MESSAGE] ✅ Successfully sent voice response to voice_gateway for session {session_id}")
            logger.info(f"🎤 [SEND_USER_MESSAGE] ✅ Message is now queued for TTS generation")

            self.send_result_notification(
                status="SUCCESS",
                result={
                    "message_sent": True,
                    "audio_message": True,
                    "session_id": session_id,
                    "content_length": len(content),
                    "timestamp": datetime.now().isoformat()
                }
            )

        except Exception as e:
            logger.error(f"❌ Failed to send message to voice_gateway: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to send voice message: {str(e)}"
            )

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate message content."""
        if "content" not in arguments:
            return False, "Missing required argument: 'content'"

        if not isinstance(arguments["content"], str):
            return False, f"'content' must be a string, got {type(arguments['content']).__name__}"

        if not arguments["content"].strip():
            return False, "'content' cannot be empty"

        # Only primary_agent should send user messages
        if self.agent_name != "primary_agent":
            logger.warning(f"Agent {self.agent_name} attempting to send user message")

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "send_user_message",
            "description": "Sends a message to the user through the API Gateway, optionally with images",
            "parameters": [
                {
                    "name": "content",
                    "type": "str",
                    "description": "Message content to send to the user",
                    "required": True
                },
                {
                    "name": "content_type",
                    "type": "str",
                    "description": "Type of content (text, code, etc.)",
                    "required": False
                },
                {
                    "name": "session_id",
                    "type": "str",
                    "description": "Session ID for message tracking",
                    "required": False
                },
                {
                    "name": "attachment_ids",
                    "type": "list[str]",
                    "description": "List of attachment IDs (images) to include with the message. The user will see these images in the chat.",
                    "required": False
                },
                {
                    "name": "document_ids",
                    "type": "list[str]",
                    "description": "List of document IDs to reference in the message",
                    "required": False
                },
                {
                    "name": "audio_message",
                    "type": "bool",
                    "description": (
                        "Set to true to respond with VOICE (text-to-speech audio), "
                        "or false for TEXT-ONLY response. Default is false. "
                        "YOU choose this - it's independent of how the user sent their message. "
                        "Example: User sends text, you can still respond with voice by setting audio_message=true."
                    ),
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Send message to user via API Gateway.

        Args:
            arguments: Must contain 'content' key
        """
        self._execute_with_retry(arguments, retry=True)

    def _execute_with_retry(self, arguments: Dict[str, Any], retry: bool = True) -> None:
        """
        Internal method to send message with optional retry on auth failure.

        Args:
            arguments: Must contain 'content' key
            retry: Whether to retry with key reload on 401 response
        """
        content = arguments["content"]
        content_type = arguments.get("content_type", "text")
        session_id = arguments.get("session_id")
        attachment_ids = arguments.get("attachment_ids", [])
        document_ids = arguments.get("document_ids", [])
        audio_message = arguments.get("audio_message", False)

        # If audio_message is True, route to voice_gateway for TTS
        if audio_message and session_id:
            self._send_to_voice_gateway(content, session_id)
            return

        try:
            # Send message to API Gateway
            message_data = {
                "agent_id": self.agent_name,
                "content": content,
                "content_type": content_type,
                "timestamp": datetime.now().isoformat()
            }

            if session_id:
                message_data["session_id"] = session_id

            # Include attachments (images) if provided
            if attachment_ids:
                message_data["attachment_ids"] = attachment_ids
                logger.info(f"Including {len(attachment_ids)} attachments in user message")

            # Include document references if provided
            if document_ids:
                message_data["document_ids"] = document_ids
                logger.info(f"Including {len(document_ids)} document references in user message")

            # Build headers with internal API key for authentication
            headers = {"Content-Type": "application/json"}
            if self.internal_api_key:
                headers["X-Internal-Key"] = self.internal_api_key

            response = requests.post(
                f"{self.api_gateway_url}/api/v1/messages/user",
                json=message_data,
                headers=headers,
                timeout=10
            )

            if response.status_code == 200:
                result_data = {
                    "message_sent": True,
                    "content_length": len(content),
                    "timestamp": datetime.now().isoformat()
                }
                if attachment_ids:
                    result_data["attachments_included"] = len(attachment_ids)
                if document_ids:
                    result_data["documents_referenced"] = len(document_ids)

                self.send_result_notification(
                    status="SUCCESS",
                    result=result_data
                )
            elif response.status_code == 401 and retry:
                # Authentication failed - try reloading the internal API key
                logger.warning("⚠️ Request returned 401 Unauthorized - attempting to reload internal API key")
                if self.reload_internal_api_key():
                    logger.info("Retrying send_user_message with new API key...")
                    self._execute_with_retry(arguments, retry=False)
                else:
                    self.send_result_notification(
                        status="FAILURE",
                        error_message="Authentication failed and could not reload API key"
                    )
            else:
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"API Gateway returned status {response.status_code}"
                )

        except requests.exceptions.RequestException as e:
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to send message to API Gateway: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Unexpected error sending user message: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to send user message: {str(e)}"
            )


class SendAgentMessageTool(BaseTool):
    """
    Sends a message to another agent via their RabbitMQ queue.
    Supports sending images/attachments along with the message.
    """

    def __init__(self):
        super().__init__(
            name="send_agent_message",
            description="Sends a message to another agent in the system, optionally with images"
        )
        self.database_url = os.environ.get("DATABASE_URL")

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate agent message arguments."""
        if "agent_id" not in arguments:
            return False, "Missing required argument: 'agent_id'"

        if "content" not in arguments:
            return False, "Missing required argument: 'content'"

        if not isinstance(arguments["agent_id"], str):
            return False, f"'agent_id' must be a string"

        if not isinstance(arguments["content"], str):
            return False, f"'content' must be a string"

        if arguments["agent_id"] == self.agent_name:
            return False, "Cannot send message to self"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "send_agent_message",
            "description": "Sends a message to another agent in the VOS system, optionally with images",
            "parameters": [
                {
                    "name": "agent_id",
                    "type": "str",
                    "description": "Target agent ID (e.g., 'weather_agent', 'search_agent', 'primary_agent')",
                    "required": True
                },
                {
                    "name": "content",
                    "type": "str",
                    "description": "Message content to send to the agent",
                    "required": True
                },
                {
                    "name": "attachment_ids",
                    "type": "list[str]",
                    "description": "List of attachment IDs (images) to include with the message",
                    "required": False
                },
                {
                    "name": "document_ids",
                    "type": "list[str]",
                    "description": "List of document IDs to reference in the message",
                    "required": False
                }
            ]
        }

    def _fetch_attachment_data(self, attachment_id: str) -> Optional[Dict[str, Any]]:
        """Fetch attachment metadata and base64 data from database."""
        try:
            import psycopg2

            if not self.database_url:
                logger.warning("DATABASE_URL not set - cannot fetch attachment")
                return None

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
                    logger.warning(f"Attachment not found: {attachment_id}")
                    return None

                att_id, filename, content_type, file_size, width, height, storage_path = result

                # For now, we need to fetch the actual image data
                # This could be from GCS or local storage
                # TODO: Implement proper storage retrieval

                return {
                    "attachment_id": att_id,
                    "filename": filename,
                    "content_type": content_type,
                    "file_size_bytes": file_size,
                    "width": width,
                    "height": height,
                    # base64_data would be fetched from storage
                }

            finally:
                cursor.close()
                conn.close()

        except Exception as e:
            logger.error(f"Error fetching attachment {attachment_id}: {e}")
            return None

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Send message to another agent, optionally with images and documents.

        Args:
            arguments: Must contain 'agent_id' and 'content'.
                       Optional: 'attachment_ids' (list of image attachments)
                       Optional: 'document_ids' (list of document references)
        """
        target_agent = arguments["agent_id"]
        content = arguments["content"]
        attachment_ids = arguments.get("attachment_ids", [])
        document_ids = arguments.get("document_ids", [])

        try:
            # Fetch attachment metadata if any
            attachments_data = []
            if attachment_ids:
                for att_id in attachment_ids:
                    att_data = self._fetch_attachment_data(att_id)
                    if att_data:
                        attachments_data.append(att_data)
                    else:
                        logger.warning(f"Could not fetch attachment: {att_id}")

            # Create agent message notification
            notification = {
                "notification_id": str(uuid.uuid4()),
                "timestamp": datetime.utcnow().isoformat(),
                "recipient_agent_id": target_agent,
                "notification_type": "agent_message",
                "source": f"agent_{self.agent_name}",
                "payload": {
                    "sender_agent_id": self.agent_name,
                    "content": content,
                    "attachment_ids": attachment_ids,
                    "attachments": attachments_data,
                    "document_ids": document_ids
                }
            }

            # Send to target agent's queue using async helper (non-blocking)
            target_queue = f"{target_agent}_queue"
            _run_async(_publish_to_queue_async(
                self.rabbitmq_url,
                target_queue,
                notification
            ))

            result_msg = {
                "message_sent": True,
                "recipient": target_agent,
                "notification_id": notification["notification_id"]
            }

            if attachment_ids:
                result_msg["attachments_included"] = len(attachments_data)
            if document_ids:
                result_msg["documents_referenced"] = len(document_ids)

            self.send_result_notification(
                status="SUCCESS",
                result=result_msg
            )

        except Exception as e:
            logger.error(f"Failed to send agent message: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to send message to {target_agent}: {str(e)}"
            )


# Export messaging tools
MESSAGING_TOOLS = [
    SendUserMessageTool,
    SendAgentMessageTool
]