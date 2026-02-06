"""
WebSocket Connection Manager for VOS API Gateway.

Manages active WebSocket connections, routes notifications to clients,
and handles connection lifecycle with JWT authentication.
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, Set, Optional
from uuid import UUID

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.schemas_notifications import FrontendNotification, WebSocketMessage

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Manages WebSocket connections for real-time frontend notifications.

    Features:
    - Session-based connection tracking
    - Multiple connections per session support
    - Automatic cleanup on disconnect
    - Pending notification delivery on connect
    """

    def __init__(self):
        # Map of session_id -> Set[WebSocket]
        # Supports multiple connections per session (multiple browser tabs)
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        self._lock = asyncio.Lock()

        logger.info("WebSocket Connection Manager initialized")

    async def connect(self, websocket: WebSocket, session_id: str) -> bool:
        """
        Accept and register a new WebSocket connection.

        Args:
            websocket: The WebSocket connection
            session_id: The conversation session ID

        Returns:
            True if connection was successful
        """
        try:
            await websocket.accept()

            async with self._lock:
                if session_id not in self.active_connections:
                    self.active_connections[session_id] = set()

                self.active_connections[session_id].add(websocket)

            logger.info(f"✅ WebSocket connected for session {session_id} "
                       f"(total connections: {len(self.active_connections[session_id])})")

            # Send connection confirmation
            await self._send_to_websocket(websocket, {
                "type": "connected",
                "data": {
                    "session_id": session_id,
                    "timestamp": datetime.utcnow().isoformat()
                }
            })

            return True

        except Exception as e:
            logger.error(f"Failed to accept WebSocket connection: {e}")
            return False

    async def disconnect(self, websocket: WebSocket, session_id: str):
        """
        Remove a WebSocket connection and clean up.

        Args:
            websocket: The WebSocket connection to remove
            session_id: The conversation session ID
        """
        async with self._lock:
            if session_id in self.active_connections:
                self.active_connections[session_id].discard(websocket)

                # Clean up empty session sets
                if not self.active_connections[session_id]:
                    del self.active_connections[session_id]
                    logger.info(f"🔌 All connections closed for session {session_id}")
                else:
                    logger.info(f"🔌 WebSocket disconnected for session {session_id} "
                              f"(remaining: {len(self.active_connections[session_id])})")

    async def send_notification(self, session_id: str, notification: FrontendNotification) -> int:
        """
        Send a notification to all active connections for a session.

        Args:
            session_id: The target session ID
            notification: The notification to send

        Returns:
            Number of successful deliveries
        """
        if session_id not in self.active_connections:
            logger.debug(f"No active connections for session {session_id}")
            return 0

        # Get connections (make a copy to avoid modification during iteration)
        async with self._lock:
            connections = list(self.active_connections.get(session_id, []))

        if not connections:
            return 0

        # Prepare message
        message = WebSocketMessage(
            type="notification",
            data=notification.model_dump(mode='json')
        )

        # Send to all connections
        successful = 0
        failed_connections = []

        for websocket in connections:
            try:
                await self._send_to_websocket(websocket, message.model_dump(mode='json'))
                successful += 1
            except Exception as e:
                logger.warning(f"Failed to send to WebSocket: {e}")
                failed_connections.append(websocket)

        # Clean up failed connections
        if failed_connections:
            async with self._lock:
                for ws in failed_connections:
                    if session_id in self.active_connections:
                        self.active_connections[session_id].discard(ws)

        logger.info(f"📤 Sent notification to {successful}/{len(connections)} "
                   f"connections for session {session_id}")

        return successful

    async def send_raw_to_session(self, session_id: str, message: dict) -> int:
        """
        Send a raw message dict to all connections for a session.

        Args:
            session_id: The target session ID
            message: Raw message dictionary to send

        Returns:
            Number of successful deliveries
        """
        if session_id not in self.active_connections:
            logger.debug(f"No active connections for session {session_id}")
            return 0

        async with self._lock:
            connections = list(self.active_connections.get(session_id, []))

        if not connections:
            return 0

        successful = 0
        for websocket in connections:
            try:
                await self._send_to_websocket(websocket, message)
                successful += 1
            except Exception as e:
                logger.warning(f"Failed to send raw message to WebSocket: {e}")

        logger.info(f"📤 Sent raw message to {successful}/{len(connections)} "
                   f"connections for session {session_id}")

        return successful

    async def broadcast_notification(self, notification: FrontendNotification) -> int:
        """
        Broadcast a notification to all active sessions.

        Useful for system-wide alerts.

        Args:
            notification: The notification to broadcast

        Returns:
            Total number of successful deliveries
        """
        total_sent = 0

        async with self._lock:
            session_ids = list(self.active_connections.keys())

        for session_id in session_ids:
            sent = await self.send_notification(session_id, notification)
            total_sent += sent

        logger.info(f"📢 Broadcast notification to {len(session_ids)} sessions "
                   f"({total_sent} total connections)")

        return total_sent

    async def send_ping(self, session_id: str):
        """
        Send a ping message to keep connection alive.

        Args:
            session_id: The target session ID
        """
        if session_id not in self.active_connections:
            return

        async with self._lock:
            connections = list(self.active_connections.get(session_id, []))

        for websocket in connections:
            try:
                await self._send_to_websocket(websocket, {
                    "type": "ping",
                    "timestamp": datetime.utcnow().isoformat()
                })
            except Exception:
                pass  # Ignore ping failures

    async def _send_to_websocket(self, websocket: WebSocket, message: dict):
        """
        Send a message to a specific WebSocket.

        Args:
            websocket: The WebSocket connection
            message: The message dictionary to send
        """
        await websocket.send_text(json.dumps(message))

    def get_active_sessions(self) -> Set[str]:
        """
        Get set of all active session IDs.

        Returns:
            Set of session IDs with active connections
        """
        return set(self.active_connections.keys())

    def get_connection_count(self, session_id: str) -> int:
        """
        Get number of active connections for a session.

        Args:
            session_id: The session ID to check

        Returns:
            Number of active connections
        """
        return len(self.active_connections.get(session_id, set()))

    def get_total_connections(self) -> int:
        """
        Get total number of active WebSocket connections.

        Returns:
            Total connection count across all sessions
        """
        return sum(len(conns) for conns in self.active_connections.values())


# Global connection manager instance
connection_manager = ConnectionManager()
