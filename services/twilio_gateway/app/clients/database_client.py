"""
Database Client for Twilio Gateway
Handles phone number whitelist and call tracking in PostgreSQL
"""

import json
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID
import asyncpg

from ..config import settings

logger = logging.getLogger(__name__)


class DatabaseClient:
    """Async PostgreSQL client for Twilio phone call tracking"""

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

    async def is_phone_number_allowed(self, phone_number: str) -> bool:
        """
        Check if a phone number is in the allowed whitelist.

        Args:
            phone_number: Phone number in E.164 format (+1234567890)

        Returns:
            True if allowed, False otherwise
        """
        try:
            query = """
            SELECT EXISTS(
                SELECT 1 FROM allowed_phone_numbers
                WHERE phone_number = $1 AND is_active = true
            )
            """

            async with self.pool.acquire() as conn:
                is_allowed = await conn.fetchval(query, phone_number)

            logger.info(f"Phone number {phone_number} allowed: {is_allowed}")
            return is_allowed

        except Exception as e:
            logger.error(f"Error checking phone number whitelist: {e}")
            # Default to rejecting on error for security
            return False

    async def get_allowed_number_info(self, phone_number: str) -> Optional[Dict[str, Any]]:
        """
        Get details about an allowed phone number.

        Args:
            phone_number: Phone number in E.164 format

        Returns:
            Dict with number info or None if not found/inactive
        """
        try:
            query = """
            SELECT id, phone_number, display_name, user_id, metadata, created_at
            FROM allowed_phone_numbers
            WHERE phone_number = $1 AND is_active = true
            """

            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(query, phone_number)

            if row:
                return {
                    "id": row["id"],
                    "phone_number": row["phone_number"],
                    "display_name": row["display_name"],
                    "user_id": str(row["user_id"]) if row["user_id"] else None,
                    "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None
                }

            return None

        except Exception as e:
            logger.error(f"Error getting phone number info: {e}")
            return None

    async def get_all_allowed_numbers(self, active_only: bool = True) -> List[Dict[str, Any]]:
        """
        Get all allowed phone numbers.

        Args:
            active_only: Only return active numbers

        Returns:
            List of phone number records
        """
        try:
            if active_only:
                query = """
                SELECT id, phone_number, display_name, user_id, is_active, metadata, created_at
                FROM allowed_phone_numbers
                WHERE is_active = true
                ORDER BY created_at DESC
                """
            else:
                query = """
                SELECT id, phone_number, display_name, user_id, is_active, metadata, created_at
                FROM allowed_phone_numbers
                ORDER BY created_at DESC
                """

            async with self.pool.acquire() as conn:
                rows = await conn.fetch(query)

            return [
                {
                    "id": row["id"],
                    "phone_number": row["phone_number"],
                    "display_name": row["display_name"],
                    "user_id": str(row["user_id"]) if row["user_id"] else None,
                    "is_active": row["is_active"],
                    "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None
                }
                for row in rows
            ]

        except Exception as e:
            logger.error(f"Error getting allowed numbers: {e}")
            return []

    async def add_allowed_number(
        self,
        phone_number: str,
        display_name: Optional[str] = None,
        user_id: Optional[int] = None,
        metadata: Optional[Dict] = None
    ) -> int:
        """
        Add a phone number to the whitelist.

        Args:
            phone_number: Phone number in E.164 format
            display_name: Optional display name
            user_id: Optional linked VOS user ID
            metadata: Optional additional metadata

        Returns:
            ID of the created record
        """
        try:
            query = """
            INSERT INTO allowed_phone_numbers (phone_number, display_name, user_id, metadata)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (phone_number) DO UPDATE SET
                display_name = COALESCE(EXCLUDED.display_name, allowed_phone_numbers.display_name),
                user_id = COALESCE(EXCLUDED.user_id, allowed_phone_numbers.user_id),
                metadata = COALESCE(EXCLUDED.metadata, allowed_phone_numbers.metadata),
                is_active = true
            RETURNING id
            """

            async with self.pool.acquire() as conn:
                record_id = await conn.fetchval(
                    query,
                    phone_number,
                    display_name,
                    user_id,
                    json.dumps(metadata or {})
                )

            logger.info(f"Added allowed phone number: {phone_number} (id={record_id})")
            return record_id

        except Exception as e:
            logger.error(f"Error adding allowed number: {e}")
            raise

    async def remove_allowed_number(self, phone_number: str) -> bool:
        """
        Remove (deactivate) a phone number from the whitelist.

        Args:
            phone_number: Phone number in E.164 format

        Returns:
            True if number was found and deactivated
        """
        try:
            query = """
            UPDATE allowed_phone_numbers
            SET is_active = false
            WHERE phone_number = $1
            RETURNING id
            """

            async with self.pool.acquire() as conn:
                result = await conn.fetchval(query, phone_number)

            if result:
                logger.info(f"Removed allowed phone number: {phone_number}")
                return True
            return False

        except Exception as e:
            logger.error(f"Error removing allowed number: {e}")
            return False

    async def update_call_twilio_info(
        self,
        call_id: UUID,
        twilio_call_sid: str,
        caller_phone_number: Optional[str] = None,
        call_source: str = "twilio_inbound"
    ):
        """
        Update a call record with Twilio-specific information.

        Args:
            call_id: VOS call ID
            twilio_call_sid: Twilio's call SID
            caller_phone_number: Caller's phone number
            call_source: Source of call (twilio_inbound, twilio_outbound)
        """
        try:
            query = """
            UPDATE calls SET
                twilio_call_sid = $1,
                caller_phone_number = $2,
                call_source = $3,
                updated_at = NOW()
            WHERE call_id = $4
            """

            async with self.pool.acquire() as conn:
                await conn.execute(
                    query,
                    twilio_call_sid,
                    caller_phone_number,
                    call_source,
                    call_id
                )

            logger.info(f"Updated call {call_id} with Twilio info: sid={twilio_call_sid}")

        except Exception as e:
            logger.error(f"Error updating call Twilio info: {e}")

    async def get_call_by_twilio_sid(self, twilio_call_sid: str) -> Optional[Dict[str, Any]]:
        """
        Get call record by Twilio call SID.

        Args:
            twilio_call_sid: Twilio's call SID

        Returns:
            Call record or None
        """
        try:
            # Note: Only select columns that exist in the calls table
            # status, created_at, updated_at are managed in-memory by CallManager
            query = """
            SELECT call_id, session_id,
                   twilio_call_sid, caller_phone_number, call_source
            FROM calls
            WHERE twilio_call_sid = $1
            """

            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(query, twilio_call_sid)

            if row:
                return {
                    "call_id": str(row["call_id"]),
                    "session_id": row["session_id"],
                    "twilio_call_sid": row["twilio_call_sid"],
                    "caller_phone_number": row["caller_phone_number"],
                    "call_source": row["call_source"]
                }

            return None

        except Exception as e:
            logger.error(f"Error getting call by Twilio SID: {e}")
            return None

    async def close(self):
        """Close database connection pool"""
        if self.pool:
            await self.pool.close()
            logger.info("PostgreSQL connection pool closed")
