"""
Outbound Call Router
API endpoints for initiating outbound phone calls
"""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel, Field

from ..config import settings
from ..utils.twilio_client import initiate_outbound_call, end_call, get_call_status

logger = logging.getLogger(__name__)

router = APIRouter()


class OutboundCallRequest(BaseModel):
    """Request model for initiating an outbound call"""
    to_number: str = Field(..., description="Phone number to call (E.164 format)")
    session_id: str = Field(..., description="VOS session ID")
    call_id: Optional[str] = Field(default=None, description="Optional VOS call ID")


class OutboundCallResponse(BaseModel):
    """Response model for outbound call initiation"""
    success: bool
    twilio_call_sid: Optional[str] = None
    call_id: str
    session_id: str
    to_number: str
    error: Optional[str] = None


def verify_internal_key(x_internal_key: Optional[str] = Header(default=None)) -> bool:
    """
    Verify internal API key for service-to-service communication.

    SECURITY: Never bypass authentication, even in development.
    If the key file is missing, reject requests with a clear error.
    """
    if not x_internal_key:
        raise HTTPException(status_code=403, detail="Missing internal API key header")

    # Read internal API key from shared volume
    try:
        with open("/shared/internal_api_key", "r") as f:
            expected_key = f.read().strip()

        if not expected_key:
            logger.error("Internal API key file is empty - this is a configuration error")
            raise HTTPException(status_code=503, detail="Service misconfigured: internal API key not set")

        if x_internal_key == expected_key:
            return True

        logger.warning("Invalid internal API key provided")
        raise HTTPException(status_code=403, detail="Invalid internal API key")

    except FileNotFoundError:
        logger.error("Internal API key file not found at /shared/internal_api_key - this is a deployment error")
        raise HTTPException(status_code=503, detail="Service misconfigured: internal API key file missing")


def get_rabbitmq_client():
    """Get RabbitMQ client from main module"""
    from ..main import rabbitmq_client
    return rabbitmq_client


def get_db_client():
    """Get database client from main module"""
    from ..main import db_client
    return db_client


@router.post("/call/outbound", response_model=OutboundCallResponse)
async def create_outbound_call(
    request: OutboundCallRequest,
    _: bool = Depends(verify_internal_key),
    rabbitmq_client=Depends(get_rabbitmq_client),
    db_client=Depends(get_db_client)
):
    """
    Initiate an outbound phone call.

    This endpoint is called by the CallUserTool when an agent
    wants to call a phone number.
    """
    call_id = request.call_id or str(uuid.uuid4())

    logger.info(f"Initiating outbound call: to={request.to_number}, session={request.session_id}")

    # Initiate the call via Twilio
    result = await initiate_outbound_call(
        to_number=request.to_number,
        session_id=request.session_id,
        call_id=call_id
    )

    if result.get("success"):
        # Update database with Twilio info
        await db_client.update_call_twilio_info(
            call_id=call_id,
            twilio_call_sid=result["twilio_call_sid"],
            caller_phone_number=request.to_number,
            call_source="twilio_outbound"
        )

        # Publish event
        await rabbitmq_client.publish_call_event(
            event_type="outbound_call_initiated",
            session_id=request.session_id,
            call_id=call_id,
            twilio_call_sid=result["twilio_call_sid"],
            phone_number=request.to_number
        )

        return OutboundCallResponse(
            success=True,
            twilio_call_sid=result["twilio_call_sid"],
            call_id=call_id,
            session_id=request.session_id,
            to_number=request.to_number
        )

    else:
        return OutboundCallResponse(
            success=False,
            call_id=call_id,
            session_id=request.session_id,
            to_number=request.to_number,
            error=result.get("error", "Unknown error")
        )


@router.post("/call/{twilio_call_sid}/end")
async def terminate_call(
    twilio_call_sid: str,
    _: bool = Depends(verify_internal_key)
):
    """
    End an active phone call.
    """
    success = await end_call(twilio_call_sid)

    if success:
        return {"success": True, "message": f"Call {twilio_call_sid} ended"}
    else:
        raise HTTPException(status_code=500, detail="Failed to end call")


@router.get("/call/{twilio_call_sid}/status")
async def get_call_info(
    twilio_call_sid: str,
    _: bool = Depends(verify_internal_key)
):
    """
    Get the status of a phone call.
    """
    status = await get_call_status(twilio_call_sid)

    if status:
        return status
    else:
        raise HTTPException(status_code=404, detail="Call not found")
