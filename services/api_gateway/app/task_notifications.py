"""
Task notification helper for the VOS system.

Sends notifications to agents when tasks are created, updated, or assigned.
"""

import json
import logging
import uuid
from datetime import datetime
from typing import List, Optional

import pika

logger = logging.getLogger(__name__)


def send_task_notification(
    rabbitmq_url: str,
    agent_ids: List[str],
    event_type: str,
    task_id: int,
    task_title: str,
    task_status: str,
    created_by: Optional[str] = None,
    old_status: Optional[str] = None
) -> bool:
    """
    Send task notification to multiple agents.

    Args:
        rabbitmq_url: RabbitMQ connection URL
        agent_ids: List of agent IDs to notify
        event_type: Type of event (task_created, task_updated, task_assigned, task_unassigned)
        task_id: Task ID
        task_title: Task title
        task_status: Current task status
        created_by: Agent/user who created the task
        old_status: Previous status (for status changes)

    Returns:
        True if all notifications sent successfully, False otherwise
    """
    if not agent_ids:
        logger.debug("No agents to notify")
        return True

    try:
        connection = pika.BlockingConnection(pika.URLParameters(rabbitmq_url))
        channel = connection.channel()

        success_count = 0

        for agent_id in agent_ids:
            try:
                # Create notification payload
                notification = {
                    "notification_id": str(uuid.uuid4()),
                    "timestamp": datetime.utcnow().isoformat(),
                    "recipient_agent_id": agent_id,
                    "notification_type": "task_notification",
                    "source": "task_management_system",
                    "payload": {
                        "task_id": task_id,
                        "task_title": task_title,
                        "event_type": event_type,
                        "status": task_status,
                        "old_status": old_status,
                        "created_by": created_by
                    }
                }

                # Send to agent's queue
                queue_name = f"{agent_id}_queue"

                # Declare queue to ensure it exists
                channel.queue_declare(queue=queue_name, durable=True)

                # Publish notification
                channel.basic_publish(
                    exchange='',
                    routing_key=queue_name,
                    body=json.dumps(notification).encode(),
                    properties=pika.BasicProperties(
                        delivery_mode=2,  # Make message persistent
                        content_type='application/json'
                    )
                )

                logger.info(f"✅ Sent {event_type} notification to {agent_id} for task {task_id}")
                success_count += 1

            except Exception as e:
                logger.error(f"❌ Failed to send notification to {agent_id}: {e}")

        connection.close()

        return success_count == len(agent_ids)

    except Exception as e:
        logger.error(f"❌ Failed to send task notifications: {e}")
        return False


def send_task_created_notification(
    rabbitmq_url: str,
    assignee_ids: List[str],
    task_id: int,
    task_title: str,
    task_status: str,
    created_by: Optional[str] = None
) -> bool:
    """
    Send notification when a task is created.

    Args:
        rabbitmq_url: RabbitMQ connection URL
        assignee_ids: List of agent IDs assigned to the task
        task_id: Task ID
        task_title: Task title
        task_status: Task status
        created_by: Agent/user who created the task

    Returns:
        True if successful
    """
    return send_task_notification(
        rabbitmq_url=rabbitmq_url,
        agent_ids=assignee_ids,
        event_type="task_created",
        task_id=task_id,
        task_title=task_title,
        task_status=task_status,
        created_by=created_by
    )


def send_task_status_change_notification(
    rabbitmq_url: str,
    assignee_ids: List[str],
    task_id: int,
    task_title: str,
    old_status: str,
    new_status: str
) -> bool:
    """
    Send notification when a task's status changes.

    Args:
        rabbitmq_url: RabbitMQ connection URL
        assignee_ids: List of agent IDs assigned to the task
        task_id: Task ID
        task_title: Task title
        old_status: Previous status
        new_status: New status

    Returns:
        True if successful
    """
    return send_task_notification(
        rabbitmq_url=rabbitmq_url,
        agent_ids=assignee_ids,
        event_type="task_updated",
        task_id=task_id,
        task_title=task_title,
        task_status=new_status,
        old_status=old_status
    )


def send_task_assignment_notification(
    rabbitmq_url: str,
    agent_id: str,
    task_id: int,
    task_title: str,
    task_status: str
) -> bool:
    """
    Send notification when an agent is assigned to a task.

    Args:
        rabbitmq_url: RabbitMQ connection URL
        agent_id: Agent ID being assigned
        task_id: Task ID
        task_title: Task title
        task_status: Task status

    Returns:
        True if successful
    """
    return send_task_notification(
        rabbitmq_url=rabbitmq_url,
        agent_ids=[agent_id],
        event_type="task_assigned",
        task_id=task_id,
        task_title=task_title,
        task_status=task_status
    )


def send_task_unassignment_notification(
    rabbitmq_url: str,
    agent_id: str,
    task_id: int,
    task_title: str,
    task_status: str
) -> bool:
    """
    Send notification when an agent is unassigned from a task.

    Args:
        rabbitmq_url: RabbitMQ connection URL
        agent_id: Agent ID being unassigned
        task_id: Task ID
        task_title: Task title
        task_status: Task status

    Returns:
        True if successful
    """
    return send_task_notification(
        rabbitmq_url=rabbitmq_url,
        agent_ids=[agent_id],
        event_type="task_unassigned",
        task_id=task_id,
        task_title=task_title,
        task_status=task_status
    )
