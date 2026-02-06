"""
App Interaction WebSocket Manager

Manages WebSocket connections for app interaction notifications
(calendar, reminders, timers, etc.) and forwards messages from agents.
"""

import asyncio
import logging
from typing import Dict, Set, Optional
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class AppInteractionManager:
    """Manages WebSocket connections for app interaction notifications"""

    def __init__(self):
        # agent_id -> Set[WebSocket]
        self.connections: Dict[str, Set[WebSocket]] = {}
        # Track all connections regardless of agent
        self.all_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket, agent_id: Optional[str] = None):
        """
        Register a new WebSocket connection for app interactions.

        Args:
            websocket: WebSocket connection
            agent_id: Optional agent ID to filter notifications
        """
        # Add to all connections
        self.all_connections.add(websocket)

        # Add to agent-specific connections if agent_id provided
        if agent_id:
            if agent_id not in self.connections:
                self.connections[agent_id] = set()
            self.connections[agent_id].add(websocket)
            logger.info(f"📱 App interaction connection registered for agent: {agent_id}")
        else:
            logger.info(f"📱 App interaction connection registered (all agents)")

    async def disconnect(self, websocket: WebSocket, agent_id: Optional[str] = None):
        """
        Remove a WebSocket connection.

        Args:
            websocket: WebSocket connection to remove
            agent_id: Optional agent ID
        """
        # Remove from all connections
        self.all_connections.discard(websocket)

        # Remove from agent-specific connections
        if agent_id and agent_id in self.connections:
            self.connections[agent_id].discard(websocket)
            if not self.connections[agent_id]:
                del self.connections[agent_id]
            logger.info(f"📱 App interaction connection removed for agent: {agent_id}")
        else:
            # Remove from all agent connections
            for connections in self.connections.values():
                connections.discard(websocket)
            logger.info(f"📱 App interaction connection removed (all agents)")

    async def broadcast_notification(self, notification: dict, agent_id: Optional[str] = None):
        """
        Broadcast a notification to relevant WebSocket connections.

        Args:
            notification: Notification data to send
            agent_id: Optional agent ID - if provided, only send to connections subscribed to that agent
        """
        import json

        message = json.dumps(notification)

        # Determine which connections to send to
        target_connections = set()

        if agent_id and agent_id in self.connections:
            # Send to connections subscribed to this specific agent
            target_connections = self.connections[agent_id].copy()
        else:
            # Send to all connections if no agent_id specified
            target_connections = self.all_connections.copy()

        if not target_connections:
            logger.debug(f"No connections to broadcast notification for agent: {agent_id or 'all'}")
            return

        # Send to all target connections
        disconnected = []
        for connection in target_connections:
            try:
                await connection.send_text(message)
                logger.debug(f"📤 Sent app interaction notification to client")
            except Exception as e:
                logger.error(f"Failed to send notification: {e}")
                disconnected.append(connection)

        # Clean up disconnected clients
        for connection in disconnected:
            await self.disconnect(connection, agent_id)

    def get_connection_count(self, agent_id: Optional[str] = None) -> int:
        """
        Get the number of active connections.

        Args:
            agent_id: Optional agent ID to count connections for

        Returns:
            Number of active connections
        """
        if agent_id and agent_id in self.connections:
            return len(self.connections[agent_id])
        return len(self.all_connections)


# Global instance
app_interaction_manager = AppInteractionManager()
