"""
Pending Notifications Delivery System.

Handles delivery of stored notifications when clients reconnect.
"""

import json
import logging
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from app.database import DatabaseClient
from app.schemas_notifications import FrontendNotification
from app.websocket_manager import connection_manager

logger = logging.getLogger(__name__)


async def deliver_pending_notifications(session_id: str, db_client: DatabaseClient) -> int:
    """
    Deliver all pending notifications for a session via WebSocket.

    Called when a WebSocket connection is established to catch up
    on notifications that were sent while disconnected.

    Args:
        session_id: The session ID to deliver notifications for
        db_client: Database client instance

    Returns:
        Number of notifications delivered
    """
    try:
        # Fetch all pending notifications for this session
        query = """
        SELECT id, notification_id, notification_type, notification_payload, created_at
        FROM pending_notifications
        WHERE session_id = %s AND delivered_at IS NULL
        ORDER BY created_at ASC;
        """

        results = db_client.execute_query(query, (session_id,))

        if not results or len(results) == 0:
            logger.debug(f"No pending notifications for session {session_id}")
            return 0

        logger.info(f"📦 Found {len(results)} pending notification(s) for session {session_id}")

        delivered_count = 0
        failed_ids = []

        for row in results:
            db_id, notification_id, notification_type, payload_json, created_at = row

            try:
                # Parse notification from stored JSON
                notification = FrontendNotification(**payload_json)

                # Attempt delivery
                sent = await connection_manager.send_notification(session_id, notification)

                if sent > 0:
                    # Mark as delivered
                    _mark_as_delivered(db_client, notification_id)
                    delivered_count += 1
                    logger.debug(f"✅ Delivered pending notification {notification_id}")
                else:
                    logger.warning(f"Failed to deliver notification {notification_id}")
                    failed_ids.append(notification_id)

            except Exception as e:
                logger.error(f"Error delivering pending notification {notification_id}: {e}")
                failed_ids.append(notification_id)

        # Update delivery attempts for failed notifications
        if failed_ids:
            _increment_delivery_attempts(db_client, failed_ids)

        logger.info(f"✅ Delivered {delivered_count}/{len(results)} pending notifications "
                   f"to session {session_id}")

        return delivered_count

    except Exception as e:
        logger.error(f"Error delivering pending notifications: {e}", exc_info=True)
        return 0


def _mark_as_delivered(db_client: DatabaseClient, notification_id: str):
    """
    Mark a notification as delivered in the database.

    Args:
        db_client: Database client instance
        notification_id: The notification ID to mark as delivered
    """
    try:
        query = """
        UPDATE pending_notifications
        SET delivered_at = %s
        WHERE notification_id = %s;
        """

        db_client.execute_query(query, (datetime.utcnow(), notification_id))

    except Exception as e:
        logger.error(f"Failed to mark notification {notification_id} as delivered: {e}")


def _increment_delivery_attempts(db_client: DatabaseClient, notification_ids: List[str]):
    """
    Increment delivery attempt counter for failed notifications.

    Args:
        db_client: Database client instance
        notification_ids: List of notification IDs that failed delivery
    """
    try:
        query = """
        UPDATE pending_notifications
        SET delivery_attempts = delivery_attempts + 1,
            last_attempt_at = %s
        WHERE notification_id = ANY(%s);
        """

        db_client.execute_query(query, (datetime.utcnow(), notification_ids))

        logger.debug(f"Incremented delivery attempts for {len(notification_ids)} notification(s)")

    except Exception as e:
        logger.error(f"Failed to increment delivery attempts: {e}")


def get_pending_count(session_id: str, db_client: DatabaseClient) -> int:
    """
    Get count of pending notifications for a session.

    Args:
        session_id: The session ID to check
        db_client: Database client instance

    Returns:
        Number of pending notifications
    """
    try:
        query = """
        SELECT COUNT(*)
        FROM pending_notifications
        WHERE session_id = %s AND delivered_at IS NULL;
        """

        result = db_client.execute_query(query, (session_id,))

        if result and len(result) > 0:
            return result[0][0]

        return 0

    except Exception as e:
        logger.error(f"Error getting pending count: {e}")
        return 0


def cleanup_old_notifications(db_client: DatabaseClient, days: int = 7) -> int:
    """
    Clean up old delivered notifications.

    Args:
        db_client: Database client instance
        days: Delete notifications older than this many days (default: 7)

    Returns:
        Number of notifications deleted
    """
    try:
        query = """
        SELECT cleanup_old_notifications();
        """

        result = db_client.execute_query(query, ())

        if result and len(result) > 0:
            deleted_count = result[0][0]
            logger.info(f"🗑️ Cleaned up {deleted_count} old notification(s)")
            return deleted_count

        return 0

    except Exception as e:
        logger.error(f"Error cleaning up old notifications: {e}")
        return 0
