"""
RabbitMQ Client for Twilio Gateway
Handles messaging with VOS voice pipeline
"""

import json
import logging
import uuid
import base64
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

            # Declare queue for Twilio TTS audio
            await self.channel.declare_queue("twilio_tts_queue", durable=True)

            # Declare queue for Twilio audio going to Voice Gateway
            await self.channel.declare_queue("twilio_audio_queue", durable=True)

            logger.info("RabbitMQ connection established")

        except Exception as e:
            logger.error(f"Failed to connect to RabbitMQ: {e}")
            raise

    async def publish_stream_started(
        self,
        session_id: str,
        call_id: str,
        twilio_call_sid: str,
        stream_sid: str
    ):
        """
        Publish stream started event to Voice Gateway.

        This ensures the voice_gateway has stream_sid available before any TTS is attempted.
        Critical for greeting messages that play before user speaks.

        Args:
            session_id: VOS session ID
            call_id: VOS call ID
            twilio_call_sid: Twilio's call SID
            stream_sid: Twilio's stream SID
        """
        try:
            message = {
                "type": "stream_started",
                "session_id": session_id,
                "call_id": call_id,
                "source": "twilio",
                "twilio_call_sid": twilio_call_sid,
                "stream_sid": stream_sid,
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }

            await self.channel.default_exchange.publish(
                aio_pika.Message(
                    body=json.dumps(message).encode(),
                    content_type="application/json",
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT
                ),
                routing_key="call_audio_queue"
            )

            logger.info(f"Published stream_started to Voice Gateway: session={session_id}, stream_sid={stream_sid}")

        except Exception as e:
            logger.error(f"Error publishing stream_started to Voice Gateway: {e}")

    async def publish_audio_to_voice_gateway(
        self,
        session_id: str,
        call_id: str,
        audio_data: bytes,
        twilio_call_sid: str,
        stream_sid: str
    ):
        """
        Publish audio from Twilio to Voice Gateway for STT processing.

        Args:
            session_id: VOS session ID
            call_id: VOS call ID
            audio_data: PCM audio data (converted from mulaw)
            twilio_call_sid: Twilio's call SID
            stream_sid: Twilio's stream SID
        """
        try:
            message = {
                "type": "call_audio",
                "session_id": session_id,
                "call_id": call_id,
                "audio_data": base64.b64encode(audio_data).decode(),
                "source": "twilio",
                "twilio_call_sid": twilio_call_sid,
                "stream_sid": stream_sid,
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }

            await self.channel.default_exchange.publish(
                aio_pika.Message(
                    body=json.dumps(message).encode(),
                    content_type="application/json",
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT
                ),
                routing_key="call_audio_queue"
            )

            logger.debug(f"Published Twilio audio to Voice Gateway: session={session_id}")

        except Exception as e:
            logger.error(f"Error publishing audio to Voice Gateway: {e}")

    async def publish_tts_to_twilio(
        self,
        call_sid: str,
        stream_sid: str,
        audio_data: bytes
    ):
        """
        Publish TTS audio to be sent back to Twilio.

        Args:
            call_sid: Twilio call SID
            stream_sid: Twilio stream SID
            audio_data: Mulaw audio data for Twilio
        """
        try:
            message = {
                "call_sid": call_sid,
                "stream_sid": stream_sid,
                "audio_data": base64.b64encode(audio_data).decode(),
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }

            await self.channel.default_exchange.publish(
                aio_pika.Message(
                    body=json.dumps(message).encode(),
                    content_type="application/json",
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT
                ),
                routing_key="twilio_tts_queue"
            )

            logger.debug(f"Published TTS audio for Twilio: call_sid={call_sid}")

        except Exception as e:
            logger.error(f"Error publishing TTS to Twilio: {e}")

    async def publish_call_event(
        self,
        event_type: str,
        session_id: str,
        call_id: str,
        twilio_call_sid: str,
        phone_number: Optional[str] = None,
        caller_name: Optional[str] = None,
        metadata: Optional[dict] = None
    ):
        """
        Publish call event (incoming, answered, ended) to the system.

        Args:
            event_type: Type of event (incoming_call, call_answered, call_ended)
            session_id: VOS session ID
            call_id: VOS call ID
            twilio_call_sid: Twilio's call SID
            phone_number: Caller's phone number
            caller_name: Caller's display name
            metadata: Additional metadata
        """
        try:
            notification = {
                "notification_id": str(uuid.uuid4()),
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "recipient_agent_id": "primary_agent",
                "notification_type": event_type,
                "source": "twilio_gateway",
                "payload": {
                    "session_id": session_id,
                    "call_id": call_id,
                    "twilio_call_sid": twilio_call_sid,
                    "phone_number": phone_number,
                    "caller_name": caller_name,
                    "call_source": "twilio",
                    "metadata": metadata or {}
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

            logger.info(f"Published {event_type} event: session={session_id}, call_sid={twilio_call_sid}")

        except Exception as e:
            logger.error(f"Error publishing call event: {e}")

    async def publish_notification(
        self,
        queue_name: str,
        notification: dict
    ):
        """
        Publish a notification to a specific agent queue.

        Args:
            queue_name: The agent queue name (e.g., 'primary_agent')
            notification: The notification dictionary to publish
        """
        try:
            # Ensure the timestamp is set
            if "timestamp" not in notification:
                notification["timestamp"] = datetime.utcnow().isoformat() + "Z"

            # Publish to the specified queue
            routing_key = f"{queue_name}_queue"
            await self.channel.default_exchange.publish(
                aio_pika.Message(
                    body=json.dumps(notification).encode(),
                    content_type="application/json",
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT
                ),
                routing_key=routing_key
            )

            logger.info(f"Published notification to {routing_key}: type={notification.get('notification_type')}")

        except Exception as e:
            logger.error(f"Error publishing notification: {e}")

    async def close(self):
        """Close RabbitMQ connection"""
        try:
            if self.connection:
                await self.connection.close()
                logger.info("RabbitMQ connection closed")

        except Exception as e:
            logger.error(f"Error closing RabbitMQ connection: {e}")
