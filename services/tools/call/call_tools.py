"""
Call Tools for Voice Calls

Tools for agents to interact with voice calls:
- SpeakTool: Say something to the caller (generates TTS)
- AnswerCallTool: Answer an incoming call
- HangUpTool: End the current call
- TransferCallTool: Hand off the call to another agent
- RecallPhoneTool: Take back the phone from another agent
- CallUserTool: Initiate an outbound call to the user (Primary Agent only)
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
from concurrent.futures import ThreadPoolExecutor

from vos_sdk import BaseTool
from vos_sdk.tools.base import ToolAvailabilityContext
import aio_pika

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

    # There's a running loop - check if we're in the loop's thread
    # To avoid deadlock, always run in a separate thread
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


class SpeakTool(BaseTool):
    """
    Say something to the caller during a voice call.

    This tool generates text-to-speech audio and sends it to the user.
    Use this tool instead of send_user_message when on an active call.
    """

    def __init__(self):
        super().__init__(
            name="speak",
            description=(
                "Say something to the caller during a voice call. "
                "Generates speech from text and plays it to the user. "
                "Use this for ALL responses during a call - not send_user_message."
            )
        )
        self.api_gateway_url = os.environ.get("API_GATEWAY_URL", "http://localhost:8000")
        self.internal_api_key = self._load_internal_api_key()

    def _load_internal_api_key(self) -> Optional[str]:
        """Load internal API key from shared volume."""
        try:
            if not os.path.exists("/shared/internal_api_key"):
                 logger.warning("Internal API key file not found at /shared/internal_api_key")
                 return None
                 
            with open("/shared/internal_api_key", "r") as f:
                key = f.read().strip()
                if key:
                    logger.info(f"SpeakTool loaded internal API key")
                    return key
                else:
                    logger.warning("Internal API key file is empty")
        except Exception as e:
            logger.warning(f"Could not load internal API key: {e}")
        return None

    def is_available(self, context: ToolAvailabilityContext) -> bool:
        """Speak tool is only available during active calls."""
        return context.is_on_call

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate speak arguments."""
        if "text" not in arguments:
            return False, "Missing required argument: 'text'"

        if not isinstance(arguments["text"], str):
            return False, "'text' must be a string"

        if not arguments["text"].strip():
            return False, "'text' cannot be empty"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "speak",
            "description": "Say something to the caller during a voice call (generates speech)",
            "parameters": [
                {
                    "name": "text",
                    "type": "str",
                    "description": "What to say to the caller",
                    "required": True
                },
                {
                    "name": "emotion",
                    "type": "str",
                    "description": "Emotional tone: neutral, happy, sad, excited, calm (default: neutral)",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Speak to the caller via TTS.

        Args:
            arguments: Must contain 'text', optionally 'emotion'
        """
        text = arguments["text"]
        emotion = arguments.get("emotion", "neutral")
        session_id = arguments.get("session_id")
        call_id = arguments.get("call_id")
        fast_mode = arguments.get("fast_mode", False)

        try:
            # Send to voice gateway for TTS generation
            notification = {
                "notification_id": str(uuid.uuid4()),
                "timestamp": datetime.utcnow().isoformat(),
                "recipient_agent_id": "voice_gateway",
                "notification_type": "call_speak",
                "source": f"agent_{self.agent_name}",
                "payload": {
                    "sender_agent_id": self.agent_name,
                    "content": text,
                    "emotion": emotion,
                    "session_id": session_id,
                    "call_id": call_id,
                    "is_call_speech": True,
                    "fast_mode": fast_mode
                }
            }

            # Send to voice_gateway queue using async helper (non-blocking)
            _run_async(_publish_to_queue_async(
                self.rabbitmq_url,
                "voice_gateway_queue",
                notification
            ))

            logger.info(f"Agent {self.agent_name} speaking: '{text[:50]}...'")

            self.send_result_notification(
                status="SUCCESS",
                result={
                    "spoken": True,
                    "text_length": len(text),
                    "emotion": emotion
                }
            )

        except Exception as e:
            logger.error(f"Failed to speak: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to speak: {str(e)}"
            )


class AnswerCallTool(BaseTool):
    """
    Answer an incoming call.

    Use this tool when you receive a call notification and want to accept it.
    """

    def __init__(self):
        super().__init__(
            name="answer_call",
            description="Answer an incoming call from the user"
        )
        self.api_gateway_url = os.environ.get("API_GATEWAY_URL", "http://localhost:8000")
        self.internal_api_key = self._load_internal_api_key()

    def _load_internal_api_key(self) -> Optional[str]:
        try:
            if not os.path.exists("/shared/internal_api_key"):
                 return None
            with open("/shared/internal_api_key", "r") as f:
                return f.read().strip()
        except Exception:
            return None

    def is_available(self, context: ToolAvailabilityContext) -> bool:
        """Answer call is available when there's a ringing call (call_id present but not yet connected)."""
        # This tool is needed during the ringing phase, so we check for call_id
        # The actual validation happens in validate_arguments
        return True  # Always available - the call_id validation handles the logic

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        if "call_id" not in arguments:
            return False, "Missing required argument: 'call_id'"
        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        return {
            "command": "answer_call",
            "description": "Answer an incoming call from the user",
            "parameters": [
                {
                    "name": "call_id",
                    "type": "str",
                    "description": "ID of the incoming call to answer",
                    "required": True
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """Answer the incoming call."""
        call_id = arguments["call_id"]

        try:
            headers = {"Content-Type": "application/json"}
            if self.internal_api_key:
                headers["X-Internal-Key"] = self.internal_api_key

            response = requests.post(
                f"{self.api_gateway_url}/api/v1/calls/{call_id}/answer",
                json={"answered_by": self.agent_name},
                headers=headers,
                timeout=10
            )

            if response.status_code == 200:
                logger.info(f"Agent {self.agent_name} answered call {call_id}")
                self.send_result_notification(
                    status="SUCCESS",
                    result={
                        "call_id": call_id,
                        "answered": True,
                        "answered_by": self.agent_name
                    }
                )
            else:
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Failed to answer call: {response.status_code}"
                )

        except Exception as e:
            logger.error(f"Failed to answer call: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to answer call: {str(e)}"
            )


class HangUpTool(BaseTool):
    """
    End the current call.

    Use this tool to gracefully end a voice call.
    """

    def __init__(self):
        super().__init__(
            name="hang_up",
            description="End the current voice call gracefully"
        )
        self.api_gateway_url = os.environ.get("API_GATEWAY_URL", "http://localhost:8000")
        self.internal_api_key = self._load_internal_api_key()

    def _load_internal_api_key(self) -> Optional[str]:
        try:
            if not os.path.exists("/shared/internal_api_key"):
                 return None
            with open("/shared/internal_api_key", "r") as f:
                return f.read().strip()
        except Exception:
            return None

    def is_available(self, context: ToolAvailabilityContext) -> bool:
        """Hang up tool is only available during active calls."""
        return context.is_on_call

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        # call_id can be optional - will use active call for session
        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        return {
            "command": "hang_up",
            "description": "End the current voice call",
            "parameters": [
                {
                    "name": "call_id",
                    "type": "str",
                    "description": "ID of the call to end (optional if on active call)",
                    "required": False
                },
                {
                    "name": "farewell",
                    "type": "str",
                    "description": "Optional goodbye message to say before hanging up",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """End the call."""
        call_id = arguments.get("call_id")
        farewell = arguments.get("farewell")
        session_id = arguments.get("session_id")
        twilio_call_sid = arguments.get("twilio_call_sid")

        try:
            # If farewell message provided, speak it first
            if farewell:
                speak_notification = {
                    "notification_id": str(uuid.uuid4()),
                    "timestamp": datetime.utcnow().isoformat(),
                    "recipient_agent_id": "voice_gateway",
                    "notification_type": "call_speak",
                    "source": f"agent_{self.agent_name}",
                    "payload": {
                        "sender_agent_id": self.agent_name,
                        "content": farewell,
                        "session_id": session_id,
                        "call_id": call_id,
                        "is_call_speech": True,
                        "is_farewell": True
                    }
                }

                # Send farewell using async helper (non-blocking)
                _run_async(_publish_to_queue_async(
                    self.rabbitmq_url,
                    "voice_gateway_queue",
                    speak_notification
                ))

                # Brief pause for farewell to be spoken
                time.sleep(0.5)

            # End the call
            headers = {"Content-Type": "application/json"}
            if self.internal_api_key:
                headers["X-Internal-Key"] = self.internal_api_key

            # Always use the session-based endpoint with fallback fields
            # This is more resilient as it tries multiple lookup methods
            endpoint = f"{self.api_gateway_url}/api/v1/calls/active/end"
            payload = {
                "session_id": session_id or "",
                "ended_by": self.agent_name,
                "call_id": call_id,  # Fallback lookup
                "twilio_call_sid": twilio_call_sid  # Fallback for phone calls
            }

            response = requests.post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=10
            )

            if response.status_code == 200:
                logger.info(f"Agent {self.agent_name} ended call")
                self.send_result_notification(
                    status="SUCCESS",
                    result={
                        "call_ended": True,
                        "ended_by": self.agent_name
                    }
                )
            else:
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Failed to end call: {response.status_code}"
                )

        except Exception as e:
            logger.error(f"Failed to hang up: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to hang up: {str(e)}"
            )


class TransferCallTool(BaseTool):
    """
    Transfer the call to another agent.

    Use this tool to hand off the call to a specialist agent.
    """

    def __init__(self):
        super().__init__(
            name="transfer_call",
            description="Transfer the call to another agent (e.g., weather_agent, calendar_agent)"
        )
        self.api_gateway_url = os.environ.get("API_GATEWAY_URL", "http://localhost:8000")
        self.internal_api_key = self._load_internal_api_key()

    def _load_internal_api_key(self) -> Optional[str]:
        try:
            if not os.path.exists("/shared/internal_api_key"):
                 return None
            with open("/shared/internal_api_key", "r") as f:
                return f.read().strip()
        except Exception:
            return None

    def is_available(self, context: ToolAvailabilityContext) -> bool:
        """Transfer call tool is only available during active calls."""
        return context.is_on_call

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        if "to_agent" not in arguments:
            return False, "Missing required argument: 'to_agent'"
        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        return {
            "command": "transfer_call",
            "description": "Transfer the call to another agent",
            "parameters": [
                {
                    "name": "to_agent",
                    "type": "str",
                    "description": "Agent ID to transfer to (e.g., 'weather_agent', 'calendar_agent')",
                    "required": True
                },
                {
                    "name": "announcement",
                    "type": "str",
                    "description": "Message to say before transfer (e.g., 'Let me connect you with our weather specialist')",
                    "required": False
                },
                {
                    "name": "call_id",
                    "type": "str",
                    "description": "ID of the call to transfer",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """Transfer the call to another agent."""
        to_agent = arguments["to_agent"]
        announcement = arguments.get("announcement")
        call_id = arguments.get("call_id")
        session_id = arguments.get("session_id")
        twilio_call_sid = arguments.get("twilio_call_sid")

        try:
            # If announcement provided, speak it first
            if announcement:
                speak_notification = {
                    "notification_id": str(uuid.uuid4()),
                    "timestamp": datetime.utcnow().isoformat(),
                    "recipient_agent_id": "voice_gateway",
                    "notification_type": "call_speak",
                    "source": f"agent_{self.agent_name}",
                    "payload": {
                        "sender_agent_id": self.agent_name,
                        "content": announcement,
                        "session_id": session_id,
                        "call_id": call_id,
                        "is_call_speech": True,
                        "is_transfer_announcement": True
                    }
                }

                # Send announcement using async helper (non-blocking)
                _run_async(_publish_to_queue_async(
                    self.rabbitmq_url,
                    "voice_gateway_queue",
                    speak_notification
                ))

                # Brief pause for announcement
                time.sleep(0.5)

            # Perform transfer
            headers = {"Content-Type": "application/json"}
            if self.internal_api_key:
                headers["X-Internal-Key"] = self.internal_api_key

            # Always use the session-based endpoint with fallback fields
            # This is more resilient as it tries multiple lookup methods
            endpoint = f"{self.api_gateway_url}/api/v1/calls/active/transfer"
            payload = {
                "session_id": session_id or "",
                "from_agent": self.agent_name,
                "to_agent": to_agent,
                "call_id": call_id,  # Fallback lookup
                "twilio_call_sid": twilio_call_sid  # Fallback for phone calls
            }

            response = requests.post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=10
            )

            if response.status_code == 200:
                logger.info(f"Agent {self.agent_name} transferred call to {to_agent}")
                self.send_result_notification(
                    status="SUCCESS",
                    result={
                        "transferred": True,
                        "from_agent": self.agent_name,
                        "to_agent": to_agent
                    }
                )
            else:
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Failed to transfer call: {response.status_code}"
                )

        except Exception as e:
            logger.error(f"Failed to transfer call: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to transfer: {str(e)}"
            )


class RecallPhoneTool(BaseTool):
    """
    Take back the phone from another agent.

    Use this tool (Primary Agent only) to reclaim a call that was transferred
    to a specialist agent.
    """

    def __init__(self):
        super().__init__(
            name="recall_phone",
            description="Take back the phone from another agent (Primary Agent only)"
        )
        self.api_gateway_url = os.environ.get("API_GATEWAY_URL", "http://localhost:8000")
        self.internal_api_key = self._load_internal_api_key()

    def _load_internal_api_key(self) -> Optional[str]:
        try:
            if not os.path.exists("/shared/internal_api_key"):
                 return None
            with open("/shared/internal_api_key", "r") as f:
                return f.read().strip()
        except Exception:
            return None

    def is_available(self, context: ToolAvailabilityContext) -> bool:
        """Recall phone tool is only available during active calls."""
        return context.is_on_call

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        if self.agent_name != "primary_agent":
            return False, "Only primary_agent can recall the phone"
        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        return {
            "command": "recall_phone",
            "description": "Take back the phone from another agent who has the call",
            "parameters": [
                {
                    "name": "call_id",
                    "type": "str",
                    "description": "ID of the call to reclaim",
                    "required": False
                },
                {
                    "name": "reason",
                    "type": "str",
                    "description": "Reason for recalling (optional, for logging)",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """Recall the phone from another agent."""
        call_id = arguments.get("call_id")
        session_id = arguments.get("session_id")

        try:
            headers = {"Content-Type": "application/json"}
            if self.internal_api_key:
                headers["X-Internal-Key"] = self.internal_api_key

            response = requests.post(
                f"{self.api_gateway_url}/api/v1/calls/{call_id}/recall",
                json={"by_agent": self.agent_name},
                headers=headers,
                timeout=10
            )

            if response.status_code == 200:
                logger.info(f"Primary agent recalled the phone")
                self.send_result_notification(
                    status="SUCCESS",
                    result={
                        "recalled": True,
                        "now_handled_by": self.agent_name
                    }
                )
            else:
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Failed to recall phone: {response.status_code}"
                )

        except Exception as e:
            logger.error(f"Failed to recall phone: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to recall: {str(e)}"
            )


class CallUserTool(BaseTool):
    """
    Initiate an outbound call to the user.

    Only the Primary Agent should use this tool. Use it for important
    notifications like reminders, alarms, or completed tasks.
    """

    def __init__(self):
        super().__init__(
            name="call_user",
            description="Call the user (Primary Agent only). Use for important notifications."
        )
        self.api_gateway_url = os.environ.get("API_GATEWAY_URL", "http://localhost:8000")
        self.internal_api_key = self._load_internal_api_key()

    def _load_internal_api_key(self) -> Optional[str]:
        try:
            if not os.path.exists("/shared/internal_api_key"):
                 return None
            with open("/shared/internal_api_key", "r") as f:
                return f.read().strip()
        except Exception:
            return None

    def is_available(self, context: ToolAvailabilityContext) -> bool:
        """Call user tool is only available when NOT on an active call."""
        return not context.is_on_call

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        if self.agent_name != "primary_agent":
            return False, "Only primary_agent can initiate outbound calls"

        if "reason" not in arguments:
            return False, "Missing required argument: 'reason' (why you're calling)"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        return {
            "command": "call_user",
            "description": "Initiate an outbound call to the user (Primary Agent only)",
            "parameters": [
                {
                    "name": "reason",
                    "type": "str",
                    "description": "Why you're calling (e.g., 'reminder', 'task_complete', 'urgent_update')",
                    "required": True
                },
                {
                    "name": "session_id",
                    "type": "str",
                    "description": "Session ID for the call",
                    "required": True
                },
                {
                    "name": "opening_message",
                    "type": "str",
                    "description": "First thing to say when user picks up",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """Initiate outbound call to user."""
        session_id = arguments.get("session_id")
        reason = arguments["reason"]
        opening_message = arguments.get("opening_message")

        try:
            headers = {"Content-Type": "application/json"}
            if self.internal_api_key:
                headers["X-Internal-Key"] = self.internal_api_key

            response = requests.post(
                f"{self.api_gateway_url}/api/v1/calls/initiate",
                json={
                    "session_id": session_id,
                    "initiated_by": self.agent_name,
                    "target": "user",
                    "reason": reason,
                    "opening_message": opening_message
                },
                headers=headers,
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()
                call_id = data.get("call_id")
                logger.info(f"Primary agent initiated outbound call: {call_id}")
                self.send_result_notification(
                    status="SUCCESS",
                    result={
                        "call_initiated": True,
                        "call_id": call_id,
                        "reason": reason,
                        "status": "ringing"
                    }
                )
            else:
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Failed to initiate call: {response.status_code}"
                )

        except Exception as e:
            logger.error(f"Failed to call user: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to call user: {str(e)}"
            )


class CallPhoneTool(BaseTool):
    """
    Initiate an outbound phone call via Twilio.

    Only the Primary Agent should use this tool. This calls an actual
    phone number (must be in the allowed whitelist).
    """

    def __init__(self):
        super().__init__(
            name="call_phone",
            description="Call a phone number via Twilio (Primary Agent only). Phone must be whitelisted."
        )
        self.api_gateway_url = os.environ.get("API_GATEWAY_URL", "http://localhost:8000")
        self.internal_api_key = self._load_internal_api_key()

    def _load_internal_api_key(self) -> Optional[str]:
        try:
            if not os.path.exists("/shared/internal_api_key"):
                 return None
            with open("/shared/internal_api_key", "r") as f:
                return f.read().strip()
        except Exception:
            return None

    def is_available(self, context: ToolAvailabilityContext) -> bool:
        """Call phone tool is only available when NOT on an active call."""
        return not context.is_on_call

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        if self.agent_name != "primary_agent":
            return False, "Only primary_agent can initiate outbound phone calls"

        if "phone_number" not in arguments:
            return False, "Missing required argument: 'phone_number' (E.164 format, e.g., +12125551234)"

        phone_number = arguments["phone_number"]
        if not phone_number.startswith("+"):
            return False, "Phone number must be in E.164 format (start with '+', e.g., +12125551234)"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        return {
            "command": "call_phone",
            "description": "Initiate an outbound phone call via Twilio (Primary Agent only)",
            "parameters": [
                {
                    "name": "phone_number",
                    "type": "str",
                    "description": "Phone number to call in E.164 format (e.g., +12125551234). Must be whitelisted.",
                    "required": True
                },
                {
                    "name": "session_id",
                    "type": "str",
                    "description": "Session ID for the call context",
                    "required": True
                },
                {
                    "name": "reason",
                    "type": "str",
                    "description": "Why you're calling (e.g., 'follow_up', 'notification', 'reminder')",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """Initiate outbound phone call via Twilio."""
        phone_number = arguments["phone_number"]
        session_id = arguments.get("session_id")
        reason = arguments.get("reason", "outbound_call")

        try:
            headers = {"Content-Type": "application/json"}
            if self.internal_api_key:
                headers["X-Internal-Key"] = self.internal_api_key

            # First check if number is whitelisted
            check_response = requests.get(
                f"{self.api_gateway_url}/api/v1/twilio/check-number/{phone_number}",
                headers=headers,
                timeout=5
            )

            if check_response.status_code == 200:
                check_data = check_response.json()
                if not check_data.get("is_allowed"):
                    self.send_result_notification(
                        status="FAILURE",
                        error_message=f"Phone number {phone_number} is not in the allowed whitelist"
                    )
                    return
            else:
                logger.warning(f"Could not verify phone number whitelist: {check_response.status_code}")

            # Initiate the outbound call via Twilio Gateway
            response = requests.post(
                f"{self.api_gateway_url}/api/v1/twilio/call/outbound",
                json={
                    "to_number": phone_number,
                    "session_id": session_id
                },
                headers=headers,
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    call_id = data.get("call_id")
                    twilio_call_sid = data.get("twilio_call_sid")
                    logger.info(f"Primary agent initiated phone call to {phone_number}: {call_id}")
                    self.send_result_notification(
                        status="SUCCESS",
                        result={
                            "call_initiated": True,
                            "call_id": call_id,
                            "twilio_call_sid": twilio_call_sid,
                            "to_number": phone_number,
                            "reason": reason,
                            "status": "ringing"
                        }
                    )
                else:
                    self.send_result_notification(
                        status="FAILURE",
                        error_message=f"Failed to initiate phone call: {data.get('error', 'Unknown error')}"
                    )
            else:
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Failed to initiate phone call: {response.status_code}"
                )

        except Exception as e:
            logger.error(f"Failed to call phone: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to call phone: {str(e)}"
            )


class SendSMSTool(BaseTool):
    """
    Send an SMS text message via Twilio.

    Only the Primary Agent should use this tool. Use it to send text
    messages to any phone number (no whitelist required for outbound).
    """

    def __init__(self):
        super().__init__(
            name="send_sms",
            description="Send an SMS text message to a phone number (Primary Agent only). No whitelist required."
        )
        self.api_gateway_url = os.environ.get("API_GATEWAY_URL", "http://localhost:8000")
        self.internal_api_key = self._load_internal_api_key()

    def _load_internal_api_key(self) -> Optional[str]:
        try:
            if not os.path.exists("/shared/internal_api_key"):
                 return None
            with open("/shared/internal_api_key", "r") as f:
                return f.read().strip()
        except Exception:
            return None

    def is_available(self, context: ToolAvailabilityContext) -> bool:
        """SMS tool is always available (not dependent on call state)."""
        return True

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        if self.agent_name != "primary_agent":
            return False, "Only primary_agent can send SMS messages"

        if "phone_number" not in arguments:
            return False, "Missing required argument: 'phone_number' (E.164 format, e.g., +12125551234)"

        if "message" not in arguments:
            return False, "Missing required argument: 'message' (text content to send)"

        phone_number = arguments["phone_number"]
        if not phone_number.startswith("+"):
            return False, "Phone number must be in E.164 format (start with '+', e.g., +12125551234)"

        message = arguments["message"]
        if not message or not message.strip():
            return False, "Message cannot be empty"

        if len(message) > 1600:
            return False, "Message too long (max 1600 characters)"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        return {
            "command": "send_sms",
            "description": "Send an SMS text message to a phone number (Primary Agent only)",
            "parameters": [
                {
                    "name": "phone_number",
                    "type": "str",
                    "description": "Phone number to send SMS to in E.164 format (e.g., +12125551234)",
                    "required": True
                },
                {
                    "name": "message",
                    "type": "str",
                    "description": "Text message content to send (max 1600 characters)",
                    "required": True
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """Send SMS via Twilio."""
        phone_number = arguments["phone_number"]
        message = arguments["message"]

        try:
            headers = {"Content-Type": "application/json"}
            if self.internal_api_key:
                headers["X-Internal-Key"] = self.internal_api_key

            response = requests.post(
                f"{self.api_gateway_url}/api/v1/twilio/sms/send",
                json={
                    "to_number": phone_number,
                    "body": message
                },
                headers=headers,
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    twilio_message_sid = data.get("twilio_message_sid")
                    logger.info(f"Primary agent sent SMS to {phone_number}: {twilio_message_sid}")
                    self.send_result_notification(
                        status="SUCCESS",
                        result={
                            "sms_sent": True,
                            "twilio_message_sid": twilio_message_sid,
                            "to_number": phone_number,
                            "message_length": len(message),
                            "status": data.get("status")
                        }
                    )
                else:
                    self.send_result_notification(
                        status="FAILURE",
                        error_message=f"Failed to send SMS: {data.get('error', 'Unknown error')}"
                    )
            else:
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Failed to send SMS: HTTP {response.status_code}"
                )

        except Exception as e:
            logger.error(f"Failed to send SMS: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to send SMS: {str(e)}"
            )


# Export all call tools
CALL_TOOLS = [
    SpeakTool,
    AnswerCallTool,
    HangUpTool,
    TransferCallTool,
    RecallPhoneTool,
    CallUserTool,
    CallPhoneTool,
    SendSMSTool
]
