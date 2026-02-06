"""
Authentication router for user login, registration, and account management.
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel, Field, EmailStr

from app.database import get_database, DatabaseClient
from app.middleware.auth import create_jwt_token
from app.utils.password import hash_password, verify_password

logger = logging.getLogger(__name__)

# Create router for auth endpoints
router = APIRouter(prefix="/auth", tags=["authentication"])


# ============================================================================
# Request/Response Models
# ============================================================================

class LoginRequest(BaseModel):
    """Login request body."""
    username: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1)


class RegisterRequest(BaseModel):
    """User registration request body."""
    username: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, description="Minimum 8 characters")
    email: Optional[EmailStr] = None
    full_name: Optional[str] = None


class LoginResponse(BaseModel):
    """Login response with JWT token."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds
    username: str
    is_admin: bool


class UserResponse(BaseModel):
    """User profile response."""
    id: int
    username: str
    email: Optional[str]
    full_name: Optional[str]
    is_admin: bool
    is_active: bool
    created_at: datetime
    last_login_at: Optional[datetime]


class PasswordChangeRequest(BaseModel):
    """Password change request."""
    current_password: str
    new_password: str = Field(min_length=8)


# ============================================================================
# Helper Functions
# ============================================================================

def get_db() -> DatabaseClient:
    """Dependency to get database client instance."""
    return get_database()


def check_account_locked(user: dict) -> bool:
    """
    Check if user account is locked.

    Args:
        user: User dict from database

    Returns:
        True if account is locked, False otherwise
    """
    if user.get('locked_until'):
        if user['locked_until'] > datetime.now():
            return True
        # Lock expired, will be cleared on next successful login
    return False


def increment_failed_attempts(db: DatabaseClient, username: str, current_attempts: int):
    """
    Increment failed login attempts and lock account if threshold exceeded.

    Args:
        db: Database client
        username: Username
        current_attempts: Current number of failed attempts
    """
    new_attempts = current_attempts + 1

    # Lock account for 15 minutes after 5 failed attempts
    LOCK_THRESHOLD = 5
    LOCK_DURATION_MINUTES = 15

    if new_attempts >= LOCK_THRESHOLD:
        locked_until = datetime.now() + timedelta(minutes=LOCK_DURATION_MINUTES)
        query = """
        UPDATE users
        SET failed_login_attempts = %s, locked_until = %s
        WHERE username = %s
        """
        db.execute_query(query, (new_attempts, locked_until, username))
        logger.warning(f"Account locked for user '{username}' due to {new_attempts} failed login attempts")
    else:
        query = """
        UPDATE users
        SET failed_login_attempts = %s
        WHERE username = %s
        """
        db.execute_query(query, (new_attempts, username))


def reset_failed_attempts(db: DatabaseClient, username: str):
    """
    Reset failed login attempts and unlock account on successful login.

    Args:
        db: Database client
        username: Username
    """
    query = """
    UPDATE users
    SET failed_login_attempts = 0, locked_until = NULL, last_login_at = NOW()
    WHERE username = %s
    """
    db.execute_query(query, (username,))


# ============================================================================
# Endpoints
# ============================================================================

@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest, db: DatabaseClient = Depends(get_db)):
    """
    Authenticate user and return JWT token.

    Args:
        request: Login credentials
        db: Database client dependency

    Returns:
        JWT token and user info

    Raises:
        HTTPException: 401 if credentials invalid, 403 if account locked/inactive
    """
    try:
        # Query user from database
        query = """
        SELECT id, username, email, password_hash, is_active, is_admin,
               failed_login_attempts, locked_until
        FROM users
        WHERE username = %s
        """
        result = db.execute_query_dict(query, (request.username,))

        if not result:
            logger.warning(f"Login attempt for non-existent user: {request.username}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password"
            )

        user = result[0]

        # Check if account is active
        if not user['is_active']:
            logger.warning(f"Login attempt for inactive account: {request.username}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is inactive. Please contact an administrator."
            )

        # Check if account is locked
        if check_account_locked(user):
            lock_time = user['locked_until']
            logger.warning(f"Login attempt for locked account: {request.username}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Account is locked due to multiple failed login attempts. Try again after {lock_time.strftime('%Y-%m-%d %H:%M:%S')}."
            )

        # Verify password
        if not verify_password(request.password, user['password_hash']):
            logger.warning(f"Failed login attempt for user: {request.username}")
            increment_failed_attempts(db, request.username, user['failed_login_attempts'])
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password"
            )

        # Successful login - reset failed attempts and create token
        reset_failed_attempts(db, request.username)

        # Create JWT token (24 hours expiry)
        token_expiry = timedelta(hours=24)
        token = create_jwt_token(request.username, expires_delta=token_expiry)

        logger.info(f"User '{request.username}' authenticated successfully")

        return LoginResponse(
            access_token=token,
            token_type="bearer",
            expires_in=int(token_expiry.total_seconds()),
            username=user['username'],
            is_admin=user['is_admin']
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error during login: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred during login"
        )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(request: RegisterRequest, db: DatabaseClient = Depends(get_db)):
    """
    Register a new user account.

    Args:
        request: Registration data
        db: Database client dependency

    Returns:
        Created user profile

    Raises:
        HTTPException: 400 if username/email already exists, 500 on error
    """
    try:
        # Check if username already exists
        check_query = "SELECT id FROM users WHERE username = %s"
        existing_user = db.execute_query_dict(check_query, (request.username,))

        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already exists"
            )

        # Check if email already exists (if provided)
        if request.email:
            email_check_query = "SELECT id FROM users WHERE email = %s"
            existing_email = db.execute_query_dict(email_check_query, (request.email,))

            if existing_email:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Email already registered"
                )

        # Hash password
        password_hash = hash_password(request.password)

        # Insert new user
        insert_query = """
        INSERT INTO users (username, email, password_hash, full_name, is_active, is_admin)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id, username, email, full_name, is_active, is_admin, created_at, last_login_at
        """

        result = db.execute_query_dict(
            insert_query,
            (request.username, request.email, password_hash, request.full_name, True, False)
        )

        if not result:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create user"
            )

        user = result[0]
        logger.info(f"New user registered: {user['username']}")

        return UserResponse(**user)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error during registration: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred during registration"
        )


@router.get("/me", response_model=UserResponse)
async def get_current_user(db: DatabaseClient = Depends(get_db)):
    """
    Get current authenticated user profile.

    Note: This endpoint requires JWT authentication.
    The username is extracted from the JWT token by the auth middleware.

    Args:
        db: Database client dependency

    Returns:
        Current user profile

    Raises:
        HTTPException: 401 if not authenticated, 404 if user not found
    """
    # TODO: Extract user_id from request.state.user_id (set by auth middleware)
    # For now, this is a placeholder that would need integration with FastAPI's request context
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="This endpoint requires request context integration"
    )
