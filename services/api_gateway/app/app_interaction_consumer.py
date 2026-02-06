"""
App Interaction RabbitMQ Consumer

Consumes app interaction notifications from RabbitMQ and forwards them
to connected WebSocket clients.
"""

import asyncio
import json
import logging
import pika
from typing import Optional
from app.app_interaction_manager import app_interaction_manager

logger = logging.getLogger(__name__)


class AppInteractionConsumer:
    """Consumes app interaction notifications from RabbitMQ"""

    def __init__(self, rabbitmq_url: str):
        self.rabbitmq_url = rabbitmq_url
        self.connection = None
        self.channel = None
        self.running = False
        self.consumer_task: Optional[asyncio.Task] = None

    async def start(self):
        """Start consuming app interaction notifications"""
        if self.running:
            logger.warning("App interaction consumer already running")
            return

        self.running = True

        # Create exchange and queue for app interactions
        try:
            # Connect to RabbitMQ
            connection_params = pika.URLParameters(self.rabbitmq_url)
            self.connection = pika.BlockingConnection(connection_params)
            self.channel = self.connection.channel()

            # Declare exchange
            self.channel.exchange_declare(
                exchange='app_interactions',
                exchange_type='topic',
                durable=True
            )

            # Declare queue
            self.channel.queue_declare(
                queue='app_interaction_notifications',
                durable=True
            )

            # Bind queue to exchange with routing key pattern
            # This will capture messages like:
            # - calendar_agent.event_created
            # - calendar_agent.reminder_triggered
            # - timer_agent.alarm_triggered
            self.channel.queue_bind(
                exchange='app_interactions',
                queue='app_interaction_notifications',
                routing_key='*.#'  # Match all agent messages
            )

            logger.info("✅ App interaction consumer queues configured")

            # Start consuming in a background task
            self.consumer_task = asyncio.create_task(self._consume_loop())
            logger.info("✅ App interaction consumer started")

        except Exception as e:
            logger.error(f"Failed to start app interaction consumer: {e}")
            self.running = False

    async def _consume_loop(self):
        """Main consume loop - polls RabbitMQ and forwards to WebSockets"""
        logger.info("App interaction consumer loop started")

        while self.running:
            try:
                # Get a message from the queue
                method_frame, properties, body = self.channel.basic_get(
                    queue='app_interaction_notifications',
                    auto_ack=False
                )

                if method_frame:
                    try:
                        # Parse the notification
                        notification = json.loads(body.decode('utf-8'))
                        logger.info(f"📬 Received app interaction notification: {notification.get('action', 'unknown')}")

                        # Extract agent_id from routing key (e.g., "calendar_agent.event_created")
                        routing_key = method_frame.routing_key
                        agent_id = routing_key.split('.')[0] if '.' in routing_key else None

                        # Forward to WebSocket clients
                        await app_interaction_manager.broadcast_notification(
                            notification=notification,
                            agent_id=agent_id
                        )

                        # Acknowledge the message
                        self.channel.basic_ack(delivery_tag=method_frame.delivery_tag)
                        logger.debug(f"✅ App interaction notification forwarded and acknowledged")

                    except Exception as e:
                        logger.error(f"Error processing app interaction notification: {e}")
                        # Reject and requeue the message
                        self.channel.basic_nack(
                            delivery_tag=method_frame.delivery_tag,
                            requeue=True
                        )
                else:
                    # No messages available, sleep briefly
                    await asyncio.sleep(0.1)

            except Exception as e:
                logger.error(f"Error in app interaction consume loop: {e}")
                await asyncio.sleep(1)

        logger.info("App interaction consumer loop stopped")

    async def stop(self):
        """Stop consuming app interaction notifications"""
        logger.info("Stopping app interaction consumer...")
        self.running = False

        if self.consumer_task:
            self.consumer_task.cancel()
            try:
                await self.consumer_task
            except asyncio.CancelledError:
                pass

        # Close RabbitMQ connection
        if self.channel:
            try:
                self.channel.close()
            except:
                pass

        if self.connection:
            try:
                self.connection.close()
            except:
                pass

        logger.info("✅ App interaction consumer stopped")


# Global instance (will be initialized in main.py)
app_interaction_consumer: Optional[AppInteractionConsumer] = None


def initialize_consumer(rabbitmq_url: str):
    """Initialize the global app interaction consumer"""
    global app_interaction_consumer
    app_interaction_consumer = AppInteractionConsumer(rabbitmq_url)
    return app_interaction_consumer
