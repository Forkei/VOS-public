"""
Base tool class for VOS agents.

All tools must inherit from BaseTool and implement the execute method.
Tools are responsible for sending their results back as notifications to the agent's queue.
"""

import json
import logging
import time
from typing import Dict, Any, Optional
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

import pika


@dataclass
class ToolAvailabilityContext:
    """
    Context information for determining tool availability.

    This is passed to tools during system prompt generation and tool execution
    to allow context-aware tool filtering (e.g., call-only tools vs chat-only tools).
    """
    session_id: Optional[str] = None
    call_id: Optional[str] = None
    is_on_call: bool = False

    @classmethod
    def from_agent_state(cls, session_id: Optional[str] = None,
                         call_id: Optional[str] = None) -> "ToolAvailabilityContext":
        """Create context from agent state."""
        return cls(
            session_id=session_id,
            call_id=call_id,
            is_on_call=call_id is not None
        )


class DateTimeEncoder(json.JSONEncoder):
    """Custom JSON encoder that handles datetime objects."""
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)

logger = logging.getLogger(__name__)


class BaseTool(ABC):
    """
    Abstract base class for agent tools.

    All tools must inherit from this class and implement the execute method.
    Tools send their results back to the agent via RabbitMQ notifications.
    """

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.agent_name = None  # Set when registered with agent
        self.rabbitmq_url = None  # Set when registered with agent

    def setup(self, agent_name: str, rabbitmq_url: str) -> None:
        """
        Setup tool with agent configuration.
        Called automatically when tool is registered with an agent.

        Args:
            agent_name: Name of the agent using this tool
            rabbitmq_url: RabbitMQ connection URL
        """
        self.agent_name = agent_name
        self.queue_name = f"{agent_name}_queue"
        self.rabbitmq_url = rabbitmq_url

    def send_result_notification(self, status: str, result: Optional[Dict[str, Any]] = None,
                                error_message: Optional[str] = None) -> None:
        """
        Send tool result as notification to agent's queue.

        Args:
            status: SUCCESS or FAILURE
            result: Tool result data (if successful)
            error_message: Error description (if failed)
        """
        if not self.agent_name or not self.rabbitmq_url:
            logger.error(f"Tool {self.name} not properly setup - missing agent configuration")
            return

        notification = {
            "notification_id": f"tool_{self.name}_{int(time.time() * 1000)}",
            "timestamp": time.time(),
            "recipient_agent_id": self.agent_name,
            "notification_type": "tool_result",
            "source": f"tool_{self.name}",
            "payload": {
                "tool_name": self.name,
                "status": status,
                "result": result,
                "error_message": error_message
            }
        }

        # Create temporary connection to send notification
        connection = None
        channel = None
        try:
            connection = pika.BlockingConnection(pika.URLParameters(self.rabbitmq_url))
            channel = connection.channel()

            # Ensure queue exists
            channel.queue_declare(queue=self.queue_name, durable=True)

            # Send notification
            channel.basic_publish(
                exchange='',
                routing_key=self.queue_name,
                body=json.dumps(notification, cls=DateTimeEncoder)
            )

            logger.debug(f"Tool {self.name} sent result notification: {status}")

        except Exception as e:
            logger.error(f"Tool {self.name} failed to send notification: {e}")
        finally:
            # Clean up connection
            if channel and not channel.is_closed:
                channel.close()
            if connection and not connection.is_closed:
                connection.close()

    @abstractmethod
    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Execute the tool with given arguments.

        Tool MUST send result notification via send_result_notification().
        Do NOT return anything - results go through notifications.

        Args:
            arguments: Tool parameters
        """
        pass

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """
        Validate tool arguments before execution.

        Override this method to add custom validation with detailed error messages.

        Args:
            arguments: Tool parameters to validate

        Returns:
            Tuple of (is_valid, error_message)
            - is_valid: True if arguments are valid, False otherwise
            - error_message: Detailed error message if invalid, None if valid
        """
        return True, None

    def is_available(self, context: ToolAvailabilityContext) -> bool:
        """
        Check if this tool is available in the given context.

        Override this method to implement context-aware tool filtering.
        For example, call tools (speak, hang_up) should only be available
        during active calls, while messaging tools (send_user_message) should
        only be available when NOT on a call.

        Args:
            context: Context information including session_id, call_id, is_on_call

        Returns:
            True if the tool is available, False otherwise
        """
        return True  # Default: tool is always available

    @abstractmethod
    def get_tool_info(self) -> Dict[str, Any]:
        """
        Get tool information for system prompt generation.

        Each tool must implement this method to provide its metadata.

        Returns:
            Dictionary containing:
            - command: How the agent will call this tool
            - description: Short description of what the tool does
            - parameters: List of parameter definitions, each containing:
                - name: Parameter name
                - type: Parameter type (str, int, list, etc.)
                - description: Short description of the parameter
                - required: Whether the parameter is required (True/False)

        Example:
            {
                "command": "get_weather",
                "description": "Fetches current weather data for a location",
                "parameters": [
                    {
                        "name": "location",
                        "type": "str",
                        "description": "City or location name",
                        "required": True
                    },
                    {
                        "name": "units",
                        "type": "str",
                        "description": "Temperature units (metric/imperial)",
                        "required": False
                    }
                ]
            }
        """
        pass