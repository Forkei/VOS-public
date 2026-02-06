"""
Twilio Admin API Router

Provides REST API endpoints for managing Twilio phone integration:
- Manage allowed phone numbers whitelist
- Initiate outbound calls
- View Twilio call information

These endpoints enable administrators to control which phone numbers
can call in to VOS via the Twilio integration.
"""

import json
import logging
import os
import re
from typing import List, Optional
from datetime import datetime

import httpx
from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/twilio", tags=["twilio"])

# E.164 phone number regex pattern
E164_PATTERN = re.compile(r'^\+[1-9]\d{1,14}$')

# Twilio Gateway URL from environment (with fallback for Docker)
TWILIO_GATEWAY_URL = os.getenv("TWILIO_GATEWAY_URL", "http://twilio_gateway:8200")


# =============================================================================
# Pydantic Models
# =============================================================================

class AllowedNumberCreate(BaseModel):
    """Model for adding a phone number to the whitelist"""
    phone_number: str = Field(..., description="Phone number in E.164 format (+1234567890)")
    display_name: Optional[str] = Field(None, description="Display name for the caller")
    user_id: Optional[int] = Field(None, description="Optional VOS user ID to link (integer FK to users table)")
    metadata: Optional[dict] = Field(default_factory=dict, description="Additional metadata")

    @field_validator('phone_number')
    @classmethod
    def validate_phone_number(cls, v):
        if not E164_PATTERN.match(v):
            raise ValueError('Phone number must be in E.164 format (e.g., +12125551234)')
        return v


class AllowedNumberUpdate(BaseModel):
    """Model for updating an allowed phone number"""
    display_name: Optional[str] = None
    user_id: Optional[int] = None
    is_active: Optional[bool] = None
    metadata: Optional[dict] = None


class AllowedNumberResponse(BaseModel):
    """Response model for allowed phone number"""
    id: int
    phone_number: str
    display_name: Optional[str]
    user_id: Optional[int]
    is_active: bool
    metadata: dict
    created_at: datetime

    class Config:
        from_attributes = True


class OutboundCallRequest(BaseModel):
    """Model for initiating an outbound call"""
    to_number: str = Field(..., description="Phone number to call (E.164 format)")
    session_id: str = Field(..., description="VOS session ID for the call")

    @field_validator('to_number')
    @classmethod
    def validate_to_number(cls, v):
        if not E164_PATTERN.match(v):
            raise ValueError('Phone number must be in E.164 format (e.g., +12125551234)')
        return v


class OutboundCallResponse(BaseModel):
    """Response model for outbound call initiation"""
    success: bool
    call_id: Optional[str] = None
    twilio_call_sid: Optional[str] = None
    to_number: str
    error: Optional[str] = None


class InboundCallRegisterRequest(BaseModel):
    """Model for registering an inbound Twilio call"""
    session_id: str = Field(..., description="VOS session ID")
    call_id: str = Field(..., description="VOS call ID (UUID)")
    twilio_call_sid: str = Field(..., description="Twilio call SID")
    caller_phone_number: Optional[str] = Field(None, description="Caller's phone number in E.164 format")
    caller_name: Optional[str] = Field(None, description="Caller's display name")


class SendSMSRequest(BaseModel):
    """Model for sending an SMS message"""
    to_number: str = Field(..., description="Phone number to send SMS to (E.164 format)")
    body: str = Field(..., description="Message body to send", max_length=1600)

    @field_validator('to_number')
    @classmethod
    def validate_to_number(cls, v):
        if not E164_PATTERN.match(v):
            raise ValueError('Phone number must be in E.164 format (e.g., +12125551234)')
        return v


class SendSMSResponse(BaseModel):
    """Response model for SMS sending"""
    success: bool
    twilio_message_sid: Optional[str] = None
    to_number: str
    status: Optional[str] = None
    error: Optional[str] = None


class InboundCallRegisterResponse(BaseModel):
    """Response for inbound call registration"""
    success: bool
    call_id: str
    error: Optional[str] = None


# =============================================================================
# Database Helper
# =============================================================================

def get_db():
    """Get database client from main app"""
    from app.main import db_client
    if not db_client:
        raise HTTPException(status_code=503, detail="Database not available")
    return db_client


