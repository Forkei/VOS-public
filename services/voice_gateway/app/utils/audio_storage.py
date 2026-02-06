"""
Audio Storage Utility
Handles saving and retrieving voice message audio files
"""

import os
import logging
from pathlib import Path
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class AudioStorage:
    """Manages audio file storage for voice messages"""

    def __init__(self, base_path: str = "/shared/audio_files"):
        """
        Initialize audio storage

        Args:
            base_path: Base directory for storing audio files
        """
        self.base_path = Path(base_path)
        self.user_recordings_path = self.base_path / "user_recordings"
        self.agent_responses_path = self.base_path / "agent_responses"
        self._ensure_directories_exist()

    def _ensure_directories_exist(self):
        """Create storage directories if they don't exist"""
        try:
            self.user_recordings_path.mkdir(parents=True, exist_ok=True)
            self.agent_responses_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"Audio storage initialized: {self.base_path}")
        except Exception as e:
            logger.error(f"Failed to create audio storage directories: {e}")
            raise

    def save_user_recording(
        self,
        audio_data: bytes,
        session_id: str,
        voice_message_id: int,
        format: str = "webm"
    ) -> str:
        """
        Save user voice recording

        Args:
            audio_data: Audio bytes
            session_id: Session ID
            voice_message_id: Voice message ID from database
            format: Audio format (webm, wav, etc.)

        Returns:
            Relative file path for database storage
        """
        try:
            # Create session directory
            session_dir = self.user_recordings_path / session_id
            session_dir.mkdir(exist_ok=True)

            # Generate filename: vm_{voice_message_id}.{format}
            filename = f"vm_{voice_message_id}.{format}"
            file_path = session_dir / filename

            # Write audio file
            with open(file_path, 'wb') as f:
                f.write(audio_data)

            # Return relative path
            relative_path = f"user_recordings/{session_id}/{filename}"

            logger.info(f"Saved user recording: {relative_path} ({len(audio_data)} bytes)")

            return relative_path

        except Exception as e:
            logger.error(f"Error saving user recording: {e}")
            raise

    def save_agent_response(
        self,
        audio_data: bytes,
        session_id: str,
        voice_message_id: int,
        format: str = "mp3"
    ) -> str:
        """
        Save agent TTS response

        Args:
            audio_data: Audio bytes
            session_id: Session ID
            voice_message_id: Voice message ID from database
            format: Audio format (mp3, wav, etc.)

        Returns:
            Relative file path for database storage
        """
        try:
            # Create session directory
            session_dir = self.agent_responses_path / session_id
            session_dir.mkdir(exist_ok=True)

            # Generate filename: vm_{voice_message_id}.{format}
            filename = f"vm_{voice_message_id}.{format}"
            file_path = session_dir / filename

            # Write audio file
            with open(file_path, 'wb') as f:
                f.write(audio_data)

            # Return relative path
            relative_path = f"agent_responses/{session_id}/{filename}"

            logger.info(f"Saved agent response: {relative_path} ({len(audio_data)} bytes)")

            return relative_path

        except Exception as e:
            logger.error(f"Error saving agent response: {e}")
            raise

    def get_audio_file(self, audio_file_path: str) -> Optional[bytes]:
        """
        Retrieve audio file by relative path

        Args:
            audio_file_path: Relative path (e.g., "agent_responses/session_123/vm_456.mp3")

        Returns:
            Audio bytes or None if not found
        """
        try:
            full_path = self.base_path / audio_file_path

            if full_path.exists():
                with open(full_path, 'rb') as f:
                    return f.read()
            else:
                logger.warning(f"Audio file not found: {full_path}")
                return None

        except Exception as e:
            logger.error(f"Error reading audio file: {e}")
            return None

    def delete_session_audio(self, session_id: str):
        """
        Delete all audio files for a session

        Args:
            session_id: Session ID
        """
        try:
            # Delete user recordings
            user_session_dir = self.user_recordings_path / session_id
            if user_session_dir.exists():
                import shutil
                shutil.rmtree(user_session_dir)

            # Delete agent responses
            agent_session_dir = self.agent_responses_path / session_id
            if agent_session_dir.exists():
                import shutil
                shutil.rmtree(agent_session_dir)

            logger.info(f"Deleted audio files for session {session_id}")

        except Exception as e:
            logger.error(f"Error deleting session audio: {e}")

    def cleanup_old_files(self, days: int = 30):
        """
        Delete audio files older than specified days

        Args:
            days: Age threshold in days
        """
        try:
            cutoff_time = datetime.now().timestamp() - (days * 24 * 60 * 60)
            deleted_count = 0

            # Cleanup user recordings
            for session_dir in self.user_recordings_path.iterdir():
                if not session_dir.is_dir():
                    continue

                if session_dir.stat().st_mtime < cutoff_time:
                    import shutil
                    shutil.rmtree(session_dir)
                    deleted_count += 1

            # Cleanup agent responses
            for session_dir in self.agent_responses_path.iterdir():
                if not session_dir.is_dir():
                    continue

                if session_dir.stat().st_mtime < cutoff_time:
                    import shutil
                    shutil.rmtree(session_dir)
                    deleted_count += 1

            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} old session directories")

        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
