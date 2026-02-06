"""
JWT Authentication Utilities
Handles token generation and verification for WebSocket connections
"""

import jwt
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict
from ..config import settings

logger = logging.getLogger(__name__)


class JWTAuth:
    """JWT authentication helper"""

    @staticmethod
    def generate_voice_token(session_id: str, user_id: Optional[str] = None) -> str:
        """
        Generate JWT token for voice WebSocket connection

        Args:
            session_id: Session ID for voice connection
            user_id: Optional user ID for multi-user support

        Returns:
            JWT token string
        """
        try:
            # Token payload
            payload = {
                "session_id": session_id,
                "type": "voice_session",
                "iat": datetime.utcnow(),
                "exp": datetime.utcnow() + timedelta(minutes=settings.JWT_EXPIRATION_MINUTES)
            }

            if user_id:
                payload["user_id"] = user_id

            # Generate token
            token = jwt.encode(
                payload,
                settings.JWT_SECRET,
                algorithm=settings.JWT_ALGORITHM
            )

            logger.debug(f"Generated voice token for session {session_id}")

            return token

        except Exception as e:
            logger.error(f"Error generating JWT token: {e}")
            raise

    @staticmethod
    def verify_voice_token(token: str) -> Dict:
        """
        Verify and decode JWT token

        Args:
            token: JWT token string

        Returns:
            Decoded token payload

        Raises:
            jwt.ExpiredSignatureError: Token has expired
            jwt.InvalidTokenError: Token is invalid
        """
        try:
            # Decode and verify token
            payload = jwt.decode(
                token,
                settings.JWT_SECRET,
                algorithms=[settings.JWT_ALGORITHM]
            )

            # Verify token type
            if payload.get("type") != "voice_session":
                raise jwt.InvalidTokenError("Invalid token type")

            logger.debug(f"Verified voice token for session {payload.get('session_id')}")

            return payload

        except jwt.ExpiredSignatureError:
            logger.warning("JWT token has expired")
            raise
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid JWT token: {e}")
            raise
        except Exception as e:
            logger.error(f"Error verifying JWT token: {e}")
            raise jwt.InvalidTokenError(str(e))

    @staticmethod
    def extract_session_id(token: str) -> Optional[str]:
        """
        Extract session_id from token without full verification
        Useful for logging/debugging

        Args:
            token: JWT token string

        Returns:
            Session ID or None
        """
        try:
            # Decode without verification (just to extract session_id)
            unverified = jwt.decode(
                token,
                options={"verify_signature": False}
            )
            return unverified.get("session_id")
        except Exception:
            return None