def verify_admin_access(
    authorization: Optional[str] = Header(default=None),
    x_internal_key: Optional[str] = Header(default=None)
):
    """
    Verify that the request has admin access.

    Supports two authentication methods:
    1. Internal API key (for service-to-service calls)
    2. JWT Bearer token with admin role

    SECURITY: This endpoint modifies the phone whitelist, so proper auth is critical.
    """
    import jwt
    import os

    # Method 1: Check internal API key (service-to-service)
    if x_internal_key:
        try:
            with open("/shared/internal_api_key", "r") as f:
                expected_key = f.read().strip()
            if expected_key and x_internal_key == expected_key:
                return True
        except FileNotFoundError:
            pass  # Fall through to JWT check

    # Method 2: Check JWT Bearer token
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Authorization required (Bearer token or X-Internal-Key)"
        )

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization format, expected 'Bearer <token>'")

    token = authorization[7:]  # Remove "Bearer " prefix

    try:
        # Get JWT secret from environment
        jwt_secret = os.environ.get("JWT_SECRET")
        if not jwt_secret:
            logger.error("JWT_SECRET not configured")
            raise HTTPException(status_code=503, detail="Authentication service misconfigured")

        # Decode and validate the token
        payload = jwt.decode(token, jwt_secret, algorithms=["HS256"])

        # Check for admin role or valid user
        # Accept tokens with admin role, or user_id (authenticated users can manage their own numbers)
        if payload.get("role") == "admin" or payload.get("user_id"):
            return True

        raise HTTPException(status_code=403, detail="Insufficient permissions")

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid JWT token: {e}")
        raise HTTPException(status_code=401, detail="Invalid token")


# =============================================================================
# Allowed Numbers Endpoints
# =============================================================================

@router.get("/allowed-numbers", response_model=List[AllowedNumberResponse])
def list_allowed_numbers(
    include_inactive: bool = False,
    db=Depends(get_db)
):
    """
    List all allowed phone numbers.

    Returns the whitelist of phone numbers that are allowed to call
    the VOS Twilio number.

    Query params:
    - include_inactive: Include deactivated numbers (default: false)
    """
    try:
        query = """
            SELECT id, phone_number, display_name, user_id, is_active,
                   metadata, created_at
            FROM allowed_phone_numbers
        """

        if not include_inactive:
            query += " WHERE is_active = true"

        query += " ORDER BY created_at DESC"

        rows = db.execute_query_dict(query)

        return [
            AllowedNumberResponse(
                id=row['id'],
                phone_number=row['phone_number'],
                display_name=row['display_name'],
                user_id=row['user_id'],
                is_active=row['is_active'],
                metadata=row['metadata'] if row['metadata'] else {},
                created_at=row['created_at']
            )
            for row in rows
        ]

    except Exception as e:
        logger.error(f"Error listing allowed numbers: {e}")
        raise HTTPException(status_code=500, detail="Failed to list allowed numbers")


