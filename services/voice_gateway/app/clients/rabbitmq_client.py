"""
RabbitMQ Client for Voice Gateway
Handles messaging with VOS agents
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Optional, Callable
import aio_pika
from aio_pika import Connection, Channel, Exchange, Queue

from ..config import settings

logger = logging.getLogger(__name__)


class RabbitMQClient:
    """Client for RabbitMQ messaging"""

    def __init__(self):
        """Initialize RabbitMQ client"""
        self.connection: Optional[Connection] = None
        self.channel: Optional[Channel] = None
        self.exchange: Optional[Exchange] = None
        self.response_queues: dict[str, Queue] = {}  # session_id → queue

    async def connect(self):
        """Establish connection to RabbitMQ"""
        try:
            logger.info(f"Connecting to RabbitMQ: {settings.RABBITMQ_URL}")

            self.connection = await aio_pika.connect_robust(
                settings.RABBITMQ_URL
            )

            self.channel = await self.connection.channel()

            # Declare exchange (should already exist from other services)
            self.exchange = await self.channel.declare_exchange(
                settings.RABBITMQ_EXCHANGE,
                aio_pika.ExchangeType.TOPIC,
                durable=True
            )

            logger.info("RabbitMQ connection established")

        except Exception as e:
            logger.error(f"Failed to connect to RabbitMQ: {e}")
            raise

    async def publish_user_message(
        self,
        session_id: str,
        content: str,
        voice_metadata: Optional[dict] = None,
        user_timezone: Optional[str] = None
    ):
        """
        Publish user message to primary agent

        Args:
            session_id: User session ID
            content: Transcribed text
            voice_metadata: Additional voice-related metadata
            user_timezone: User's IANA timezone (e.g., "America/New_York")
        """
        try:
            notification = {
                "notification_id": str(uuid.uuid4()),
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "recipient_agent_id": "primary_agent",
                "notification_type": "user_message",
                "source": "voice_gateway",
                "payload": {
                    "content": content,
                    "content_type": "voice_transcript",
                    "session_id": session_id,
                    "voice_metadata": voice_metadata or {},
                    "user_timezone": user_timezone
                }
            }

            # Publish to primary_agent queue
            await self.channel.default_exchange.publish(
                aio_pika.Message(
                    body=json.dumps(notification).encode(),
                    content_type="application/json",
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT
                ),
                routing_key="primary_agent_queue"
            )

            logger.info(
                f"Published user message to primary_agent: "
                f"session={session_id}, text='{content[:50]}...'"
            )

        except Exception as e:
            logger.error(f"Error publishing user message: {e}")
            raise

    async def publish_failure_notification(
        self,
        session_id: str,
        error_type: str,
        error_message: str,
        original_text: Optional[str] = None
    ):
        """
        Notify primary agent that voice processing failed
        Allows agent to fall back to text mode

        Args:
            session_id: User session ID
            error_type: Type of failure (stt_failed, tts_failed, etc.)
            error_message: Error details
            original_text: Original text that failed to process (for TTS failures)
        """
        try:
            notification = {
                "notification_id": str(uuid.uuid4()),
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "recipient_agent_id": "primary_agent",
                "notification_type": "voice_failure",
                "source": "voice_gateway",
                "payload": {
                    "session_id": session_id,
                    "error_type": error_type,
                    "error_message": error_message,
                    "original_text": original_text,
                    "fallback_action": "use_text_mode"
                }
            }

            # Publish to primary_agent queue
            await self.channel.default_exchange.publish(
                aio_pika.Message(
                    body=json.dumps(notification).encode(),
                    content_type="application/json",
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT
                ),
                routing_key="primary_agent_queue"
            )

            logger.warning(
                f"Notified primary_agent of voice failure: "
                f"session={session_id}, error={error_type}"
            )

        except Exception as e:
            logger.error(f"Error publishing failure notification: {e}")
            # Don't raise - notification failure shouldn't crash the session

    async def subscribe_to_responses(
        self,
        session_id: str,
        callback: Callable
    ) -> Queue:
        """
        Subscribe to agent responses for a specific session

        Args:
            session_id: Session ID to listen for
            callback: Async function to handle incoming messages

        Returns:
            Queue object for this subscription
        """
        try:
            # Create temporary queue for this session
            queue_name = f"voice_response_{session_id}"

            queue = await self.channel.declare_queue(
                queue_name,
                durable=False,
                auto_delete=True,
                exclusive=False
            )

            # Bind to exchange with routing pattern
            # Listen for messages targeted to this voice session
            await queue.bind(
                self.exchange,
                routing_key=f"voice.response.{session_id}"
            )

            # Start consuming
            await queue.consume(callback)

            self.response_queues[session_id] = queue

            logger.info(f"Subscribed to responses for session {session_id}")

            return queue

        except Exception as e:
            logger.error(f"Error subscribing to responses: {e}")
            raise

    async def unsubscribe_from_responses(self, session_id: str):
        """
        Unsubscribe from agent responses

        Args:
            session_id: Session ID to unsubscribe
        """
        try:
            if session_id in self.response_queues:
                queue = self.response_queues[session_id]
                await queue.delete()
                del self.response_queues[session_id]

                logger.info(f"Unsubscribed from responses for session {session_id}")

        except Exception as e:
            logger.error(f"Error unsubscribing from responses: {e}")

    async def close(self):
        """Close RabbitMQ connection"""
        try:
            # Clean up all response queues
            for session_id in list(self.response_queues.keys()):
                await self.unsubscribe_from_responses(session_id)

            if self.connection:
                await self.connection.close()
                logger.info("RabbitMQ connection closed")

        except Exception as e:
            logger.error(f"Error closing RabbitMQ connection: {e}")
