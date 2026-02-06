"""
Sleep and shutdown tools for VOS agents.

These tools manage agent sleep states and shutdown with proper interrupt handling.
"""

import os
import json
import logging
import time
import uuid
import threading
from typing import Dict, Any, Optional
from datetime import datetime, timedelta

from vos_sdk import BaseTool
from vos_sdk.core.database import DatabaseClient, AgentStatus, AgentConfig
import pika

logger = logging.getLogger(__name__)


class SleepTool(BaseTool):
    """
    Puts the agent into a true sleep state.

    The agent will be marked as SLEEPING in the database and will wake up when:
    1. The sleep duration expires (sends wake notification)
    2. ANY other notification arrives (cancels sleep, no wake notification)

    IMPORTANT: This tool does NOT send a success notification to avoid
    immediately waking the agent with its own tool result.
    """

    # Class-level registry of active sleep timers
    _active_sleep_timers = {}
    _lock = threading.Lock()

    def __init__(self):
        super().__init__(
            name="sleep",
            description="Puts the agent into sleep state until duration expires or notification arrives"
        )
        self.db_client = None

    def setup(self, agent_name: str, rabbitmq_url: str) -> None:
        """Setup tool with agent configuration."""
        super().setup(agent_name, rabbitmq_url)
        # Initialize database client when we have the config
        # Use from_env to properly create the config
        config = AgentConfig.from_env(agent_name, agent_name)
        self.db_client = DatabaseClient(config)

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate sleep arguments."""
        if "duration" not in arguments:
            return False, "Missing required argument: 'duration' (in seconds)"

        try:
            duration = float(arguments["duration"])
            if duration <= 0:
                return False, "'duration' must be positive"

            if duration > 86400:  # 24 hours
                return False, "'duration' cannot exceed 24 hours (86400 seconds)"

        except (TypeError, ValueError):
            return False, "'duration' must be a number"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "sleep",
            "description": "Puts the agent into sleep state until duration expires or notification arrives",
            "parameters": [
                {
                    "name": "duration",
                    "type": "float",
                    "description": "Sleep duration in seconds (max 86400)",
                    "required": True
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Put agent to sleep without sending a success notification.

        Args:
            arguments: Must contain 'duration' in seconds
        """
        duration = float(arguments["duration"])
        sleep_id = f"sleep_{uuid.uuid4().hex[:8]}"

        try:
            # 1. Update agent status to SLEEPING in database
            logger.debug(f"Setting {self.agent_name} status to SLEEPING")

            if self.db_client:
                result = self.db_client.update_agent_status(self.agent_name, AgentStatus.SLEEPING)
                if result.status != "SUCCESS":
                    logger.error(f"Failed to set SLEEPING status: {result.error_message}")
                    # Continue anyway - sleep still works without DB update

            # 2. Cancel any existing sleep timer for this agent
            self.cancel_sleep(self.agent_name)

            # 3. Create new cancelable timer
            timer_event = threading.Event()

            with self._lock:
                self._active_sleep_timers[self.agent_name] = (sleep_id, timer_event, time.time())

            # 4. Schedule wake notification (cancelable)
            def send_wake_if_not_canceled():
                """Background thread that waits and sends wake notification if not canceled."""
                logger.debug(f"Sleep timer {sleep_id} started for {duration}s")

                # Wait for duration OR until event is set (canceled)
                was_canceled = timer_event.wait(timeout=duration)

                if not was_canceled:
                    # Timer completed without interruption - send wake notification
                    logger.debug(f"Sleep {sleep_id} completed - sending wake notification")

                    wake_notification = {
                        "notification_id": f"wake_{sleep_id}",
                        "timestamp": time.time(),
                        "recipient_agent_id": self.agent_name,
                        "notification_type": "system_alert",
                        "source": "system",
                        "payload": {
                            "alert_type": "WAKE",
                            "alert_name": "sleep_wake",
                            "message": f"Sleep completed after {duration} seconds",
                            "sleep_id": sleep_id,
                            "duration": duration
                        }
                    }

                    # Send wake notification to agent's queue
                    try:
                        connection = pika.BlockingConnection(pika.URLParameters(self.rabbitmq_url))
                        channel = connection.channel()
                        channel.queue_declare(queue=self.queue_name, durable=True)

                        channel.basic_publish(
                            exchange='',
                            routing_key=self.queue_name,
                            body=json.dumps(wake_notification)
                        )

                        channel.close()
                        connection.close()
                        logger.debug(f"Wake notification sent for sleep {sleep_id}")

                    except Exception as e:
                        logger.error(f"Failed to send wake notification: {e}")
                else:
                    logger.debug(f"Sleep {sleep_id} was canceled - no wake notification sent")

                # Clean up timer registry
                with self._lock:
                    if self.agent_name in self._active_sleep_timers:
                        stored_id, _, _ = self._active_sleep_timers[self.agent_name]
                        if stored_id == sleep_id:  # Only remove if it's our timer
                            del self._active_sleep_timers[self.agent_name]

            # Start timer in background thread
            timer_thread = threading.Thread(
                target=send_wake_if_not_canceled,
                daemon=True,
                name=f"sleep-{sleep_id}"
            )
            timer_thread.start()

            # 5. DO NOT send success notification - that would wake the agent!
            # The agent is now sleeping and will wake on:
            # - Timer expiration (wake notification)
            # - Any other notification (cancels timer)

            logger.info(f"💤 Agent {self.agent_name} entering sleep for {duration}s (id: {sleep_id})")

        except Exception as e:
            logger.error(f"Failed to initiate sleep: {e}")
            # Even on error, don't send notification - let agent continue normally

    @classmethod
    def cancel_sleep(cls, agent_name: str) -> Optional[str]:
        """
        Cancel active sleep timer for an agent.

        Returns:
            Sleep ID that was canceled, or None if no active sleep
        """
        with cls._lock:
            if agent_name in cls._active_sleep_timers:
                sleep_id, timer_event, start_time = cls._active_sleep_timers[agent_name]
                timer_event.set()  # Signal the timer thread to stop

                elapsed = time.time() - start_time
                logger.debug(f"Canceled sleep {sleep_id} for {agent_name} after {elapsed:.1f}s")

                # Remove from registry
                del cls._active_sleep_timers[agent_name]
                return sleep_id

        return None

    @classmethod
    def is_agent_sleeping(cls, agent_name: str) -> bool:
        """Check if an agent has an active sleep timer."""
        with cls._lock:
            return agent_name in cls._active_sleep_timers

    @classmethod
    def get_sleep_info(cls, agent_name: str) -> Optional[Dict[str, Any]]:
        """Get information about an agent's active sleep."""
        with cls._lock:
            if agent_name in cls._active_sleep_timers:
                sleep_id, _, start_time = cls._active_sleep_timers[agent_name]
                return {
                    "sleep_id": sleep_id,
                    "start_time": start_time,
                    "elapsed": time.time() - start_time
                }
        return None


