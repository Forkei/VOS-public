import json
import logging
import threading
from typing import Any, Dict

import pika
from pika.exceptions import AMQPConnectionError, AMQPChannelError

logger = logging.getLogger(__name__)


class RabbitMQClient:
    def __init__(self, rabbitmq_url: str):
        self.rabbitmq_url = rabbitmq_url
        self.connection_params = None
        self._lock = threading.Lock()
    
    def connect(self) -> bool:
        """Parse and store connection parameters."""
        try:
            # Parse the connection URL and store parameters
            self.connection_params = pika.URLParameters(self.rabbitmq_url)
            
            logger.info("RabbitMQ connection parameters initialized")
            return True
            
        except Exception as e:
            logger.error(f"Failed to parse RabbitMQ URL: {e}")
            return False
    
    def publish_message(self, queue_name: str, message: Dict[str, Any]) -> bool:
        """Publish a message to the specified queue with thread-safe connection."""
        if not self.connection_params:
            logger.error("Connection parameters not initialized. Call connect() first.")
            return False
        
        # Use thread lock to ensure thread safety
        with self._lock:
            connection = None
            channel = None
            
            try:
                # Create new connection and channel for this operation
                connection = pika.BlockingConnection(self.connection_params)
                channel = connection.channel()
                
                # Declare the queue to ensure it exists
                channel.queue_declare(queue=queue_name, durable=True)
                
                # Convert message to JSON
                message_body = json.dumps(message)
                
                # Publish the message
                channel.basic_publish(
                    exchange='',
                    routing_key=queue_name,
                    body=message_body,
                    properties=pika.BasicProperties(
                        delivery_mode=2,  # Make message persistent
                    )
                )
                
                logger.info(f"Message published to queue '{queue_name}': {message}")
                return True
                
            except AMQPChannelError as e:
                logger.error(f"Channel error while publishing message: {e}")
                return False
            except AMQPConnectionError as e:
                logger.error(f"Connection error while publishing message: {e}")
                return False
            except Exception as e:
                logger.error(f"Unexpected error publishing message: {e}")
                return False
            finally:
                # Clean up resources
                try:
                    if channel and not channel.is_closed:
                        channel.close()
                    if connection and not connection.is_closed:
                        connection.close()
                except Exception as e:
                    logger.warning(f"Error closing connection/channel: {e}")
    
    def close(self):
        """No persistent connections to close in this implementation."""
        logger.info("RabbitMQ client closed (no persistent connections)")
    
    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()