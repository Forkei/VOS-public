"""
RabbitMQ client for scheduler service.
Handles message publishing to agents and frontend.
"""

import pika
import json
import os
from typing import Dict, Any


class RabbitMQClient:
    def __init__(self):
        self.host = os.getenv('RABBITMQ_HOST', 'rabbitmq')
        self.port = int(os.getenv('RABBITMQ_PORT', 5672))
        self.user = os.getenv('RABBITMQ_USER', 'vos_user')
        self.password = os.getenv('RABBITMQ_PASSWORD')
        self.vhost = os.getenv('RABBITMQ_VHOST', 'vos_vhost')

        self.connection = None
        self.channel = None
        self.connect()

    def connect(self):
        """Establish RabbitMQ connection"""
        try:
            credentials = pika.PlainCredentials(self.user, self.password)
            parameters = pika.ConnectionParameters(
                host=self.host,
                port=self.port,
                virtual_host=self.vhost,
                credentials=credentials,
                heartbeat=600,
                blocked_connection_timeout=300
            )
            self.connection = pika.BlockingConnection(parameters)
            self.channel = self.connection.channel()
            print(f"✓ Connected to RabbitMQ: {self.host}:{self.port}")
        except Exception as e:
            print(f"✗ RabbitMQ connection failed: {e}")
            raise

    def ensure_connection(self):
        """Ensure RabbitMQ connection is alive"""
        if self.connection is None or self.connection.is_closed:
            self.connect()

    def publish_to_agent(self, agent_id: str, message: Dict[Any, Any]):
        """Publish message to an agent's queue"""
        self.ensure_connection()

        queue_name = f"{agent_id}_queue"

        try:
            # Declare queue (idempotent)
            self.channel.queue_declare(queue=queue_name, durable=True)

            # Publish message
            self.channel.basic_publish(
                exchange='',
                routing_key=queue_name,
                body=json.dumps(message),
                properties=pika.BasicProperties(
                    delivery_mode=2,  # Persistent message
                    content_type='application/json'
                )
            )
            print(f"  → Sent message to {queue_name}")
        except Exception as e:
            print(f"  ✗ Failed to publish to {queue_name}: {e}")
            raise

    def close(self):
        """Close RabbitMQ connection"""
        if self.connection and not self.connection.is_closed:
            self.connection.close()
            print("✓ RabbitMQ connection closed")
