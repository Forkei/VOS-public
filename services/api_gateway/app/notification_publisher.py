"""
RabbitMQ Publisher for Frontend Notifications.

Publishes notifications to the frontend_notifications exchange for delivery to WebSocket clients.
"""

import json
import logging
import threading
from datetime import datetime
from typing import Optional
from uuid import uuid4

import pika
from pika.exceptions import AMQPConnectionError, AMQPChannelError

from app.schemas_notifications import FrontendNotification, FrontendNotificationType, NewMessagePayload

logger = logging.getLogger(__name__)


class NotificationPublisher:
    """
    Publishes notifications to the frontend_notifications exchange.

    Thread-safe publisher for sending notifications from API endpoints.
    """

    def __init__(self, rabbitmq_url: str):
        self.rabbitmq_url = rabbitmq_url
        self.connection_params = None
        self._lock = threading.Lock()

        # Exchange configuration
        self.exchange_name = "frontend_notifications"
        self.exchange_type = "fanout"

        # Parse connection parameters
        try:
            self.connection_params = pika.URLParameters(rabbitmq_url)
            logger.info("✅ Notification publisher initialized")
        except Exception as e:
            logger.error(f"Failed to parse RabbitMQ URL: {e}")
            raise

    def publish_new_message(
        self,
        session_id: str,
        message_id: int,
        agent_id: str,
        content: str,
        content_type: str = "text",
        input_mode: Optional[str] = "text",
        voice_message_id: Optional[int] = None,
        audio_file_path: Optional[str] = None,
        audio_url: Optional[str] = None,
        audio_duration_ms: Optional[int] = None,
        attachment_ids: Optional[list[str]] = None,
        document_ids: Optional[list[str]] = None
    ) -> bool:
        """
        Publish a new_message notification.

        Args:
            session_id: Conversation session ID
            message_id: Database message ID
            agent_id: Agent that sent the message
            content: Message content
            content_type: Content type (default: "text")
            input_mode: Input mode - "text" or "voice" (default: "text")
            voice_message_id: Voice message ID if audio exists
            audio_file_path: Path to audio file
            audio_url: Signed URL for audio playback
            audio_duration_ms: Audio duration in milliseconds
            attachment_ids: Image attachment IDs
            document_ids: Document reference IDs

        Returns:
            True if published successfully
        """
        payload = NewMessagePayload(
            session_id=session_id,
            message_id=message_id,
            agent_id=agent_id,
            content=content,
            content_type=content_type,
            timestamp=datetime.utcnow(),
            input_mode=input_mode,
            voice_message_id=voice_message_id,
            audio_file_path=audio_file_path,
            audio_url=audio_url,
            audio_duration_ms=audio_duration_ms,
            attachment_ids=attachment_ids,
            document_ids=document_ids
        )

        notification = FrontendNotification(
            notification_id=uuid4(),
            notification_type=FrontendNotificationType.NEW_MESSAGE,
            session_id=session_id,
            payload=payload.model_dump(mode='json'),
            timestamp=datetime.utcnow()
        )

        return self._publish_notification(notification)

    def publish_agent_status(
        self,
        agent_id: str,
        status: str,
        processing_state: str,
        session_id: Optional[str] = None
    ) -> bool:
        """
        Publish an agent_status notification.

        Args:
            agent_id: Agent ID
            status: Agent status (active/sleeping/off)
            processing_state: Processing state (idle/thinking/executing_tools)
            session_id: Optional session ID for targeted delivery

        Returns:
            True if published successfully
        """
        from app.schemas_notifications import AgentStatusPayload

        payload = AgentStatusPayload(
            agent_id=agent_id,
            status=status,
            processing_state=processing_state,
            timestamp=datetime.utcnow()
        )

        notification = FrontendNotification(
            notification_id=uuid4(),
            notification_type=FrontendNotificationType.AGENT_STATUS,
            session_id=session_id,  # None = broadcast to all sessions
            payload=payload.model_dump(mode='json'),
            timestamp=datetime.utcnow()
        )

        return self._publish_notification(notification)

    def publish_agent_action_status(
        self,
        agent_id: str,
        session_id: Optional[str],
        action_description: str
    ) -> bool:
        """
        Publish an agent action status description (LLM-generated status text).

        Args:
            agent_id: Agent ID
            session_id: Optional session ID (None = broadcast to all sessions)
            action_description: User-facing action description

        Returns:
            True if published successfully
        """
        # Define inline to avoid circular import
        from pydantic import BaseModel, Field

        class AgentActionStatusPayload(BaseModel):
            agent_id: str = Field(...)
            session_id: Optional[str] = Field(default=None)
            action_description: str = Field(...)
            timestamp: datetime = Field(default_factory=datetime.utcnow)

        payload = AgentActionStatusPayload(
            agent_id=agent_id,
            session_id=session_id,
            action_description=action_description,
            timestamp=datetime.utcnow()
        )

        # Use AGENT_ACTION_STATUS for LLM-generated status descriptions
        notification = FrontendNotification(
            notification_id=uuid4(),
            notification_type=FrontendNotificationType.AGENT_ACTION_STATUS,
            session_id=session_id,
            payload=payload.model_dump(mode='json'),
            timestamp=datetime.utcnow()
        )

        return self._publish_notification(notification)

    def publish_app_interaction(
        self,
        agent_id: str,
        app_name: str,
        action: str,
        result: dict,
        session_id: Optional[str] = None
    ) -> bool:
        """
        Publish an app_interaction notification (e.g., weather data for weather app).

        Args:
            agent_id: Agent performing the interaction
            app_name: Application name (e.g., "weather_app")
            action: Action performed (e.g., "weather_data_fetched")
            result: Interaction result data (e.g., weather data)
            session_id: Optional session ID for targeted delivery

        Returns:
            True if published successfully
        """
        from app.schemas_notifications import AppInteractionPayload

        payload = AppInteractionPayload(
            agent_id=agent_id,
            app_name=app_name,
            action=action,
            result=result,
            timestamp=datetime.utcnow()
        )

        notification = FrontendNotification(
            notification_id=uuid4(),
            notification_type=FrontendNotificationType.APP_INTERACTION,
            session_id=session_id,
            payload=payload.model_dump(mode='json'),
            timestamp=datetime.utcnow()
        )

        return self._publish_notification(notification)

    def _publish_notification(self, notification: FrontendNotification) -> bool:
        """
        Publish a notification to the exchange.

        Args:
            notification: The notification to publish

        Returns:
            True if published successfully
        """
        with self._lock:
            connection = None
            channel = None

            try:
                # Create connection and channel
                connection = pika.BlockingConnection(self.connection_params)
                channel = connection.channel()

                # Declare exchange (idempotent)
                channel.exchange_declare(
                    exchange=self.exchange_name,
                    exchange_type=self.exchange_type,
                    durable=True
                )

                # Convert notification to JSON
                message_body = json.dumps(notification.model_dump(mode='json'))

                # Publish to exchange
                channel.basic_publish(
                    exchange=self.exchange_name,
                    routing_key='',  # Fanout exchange ignores routing key
                    body=message_body,
                    properties=pika.BasicProperties(
                        delivery_mode=2,  # Persistent message
                        content_type='application/json'
                    )
                )

                logger.info(f"📤 Published {notification.notification_type} notification "
                           f"to {self.exchange_name} (session: {notification.session_id})")

                return True

            except (AMQPConnectionError, AMQPChannelError) as e:
                logger.error(f"RabbitMQ connection error: {e}")
                return False
            except Exception as e:
                logger.error(f"Failed to publish notification: {e}", exc_info=True)
                return False
            finally:
                # Clean up
                try:
                    if channel and not channel.is_closed:
                        channel.close()
                    if connection and not connection.is_closed:
                        connection.close()
                except Exception as e:
                    logger.warning(f"Error closing RabbitMQ connection: {e}")


