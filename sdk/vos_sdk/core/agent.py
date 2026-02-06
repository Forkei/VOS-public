"""
VOSAgent - The core autonomous agent class.

Implements the LLM-powered agent processing cycle:
1. Check processing state (idle -> thinking -> executing_tools -> idle)
2. Get pending notifications from RabbitMQ queue
3. Build context from notifications + message history
4. Call Gemini LLM to decide what tools to use
5. Execute tool calls and collect results
6. Update message history and return to idle state
"""

import asyncio
import json
import logging
import threading
import time
from typing import Dict, Any, List, Optional, Callable
from abc import ABC, abstractmethod
from pathlib import Path
from datetime import datetime

import pika
from google import genai
from pydantic import BaseModel
from pika.exceptions import AMQPConnectionError, ChannelClosedByBroker

from .config import AgentConfig
from .database import DatabaseClient, ProcessingState, AgentStatus
from .context import ContextBuilder, NotificationType, MessageRole

# Initialize logger early for memory module imports
logger = logging.getLogger(__name__)


class DateTimeEncoder(json.JSONEncoder):
    """Custom JSON encoder that handles datetime objects."""
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)

# Try to import memory modules - optional but recommended
try:
    import sys
    import os
    from pathlib import Path

    # Get tools directory from environment or use default container path
    tools_path_str = os.getenv("VOS_TOOLS_PATH", "/app/tools")
    tools_path = Path(tools_path_str)

    if tools_path.exists() and str(tools_path) not in sys.path:
        sys.path.insert(0, str(tools_path))
        logger.debug(f"Added tools path to sys.path: {tools_path}")
    elif not tools_path.exists():
        logger.warning(f"Tools path does not exist: {tools_path}")

    from memory.memory_creator import MemoryCreatorModule
    from memory.memory_retriever import MemoryRetrieverModule
    MEMORY_MODULES_AVAILABLE = True
    logger.info("Memory modules available and imported")
except ImportError as e:
    MEMORY_MODULES_AVAILABLE = False
    logger.warning(f"Memory modules not available: {e}")
except Exception as e:
    MEMORY_MODULES_AVAILABLE = False
    logger.error(f"Unexpected error loading memory modules: {e}")

# Try to import metrics - optional dependency
try:
    from prometheus_client import Counter, Histogram, Gauge
    METRICS_AVAILABLE = True

    # Agent metrics (shared across all agent instances)
    agent_notifications_processed = Counter(
        'agent_notifications_processed_total',
        'Total notifications processed by agent',
        ['agent_name', 'notification_type']
    )
    agent_llm_calls = Counter(
        'agent_llm_calls_total',
        'Total LLM API calls',
        ['agent_name', 'model', 'status']
    )
    agent_llm_duration = Histogram(
        'agent_llm_call_duration_seconds',
        'LLM call duration in seconds',
        ['agent_name', 'model']
    )
    agent_tool_executions = Counter(
        'agent_tool_executions_total',
        'Total tool executions',
        ['agent_name', 'tool_name', 'status']
    )
    agent_queue_depth = Gauge(
        'agent_notification_queue_depth',
        'Number of notifications waiting in queue',
        ['agent_name']
    )
    agent_processing_loop_duration = Histogram(
        'agent_processing_loop_duration_seconds',
        'Time spent in notification processing loop',
        ['agent_name']
    )
    agent_errors_total = Counter(
        'agent_errors_total',
        'Total errors encountered',
        ['agent_name', 'error_type']
    )
except ImportError:
    METRICS_AVAILABLE = False

# Configuration for retry logic
MAX_RETRIES = 3  # Maximum times a notification will be requeued
TRANSIENT_ERRORS = (
    AMQPConnectionError,
    ConnectionError,
    TimeoutError,
    # Add more transient error types as needed
)




