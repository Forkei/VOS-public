"""
RabbitMQ Consumer for Frontend Notifications.

Listens to the frontend_notifications exchange and routes notifications
to WebSocket clients or stores them for later delivery.
"""

import asyncio
import json
import logging
import threading
from datetime import datetime
from typing import Optional
from uuid import UUID

import pika
from pika.exceptions import AMQPConnectionError, AMQPChannelError

from app.schemas_notifications import FrontendNotification, FrontendNotificationType
from app.websocket_manager import connection_manager
from app.database import DatabaseClient

logger = logging.getLogger(__name__)


class NotificationConsumer:
    """
    Consumes notifications from RabbitMQ and delivers them to WebSocket clients.

    Features:
    - Listens to frontend_notifications fanout exchange
    - Routes notifications to active WebSocket connections
    - Stores undelivered notifications in database
    - Automatic retry on connection failure
    """

    def __init__(self, rabbitmq_url: str, db_client: DatabaseClient):
        self.rabbitmq_url = rabbitmq_url
        self.db_client = db_client
        self.connection = None
        self.channel = None
        self.consumer_tag = None
        self._running = False
        self._thread = None

        # Exchange and queue configuration
        self.exchange_name = "frontend_notifications"
        self.exchange_type = "fanout"
        # Each API Gateway instance gets its own queue (auto-delete on disconnect)
        self.queue_name = ""  # Let RabbitMQ generate a unique queue name

        logger.info("Notification Consumer initialized")

    def start(self):
        """Start the consumer in a background thread."""
        if self._running:
            logger.warning("Notification consumer already running")
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_consumer, daemon=True)
        self._thread.start()
        logger.info("✅ Notification consumer started in background thread")

    def stop(self):
        """Stop the consumer gracefully."""
        if not self._running:
            return

        self._running = False

        if self.channel and not self.channel.is_closed:
            try:
                if self.consumer_tag:
                    self.channel.basic_cancel(self.consumer_tag)
                self.channel.close()
            except Exception as e:
                logger.warning(f"Error closing channel: {e}")

        if self.connection and not self.connection.is_closed:
            try:
                self.connection.close()
            except Exception as e:
                logger.warning(f"Error closing connection: {e}")

        if self._thread:
            self._thread.join(timeout=5)

        logger.info("🛑 Notification consumer stopped")

    def _run_consumer(self):
        """Main consumer loop with auto-reconnect."""
        retry_delay = 1
        max_retry_delay = 60

        while self._running:
            try:
                self._connect_and_consume()
            except (AMQPConnectionError, AMQPChannelError) as e:
                logger.error(f"RabbitMQ connection error: {e}")
                if self._running:
                    logger.info(f"Retrying in {retry_delay} seconds...")
                    threading.Event().wait(retry_delay)
                    retry_delay = min(retry_delay * 2, max_retry_delay)
            except Exception as e:
                logger.error(f"Unexpected error in consumer: {e}", exc_info=True)
                if self._running:
                    threading.Event().wait(retry_delay)

    def _connect_and_consume(self):
        """Connect to RabbitMQ and start consuming."""
        logger.info("Connecting to RabbitMQ...")

        # Create connection
        params = pika.URLParameters(self.rabbitmq_url)
        self.connection = pika.BlockingConnection(params)
        self.channel = self.connection.channel()

        # Declare fanout exchange
        self.channel.exchange_declare(
            exchange=self.exchange_name,
            exchange_type=self.exchange_type,
            durable=True
        )

        # Declare exclusive queue (auto-delete when consumer disconnects)
        result = self.channel.queue_declare(
            queue=self.queue_name,  # Empty string = auto-generate name
            exclusive=True,         # Delete when connection closes
            auto_delete=True
        )
        self.queue_name = result.method.queue

        # Bind queue to exchange
        self.channel.queue_bind(
            exchange=self.exchange_name,
            queue=self.queue_name
        )

        logger.info(f"✅ Bound to exchange '{self.exchange_name}' with queue '{self.queue_name}'")

        # Set up consumer
        self.consumer_tag = self.channel.basic_consume(
            queue=self.queue_name,
            on_message_callback=self._handle_notification,
            auto_ack=False
        )

        logger.info("🎧 Listening for frontend notifications...")

        # Start consuming (blocks until stopped)
        self.channel.start_consuming()

    def _handle_notification(self, ch, method, properties, body):
        """
        Handle incoming notification from RabbitMQ.

        Args:
            ch: Channel
            method: Delivery method
            properties: Message properties
            body: Message body (JSON)
        """
        try:
            # Parse notification
            notification_data = json.loads(body)
            notification = FrontendNotification(**notification_data)

            logger.info(f"📬 Received {notification.notification_type} notification "
                       f"for session {notification.session_id}")

            # Attempt to deliver via WebSocket
            if notification.session_id:
                delivered_count = asyncio.run(
                    connection_manager.send_notification(
                        notification.session_id,
                        notification
                    )
                )

                if delivered_count > 0:
                    logger.info(f"✅ Delivered to {delivered_count} active connection(s)")
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                else:
                    # No active connections - store for later delivery
                    logger.info("💾 No active connections - storing for later delivery")
                    self._store_pending_notification(notification)
                    ch.basic_ack(delivery_tag=method.delivery_tag)
            else:
                # Broadcast notification (no specific session)
                delivered_count = asyncio.run(
                    connection_manager.broadcast_notification(notification)
                )
                logger.info(f"📢 Broadcast to {delivered_count} connection(s)")
                ch.basic_ack(delivery_tag=method.delivery_tag)

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse notification JSON: {e}")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        except Exception as e:
            logger.error(f"Error handling notification: {e}", exc_info=True)
            # Requeue for retry
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

    def _store_pending_notification(self, notification: FrontendNotification):
        """
        Store notification in database for later delivery.

        Args:
            notification: The notification to store
        """
        try:
            query = """
            INSERT INTO pending_notifications
                (session_id, notification_id, notification_type, notification_payload, created_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (notification_id) DO NOTHING;
            """

            self.db_client.execute_query(query, (
                notification.session_id,
                str(notification.notification_id),
                notification.notification_type.value,
                json.dumps(notification.model_dump(mode='json')),
                datetime.utcnow()
            ))

            logger.info(f"💾 Stored pending notification {notification.notification_id}")

        except Exception as e:
            logger.error(f"Failed to store pending notification: {e}")


# Global consumer instance
notification_consumer: Optional[NotificationConsumer] = None


def start_notification_consumer(rabbitmq_url: str, db_client: DatabaseClient):
    """
    Initialize and start the global notification consumer.

    Args:
        rabbitmq_url: RabbitMQ connection URL
        db_client: Database client instance
    """
    global notification_consumer

    if notification_consumer is not None:
        logger.warning("Notification consumer already initialized")
        return

    notification_consumer = NotificationConsumer(rabbitmq_url, db_client)
    notification_consumer.start()


def stop_notification_consumer():
    """Stop the global notification consumer."""
    global notification_consumer

    if notification_consumer:
        notification_consumer.stop()
        notification_consumer = None
