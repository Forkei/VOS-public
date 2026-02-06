"""
Session Manager
Tracks active voice sessions and provides session lookup
"""

import logging
from typing import Dict, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages active voice sessions"""

    def __init__(self, session_timeout: int = 300):
        """
        Initialize session manager

        Args:
            session_timeout: Session timeout in seconds (default 5 minutes)
        """
        self.sessions: Dict[str, 'VoiceSession'] = {}
        self.session_timeout = session_timeout

    def add_session(self, session_id: str, session: 'VoiceSession'):
        """
        Add a voice session

        Args:
            session_id: Session ID
            session: VoiceSession instance
        """
        self.sessions[session_id] = session
        logger.info(f"Added session {session_id}. Total active sessions: {len(self.sessions)}")

    def get_session(self, session_id: str) -> Optional['VoiceSession']:
        """
        Get a voice session by ID

        Args:
            session_id: Session ID

        Returns:
            VoiceSession or None if not found
        """
        return self.sessions.get(session_id)

    def remove_session(self, session_id: str):
        """
        Remove a voice session

        Args:
            session_id: Session ID
        """
        if session_id in self.sessions:
            del self.sessions[session_id]
            logger.info(f"Removed session {session_id}. Total active sessions: {len(self.sessions)}")

    def get_active_session_count(self) -> int:
        """Get number of active sessions"""
        return len(self.sessions)