class VOSAgent:
    """
    Autonomous LLM-powered agent for the VOS ecosystem.

    Manages the complete agent lifecycle:
    - RabbitMQ message consumption
    - LLM-based decision making
    - Tool execution
    - State management
    - Message history persistence
    """

    def __init__(self, config: AgentConfig, agent_description: str):
        self.config = config
        self.agent_description = agent_description
        self.agent_name = config.agent_name
        self.agent_display_name = config.agent_display_name

        # Initialize components
        self.db = DatabaseClient(config)

        # System prompt path - use /app/system_prompt.txt by default (volume mounted)
        # Can be overridden via SYSTEM_PROMPT_PATH env var
        import os
        self._system_prompt_path = os.getenv(
            "SYSTEM_PROMPT_PATH",
            "/app/system_prompt.txt"
        )

        # Use live prompt getter for instant prompt updates without restart
        # on_prompt_changed syncs the new prompt to the transcript database
        self.context_builder = ContextBuilder(
            config.agent_name,
            agent_description=agent_description,  # Fallback for backward compat
            max_conversation_messages=config.max_conversation_messages,
            system_prompt_getter=self.get_live_system_prompt,
            on_prompt_changed=self._handle_prompt_changed
        )

        # RabbitMQ connection components
        self.connection: Optional[pika.BlockingConnection] = None
        self.channel: Optional[pika.channel.Channel] = None

        # Tool registry - import here to avoid circular dependency
        from ..tools.base import BaseTool, ToolAvailabilityContext
        self.tools: Dict[str, BaseTool] = {}
        self._tool_availability_context_class = ToolAvailabilityContext

        # Agent state
        self.running = False
        self.last_check_time = 0
        self._processing_lock = threading.Lock()  # Prevent concurrent processing
        self._last_session_id: Optional[str] = None  # Track last session for action_status
        self._last_call_id: Optional[str] = None  # Track last call_id for call tools
        self._fast_mode: bool = False  # Track fast mode for low-latency calls

        # Error circuit breaker to prevent infinite error notification loops
        self._error_count = 0
        self._error_reset_time = time.time()
        self._max_errors_per_minute = 5  # Stop sending error notifications after 5 errors/minute

        # Pending images for visual context (from view_image tool)
        self._pending_images: List[Dict[str, Any]] = []

        # Configure Gemini LLM
        self.genai_client = genai.Client(api_key=config.gemini_api_key)

        # Setup logging early so we can see initialization logs
        config.setup_logging()

        # Initialize memory modules if available
        self.memory_creator = None
        self.memory_retriever = None
        if MEMORY_MODULES_AVAILABLE:
            try:
                self.memory_creator = MemoryCreatorModule(self.agent_name, config.gemini_api_key, self.db)
                self.memory_retriever = MemoryRetrieverModule(self.agent_name, config.gemini_api_key, self.db)
                logger.info(f"Memory modules initialized: creator={self.memory_creator is not None}, retriever={self.memory_retriever is not None}")
            except Exception as e:
                logger.warning(f"Failed to initialize memory modules: {e}")
        else:
            logger.info("Memory modules not available (import failed)")

        logger.info(f"Initialized VOSAgent: {self.agent_display_name} ({self.agent_name})")

    def register_tool(self, tool) -> None:
        """
        Register a tool for use by this agent.

        Args:
            tool: Tool instance to register (must be BaseTool subclass)
        """
        # Setup tool with agent configuration
        tool.setup(self.agent_name, self.config.rabbitmq_url)
        self.tools[tool.name] = tool
        logger.debug(f"Registered tool: {tool.name}")

    def get_live_system_prompt(self) -> str:
        """
        Get the current system prompt with tools injected.

        Uses a database-first approach with file fallback:
        1. First, try to load the active prompt from the database
        2. If database fetch fails, fall back to the file-based system

        This enables:
        - API-editable prompts via the system-prompts endpoints
        - Live prompt editing without restarting agents
        - Version history and rollback capability
        - Gradual migration from files to database

        Returns:
            Complete system prompt with tools section included
        """
        # Try database-first approach
        try:
            prompt_result = self.db.get_full_prompt_content(self.agent_name)
            if prompt_result.status == "SUCCESS":
                full_content = prompt_result.result.get("full_content", "")
                tools_position = prompt_result.result.get("tools_position", "end")

                if full_content:
                    # Generate tools section
                    tools_section = self._format_tools_section()

                    # Inject tools based on position
                    if tools_position == "start":
                        return f"## Available Tools\n\n{tools_section}\n\n{full_content}"
                    elif tools_position == "end":
                        return f"{full_content}\n\n## Available Tools\n\n{tools_section}"
                    else:  # "none" or unknown
                        return full_content

        except Exception as e:
            logger.debug(f"Database prompt fetch failed, using file fallback: {e}")

        # Fallback to file-based system
        return self.generate_system_prompt(self._system_prompt_path)

    def _handle_prompt_changed(self, new_content: str) -> None:
        """
        Handle system prompt changes by updating the transcript database.

        Called by ContextBuilder when it detects the prompt has changed
        (via hash comparison). Updates only the system message in the
        transcript without affecting conversation history.

        Args:
            new_content: The new system prompt content
        """
        logger.info(f"📝 System prompt changed, updating transcript database...")
        result = self.db.update_system_prompt(self.agent_name, new_content)
        if result.status == "SUCCESS":
            logger.info(f"✅ Transcript system prompt updated successfully")
        else:
            logger.error(f"❌ Failed to update transcript system prompt: {result.error_message}")

    def generate_system_prompt(self, prompt_file: str = None) -> str:
        """
        Generate system prompt with available tools dynamically included.

        Only replaces the {tools} placeholder - the prompt file should have all other
        content already filled in.

        Args:
            prompt_file: Path to prompt file. If None, uses self._system_prompt_path

        Returns:
            System prompt string with {tools} placeholder replaced
        """
        import os

        # Use the configured path if not specified
        if prompt_file is None:
            prompt_file = getattr(self, '_system_prompt_path', '/app/system_prompt.txt')

        # Load prompt from file
        try:
            with open(prompt_file, 'r', encoding='utf-8') as f:
                template = f.read()
        except FileNotFoundError:
            logger.warning(f"System prompt file not found at {prompt_file}, using fallback")
            # Fall back to agent_description if file not found
            if self.agent_description:
                return self.agent_description
            logger.error(f"Agent {self.agent_name} has no system prompt file at {prompt_file}")
            return ""

        # Generate tools section
        tools_section = self._format_tools_section()

        # Only replace the {tools} placeholder using safe string replacement
        # Don't use .format() because it treats all {} as placeholders,
        # which breaks JSON examples in the system prompts
        prompt = template.replace("{tools}", tools_section)

        return prompt

    def _build_tool_availability_context(self):
        """
        Build the tool availability context from current agent state.

        Returns:
            ToolAvailabilityContext with session_id, call_id, and is_on_call
        """
        return self._tool_availability_context_class.from_agent_state(
            session_id=self._last_session_id,
            call_id=self._last_call_id
        )

    def _format_tools_section(self) -> str:
        """
        Format tool descriptions for inclusion in system prompt.

        Only includes tools that are available in the current context
        (e.g., call tools only shown during calls, messaging tools only when not on call).

        In fast_mode, only voice tools (speak, hang_up) are available for low-latency responses.

        Returns:
            Formatted string describing all available registered tools
        """
        if not self.tools:
            return "No tools are currently registered."

        # Build context for filtering tools
        context = self._build_tool_availability_context()

        # Filter tools by availability
        available_tools = {
            name: tool for name, tool in self.tools.items()
            if tool.is_available(context)
        }

        # In fast_mode, restrict to only essential voice tools for low latency
        if self._fast_mode:
            fast_mode_tools = {"speak", "hang_up"}
            available_tools = {
                name: tool for name, tool in available_tools.items()
                if name in fast_mode_tools
            }
            logger.debug(f"⚡ Fast mode: Limited to {len(available_tools)} tools: {list(available_tools.keys())}")

        if not available_tools:
            return "No tools are currently available in this context."

        tools_text = []
        for tool_name, tool in available_tools.items():
            try:
                tool_info = tool.get_tool_info()

                # Format tool header
                tool_text = f"### {tool_info['command']}"
                tool_text += f"\n{tool_info['description']}"

                # Format parameters if present
                if tool_info.get('parameters'):
                    tool_text += "\n**Parameters:**"
                    for param in tool_info['parameters']:
                        required = "Required" if param.get('required', True) else "Optional"
                        tool_text += f"\n- `{param['name']}` ({param['type']}): {param['description']} [{required}]"
                else:
                    tool_text += "\n**Parameters:** None"

                tools_text.append(tool_text)

            except Exception as e:
                logger.warning(f"Failed to get info for tool {tool_name}: {e}")
                tools_text.append(f"### {tool_name}\n*Tool information unavailable*")

        return "\n\n".join(tools_text)

    def _connect_rabbitmq(self, max_retries: int = 10) -> bool:
        """
        Connect to RabbitMQ with retry logic and exponential backoff.

        Args:
            max_retries: Maximum number of connection attempts

        Returns:
            True if connection successful, False otherwise
        """
        retry_delay = 5  # Start with 5 seconds

        for attempt in range(max_retries):
            try:
                logger.debug(f"RabbitMQ connection attempt {attempt + 1}/{max_retries}")

                # Parse URL and add heartbeat configuration to prevent timeout
                import urllib.parse
                parsed = urllib.parse.urlparse(self.config.rabbitmq_url)

                # Extract connection details
                credentials = pika.PlainCredentials(
                    parsed.username or 'guest',
                    parsed.password or 'guest'
                )

                params = pika.ConnectionParameters(
                    host=parsed.hostname or 'localhost',
                    port=parsed.port or 5672,
                    virtual_host=parsed.path.lstrip('/') if parsed.path else '/',
                    credentials=credentials,
                    heartbeat=600,  # 10 minutes
                    blocked_connection_timeout=300  # 5 minutes
                )

                self.connection = pika.BlockingConnection(params)
                self.channel = self.connection.channel()

                # Declare agent's queue
                self.channel.queue_declare(queue=self.config.queue_name, durable=True)

                # CRITICAL: Set QoS to process one message at a time
                self.channel.basic_qos(prefetch_count=1)

                logger.info(f"Connected to RabbitMQ queue: {self.config.queue_name}")
                logger.info(f"QoS prefetch_count: 1 (processing one message at a time)")
                return True

            except AMQPConnectionError as e:
                logger.error(f"RabbitMQ connection failed: {e}")

                if attempt < max_retries - 1:
                    logger.info(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    # Exponential backoff, cap at 60 seconds
                    retry_delay = min(retry_delay * 2, 60)
                else:
                    logger.error(f"Max retries ({max_retries}) exhausted. Cannot connect to RabbitMQ.")
                    return False

        return False

    def _disconnect_rabbitmq(self) -> None:
        """Clean up RabbitMQ connections."""
        if self.channel and not self.channel.is_closed:
            self.channel.close()
        if self.connection and not self.connection.is_closed:
            self.connection.close()

    def _get_pending_notifications(self) -> List[Dict[str, Any]]:
        """
        Get all pending notifications from the agent's RabbitMQ queue.

        Returns:
            List of notification objects
        """
        notifications = []

        if not self.channel:
            logger.error("No RabbitMQ channel available")
            return notifications

        try:
            while True:
                method_frame, header_frame, body = self.channel.basic_get(
                    queue=self.config.queue_name,
                    auto_ack=False  # Manual acknowledgment for reliability
                )

                if method_frame is None:
                    # No more messages
                    break

                try:
                    notification = json.loads(body.decode('utf-8'))
                    # Store delivery tag and retry count for later acknowledgment
                    notification['_delivery_tag'] = method_frame.delivery_tag
                    notification['_retry_count'] = notification.get('_retry_count', 0)
                    notifications.append(notification)
                    logger.debug(f"Retrieved notification: {notification.get('notification_type')} (retry: {notification['_retry_count']})")
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON in notification: {body}")
                    # Reject malformed messages without requeue (permanent error)
                    self.channel.basic_nack(
                        delivery_tag=method_frame.delivery_tag,
                        requeue=False
                    )
                    logger.warning(f"Rejected malformed notification (no requeue)")

        except (AMQPConnectionError, ChannelClosedByBroker) as e:
            logger.error(f"RabbitMQ connection error while retrieving notifications: {e}")
            logger.info("Attempting to reconnect to RabbitMQ...")
            if self._connect_rabbitmq():
                logger.info("✅ Successfully reconnected to RabbitMQ")
            else:
                logger.error("❌ Failed to reconnect to RabbitMQ")
        except Exception as e:
            logger.error(f"Error retrieving notifications: {e}")

        if notifications:
            logger.info(f"📬 Retrieved {len(notifications)} pending notifications")
            # Update queue depth metric
            if METRICS_AVAILABLE:
                agent_queue_depth.labels(agent_name=self.agent_name).set(len(notifications))
        return notifications

    def _extract_images_from_tool_results(self, notifications: List[Dict[str, Any]]) -> None:
        """
        Extract images from tool result notifications that have _view_image flag.

        When the view_image tool is called, it returns a result with _view_image=True
        and _image_data containing the image. This method extracts those images and
        adds them to _pending_images so they'll be included in the next LLM call.

        Args:
            notifications: List of notifications to scan for image data
        """
        for notification in notifications:
            # Only process tool_result notifications
            if notification.get("notification_type") != "tool_result":
                continue

            payload = notification.get("payload", {})
            result = payload.get("result", {})

            # Check for _view_image flag
            if isinstance(result, dict) and result.get("_view_image"):
                image_data = result.get("_image_data")
                if image_data and image_data.get("base64_data"):
                    self._pending_images.append({
                        "attachment_id": image_data.get("attachment_id"),
                        "content_type": image_data.get("content_type", "image/png"),
                        "base64_data": image_data["base64_data"]
                    })
                    logger.info(f"📷 Queued image {image_data.get('attachment_id')} for visual context")

    def _inject_pending_images(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Inject pending images into the conversation messages.

        If there are pending images (from view_image tool), add them to the last
        user message so the LLM can see them.

        Args:
            messages: Conversation messages

        Returns:
            Modified messages with images injected
        """
        if not self._pending_images:
            return messages

        logger.info(f"📷 Injecting {len(self._pending_images)} images into visual context")

        # Find the last user message and add images to it
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                content = messages[i].get("content")

                # Convert content to structured format if it's a string
                if isinstance(content, str):
                    messages[i]["content"] = {
                        "text": content,
                        "images": self._pending_images
                    }
                elif isinstance(content, dict):
                    # Already structured, add images
                    if "images" not in content:
                        content["images"] = []
                    content["images"].extend(self._pending_images)
                    # Ensure text field exists
                    if "text" not in content:
                        content["text"] = str(content.get("notifications", ""))

                logger.debug(f"Injected images into message at index {i}")
                break

        return messages

    def _forward_browser_screenshots(self, notifications: List[Dict[str, Any]]) -> None:
        """
        Forward browser screenshots from tool results to the frontend.

        When browser tools complete, they include a 'screenshot' field in the result.
        This method extracts screenshots and sends them to the API Gateway for
        delivery to the frontend via WebSocket.

        Args:
            notifications: List of notifications to scan for browser screenshots
        """
        for notification in notifications:
            # Only process tool_result notifications
            if notification.get("notification_type") != "tool_result":
                continue

            payload = notification.get("payload", {})
            result = payload.get("result", {})

            # Check for screenshot field (from browser tools)
            if isinstance(result, dict) and result.get("screenshot"):
                screenshot_base64 = result.get("screenshot")
                current_url = result.get("current_url") or result.get("url")
                task = result.get("task")

                try:
                    import requests

                    # Get internal API key
                    internal_api_key = None
                    try:
                        with open("/shared/internal_api_key", "r") as f:
                            internal_api_key = f.read().strip()
                    except Exception as e:
                        logger.warning(f"Could not load internal API key for browser screenshot: {e}")
                        continue

                    if not internal_api_key:
                        logger.warning("Internal API key not found, skipping browser screenshot")
                        continue

                    # Send to API Gateway
                    headers = {
                        "Content-Type": "application/json",
                        "X-Internal-Key": internal_api_key
                    }

                    data = {
                        "agent_id": self.agent_name,
                        "session_id": self._last_session_id,
                        "screenshot_base64": screenshot_base64,
                        "current_url": current_url,
                        "task": task
                    }

                    response = requests.post(
                        f"{self.config.api_gateway_url}/api/v1/notifications/browser-screenshot",
                        json=data,
                        headers=headers,
                        timeout=10
                    )

                    if response.status_code == 200:
                        logger.info(f"Forwarded browser screenshot to frontend (url: {current_url})")
                    else:
                        logger.warning(f"Failed to forward browser screenshot: {response.status_code}")

                except Exception as e:
                    logger.error(f"Error forwarding browser screenshot: {e}")


    def _call_llm(self, messages: List[Dict[str, Any]]) -> str:
        """
        Call Gemini LLM with conversation context, supporting multimodal (vision).

        Args:
            messages: List of conversation messages in VOS format.
                      Messages can include images via content dict with "images" key:
                      {
                          "role": "user",
                          "content": {
                              "text": "What's in this image?",
                              "images": [
                                  {"content_type": "image/png", "base64_data": "..."}
                              ]
                          }
                      }

        Returns:
            LLM response string

        Raises:
            RuntimeError: If LLM call fails
        """
        try:
            # Convert VOS messages to Gemini format (with multimodal support)
            gemini_messages = []
            for message in messages:
                role = message["role"]
                content = message["content"]

                # Build parts list for this message
                parts = []

                # Handle different content formats
                if isinstance(content, dict):
                    import base64
                    from google.genai import types as genai_types

                    images_to_process = []
                    cleaned_text_content = None

                    # Check for structured content with potential images
                    if "text" in content:
                        cleaned_text_content = content["text"]
                    elif "notifications" in content:
                        # Parse notifications and strip base64 data for clean text
                        try:
                            notifications_str = content["notifications"]
                            if isinstance(notifications_str, str):
                                notifications_data = json.loads(notifications_str)
                                if isinstance(notifications_data, list):
                                    # Extract images and clean the notifications
                                    for notif in notifications_data:
                                        payload = notif.get("payload", {})
                                        if "images" in payload and isinstance(payload["images"], list):
                                            for img in payload["images"]:
                                                if isinstance(img, dict) and "base64_data" in img:
                                                    images_to_process.append(img)
                                            # Replace images with just metadata (no base64)
                                            payload["images"] = [
                                                {
                                                    "attachment_id": img.get("attachment_id", "unknown"),
                                                    "content_type": img.get("content_type", "image/png"),
                                                    "_note": "Image data sent separately to vision model"
                                                }
                                                for img in payload["images"]
                                            ]
                                    # Use cleaned notifications as text
                                    cleaned_text_content = json.dumps(notifications_data)
                                    if images_to_process:
                                        logger.info(f"📷 Extracted {len(images_to_process)} images, cleaned base64 from text")
                        except (json.JSONDecodeError, TypeError) as e:
                            logger.debug(f"Could not parse notifications: {e}")
                            cleaned_text_content = str(content)
                    else:
                        cleaned_text_content = str(content)

                    # Add cleaned text content
                    if cleaned_text_content:
                        parts.append({"text": cleaned_text_content})

                    # Location 1: Direct images in content (also check here)
                    if "images" in content and isinstance(content["images"], list):
                        for img in content["images"]:
                            if isinstance(img, dict) and "base64_data" in img:
                                images_to_process.append(img)

                    # Process all found images - send to vision model
                    for img in images_to_process:
                        if isinstance(img, dict) and "base64_data" in img:
                            img_content_type = img.get("content_type", "image/png")
                            try:
                                image_bytes = base64.b64decode(img["base64_data"])
                                parts.append(
                                    genai_types.Part.from_bytes(
                                        data=image_bytes,
                                        mime_type=img_content_type
                                    )
                                )
                                logger.info(f"📷 Added image to LLM context: {img_content_type}, {len(image_bytes)} bytes")
                            except Exception as e:
                                logger.error(f"Failed to decode image: {e}")

                elif isinstance(content, str):
                    # Try to parse as JSON notifications and extract/clean images
                    import base64
                    from google.genai import types as genai_types

                    try:
                        # Check if this is a JSON array of notifications
                        parsed_content = json.loads(content)
                        if isinstance(parsed_content, list):
                            images_found = []
                            # Extract images and clean base64 from notifications
                            for notif in parsed_content:
                                if isinstance(notif, dict):
                                    payload = notif.get("payload", {})
                                    if "images" in payload and isinstance(payload["images"], list):
                                        for img in payload["images"]:
                                            if isinstance(img, dict) and "base64_data" in img:
                                                images_found.append(img)
                                        # Replace images with metadata only
                                        payload["images"] = [
                                            {
                                                "attachment_id": img.get("attachment_id", "unknown"),
                                                "content_type": img.get("content_type", "image/png"),
                                                "_note": "Image data sent separately to vision model"
                                            }
                                            for img in payload["images"]
                                        ]

                            # Use cleaned content
                            cleaned_content = json.dumps(parsed_content)
                            parts.append({"text": cleaned_content})

                            # Add images to parts for vision
                            for img in images_found:
                                if "base64_data" in img:
                                    img_content_type = img.get("content_type", "image/png")
                                    try:
                                        image_bytes = base64.b64decode(img["base64_data"])
                                        parts.append(
                                            genai_types.Part.from_bytes(
                                                data=image_bytes,
                                                mime_type=img_content_type
                                            )
                                        )
                                        logger.info(f"📷 Added image from string content: {img_content_type}, {len(image_bytes)} bytes")
                                    except Exception as e:
                                        logger.error(f"Failed to decode image from string: {e}")

                            if images_found:
                                logger.info(f"📷 Extracted {len(images_found)} images from string content")
                        else:
                            # Not a list, use as-is
                            parts.append({"text": content})
                    except (json.JSONDecodeError, TypeError):
                        # Not JSON, use as plain text
                        parts.append({"text": content})
                else:
                    parts.append({"text": str(content)})

                # Map VOS roles to Gemini roles
                if role == "system":
                    # System messages become the first user message in Gemini
                    gemini_messages.append({
                        "role": "user",
                        "parts": [{"text": f"System: {parts[0].get('text', str(parts[0]))}"}]
                    })
                elif role == "user":
                    gemini_messages.append({
                        "role": "user",
                        "parts": parts
                    })
                elif role == "assistant":
                    # Assistant messages only include text (no images)
                    text_parts = [p for p in parts if "text" in p]
                    gemini_messages.append({
                        "role": "model",
                        "parts": text_parts if text_parts else [{"text": str(content)}]
                    })

            logger.debug(f"Preparing to call Gemini LLM with {len(gemini_messages)} messages")
            logger.debug(f"Gemini messages: {gemini_messages}")

            # Call Gemini without strict schema - just request JSON format
            # Add timeout to prevent hanging
            import signal

            def timeout_handler(signum, frame):
                raise TimeoutError("Gemini LLM call timed out")

            # Set a 90-second timeout for the LLM call
            signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(90)

            # Track LLM call with metrics
            # Use fast model for low-latency voice calls when fast_mode is enabled
            if self._fast_mode:
                model_name = "gemini-2.5-flash-lite"
                logger.info(f"⚡ Using fast model: {model_name}")
            else:
                model_name = "gemini-3-flash-preview"
            start_time = time.time()

            try:
                # Configure JSON mode for structured output
                from google.genai import types

                response = self.genai_client.models.generate_content(
                    model=model_name,
                    contents=gemini_messages,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json"
                    )
                )

                # Record successful LLM call
                if METRICS_AVAILABLE:
                    duration = time.time() - start_time
                    agent_llm_duration.labels(agent_name=self.agent_name, model=model_name).observe(duration)
                    agent_llm_calls.labels(agent_name=self.agent_name, model=model_name, status="success").inc()

            finally:
                # Cancel the alarm
                signal.alarm(0)

            if not response or not response.text:
                raise RuntimeError("Empty response from Gemini LLM")

            logger.debug(f"Raw LLM response: '{response.text}'")
            return response.text.strip()

        except Exception as e:
            logger.error(f"LLM call failed: {e}")

            # Record failed LLM call
            if METRICS_AVAILABLE:
                # Record duration even for failed calls
                duration = time.time() - start_time
                agent_llm_duration.labels(agent_name=self.agent_name, model=model_name).observe(duration)
                agent_llm_calls.labels(agent_name=self.agent_name, model=model_name, status="error").inc()
                agent_errors_total.labels(agent_name=self.agent_name, error_type="llm_call").inc()

            raise RuntimeError(f"Failed to call Gemini LLM: {e}")

    def _send_error_notification(self, error_type: str, error_message: str) -> None:
        """
        Send an error notification to this agent's queue with circuit breaker protection.

        Args:
            error_type: Type of error (e.g., "llm_parse_error", "tool_not_found")
            error_message: Detailed error message
        """
        # Circuit breaker: Reset counter every 60 seconds
        current_time = time.time()
        if current_time - self._error_reset_time > 60:
            self._error_count = 0
            self._error_reset_time = current_time

        # Circuit breaker: Stop after max errors per minute to prevent infinite loops
        self._error_count += 1
        if self._error_count > self._max_errors_per_minute:
            logger.error(
                f"⚠️ Circuit breaker triggered: Too many errors ({self._error_count}) in 60s. "
                f"Stopping error notifications to prevent infinite loop. Error: {error_type}: {error_message}"
            )
            return

        if not self.channel:
            logger.error("No RabbitMQ channel available for sending error notification")
            return

        try:
            notification = {
                "notification_id": f"error_{int(time.time() * 1000)}",
                "timestamp": time.time(),  # Unix timestamp (float)
                "recipient_agent_id": self.agent_name,
                "notification_type": "error_message",
                "source": "system",
                "payload": {
                    "error_type": error_type,
                    "error_message": error_message
                }
            }

            self.channel.basic_publish(
                exchange='',
                routing_key=self.config.queue_name,
                body=json.dumps(notification, cls=DateTimeEncoder)
            )
            logger.debug(f"Sent error notification: {error_type}")

        except Exception as e:
            # CRITICAL: Don't create error notification about error notifications!
            # Just log it to prevent infinite recursion
            logger.error(f"Failed to send error notification (will not recurse): {e}")

    def _execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> None:
        """
        Execute a single tool. Tool sends its own result notification.

        Args:
            tool_name: Name of tool to execute
            arguments: Tool arguments
        """
        if tool_name not in self.tools:
            # Tool not found - send error as tool_result notification
            from ..tools.base import BaseTool
            temp_tool = BaseTool.__new__(BaseTool)
            temp_tool.name = tool_name
            temp_tool.agent_name = self.agent_name
            temp_tool.rabbitmq_url = self.config.rabbitmq_url
            temp_tool.send_result_notification(
                status="FAILURE",
                error_message=f"Tool '{tool_name}' not found. Available tools: {list(self.tools.keys())}"
            )
            return

        tool = self.tools[tool_name]

        # Safety check: Verify tool is available in current context
        context = self._build_tool_availability_context()
        if not tool.is_available(context):
            logger.warning(f"Tool '{tool_name}' called but not available in current context (is_on_call={context.is_on_call})")
            tool.send_result_notification(
                status="FAILURE",
                error_message=f"Tool '{tool_name}' is not available in the current context. "
                             f"{'Use speak/hang_up tools during calls.' if not context.is_on_call else 'Use send_user_message when not on a call.'}"
            )
            return

        # Safety check: In fast_mode, only allow essential voice tools
        # Silently skip blocked tools to avoid creating notification loops
        if self._fast_mode:
            fast_mode_tools = {"speak", "hang_up"}
            if tool_name not in fast_mode_tools:
                logger.warning(f"⚡ Tool '{tool_name}' silently skipped in fast_mode - only {fast_mode_tools} allowed")
                # Don't send failure notification - just skip to avoid notification loops
                return

        # Validate arguments with detailed error info
        is_valid, validation_error = tool.validate_arguments(arguments)
        if not is_valid:
            # Send validation error as tool result
            tool.send_result_notification(
                status="FAILURE",
                error_message=f"Invalid tool arguments: {validation_error}"
            )
            return

        try:
            # Inject session_id for frontend notifications
            if self._last_session_id:
                arguments['session_id'] = self._last_session_id

            # Inject call_id for call-related tools
            if self._last_call_id:
                arguments['call_id'] = self._last_call_id
                logger.info(f"📞 Injecting call_id into tool arguments: {self._last_call_id}")

            # Inject fast_mode for voice tools
            if self._fast_mode:
                arguments['fast_mode'] = self._fast_mode
                logger.info(f"⚡ Injecting fast_mode into tool arguments: {self._fast_mode}")

            logger.debug(f"Executing tool: {tool_name} with args: {arguments}")
            # Tool executes and sends its own notification
            tool.execute(arguments)

            # Record successful tool execution
            if METRICS_AVAILABLE:
                agent_tool_executions.labels(agent_name=self.agent_name, tool_name=tool_name, status="success").inc()

        except Exception as e:
            logger.error(f"Tool execution failed: {e}")

            # Record failed tool execution
            if METRICS_AVAILABLE:
                agent_tool_executions.labels(agent_name=self.agent_name, tool_name=tool_name, status="error").inc()
                agent_errors_total.labels(agent_name=self.agent_name, error_type="tool_execution").inc()

            # Send execution error as tool result
            tool.send_result_notification(
                status="FAILURE",
                error_message=f"Tool execution error: {str(e)}"
            )

    def _process_notifications_cycle(self) -> None:
        """
        Enhanced processing cycle with sleep state handling.

        Handles normal processing and sleep interruption logic.
        """
        # Start timing for metrics
        cycle_start_time = time.time() if METRICS_AVAILABLE else None

        try:
            # 1. Check if we're sleeping and handle wake-on-notification
            status_result = self.db.get_agent_status(self.agent_name)
            if status_result.status == "SUCCESS":
                current_status = status_result.result.get("status")

                if current_status == AgentStatus.SLEEPING.value:
                    # Check for ANY notifications while sleeping
                    notifications = self._get_pending_notifications()

                    if notifications:
                        # WAKE UP! Cancel sleep timer and process notifications
                        logger.info(f"🌅 {self.agent_name} waking up due to notification")

                        # Cancel the sleep timer (import here to avoid circular dependency)
                        try:
                            from ..tools.standard.timer_tools import SleepTool
                            canceled_sleep_id = SleepTool.cancel_sleep(self.agent_name)
                            if canceled_sleep_id:
                                logger.info(f"Canceled sleep {canceled_sleep_id}")
                        except ImportError:
                            logger.warning("Could not import SleepTool to cancel sleep")

                        # Update status back to ACTIVE
                        self.db.update_agent_status(self.agent_name, AgentStatus.ACTIVE)

                        # Process the notifications normally (fall through to normal processing)
                        logger.info(f"Processing {len(notifications)} notifications that woke the agent")
                    else:
                        # Sleeping and no notifications - just return
                        return

            # 2. Get pending notifications (or use ones we already got during sleep check)
            if 'notifications' not in locals():
                notifications = self._get_pending_notifications()

            if not notifications:
                return  # Nothing to process

            # 3. Set state to thinking
            logger.info("🤔 Setting state to THINKING")
            result = self.db.set_processing_state(self.agent_name, ProcessingState.THINKING)
            if result.status != "SUCCESS":
                logger.error(f"Failed to set thinking state: {result.error_message}")
                return

            # 4. Get existing message history and agent state
            # Use configured retrieval limit from config
            retrieval_limit = self.config.message_history_retrieval_limit
            history_result = self.db.get_message_history(self.agent_name, limit=retrieval_limit)
            existing_messages = []
            if history_result.status == "SUCCESS":
                # Parse message history from API response
                history_data = history_result.result.get("result", {})
                existing_messages = history_data.get("messages", [])

            # Get turn number from agent_state for memory modules
            turn_number = 0
            state_result = self.db.get_agent_state(self.agent_name)
            if state_result.status == "SUCCESS":
                state_data = state_result.result.get("result", {})
                turn_number = state_data.get("total_messages", 0)

            # 5. Store system message if this is the first message ever
            if not existing_messages:
                system_msg = self.context_builder.build_system_message()
                # Wrap string content in dict for storage
                system_content_dict = {"text": system_msg["content"]}
                system_result = self.db.append_message(
                    self.agent_name,
                    MessageRole.SYSTEM.value,
                    system_content_dict
                )
                if system_result.status == "SUCCESS":
                    logger.info("Stored system message to database")
                else:
                    logger.warning(f"Failed to store system message: {system_result.error_message}")

            # 6. Store user message from notifications
            user_msg = self.context_builder.build_user_message_from_notifications(notifications)
            # Parse the JSON string content back to dict for storage
            user_content_dict = {"notifications": user_msg["content"]}
            user_result = self.db.append_message(
                self.agent_name,
                MessageRole.USER.value,
                user_content_dict
            )
            if user_result.status != "SUCCESS":
                logger.warning(f"Failed to store user message: {user_result.error_message}")

            # Track notification processing
            if METRICS_AVAILABLE:
                for notification in notifications:
                    notification_type = notification.get('notification_type', 'unknown')
                    agent_notifications_processed.labels(
                        agent_name=self.agent_name,
                        notification_type=notification_type
                    ).inc()

            # 6.5. Run Memory Retriever Module (before building context)
            retrieved_memories = []
            if self.memory_retriever and self.memory_retriever.should_run(turn_number):
                try:
                    logger.info(f"🔍 Running Memory Retriever (turn {turn_number})")
                    # Get user/assistant messages only for retriever
                    user_assistant_messages = [
                        msg for msg in existing_messages
                        if msg.get("role") in ["user", "assistant"]
                    ]
                    # Include current user message so retriever can see what user is asking
                    current_user_msg = {
                        "role": "user",
                        "content": user_msg["content"]
                    }
                    user_assistant_messages.append(current_user_msg)
                    retrieved_memories = self.memory_retriever.run(user_assistant_messages)
                    if retrieved_memories:
                        logger.info(f"📚 Retrieved {len(retrieved_memories)} memories")
                except Exception as e:
                    logger.error(f"Memory Retriever failed: {e}")

            # 6.8. Extract images from tool results (for view_image tool)
            self._extract_images_from_tool_results(notifications)

            # 6.9. Forward browser screenshots to frontend
            self._forward_browser_screenshots(notifications)

            # 7. Build conversation context
            conversation_messages = self.context_builder.build_conversation_messages(
                existing_messages=existing_messages,
                new_notifications=notifications
            )

            # 7.5. Store and inject retrieved memories into context (as last user message)
            if retrieved_memories:
                # Format memories as simplified objects (content, datetime, importance only)
                formatted_memories = []
                for mem in retrieved_memories:
                    created_at = mem.get('created_at')
                    # Convert datetime to ISO string if it's a datetime object
                    if hasattr(created_at, 'isoformat'):
                        created_at = created_at.isoformat()
                    formatted_memories.append({
                        "content": mem['content'],
                        "datetime": created_at,
                        "importance": mem['importance']
                    })

                # Create message structure with type indicator
                memory_content_dict = {
                    "type": "proactive_memories",
                    "memories": formatted_memories
                }

                # Store to database as user message
                memory_result = self.db.append_message(
                    self.agent_name,
                    MessageRole.USER.value,
                    memory_content_dict
                )
                if memory_result.status != "SUCCESS":
                    logger.warning(f"Failed to store memory message: {memory_result.error_message}")
                else:
                    logger.debug(f"Stored proactive memories message to transcript")

                # Inject into conversation context as last message (so LLM sees it at the end)
                memory_message = {
                    "role": MessageRole.USER.value,
                    "content": json.dumps(memory_content_dict)
                }
                conversation_messages.append(memory_message)

            # 7.8. Inject pending images into context (for view_image tool)
            conversation_messages = self._inject_pending_images(conversation_messages)

            # 8. Call LLM
            llm_response = self._call_llm(conversation_messages)

            # 8.1. Clear pending images after they've been sent to LLM
            self._pending_images = []

            # 9. Parse LLM response
            try:
                parsed_response = self.context_builder.parse_llm_response(llm_response)
                thought = parsed_response["thought"]
                tool_calls = parsed_response["tool_calls"]
                action_status = parsed_response.get("action_status")  # Optional field

                # DEBUG: Log the full parsed response to verify action_status
                logger.info(f"🔍 DEBUG: Parsed LLM response - thought: '{thought[:50]}...', action_status: '{action_status}', tool_calls: {len(tool_calls)}")

                # CRITICAL VALIDATION: Agents MUST use at least one tool per turn
                if not tool_calls or len(tool_calls) == 0:
                    error_msg = "Agent must use at least one tool per turn. Empty tool_calls array is not allowed."
                    logger.error(f"❌ VALIDATION ERROR: {error_msg}")
                    # Add the response to history as error
                    self.db.append_message(
                        self.agent_name,
                        MessageRole.ASSISTANT.value,
                        {"raw_response": llm_response, "validation_error": error_msg, "tool_calls": []}
                    )
                    # Send error notification
                    self._send_error_notification(
                        error_type="empty_tool_calls",
                        error_message=error_msg
                    )
                    self.db.set_processing_state(self.agent_name, ProcessingState.IDLE)

                    # This is a validation error - acknowledge notifications
                    self._handle_notification_results(notifications, error=ValueError(error_msg))
                    return

            except ValueError as e:
                logger.error(f"Invalid LLM response: {e}")
                # Add the raw LLM response to history as assistant message
                self.db.append_message(
                    self.agent_name,
                    MessageRole.ASSISTANT.value,
                    {"raw_response": llm_response, "parse_error": str(e)}
                )
                # Send error notification about invalid response
                self._send_error_notification(
                    error_type="llm_parse_error",
                    error_message=f"Failed to parse LLM response: {str(e)}"
                )
                self.db.set_processing_state(self.agent_name, ProcessingState.IDLE)

                # LLM parse errors are permanent - acknowledge notifications
                self._handle_notification_results(notifications, error=e)
                return

            # 9.5. Publish action_status notification (primary_agent only)
            if action_status and self.agent_name == "primary_agent":
                # Extract session_id from notifications
                session_id = self._extract_session_id_from_notifications(notifications)
                if session_id:
                    self._publish_action_status(session_id, action_status)

            # 10. Add assistant message to history
            assistant_msg = self.context_builder.build_assistant_message(thought, tool_calls, action_status)
            append_result = self.db.append_message(
                self.agent_name,
                MessageRole.ASSISTANT.value,
                json.loads(assistant_msg["content"])
            )
            if append_result.status != "SUCCESS":
                logger.error(f"Failed to append assistant message: {append_result.error_message}")

            # 11. Execute tools
            if tool_calls:
                logger.info(f"🔧 Executing {len(tool_calls)} tools")
                self.db.set_processing_state(self.agent_name, ProcessingState.EXECUTING_TOOLS)

                for tool_call in tool_calls:
                    # Execute tool - it will send its own result notification
                    self._execute_tool(
                        tool_call["tool_name"],
                        tool_call["arguments"]
                    )

                # Note: Tool results come back as notifications, not direct returns

            # 12. Return to idle state
            logger.info("✅ Processing complete, returning to IDLE")
            self.db.set_processing_state(self.agent_name, ProcessingState.IDLE)

            # 13. Acknowledge all successfully processed notifications
            self._handle_notification_results(notifications, error=None)

            # 13.5. Run Memory Creator Module (after processing complete)
            if self.memory_creator and self.memory_creator.should_run(turn_number):
                try:
                    logger.info(f"💾 Running Memory Creator (turn {turn_number})")
                    # Get user/assistant messages only for creator
                    user_assistant_messages = [
                        msg for msg in existing_messages
                        if msg.get("role") in ["user", "assistant"]
                    ]
                    self.memory_creator.run(user_assistant_messages)
                except Exception as e:
                    logger.error(f"Memory Creator failed: {e}")

            # 14. Record processing loop duration
            if METRICS_AVAILABLE and cycle_start_time:
                cycle_duration = time.time() - cycle_start_time
                agent_processing_loop_duration.labels(agent_name=self.agent_name).observe(cycle_duration)

        except Exception as e:
            logger.error(f"Error in processing cycle: {e}")

            # Track error in metrics
            if METRICS_AVAILABLE:
                agent_errors_total.labels(
                    agent_name=self.agent_name,
                    error_type="processing_cycle"
                ).inc()

            # Record processing loop duration even on error
            if METRICS_AVAILABLE and cycle_start_time:
                cycle_duration = time.time() - cycle_start_time
                agent_processing_loop_duration.labels(agent_name=self.agent_name).observe(cycle_duration)

            # Handle notifications based on error type
            self._handle_notification_results(
                notifications if 'notifications' in locals() else [],
                error=e
            )

            # Ensure we return to idle state even on error
            try:
                self.db.set_processing_state(self.agent_name, ProcessingState.IDLE)
            except:
                pass

    def _handle_notification_results(self, notifications: List[Dict[str, Any]], error: Optional[Exception] = None) -> None:
        """
        Smart acknowledgment/requeue logic based on error type and retry count.

        Args:
            notifications: List of notifications that were processed
            error: Exception that occurred during processing (None if successful)
        """
        if not notifications:
            return

        for notification in notifications:
            delivery_tag = notification.get('_delivery_tag')
            if not delivery_tag or not self.channel:
                continue

            notification_id = notification.get('notification_id', 'unknown')
            retry_count = notification.get('_retry_count', 0)

            try:
                if error is None:
                    # Success - acknowledge to remove from queue
                    self.channel.basic_ack(delivery_tag=delivery_tag)
                    logger.debug(f"✅ Acknowledged notification {notification_id}")

                elif self._is_transient_error(error) and retry_count < MAX_RETRIES:
                    # Transient error with retries remaining - requeue for retry
                    # Increment retry count in the notification
                    notification['_retry_count'] = retry_count + 1
                    self.channel.basic_nack(delivery_tag=delivery_tag, requeue=True)
                    logger.warning(f"🔄 Requeued notification {notification_id} (retry {retry_count + 1}/{MAX_RETRIES}) due to transient error: {error}")

                else:
                    # Permanent error OR max retries exceeded - acknowledge to remove
                    self.channel.basic_ack(delivery_tag=delivery_tag)
                    if retry_count >= MAX_RETRIES:
                        logger.error(f"💀 Dead letter: notification {notification_id} exceeded max retries ({MAX_RETRIES})")
                    else:
                        logger.error(f"💀 Dead letter: notification {notification_id} permanent error: {error}")

                    # Send error notification for investigation
                    self._send_error_notification(
                        error_type="notification_processing_failed",
                        error_message=f"Failed to process notification {notification_id}: {str(error)}"
                    )

            except Exception as ack_error:
                logger.error(f"Failed to acknowledge/nack notification {notification_id}: {ack_error}")

    def _is_transient_error(self, error: Exception) -> bool:
        """
        Determine if an error is transient (worth retrying) or permanent.

        Args:
            error: The exception that occurred

        Returns:
            True if error is transient and should be retried
        """
        # Check for specific transient error types
        if isinstance(error, TRANSIENT_ERRORS):
            return True

        # Check for specific error messages that indicate transient issues
        error_str = str(error).lower()
        transient_keywords = [
            'timeout',
            'connection',
            'network',
            'temporary',
            'unavailable',
            'service temporarily',
            'rate limit'
        ]

        if any(keyword in error_str for keyword in transient_keywords):
            return True

        # Check for HTTP-related timeout errors
        try:
            import httpx
            if isinstance(error, (httpx.TimeoutException, httpx.ConnectError)):
                return True
        except ImportError:
            pass

        # Default to permanent error (don't retry)
        return False

    def _should_check_notifications(self) -> bool:
        """
        Determine if it's time to check for notifications.

        Returns:
            True if should check now
        """
        current_time = time.time()

        # First check is always immediate
        if self.last_check_time == 0:
            self.last_check_time = current_time
            return True

        # Subsequent checks follow the configured interval
        time_since_last_check = current_time - self.last_check_time
        if time_since_last_check >= self.config.agent_check_interval_seconds:
            self.last_check_time = current_time
            return True

        return False

    def _check_and_recover_stale_state(self, current_state: str) -> str:
        """
        Check if the agent is stuck in a non-idle state and recover if stale.

        If the processing state hasn't been updated for more than STALE_STATE_TIMEOUT_SECONDS,
        force reset to IDLE to allow the agent to process new notifications.

        Args:
            current_state: The current processing state from the database

        Returns:
            The current state (possibly reset to IDLE if it was stale)
        """
        STALE_STATE_TIMEOUT_SECONDS = 300  # 5 minutes

        try:
            # Get full agent state which includes last_updated timestamp
            state_result = self.db.get_agent_state(self.agent_name)
            if state_result.status != "SUCCESS":
                logger.warning(f"Could not get agent state for stale check: {state_result.error_message}")
                return current_state

            agent_state = state_result.result.get("result", {})
            last_updated_str = agent_state.get("last_updated")

            if not last_updated_str:
                logger.warning("No last_updated timestamp in agent state")
                return current_state

            # Parse the timestamp
            from datetime import datetime, timezone

            # Handle various timestamp formats
            try:
                if last_updated_str.endswith('Z'):
                    last_updated = datetime.fromisoformat(last_updated_str.replace('Z', '+00:00'))
                elif '+' in last_updated_str or last_updated_str.endswith('00'):
                    last_updated = datetime.fromisoformat(last_updated_str)
                else:
                    # Assume UTC if no timezone
                    last_updated = datetime.fromisoformat(last_updated_str).replace(tzinfo=timezone.utc)
            except ValueError as e:
                logger.warning(f"Could not parse last_updated timestamp '{last_updated_str}': {e}")
                return current_state

            # Calculate time since last update
            now = datetime.now(timezone.utc)
            time_since_update = (now - last_updated).total_seconds()

            if time_since_update > STALE_STATE_TIMEOUT_SECONDS:
                logger.warning(
                    f"⚠️ STALE STATE DETECTED: Agent stuck in '{current_state}' for {time_since_update:.0f}s "
                    f"(threshold: {STALE_STATE_TIMEOUT_SECONDS}s). Force resetting to IDLE."
                )
                self.db.set_processing_state(self.agent_name, ProcessingState.IDLE)
                return ProcessingState.IDLE.value
            else:
                logger.debug(f"State '{current_state}' is not stale ({time_since_update:.0f}s old)")
                return current_state

        except Exception as e:
            logger.error(f"Error checking for stale state: {e}")
            return current_state

    def start(self) -> None:
        """
        Start the agent's main processing loop.

        This method blocks and runs the agent until stopped.
        """
        logger.info(f"Starting agent: {self.agent_display_name}")

        # Connect to RabbitMQ
        logger.info("🔌 Attempting to connect to RabbitMQ...")
        if not self._connect_rabbitmq():
            raise RuntimeError("Failed to connect to RabbitMQ")

        # Set agent status to active
        logger.info("📝 Setting agent status to ACTIVE...")
        self.db.update_agent_status(self.agent_name, AgentStatus.ACTIVE)
        logger.info("✅ Agent status set to ACTIVE")

        # Ensure processing state is idle
        logger.info("📝 Setting processing state to IDLE...")
        self.db.set_processing_state(self.agent_name, ProcessingState.IDLE)
        logger.info("✅ Processing state set to IDLE")

        self.running = True
        logger.info("✅ Agent running flag set to True")

        try:
            logger.info("🔄 Entering main polling loop")
            while self.running:
                # Check if we should look for notifications
                if self._should_check_notifications():
                    logger.debug("⏰ Time to check for notifications")
                    # Only process if we're in idle state and can acquire lock
                    if self._processing_lock.acquire(blocking=False):
                        try:
                            logger.debug("🔒 Acquired processing lock")
                            state_result = self.db.get_processing_state(self.agent_name)
                            if state_result.status == "SUCCESS":
                                current_state = state_result.result["result"]["processing_state"]
                                logger.debug(f"📊 Current processing state: {current_state}")

                                # Check for stale non-idle state and recover
                                if current_state != ProcessingState.IDLE.value:
                                    current_state = self._check_and_recover_stale_state(current_state)

                                if current_state == ProcessingState.IDLE.value:
                                    self._process_notifications_cycle()
                            else:
                                logger.warning(f"Failed to get processing state: {state_result.error_message}")
                        finally:
                            self._processing_lock.release()
                    else:
                        logger.debug("🔒 Could not acquire processing lock (busy)")

                # Small sleep to prevent busy waiting
                time.sleep(0.1)

        except KeyboardInterrupt:
            logger.info("Received interrupt signal")
        except Exception as e:
            logger.error(f"Agent error: {e}")
        finally:
            self.stop()

    def _extract_session_id_from_notifications(self, notifications: List[Dict[str, Any]]) -> Optional[str]:
        """
        Extract session_id and call_id from notification payloads.
        Also updates self._last_session_id and self._last_call_id for use in subsequent tool calls.

        Handles multiple notification types:
        - user_message: Regular messages and voice transcriptions
        - incoming_call: When a call is initiated
        - call_transferred: When a call is transferred to this agent
        - call_answered: When an outbound call is answered
        - tool_result: When answer_call tool succeeds

        Args:
            notifications: List of notification dictionaries

        Returns:
            The session_id if found, otherwise returns the last known session_id
        """
        # Call-related notification types that contain call_id
        call_notification_types = {"incoming_call", "call_transferred", "call_answered"}

        for notification in notifications:
            notification_type = notification.get("notification_type")
            payload = notification.get("payload", {})

            # Handle call-related notifications (incoming_call, call_transferred, call_answered)
            if notification_type in call_notification_types:
                call_id = payload.get("call_id")
                session_id = payload.get("session_id")
                if call_id:
                    logger.info(f"📞 Extracted call_id from {notification_type}: {call_id}")
                    self._last_call_id = call_id
                if session_id:
                    logger.debug(f"Extracted session_id from {notification_type}: {session_id}")
                    self._last_session_id = session_id
                    return session_id

            # Handle answer_call tool result - set call context when answer succeeds
            elif notification_type == "tool_result":
                tool_name = payload.get("tool_name")
                status = payload.get("status")
                result = payload.get("result", {})

                if tool_name == "answer_call" and status == "SUCCESS":
                    call_id = result.get("call_id")
                    if call_id:
                        logger.info(f"📞 Setting call context from answer_call success: {call_id}")
                        self._last_call_id = call_id

            # Handle user_message notifications (including voice transcriptions)
            elif notification_type == "user_message":
                session_id = payload.get("session_id")
                if session_id:
                    logger.debug(f"Extracted session_id: {session_id}")
                    self._last_session_id = session_id  # Store for future use

                # Extract call_id and is_call_mode - check both top level and voice_metadata
                # Voice transcriptions have these nested in voice_metadata
                voice_metadata = payload.get("voice_metadata", {})
                call_id = payload.get("call_id") or voice_metadata.get("call_id")
                is_call_mode = payload.get("is_call_mode", False) or voice_metadata.get("is_call_mode", False)

                # Extract fast_mode for low-latency calls
                fast_mode = payload.get("fast_mode", False) or voice_metadata.get("fast_mode", False)
                if fast_mode != self._fast_mode:
                    self._fast_mode = fast_mode
                    if fast_mode:
                        logger.info(f"⚡ Fast mode ENABLED - using low-latency model with limited tools")
                    else:
                        logger.info(f"⚡ Fast mode DISABLED - using standard model with full tools")

                if call_id:
                    logger.info(f"📞 Extracted call_id: {call_id} (is_call_mode: {is_call_mode}, fast_mode: {fast_mode})")
                    self._last_call_id = call_id
                elif not is_call_mode:
                    # If this is a regular message (not call), clear call context and fast_mode
                    self._last_call_id = None
                    self._fast_mode = False

                if session_id:
                    return session_id

        # If no session_id in current notifications, use the last known one
        if self._last_session_id:
            logger.debug(f"Using last known session_id: {self._last_session_id}")
        return self._last_session_id

    def _publish_action_status(self, session_id: str, action_description: str) -> None:
        """
        Publish action_status notification to the API Gateway.

        This sends the LLM-generated action description (e.g., "Searching weather data...")
        to the frontend via the notification system.

        Args:
            session_id: The conversation session ID
            action_description: User-facing action description from LLM
        """
        logger.info(f"🔍 DEBUG: _publish_action_status called - session_id: {session_id}, action: '{action_description}'")

        try:
            import requests

            # Get internal API key
            internal_api_key = None
            try:
                with open("/shared/internal_api_key", "r") as f:
                    internal_api_key = f.read().strip()
            except Exception as e:
                logger.warning(f"Could not load internal API key: {e}")
                return

            if not internal_api_key:
                logger.warning("Internal API key not found, skipping action_status notification")
                return

            # Get API Gateway URL from config
            api_gateway_url = self.config.api_gateway_url

            # Publish via API Gateway
            headers = {
                "Content-Type": "application/json",
                "X-Internal-Key": internal_api_key
            }

            data = {
                "agent_id": self.agent_name,
                "session_id": session_id,
                "action_description": action_description,
                "timestamp": datetime.utcnow().isoformat()
            }

            response = requests.post(
                f"{api_gateway_url}/api/v1/notifications/action-status",
                json=data,
                headers=headers,
                timeout=5
            )

            if response.status_code == 200:
                logger.info(f"✅ Published action_status: '{action_description}'")
            else:
                logger.warning(f"Failed to publish action_status: {response.status_code}")

        except Exception as e:
            # Don't crash if notification publishing fails
            logger.error(f"Error publishing action_status: {e}")

    def stop(self) -> None:
        """
        Gracefully stop the agent.
        """
        logger.info(f"Stopping agent: {self.agent_display_name}")

        self.running = False

        # Set status to off
        try:
            self.db.update_agent_status(self.agent_name, AgentStatus.OFF)
            self.db.set_processing_state(self.agent_name, ProcessingState.IDLE)
        except:
            pass

        # Close RabbitMQ connections
        self._disconnect_rabbitmq()

        logger.info("Agent stopped")


# Abstract method that agents must implement
class VOSAgentImplementation(VOSAgent):
    """
    Base class for specific agent implementations.

    Inherit from this class to create specific agents like WeatherAgent, EmailAgent, etc.
    Agents should define a TOOLS list containing all tool classes they need.
    """

    # List of tool classes this agent should have access to
    # Override this in your agent implementation
    TOOLS = []

    def __init__(self, config: AgentConfig, agent_description: str):
        super().__init__(config, agent_description)
        self._register_tools_from_list()

    def _register_tools_from_list(self) -> None:
        """
        Automatically register all tools from the TOOLS list.
        """
        for tool_class in self.TOOLS:
            try:
                tool_instance = tool_class()
                self.register_tool(tool_instance)
                logger.debug(f"Registered tool: {tool_instance.name}")
            except Exception as e:
                logger.error(f"Failed to register tool {tool_class.__name__}: {e}")