class ShutdownTool(BaseTool):
    """
    Gracefully shuts down the agent.
    """

    def __init__(self):
        super().__init__(
            name="shutdown",
            description="Gracefully shuts down the agent"
        )
        self.db_client = None

    def setup(self, agent_name: str, rabbitmq_url: str) -> None:
        """Setup tool with agent configuration."""
        super().setup(agent_name, rabbitmq_url)
        # Initialize database client when we have the config
        config = AgentConfig.from_env(agent_name, agent_name)
        self.db_client = DatabaseClient(config)

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate shutdown arguments."""
        # No arguments required for shutdown
        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "shutdown",
            "description": "Gracefully shuts down the agent",
            "parameters": []
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Shutdown the agent gracefully.
        Updates agent status to OFF and puts agent into indefinite sleep until woken up.
        """
        try:
            logger.info(f"🛑 Agent {self.agent_name} initiating shutdown (entering OFF state)")

            # Update agent status to OFF in database
            if self.db_client:
                from vos_sdk.core.database import AgentStatus
                result = self.db_client.update_agent_status(self.agent_name, AgentStatus.OFF)
                if result.status == "SUCCESS":
                    logger.debug(f"Agent {self.agent_name} status set to OFF")
                else:
                    logger.error(f"Failed to set OFF status: {result.error_message}")

            # DO NOT send success notification - agent is now OFF
            logger.debug(f"Agent {self.agent_name} successfully shutdown - entering indefinite sleep")

        except Exception as e:
            logger.error(f"Failed to shutdown: {e}")
            # Do not send failure notification either - agent should go OFF regardless


# Export timer and sleep tools
TIMER_TOOLS = [
    SleepTool,
    ShutdownTool
]