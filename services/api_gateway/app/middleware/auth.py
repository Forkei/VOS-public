"""
Authentication middleware for VOS API Gateway.
Supports both API Key and JWT token authentication.
"""

from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional
import os
import jwt
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

# Load from environment
API_KEYS = set(os.getenv("API_KEYS", "").split(",")) if os.getenv("API_KEYS") else set()
JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET environment variable must be set")
JWT_ALGORITHM = "HS256"

security = HTTPBearer()


class AuthMiddleware:
    """Middleware to validate API keys or JWT tokens."""

    async def __call__(self, request: Request, call_next):
        # Skip auth for CORS preflight requests
        if request.method == "OPTIONS":
            return await call_next(request)

        # Skip auth for public endpoints
        public_paths = ["/health", "/metrics", "/api/v1/auth/login", "/api/v1/conversations", "/docs", "/openapi.json", "/redoc", "/api/v1/docs"]

        # Public path prefixes (for dynamic URLs like signed audio/attachments)
        public_path_prefixes = ["/api/v1/audio/signed/", "/api/v1/attachments/signed/"]

        # Internal endpoints (agent-only) - DISABLED FOR TESTING - Allow regular API keys
        # Note: Be careful with substring matching - "/transcript" would match "/transcription"
        # Use "/transcript/" (with trailing slash) to match /transcript/append but not /transcription
        internal_endpoints = [
            "/transcript/", "/transcripts", "/status", "/processing-state", "/documents", "/docs", "/metadata",
            "/messages", "/conversations", "/notifications", "/tasks", "/memories",
            "/weather", "/agents", "/calls", "/twilio"
        ]

        # Check if this is a public endpoint (exact match)
        if request.url.path in public_paths:
            return await call_next(request)

        # Check if this is a public endpoint (prefix match for dynamic URLs)
        if any(request.url.path.startswith(prefix) for prefix in public_path_prefixes):
            return await call_next(request)

        # JWT-allowed paths within internal endpoint patterns
        # These paths match internal patterns but should allow JWT auth
        # These are user-facing endpoints that happen to match internal patterns
        jwt_allowed_paths = [
            "/system-prompts", "/conversations",
            "/notifications", "/messages", "/memories", "/tasks", "/documents",
            "/docs",  # Document API for frontend
            "/agents",  # Allow frontend to access agent status
            "/transcript", "/transcripts",  # Allow frontend to manage agent transcripts
            "/status", "/processing-state",  # Agent status indicators
            "/calls",  # Full call management (includes /calls/history)
            "/weather",  # Weather data display
            "/metadata"  # File/attachment metadata
        ]

        is_jwt_allowed_internal = any(allowed in request.url.path for allowed in jwt_allowed_paths)

        # Log for debugging auth issues
        if is_jwt_allowed_internal:
            logger.debug(f"JWT-allowed internal path detected: {request.url.path} (method: {request.method})")

        # If this is a JWT-allowed internal path, check JWT auth first
        if is_jwt_allowed_internal:
            auth_header = request.headers.get("Authorization")
            logger.info(f"JWT-allowed path: {request.url.path}, auth_header present: {bool(auth_header)}")
            if auth_header and auth_header.startswith("Bearer "):
                token = auth_header.split(" ")[1]
                try:
                    payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
                    if payload.get("sub"):
                        request.state.user_id = payload.get("sub")
                        request.state.auth_type = "jwt"
                        logger.debug(f"JWT auth for internal-pattern path: {request.url.path}")
                        return await call_next(request)
                    elif payload.get("type") == "voice_session" and payload.get("session_id"):
                        request.state.user_id = payload.get("user_id") or payload.get("session_id")
                        request.state.session_id = payload.get("session_id")
                        request.state.auth_type = "voice_jwt"
                        logger.debug(f"Voice JWT auth for internal-pattern path: {request.url.path}")
                        return await call_next(request)
                except jwt.ExpiredSignatureError:
                    return JSONResponse(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        content={"detail": "Authentication token has expired"},
                        headers={"WWW-Authenticate": "Bearer"}
                    )
                except jwt.InvalidTokenError as e:
                    logger.warning(f"Invalid JWT token: {e}")
                    # Fall through to internal endpoint check

        # Check if this is an internal endpoint
        is_internal_endpoint = any(endpoint in request.url.path for endpoint in internal_endpoints)

        # ALWAYS check internal API key first for internal endpoints (agents use this)
        if is_internal_endpoint:
            internal_key = request.headers.get("X-Internal-Key")

            # Read internal API key from shared file (survives process reloads)
            try:
                with open("/shared/internal_api_key", "r") as f:
                    internal_api_key = f.read().strip()
            except FileNotFoundError:
                logger.warning("Internal API key file not found at /shared/internal_api_key")
                internal_api_key = None

            if internal_key and internal_api_key and internal_key == internal_api_key:
                request.state.auth_type = "internal"
                logger.debug("Request authenticated with internal API key")
                return await call_next(request)

        # For internal endpoints that are NOT JWT-allowed, require internal key (deny if not provided)
        if is_internal_endpoint and not is_jwt_allowed_internal:
            # TESTING: Also allow regular API keys for internal endpoints
            api_key = request.headers.get("X-API-Key")
            if api_key and api_key in API_KEYS:
                request.state.auth_type = "api_key"
                logger.debug("Request authenticated with regular API key (internal endpoint)")
                return await call_next(request)

            # No valid auth for internal-only endpoint
            logger.warning(f"Unauthorized access attempt to internal endpoint: {request.url.path}")
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Internal endpoint - access denied"}
            )

        # Check for API key in header
        api_key = request.headers.get("X-API-Key")
        if api_key and api_key in API_KEYS:
            request.state.auth_type = "api_key"
            logger.debug("Request authenticated with API key")
            return await call_next(request)

        # Check for JWT token
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
            try:
                payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])

                # Support both standard JWT (with "sub") and voice tokens (with "session_id")
                if payload.get("sub"):
                    request.state.user_id = payload.get("sub")
                    request.state.auth_type = "jwt"
                    logger.debug(f"Request authenticated with JWT for user: {request.state.user_id}")
                elif payload.get("type") == "voice_session" and payload.get("session_id"):
                    # Voice session token - use session_id as identifier
                    request.state.user_id = payload.get("user_id") or payload.get("session_id")
                    request.state.session_id = payload.get("session_id")
                    request.state.auth_type = "voice_jwt"
                    logger.debug(f"Request authenticated with voice JWT for session: {payload.get('session_id')}")
                else:
                    logger.warning(f"JWT token missing required claims")
                    return JSONResponse(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        content={"detail": "Invalid token claims"},
                        headers={"WWW-Authenticate": "Bearer"}
                    )

                return await call_next(request)
            except jwt.ExpiredSignatureError:
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": "Authentication token has expired"},
                    headers={"WWW-Authenticate": "Bearer"}
                )
            except jwt.InvalidTokenError as e:
                logger.warning(f"Invalid JWT token: {e}")
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": "Invalid authentication token"},
                    headers={"WWW-Authenticate": "Bearer"}
                )

        # No valid auth found
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Missing or invalid authentication credentials"},
            headers={"WWW-Authenticate": "Bearer"}
        )


def create_jwt_token(user_id: str, expires_delta: timedelta = timedelta(hours=24)) -> str:
    """Create a JWT token for a user."""
    expire = datetime.utcnow() + expires_delta
    payload = {
        "sub": user_id,
        "exp": expire,
        "iat": datetime.utcnow()
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_jwt_token(token: str) -> Optional[dict]:
    """
    Verify and decode a JWT token.

    Args:
        token: JWT token string

    Returns:
        Decoded payload if valid, None if invalid
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        logger.warning("JWT token has expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid JWT token: {e}")
        return None