# Global publisher instance
notification_publisher: Optional[NotificationPublisher] = None


def initialize_notification_publisher(rabbitmq_url: str):
    """
    Initialize the global notification publisher.

    Args:
        rabbitmq_url: RabbitMQ connection URL
    """
    global notification_publisher

    if notification_publisher is not None:
        logger.warning("Notification publisher already initialized")
        return

    notification_publisher = NotificationPublisher(rabbitmq_url)
    logger.info("✅ Global notification publisher initialized")


def get_notification_publisher() -> Optional[NotificationPublisher]:
    """
    Get the global notification publisher instance.

    Returns:
        The notification publisher, or None if not initialized
    """
    return notification_publisher

    def publish_browser_screenshot(
        self,
        agent_id: str,
        screenshot_base64: str,
        current_url: Optional[str] = None,
        task: Optional[str] = None,
        session_id: Optional[str] = None
    ) -> bool:
        """
        Publish a browser_screenshot notification.

        Args:
            agent_id: Browser agent ID
            screenshot_base64: Base64 encoded PNG screenshot
            current_url: Current browser URL
            task: Task being performed
            session_id: Optional session ID for targeted delivery

        Returns:
            True if published successfully
        """
        from app.schemas_notifications import BrowserScreenshotPayload

        payload = BrowserScreenshotPayload(
            agent_id=agent_id,
            session_id=session_id,
            screenshot_base64=screenshot_base64,
            current_url=current_url,
            task=task,
            timestamp=datetime.utcnow()
        )

        notification = FrontendNotification(
            notification_id=uuid4(),
            notification_type=FrontendNotificationType.BROWSER_SCREENSHOT,
            session_id=session_id,
            payload=payload.model_dump(mode='json'),
            timestamp=datetime.utcnow()
        )

        return self._publish_notification(notification)