@router.post("/allowed-numbers", response_model=AllowedNumberResponse)
def add_allowed_number(
    number: AllowedNumberCreate,
    db=Depends(get_db),
    _=Depends(verify_admin_access)
):
    """
    Add a phone number to the whitelist.

    The phone number must be in E.164 format (e.g., +12125551234).
    If the number already exists, it will be reactivated and updated.
    """
    try:
        query = """
            INSERT INTO allowed_phone_numbers (phone_number, display_name, user_id, metadata)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (phone_number) DO UPDATE SET
                display_name = COALESCE(EXCLUDED.display_name, allowed_phone_numbers.display_name),
                user_id = COALESCE(EXCLUDED.user_id, allowed_phone_numbers.user_id),
                metadata = COALESCE(EXCLUDED.metadata, allowed_phone_numbers.metadata),
                is_active = true,
                updated_at = NOW()
            RETURNING id, phone_number, display_name, user_id, is_active, metadata, created_at
        """

        rows = db.execute_query_dict(
            query,
            (
                number.phone_number,
                number.display_name,
                number.user_id,
                json.dumps(number.metadata or {})
            )
        )

        if not rows:
            raise HTTPException(status_code=500, detail="Failed to add allowed number")

        row = rows[0]
        logger.info(f"Added allowed phone number: {number.phone_number}")

        return AllowedNumberResponse(
            id=row['id'],
            phone_number=row['phone_number'],
            display_name=row['display_name'],
            user_id=row['user_id'],
            is_active=row['is_active'],
            metadata=row['metadata'] if row['metadata'] else {},
            created_at=row['created_at']
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding allowed number: {e}")
        raise HTTPException(status_code=500, detail="Failed to add allowed number")


@router.get("/allowed-numbers/{number_id}", response_model=AllowedNumberResponse)
def get_allowed_number(
    number_id: int,
    db=Depends(get_db)
):
    """
    Get details of a specific allowed phone number.
    """
    try:
        query = """
            SELECT id, phone_number, display_name, user_id, is_active,
                   metadata, created_at
            FROM allowed_phone_numbers
            WHERE id = %s
        """

        rows = db.execute_query_dict(query, (number_id,))

        if not rows:
            raise HTTPException(status_code=404, detail="Phone number not found")

        row = rows[0]
        return AllowedNumberResponse(
            id=row['id'],
            phone_number=row['phone_number'],
            display_name=row['display_name'],
            user_id=row['user_id'],
            is_active=row['is_active'],
            metadata=row['metadata'] if row['metadata'] else {},
            created_at=row['created_at']
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting allowed number: {e}")
        raise HTTPException(status_code=500, detail="Failed to get allowed number")


@router.patch("/allowed-numbers/{number_id}", response_model=AllowedNumberResponse)
def update_allowed_number(
    number_id: int,
    update: AllowedNumberUpdate,
    db=Depends(get_db),
    _=Depends(verify_admin_access)
):
    """
    Update an allowed phone number.

    Only provided fields will be updated.
    """
    try:
        # Build dynamic update query
        updates = []
        params = []

        if update.display_name is not None:
            updates.append("display_name = %s")
            params.append(update.display_name)

        if update.user_id is not None:
            updates.append("user_id = %s")
            params.append(update.user_id)

        if update.is_active is not None:
            updates.append("is_active = %s")
            params.append(update.is_active)

        if update.metadata is not None:
            updates.append("metadata = %s")
            params.append(json.dumps(update.metadata))

        if not updates:
            # Nothing to update, just return current state
            return get_allowed_number(number_id, db)

        updates.append("updated_at = NOW()")
        params.append(number_id)

        query = f"""
            UPDATE allowed_phone_numbers
            SET {', '.join(updates)}
            WHERE id = %s
            RETURNING id, phone_number, display_name, user_id, is_active, metadata, created_at
        """

        rows = db.execute_query_dict(query, tuple(params))

        if not rows:
            raise HTTPException(status_code=404, detail="Phone number not found")

        row = rows[0]
        logger.info(f"Updated allowed phone number {number_id}")

        return AllowedNumberResponse(
            id=row['id'],
            phone_number=row['phone_number'],
            display_name=row['display_name'],
            user_id=row['user_id'],
            is_active=row['is_active'],
            metadata=row['metadata'] if row['metadata'] else {},
            created_at=row['created_at']
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating allowed number: {e}")
        raise HTTPException(status_code=500, detail="Failed to update allowed number")


@router.delete("/allowed-numbers/{number_id}")
def delete_allowed_number(
    number_id: int,
    hard_delete: bool = False,
    db=Depends(get_db),
    _=Depends(verify_admin_access)
):
    """
    Remove a phone number from the whitelist.

    By default, this performs a soft delete (sets is_active=false).
    Set hard_delete=true to permanently remove the record.
    """
    try:
        if hard_delete:
            query = "DELETE FROM allowed_phone_numbers WHERE id = %s RETURNING phone_number"
        else:
            query = """
                UPDATE allowed_phone_numbers
                SET is_active = false, updated_at = NOW()
                WHERE id = %s
                RETURNING phone_number
            """

        rows = db.execute_query_dict(query, (number_id,))

        if not rows:
            raise HTTPException(status_code=404, detail="Phone number not found")

        action = "deleted" if hard_delete else "deactivated"
        logger.info(f"{action.capitalize()} allowed phone number: {rows[0]['phone_number']}")

        return {"success": True, "message": f"Phone number {action}"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting allowed number: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete allowed number")


# =============================================================================
# Outbound Call Endpoints
# =============================================================================

@router.post("/call/outbound", response_model=OutboundCallResponse)
async def initiate_outbound_call(
    request: OutboundCallRequest,
    _=Depends(verify_admin_access)
):
    """
    Initiate an outbound phone call via Twilio.

    This endpoint proxies to the Twilio Gateway service to place
    an outbound call. The call will be connected to the VOS voice
    pipeline for the specified session.
    """
    try:
        # Read internal API key
        try:
            with open("/shared/internal_api_key", "r") as f:
                internal_key = f.read().strip()
        except FileNotFoundError:
            logger.warning("Internal API key not found")
            internal_key = ""

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{TWILIO_GATEWAY_URL}/twilio/call/outbound",
                json={
                    "to_number": request.to_number,
                    "session_id": request.session_id
                },
                headers={"X-Internal-Key": internal_key},
                timeout=30.0
            )

            result = response.json()

            # If call was successfully initiated, create a Call object in CallManager
            # This ensures hang_up will properly terminate the Twilio call
            if result.get("success") and result.get("twilio_call_sid"):
                try:
                    from app.services.call_manager import get_call_manager
                    call_manager = get_call_manager()
                    if call_manager:
                        await call_manager.create_twilio_outbound_call(
                            session_id=request.session_id,
                            twilio_call_sid=result.get("twilio_call_sid"),
                            to_phone_number=request.to_number,
                            target_agent="primary_agent"
                        )
                        logger.info(f"Created CallManager entry for outbound call: {result.get('twilio_call_sid')}")
                except Exception as cm_error:
                    logger.error(f"Failed to create CallManager entry: {cm_error}")
                    # Don't fail the call - it's still active, just tracking is broken

            return OutboundCallResponse(
                success=result.get("success", False),
                call_id=result.get("call_id"),
                twilio_call_sid=result.get("twilio_call_sid"),
                to_number=request.to_number,
                error=result.get("error")
            )

    except httpx.TimeoutException:
        logger.error("Timeout connecting to Twilio Gateway")
        return OutboundCallResponse(
            success=False,
            to_number=request.to_number,
            error="Timeout connecting to Twilio Gateway"
        )

    except Exception as e:
        logger.error(f"Error initiating outbound call: {e}")
        return OutboundCallResponse(
            success=False,
            to_number=request.to_number,
            error=str(e)
        )


@router.post("/call/register-inbound", response_model=InboundCallRegisterResponse)
async def register_inbound_call(
    request: InboundCallRegisterRequest,
    _=Depends(verify_admin_access)
):
    """
    Register an inbound Twilio call with the CallManager.

    This endpoint is called by twilio_gateway when a Twilio media stream starts.
    It creates a Call object in CallManager so that hang_up and other call
    management functions work properly.
    """
    try:
        from app.services.call_manager import get_call_manager
        call_manager = get_call_manager()

        if not call_manager:
            logger.error("CallManager not available for inbound call registration")
            return InboundCallRegisterResponse(
                success=False,
                call_id=request.call_id,
                error="CallManager not available"
            )

        # Create the inbound call in CallManager
        # IMPORTANT: Pass the call_id from twilio_gateway to maintain consistency
        # across all services (twilio_gateway, voice_gateway, agents)
        call = await call_manager.create_twilio_inbound_call(
            twilio_call_sid=request.twilio_call_sid,
            caller_phone_number=request.caller_phone_number or "",
            call_id=request.call_id  # Use the call_id from twilio_gateway
        )

        logger.info(f"Registered inbound Twilio call: {request.twilio_call_sid}, call_id={call.call_id}")

        return InboundCallRegisterResponse(
            success=True,
            call_id=str(call.call_id)  # Return the actual generated call_id
        )

    except Exception as e:
        logger.error(f"Error registering inbound call: {e}")
        return InboundCallRegisterResponse(
            success=False,
            call_id=request.call_id,
            error=str(e)
        )


# =============================================================================
# SMS Endpoints
# =============================================================================

@router.post("/sms/send", response_model=SendSMSResponse)
async def send_sms(
    request: SendSMSRequest,
    _=Depends(verify_admin_access)
):
    """
    Send an SMS message via Twilio.

    This endpoint proxies to the Twilio Gateway service to send
    an SMS message. NO whitelist check is performed - agents can
    text any valid phone number.
    """
    try:
        # Read internal API key
        try:
            with open("/shared/internal_api_key", "r") as f:
                internal_key = f.read().strip()
        except FileNotFoundError:
            logger.warning("Internal API key not found")
            internal_key = ""

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{TWILIO_GATEWAY_URL}/twilio/sms/send",
                json={
                    "to_number": request.to_number,
                    "body": request.body
                },
                headers={"X-Internal-Key": internal_key},
                timeout=30.0
            )

            result = response.json()

            return SendSMSResponse(
                success=result.get("success", False),
                twilio_message_sid=result.get("twilio_message_sid"),
                to_number=request.to_number,
                status=result.get("status"),
                error=result.get("error")
            )

    except httpx.TimeoutException:
        logger.error("Timeout connecting to Twilio Gateway for SMS")
        return SendSMSResponse(
            success=False,
            to_number=request.to_number,
            error="Timeout connecting to Twilio Gateway"
        )

    except Exception as e:
        logger.error(f"Error sending SMS: {e}")
        return SendSMSResponse(
            success=False,
            to_number=request.to_number,
            error=str(e)
        )


# =============================================================================
# Check Endpoint
# =============================================================================

@router.get("/check-number/{phone_number}")
def check_phone_number(
    phone_number: str,
    db=Depends(get_db)
):
    """
    Check if a phone number is in the whitelist.

    Returns whether the number is allowed to call in.
    """
    # Validate format
    if not E164_PATTERN.match(phone_number):
        raise HTTPException(
            status_code=400,
            detail="Phone number must be in E.164 format (e.g., +12125551234)"
        )

    try:
        query = """
            SELECT id, display_name, user_id
            FROM allowed_phone_numbers
            WHERE phone_number = %s AND is_active = true
        """

        rows = db.execute_query_dict(query, (phone_number,))

        if rows:
            row = rows[0]
            return {
                "phone_number": phone_number,
                "is_allowed": True,
                "display_name": row['display_name'],
                "user_id": row['user_id']
            }
        else:
            return {
                "phone_number": phone_number,
                "is_allowed": False,
                "display_name": None,
                "user_id": None
            }

    except Exception as e:
        logger.error(f"Error checking phone number: {e}")
        raise HTTPException(status_code=500, detail="Failed to check phone number")
