"""
Database Client for Voice Gateway
Handles voice session and interaction tracking in PostgreSQL
"""

import json
import logging
from typing import Optional
from datetime import datetime
from uuid import UUID, uuid4
import asyncpg

from ..config import settings

logger = logging.getLogger(__name__)


class DatabaseClient:
    """Async PostgreSQL client for voice session tracking"""

    def __init__(self):
        """Initialize database client"""
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        """Establish connection pool to PostgreSQL"""
        try:
            logger.info(f"Connecting to PostgreSQL: {settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}")

            self.pool = await asyncpg.create_pool(
                host=settings.POSTGRES_HOST,
                port=settings.POSTGRES_PORT,
                user=settings.POSTGRES_USER,
                password=settings.POSTGRES_PASSWORD,
                database=settings.POSTGRES_DB,
                min_size=2,
                max_size=10
            )

            logger.info("PostgreSQL connection pool established")

        except Exception as e:
            logger.error(f"Failed to connect to PostgreSQL: {e}")
            raise

    async def create_voice_session(
        self,
        session_id: str,
        platform: str = "web",
        audio_format: dict = None,
        connection_id: str = None,
        client_ip: str = None,
        user_agent: str = None
    ) -> UUID:
        """
        Create a new voice session record

        Args:
            session_id: User session ID
            platform: Platform (web, ios, android, desktop)
            audio_format: Audio format details
            connection_id: WebSocket connection ID
            client_ip: Client IP address
            user_agent: Client user agent

        Returns:
            voice_session_id (UUID)
        """
        try:
            query = """
            INSERT INTO voice_sessions (
                session_id, platform, audio_format, connection_id,
                client_ip, user_agent, status
            )
            VALUES ($1, $2, $3, $4, $5, $6, 'active')
            RETURNING id
            """

            async with self.pool.acquire() as conn:
                voice_session_id = await conn.fetchval(
                    query,
                    session_id,
                    platform,
                    json.dumps(audio_format or {}),
                    connection_id,
                    client_ip,
                    user_agent
                )

            logger.info(f"Created voice session: {voice_session_id} for session {session_id}")

            return voice_session_id

        except Exception as e:
            logger.error(f"Error creating voice session: {e}")
            raise

    async def end_voice_session(
        self,
        voice_session_id: UUID,
        status: str = "completed"
    ):
        """
        End a voice session and update statistics

        Args:
            voice_session_id: Voice session UUID
            status: Final status (completed, error, disconnected)
        """
        try:
            query = """
            UPDATE voice_sessions
            SET
                ended_at = NOW(),
                duration_seconds = EXTRACT(EPOCH FROM (NOW() - started_at))::INTEGER,
                status = $1,
                updated_at = NOW()
            WHERE id = $2
            """

            async with self.pool.acquire() as conn:
                await conn.execute(query, status, voice_session_id)

            logger.info(f"Ended voice session {voice_session_id} with status {status}")

        except Exception as e:
            logger.error(f"Error ending voice session: {e}")

    async def create_voice_interaction(
        self,
        voice_session_id: UUID,
        session_id: str,
        transcription: str = None,
        transcription_confidence: float = None
    ) -> UUID:
        """
        Create a new voice interaction (question/answer pair)

        Args:
            voice_session_id: Parent voice session UUID
            session_id: User session ID
            transcription: User's transcribed speech
            transcription_confidence: Confidence score

        Returns:
            interaction_id (UUID)
        """
        try:
            query = """
            INSERT INTO voice_interactions (
                voice_session_id, session_id, transcription,
                transcription_confidence
            )
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """

            async with self.pool.acquire() as conn:
                interaction_id = await conn.fetchval(
                    query,
                    voice_session_id,
                    session_id,
                    transcription,
                    transcription_confidence
                )

            logger.debug(f"Created voice interaction: {interaction_id}")

            return interaction_id

        except Exception as e:
            logger.error(f"Error creating voice interaction: {e}")
            raise

    async def update_voice_interaction(
        self,
        interaction_id: UUID,
        response_text: str = None,
        response_audio_duration_ms: int = None,
        agent_response_time_ms: int = None,
        tts_generation_time_ms: int = None,
        tts_voice_id: str = None,
        audio_file_path: str = None,
        completed_at: datetime = None
    ):
        """
        Update voice interaction with response details

        Args:
            interaction_id: Interaction UUID
            response_text: Agent's response text
            response_audio_duration_ms: Duration of TTS audio
            agent_response_time_ms: Time agent took to respond
            tts_generation_time_ms: Time to generate TTS
            tts_voice_id: Voice ID used for TTS
            completed_at: Completion timestamp
        """
        try:
            # Build dynamic update query
            updates = []
            params = []
            param_idx = 1

            if response_text is not None:
                updates.append(f"response_text = ${param_idx}")
                params.append(response_text)
                param_idx += 1

            if response_audio_duration_ms is not None:
                updates.append(f"response_audio_duration_ms = ${param_idx}")
                params.append(response_audio_duration_ms)
                param_idx += 1

            if agent_response_time_ms is not None:
                updates.append(f"agent_response_time_ms = ${param_idx}")
                params.append(agent_response_time_ms)
                param_idx += 1

            if tts_generation_time_ms is not None:
                updates.append(f"tts_generation_time_ms = ${param_idx}")
                params.append(tts_generation_time_ms)
                param_idx += 1

            if tts_voice_id is not None:
                updates.append(f"tts_voice_id = ${param_idx}")
                params.append(tts_voice_id)
                param_idx += 1

            if audio_file_path is not None:
                updates.append(f"audio_file_path = ${param_idx}")
                params.append(audio_file_path)
                param_idx += 1

            if completed_at is not None:
                updates.append(f"completed_at = ${param_idx}")
                params.append(completed_at)
                param_idx += 1

            if not updates:
                return  # Nothing to update

            # Add interaction_id as last parameter
            params.append(interaction_id)

            query = f"""
            UPDATE voice_interactions
            SET {', '.join(updates)}
            WHERE id = ${param_idx}
            """

            async with self.pool.acquire() as conn:
                await conn.execute(query, *params)

            logger.debug(f"Updated voice interaction: {interaction_id}")

        except Exception as e:
            logger.error(f"Error updating voice interaction: {e}")

    async def mark_interaction_interrupted(
        self,
        interaction_id: UUID,
        interruption_at_ms: int = 0
    ):
        """
        Mark interaction as interrupted by user

        Args:
            interaction_id: Interaction UUID
            interruption_at_ms: Position in audio where interrupted
        """
        try:
            query = """
            UPDATE voice_interactions
            SET
                was_interrupted = TRUE,
                interruption_at_ms = $1,
                completed_at = NOW()
            WHERE id = $2
            """

            async with self.pool.acquire() as conn:
                await conn.execute(query, interruption_at_ms, interaction_id)

            logger.debug(f"Marked interaction {interaction_id} as interrupted")

        except Exception as e:
            logger.error(f"Error marking interaction as interrupted: {e}")

    async def update_session_stats(
        self,
        voice_session_id: UUID,
        audio_duration_seconds: int = 0,
        transcribed_words: int = 0,
        tts_characters: int = 0
    ):
        """
        Update session usage statistics

        Args:
            voice_session_id: Voice session UUID
            audio_duration_seconds: Audio duration to add
            transcribed_words: Word count to add
            tts_characters: Character count to add
        """
        try:
            query = """
            UPDATE voice_sessions
            SET
                total_audio_duration_seconds = total_audio_duration_seconds + $1,
                total_transcribed_words = total_transcribed_words + $2,
                total_tts_characters = total_tts_characters + $3,
                updated_at = NOW()
            WHERE id = $4
            """

            async with self.pool.acquire() as conn:
                await conn.execute(
                    query,
                    audio_duration_seconds,
                    transcribed_words,
                    tts_characters,
                    voice_session_id
                )

        except Exception as e:
            logger.error(f"Error updating session stats: {e}")

    async def create_voice_message(
        self,
        session_id: str,
        transcript: str,
        role: str,
        audio_format: str = None,
        duration_ms: int = None,
        audio_size_bytes: int = None,
        metadata: dict = None
    ) -> int:
        """
        Create a voice message record (without audio file initially)

        Args:
            session_id: Session ID
            transcript: Text transcript of the voice message
            role: 'user' or 'agent'
            audio_format: Audio format (mp3, webm, etc.)
            duration_ms: Audio duration in milliseconds
            audio_size_bytes: File size in bytes
            metadata: Additional metadata (confidence, voice_id, etc.)

        Returns:
            voice_message_id (int)
        """
        try:
            query = """
            INSERT INTO voice_messages (
                session_id, audio_file_path, transcript, role,
                duration_ms, audio_size_bytes, audio_format, metadata
            )
            VALUES ($1, NULL, $2, $3, $4, $5, $6, $7)
            RETURNING id
            """

            async with self.pool.acquire() as conn:
                voice_message_id = await conn.fetchval(
                    query,
                    session_id,
                    transcript,
                    role,
                    duration_ms,
                    audio_size_bytes,
                    audio_format,
                    json.dumps(metadata or {})
                )

            logger.info(f"Created voice message: {voice_message_id} for session {session_id}")

            return voice_message_id

        except Exception as e:
            logger.error(f"Error creating voice message: {e}")
            raise

    async def update_voice_message_audio(
        self,
        voice_message_id: int,
        audio_file_path: str,
        duration_ms: int = None,
        audio_size_bytes: int = None
    ):
        """
        Update voice message with audio file path after file is saved

        Args:
            voice_message_id: Voice message ID
            audio_file_path: Relative path to audio file
            duration_ms: Audio duration in milliseconds
            audio_size_bytes: File size in bytes
        """
        try:
            # Build dynamic update query
            updates = ["audio_file_path = $1"]
            params = [audio_file_path]
            param_idx = 2

            if duration_ms is not None:
                updates.append(f"duration_ms = ${param_idx}")
                params.append(duration_ms)
                param_idx += 1

            if audio_size_bytes is not None:
                updates.append(f"audio_size_bytes = ${param_idx}")
                params.append(audio_size_bytes)
                param_idx += 1

            params.append(voice_message_id)

            query = f"""
            UPDATE voice_messages
            SET {', '.join(updates)}
            WHERE id = ${param_idx}
            """

            async with self.pool.acquire() as conn:
                await conn.execute(query, *params)

            logger.debug(f"Updated voice message {voice_message_id} with audio path: {audio_file_path}")

        except Exception as e:
            logger.error(f"Error updating voice message audio: {e}")

    async def store_conversation_message(
        self,
        session_id: str,
        sender_type: str,
        sender_id: str,
        content: str,
        voice_metadata: dict = None,
        voice_message_id: int = None
    ) -> int:
        """
        Store message in conversation_messages table

        Args:
            session_id: Session ID
            sender_type: 'user' or 'agent'
            sender_id: ID of sender
            content: Message content
            voice_metadata: Voice-specific metadata
            voice_message_id: Reference to voice_messages table (if voice message)

        Returns:
            conversation_message_id (int)
        """
        try:
            input_mode = 'voice' if voice_message_id else 'text'

            query = """
            INSERT INTO conversation_messages (
                session_id, sender_type, sender_id, content,
                input_mode, voice_metadata, voice_message_id
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
            """

            async with self.pool.acquire() as conn:
                conversation_message_id = await conn.fetchval(
                    query,
                    session_id,
                    sender_type,
                    sender_id,
                    content,
                    input_mode,
                    json.dumps(voice_metadata or {}),
                    voice_message_id
                )

            logger.debug(f"Stored conversation message {conversation_message_id} for session {session_id}")

            return conversation_message_id

        except Exception as e:
            logger.error(f"Error storing conversation message: {e}")
            raise

    async def close(self):
        """Close database connection pool"""
        if self.pool:
            await self.pool.close()
            logger.info("PostgreSQL connection pool closed")
