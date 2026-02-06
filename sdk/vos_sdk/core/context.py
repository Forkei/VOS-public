"""
Context building for VOS agents.

Handles converting notifications and tool results into the proper
message format for LLM consumption, following the VOS context flow pattern.
"""

import json
import logging
import hashlib
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class DateTimeEncoder(json.JSONEncoder):
    """Custom JSON encoder that handles datetime objects."""
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


class NotificationType(str, Enum):
    """VOS notification types"""
    USER_MESSAGE = "user_message"
    AGENT_MESSAGE = "agent_message"
    TOOL_RESULT = "tool_result"
    ALARM_TRIGGERED = "alarm_triggered"
    TIMER_EXPIRED = "timer_expired"
    SCHEDULED_EVENT = "scheduled_event"
    SLEEP_TIMER_EXPIRED = "sleep_timer_expired"
    VOS_EVENT_SUBSCRIPTION = "vos_event_subscription"


class MessageRole(str, Enum):
    """Message roles in agent conversation"""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class ContextBuilder:
    """
    Builds conversation context for LLM agents.

    Converts notifications and tool results into the standardized
    message format used by VOS agents.
    """

    def __init__(
        self,
        agent_name: str,
        agent_description: str = None,
        max_conversation_messages: int = 0,
        system_prompt_getter: Callable[[], str] = None,
        on_prompt_changed: Callable[[str], None] = None
    ):
        """
        Initialize ContextBuilder.

        Args:
            agent_name: Name of the agent
            agent_description: Static system prompt (deprecated, use system_prompt_getter)
            max_conversation_messages: Max messages to keep in context (0 = unlimited)
            system_prompt_getter: Callable that returns the current system prompt.
                                  If provided, this is called on every build_system_message()
                                  for live prompt updates.
            on_prompt_changed: Callback called when the prompt changes (for transcript sync).
                              Receives the new prompt content as argument.
        """
        self.agent_name = agent_name
        self.agent_description = agent_description  # Kept for backward compatibility
        self.max_conversation_messages = max_conversation_messages
        self._system_prompt_getter = system_prompt_getter
        self._on_prompt_changed = on_prompt_changed
        self._last_prompt_hash: Optional[str] = None
        logger.info(f"ContextBuilder initialized for {agent_name} with max_conversation_messages={max_conversation_messages}, live_prompt={system_prompt_getter is not None}")

    def build_system_message(self) -> Dict[str, Any]:
        """
        Create the system message that defines the agent's role.

        If a system_prompt_getter was provided, it is called to get the latest
        prompt from disk, enabling live prompt editing without restart.

        Also detects prompt changes via hash comparison and calls on_prompt_changed
        callback to sync the transcript database.

        Returns:
            System message in the standard format
        """
        # Use live getter if available, otherwise fall back to static description
        if self._system_prompt_getter:
            content = self._system_prompt_getter()
        else:
            content = self.agent_description

        # Check if prompt changed and notify for transcript sync
        if content and self._on_prompt_changed:
            current_hash = hashlib.md5(content.encode()).hexdigest()
            # Trigger callback on FIRST call (to sync DB with disk on startup)
            # OR when hash changes (live edit detected)
            if self._last_prompt_hash is None:
                # First call - always sync to ensure DB matches disk after restart
                logger.info(f"System prompt initial sync (hash: {current_hash[:8]}...)")
                try:
                    self._on_prompt_changed(content)
                except Exception as e:
                    logger.error(f"Failed to sync prompt on startup: {e}")
            elif current_hash != self._last_prompt_hash:
                # Hash changed - live edit detected
                logger.info(f"System prompt changed (hash: {self._last_prompt_hash[:8]}... -> {current_hash[:8]}...)")
                try:
                    self._on_prompt_changed(content)
                except Exception as e:
                    logger.error(f"Failed to notify prompt change: {e}")
            self._last_prompt_hash = current_hash

        return {
            "role": MessageRole.SYSTEM.value,
            "content": content
        }

    def format_notifications_for_llm(self, notifications: List[Dict[str, Any]]) -> str:
        """
        Format a list of notifications as JSON string for the LLM.

        Based on the example context flow, notifications are passed as
        a JSON array string in the user message content.

        Args:
            notifications: List of notification objects

        Returns:
            JSON string representation of notifications
        """
        formatted_notifications = []

        for notification in notifications:
            # Extract the core notification data
            formatted_notification = {
                "notification_type": notification.get("notification_type"),
                "source": notification.get("source"),
                "payload": notification.get("payload", {})
            }

            # Add timestamp if present
            if "timestamp" in notification:
                formatted_notification["timestamp"] = notification["timestamp"]

            formatted_notifications.append(formatted_notification)

        # Return as JSON string (as shown in the example)
        # Use DateTimeEncoder to handle any datetime objects
        return json.dumps(formatted_notifications, cls=DateTimeEncoder)

    def format_tool_results_for_llm(self, tool_results: List[Dict[str, Any]]) -> str:
        """
        Format tool execution results as JSON string for the LLM.

        Args:
            tool_results: List of tool result objects

        Returns:
            JSON string representation of tool results
        """
        formatted_results = []

        for result in tool_results:
            # Ensure proper format with all required fields
            formatted_result = {
                "tool_name": result.get("tool_name", "unknown_tool"),
                "status": result.get("status", "FAILURE"),
                "result": result.get("result"),
                "error_message": result.get("error_message")
            }

            formatted_results.append(formatted_result)

        # Use DateTimeEncoder to handle any datetime objects in results
        return json.dumps(formatted_results, cls=DateTimeEncoder)

    def build_user_message_from_notifications(self, notifications: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Create a user message containing formatted notifications.

        Args:
            notifications: List of notification objects

        Returns:
            User message in the standard format
        """
        content = self.format_notifications_for_llm(notifications)

        return {
            "role": MessageRole.USER.value,
            "content": content
        }

    def build_user_message_with_images(
        self,
        notifications: List[Dict[str, Any]],
        images: List[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Create a user message with notifications and optional images for vision.

        Args:
            notifications: List of notification objects
            images: List of image dicts with content_type and base64_data

        Returns:
            User message with structured content including images

        Example images format:
            [
                {"content_type": "image/png", "base64_data": "..."},
                {"content_type": "image/jpeg", "base64_data": "..."}
            ]
        """
        text_content = self.format_notifications_for_llm(notifications)

        # If no images, return standard message
        if not images:
            return {
                "role": MessageRole.USER.value,
                "content": text_content
            }

        # Build structured content with images
        content = {
            "text": text_content,
            "images": images
        }

        return {
            "role": MessageRole.USER.value,
            "content": content
        }

    def extract_images_from_notifications(
        self,
        notifications: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Extract image data from notification payloads.

        Looks for images in:
        - notification.payload.images (list of image dicts)
        - notification.payload.image (single image dict)

        Args:
            notifications: List of notification objects

        Returns:
            List of image dicts with content_type and base64_data
        """
        images = []

        for notification in notifications:
            payload = notification.get("payload", {})

            # Check for images list
            if "images" in payload and isinstance(payload["images"], list):
                for img in payload["images"]:
                    if isinstance(img, dict) and "base64_data" in img:
                        images.append({
                            "content_type": img.get("content_type", "image/png"),
                            "base64_data": img["base64_data"],
                            "attachment_id": img.get("attachment_id")
                        })

            # Check for single image
            elif "image" in payload and isinstance(payload["image"], dict):
                img = payload["image"]
                if "base64_data" in img:
                    images.append({
                        "content_type": img.get("content_type", "image/png"),
                        "base64_data": img["base64_data"],
                        "attachment_id": img.get("attachment_id")
                    })

        return images

    def build_user_message_from_tool_results(self, tool_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Create a user message containing tool execution results.

        Args:
            tool_results: List of tool result objects

        Returns:
            User message in the standard format
        """
        content = self.format_tool_results_for_llm(tool_results)

        return {
            "role": MessageRole.USER.value,
            "content": content
        }

    def parse_llm_response(self, llm_response: str) -> Dict[str, Any]:
        """
        Parse and validate LLM response JSON.

        Args:
            llm_response: Raw response string from LLM

        Returns:
            Parsed response with thought and tool_calls

        Raises:
            ValueError: If response is invalid JSON or missing required fields
        """
        try:
            # Handle markdown code blocks that wrap JSON
            json_content = llm_response.strip()

            # Extract JSON from markdown code blocks if present
            if json_content.startswith('```'):
                # Find the JSON content between code blocks
                lines = json_content.split('\n')
                json_lines = []
                in_code_block = False

                for line in lines:
                    if line.strip().startswith('```'):
                        if in_code_block:
                            break  # End of code block
                        else:
                            in_code_block = True  # Start of code block
                    elif in_code_block:
                        json_lines.append(line)

                json_content = '\n'.join(json_lines).strip()

            response_data = json.loads(json_content)
        except json.JSONDecodeError as e:
            # Include the raw response in the error so we can see what actually happened
            error_msg = f"JSON parse error: {e}\n"
            error_msg += "=" * 50 + "\n"
            error_msg += "RAW LLM RESPONSE:\n"
            error_msg += "=" * 50 + "\n"
            error_msg += llm_response[:2000]  # Show first 2000 chars
            if len(llm_response) > 2000:
                error_msg += f"\n... (truncated, {len(llm_response)} total chars)"
            raise ValueError(error_msg)

        # Handle single-element array wrapper (LLM sometimes wraps response in array)
        if isinstance(response_data, list):
            if len(response_data) == 1 and isinstance(response_data[0], dict):
                logger.warning("LLM returned single-element array, unwrapping to object")
                response_data = response_data[0]
            else:
                error_msg = f"LLM response must be a JSON object, got {type(response_data).__name__}\n"
                error_msg += "=" * 50 + "\n"
                error_msg += "PARSED RESPONSE:\n"
                error_msg += "=" * 50 + "\n"
                error_msg += json.dumps(response_data, indent=2)[:1000]
                raise ValueError(error_msg)

        # Ensure response is a dict
        if not isinstance(response_data, dict):
            error_msg = f"LLM response must be a JSON object, got {type(response_data).__name__}\n"
            error_msg += "=" * 50 + "\n"
            error_msg += "PARSED RESPONSE:\n"
            error_msg += "=" * 50 + "\n"
            error_msg += json.dumps(response_data, indent=2)[:1000]
            raise ValueError(error_msg)

        # Map "reasoning" to "thought" for backward compatibility with template
        if "reasoning" in response_data and "thought" not in response_data:
            logger.debug("Mapping 'reasoning' field to 'thought' for compatibility")
            response_data["thought"] = response_data.pop("reasoning")

        # Validate required fields
        if "thought" not in response_data:
            # Show what fields we DID get
            available_fields = list(response_data.keys())
            error_msg = f"LLM response missing required 'thought' field\n"
            error_msg += f"Available fields: {available_fields}\n"
            error_msg += "=" * 50 + "\n"
            error_msg += "PARSED RESPONSE:\n"
            error_msg += "=" * 50 + "\n"
            error_msg += json.dumps(response_data, indent=2)[:1000]
            raise ValueError(error_msg)

        if "tool_calls" not in response_data:
            # Show what fields we DID get
            available_fields = list(response_data.keys())
            error_msg = f"LLM response missing required 'tool_calls' field\n"
            error_msg += f"Available fields: {available_fields}\n"
            error_msg += "=" * 50 + "\n"
            error_msg += "PARSED RESPONSE:\n"
            error_msg += "=" * 50 + "\n"
            error_msg += json.dumps(response_data, indent=2)[:1000]
            raise ValueError(error_msg)

        if not isinstance(response_data["tool_calls"], list):
            raise ValueError("'tool_calls' must be a list")

        # Validate each tool call
        for i, tool_call in enumerate(response_data["tool_calls"]):
            if not isinstance(tool_call, dict):
                raise ValueError(f"tool_calls[{i}] must be an object")

            if "tool_name" not in tool_call:
                raise ValueError(f"tool_calls[{i}] missing 'tool_name'")

            if "arguments" not in tool_call:
                raise ValueError(f"tool_calls[{i}] missing 'arguments'")

            if not isinstance(tool_call["arguments"], dict):
                raise ValueError(f"tool_calls[{i}] 'arguments' must be an object")

        return response_data

    def build_assistant_message(self, thought: str, tool_calls: List[Dict[str, Any]], action_status: Optional[str] = None) -> Dict[str, Any]:
        """
        Create an assistant message with thought, tool calls, and optional action status.

        Args:
            thought: The agent's reasoning
            tool_calls: List of tool calls to execute
            action_status: Optional user-facing status description

        Returns:
            Assistant message in the standard format
        """
        response_data = {
            "thought": thought,
            "tool_calls": tool_calls
        }

        # Include action_status if provided
        if action_status:
            response_data["action_status"] = action_status

        return {
            "role": MessageRole.ASSISTANT.value,
            "content": json.dumps(response_data, cls=DateTimeEncoder)
        }

    def build_conversation_messages(
        self,
        existing_messages: List[Dict[str, Any]],
        new_notifications: Optional[List[Dict[str, Any]]] = None,
        new_tool_results: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        """
        Build complete conversation context for LLM.

        Args:
            existing_messages: Previous conversation history
            new_notifications: New notifications to process (optional)
            new_tool_results: New tool results to process (optional)

        Returns:
            Complete message list ready for LLM
        """
        messages = []

        # ALWAYS build system message from disk to check for changes
        # This triggers hash comparison and on_prompt_changed callback if needed
        current_system_msg = self.build_system_message()

        # Use the current system message (from disk), not the one from database
        messages.append(current_system_msg)

        # Add existing conversation history, skipping the old system message if present
        if existing_messages:
            if existing_messages[0].get("role") == "system":
                # Skip the old system message, use the fresh one from disk
                messages.extend(existing_messages[1:])
            else:
                messages.extend(existing_messages)

        # Add new notifications if any
        if new_notifications:
            messages.append(self.build_user_message_from_notifications(new_notifications))

        # Add new tool results if any
        if new_tool_results:
            messages.append(self.build_user_message_from_tool_results(new_tool_results))

        # Apply message trimming if limit is set
        if self.max_conversation_messages > 0 and len(messages) > self.max_conversation_messages:
            logger.info(f"Trimming messages from {len(messages)} to {self.max_conversation_messages}")
            messages = self._trim_messages(messages, self.max_conversation_messages)

        logger.debug(f"Built conversation context with {len(messages)} messages")
        return messages

    def _trim_messages(self, messages: List[Dict[str, Any]], max_messages: int) -> List[Dict[str, Any]]:
        """
        Trim messages to stay within the limit while preserving context.
        Ensures the first message after system prompt is always a user message.

        Args:
            messages: The full list of messages
            max_messages: Maximum number of messages to keep

        Returns:
            Trimmed message list
        """
        if len(messages) <= max_messages:
            return messages

        # First message is always system prompt
        system_message = messages[0]
        non_system_messages = messages[1:]

        # Calculate how many non-system messages we can keep
        available_slots = max_messages - 1  # -1 for the system message

        if available_slots <= 0:
            # Only keep system message if limit is 1
            return [system_message]

        # Calculate how many messages to remove
        messages_to_remove = len(non_system_messages) - available_slots

        if messages_to_remove <= 0:
            return messages

        # Remove messages from the beginning until:
        # 1. We've removed enough messages
        # 2. The first remaining message is a "user" message
        removed_count = 0
        while removed_count < messages_to_remove or (non_system_messages and non_system_messages[0].get("role") != "user"):
            if not non_system_messages:
                break

            # Remove the first message
            removed_msg = non_system_messages.pop(0)
            removed_count += 1

            logger.debug(f"Trimmed message {removed_count}: role={removed_msg.get('role')}")

        logger.info(f"Trimmed {removed_count} old messages to stay within limit of {max_messages}")

        # Rebuild the message list with system message first
        return [system_message] + non_system_messages


# Convenience functions
def create_context_builder(agent_name: str, agent_description: str) -> ContextBuilder:
    """Create a ContextBuilder instance."""
    return ContextBuilder(agent_name, agent_description)


def format_notifications_as_user_message(notifications: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Quick helper to format notifications as a user message.

    Args:
        notifications: List of notification objects

    Returns:
        User message containing the notifications
    """
    builder = ContextBuilder("agent", "description")  # Dummy values for formatting
    return builder.build_user_message_from_notifications(notifications)


def format_tool_results_as_user_message(tool_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Quick helper to format tool results as a user message.

    Args:
        tool_results: List of tool result objects

    Returns:
        User message containing the tool results
    """
    builder = ContextBuilder("agent", "description")  # Dummy values for formatting
    return builder.build_user_message_from_tool_results(tool_results)