"""
Password hashing and verification utilities using bcrypt.
"""

import bcrypt
import logging

logger = logging.getLogger(__name__)


def hash_password(password: str) -> str:
    """
    Hash a password using bcrypt.

    Args:
        password: Plain text password to hash

    Returns:
        Bcrypt hashed password as a string

    Raises:
        ValueError: If password is empty or None
    """
    if not password:
        raise ValueError("Password cannot be empty")

    # Generate salt and hash password
    # Using cost factor of 12 for good security/performance balance
    salt = bcrypt.gensalt(rounds=12)
    password_bytes = password.encode('utf-8')
    hashed = bcrypt.hashpw(password_bytes, salt)

    # Return as string (decode from bytes)
    return hashed.decode('utf-8')


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a password against a bcrypt hash.

    Args:
        plain_password: Plain text password to verify
        hashed_password: Bcrypt hashed password to check against

    Returns:
        True if password matches, False otherwise
    """
    if not plain_password or not hashed_password:
        return False

    try:
        password_bytes = plain_password.encode('utf-8')
        hashed_bytes = hashed_password.encode('utf-8')
        return bcrypt.checkpw(password_bytes, hashed_bytes)
    except Exception as e:
        logger.error(f"Error verifying password: {e}")
        return False
