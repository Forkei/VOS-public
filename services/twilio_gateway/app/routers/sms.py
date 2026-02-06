"""
SMS Router
API endpoints for sending and receiving SMS messages via Twilio
"""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Request, Form, Header
from fastapi.responses import Response
from pydantic import BaseModel, Field
from twilio.twiml.messaging_response import MessagingResponse
from twilio.request_validator import RequestValidator

from ..config import settings
from ..utils.twilio_client import send_sms

logger = logging.getLogger(__name__)

router = APIRouter()


class SendSMSRequest(BaseModel):
    """Request model for sending an SMS"""
    to_number: str = Field(..., description="Phone number to send SMS to (E.164 format)")
    body: str = Field(..., description="Message body to send", max_length=1600)


class SendSMSResponse(BaseModel):
    """Response model for SMS sending"""
    success: bool
    twilio_message_sid: Optional[str] = None
    to_number: str
    status: Optional[str] = None
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


async def verify_twilio_signature(request: Request) -> bool:
    """
    Verify the Twilio signature on incoming webhooks.

    Args:
        request: FastAPI request

    Returns:
        True if valid, raises HTTPException if invalid
    """
    # Only skip validation if explicitly disabled (for local development only)
    # NEVER set TWILIO_SKIP_SIGNATURE_VALIDATION=true in production!
    if getattr(settings, 'TWILIO_SKIP_SIGNATURE_VALIDATION', False):
        logger.warning("Twilio signature validation DISABLED - only use for local development!")
        return True

    validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)

    # Get the signature from header
    signature = request.headers.get("X-Twilio-Signature", "")

    # Get form data
    form_data = await request.form()
    params = {key: value for key, value in form_data.items()}

    # Build the URL (must match exactly what Twilio sent to)
    # Account for reverse proxy (Cloudflare tunnel) - use X-Forwarded-Proto header
    url = str(request.url)
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "")
    if forwarded_proto == "https" and url.startswith("http://"):
        url = "https://" + url[7:]  # Replace http:// with https://

    if not validator.validate(url, params, signature):
        logger.warning(f"Invalid Twilio signature for request to {url}")
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    return True


@router.post("/sms/send", response_model=SendSMSResponse)
async def send_sms_endpoint(
    request: SendSMSRequest,
    _: bool = Depends(verify_internal_key)
):
    """
    Send an SMS message via Twilio.

    This endpoint is called by the SendSMSTool when an agent
    wants to send an SMS message.

    Note: No whitelist check - agents can text any phone number.
    """
    logger.info(f"Sending SMS to: {request.to_number[:4]}****")

    # Send the SMS via Twilio
    result = await send_sms(
        to_number=request.to_number,
        body=request.body
    )

    if result.get("success"):
        return SendSMSResponse(
            success=True,
            twilio_message_sid=result["twilio_message_sid"],
            to_number=request.to_number,
            status=result["status"]
        )
    else:
        return SendSMSResponse(
            success=False,
            to_number=request.to_number,
            error=result.get("error", "Unknown error")
        )


@router.post("/sms/receive")
async def receive_sms_webhook(
    request: Request,
    MessageSid: str = Form(...),
    From: str = Form(...),
    To: str = Form(...),
    Body: str = Form(...),
    NumMedia: Optional[str] = Form(default="0"),
    db_client=Depends(get_db_client),
    rabbitmq_client=Depends(get_rabbitmq_client),
    _: bool = Depends(verify_twilio_signature)
):
    """
    Handle incoming SMS from Twilio webhook.

    1. Check if sender is in allowed_phone_numbers whitelist
    2. If whitelisted: publish RabbitMQ notification to primary_agent
    3. If not whitelisted: log and ignore (no notification)
    4. Return TwiML response (empty - no auto-reply)
    """
    # Redact phone number for logging (PII protection)
    from_redacted = From[:4] + "****" + From[-2:] if len(From) > 6 else "***"
    logger.info(f"Incoming SMS: MessageSid={MessageSid}, From={from_redacted}, BodyLen={len(Body)}")

    # Create TwiML response (empty - we don't auto-reply)
    response = MessagingResponse()

    # Check if phone number is allowed
    is_allowed = await db_client.is_phone_number_allowed(From)

    if not is_allowed:
        logger.info(f"Ignoring SMS from non-whitelisted number: {from_redacted}")
        return Response(
            content=str(response),
            media_type="application/xml"
        )

    # Get sender info from whitelist
    sender_info = await db_client.get_allowed_number_info(From)
    sender_name = sender_info.get("display_name") if sender_info else None

    # Publish notification to primary_agent
    notification_id = str(uuid.uuid4())
    await rabbitmq_client.publish_notification(
        queue_name="primary_agent",
        notification={
            "notification_id": notification_id,
            "notification_type": "incoming_sms",
            "source": "twilio_gateway",
            "recipient_agent_id": "primary_agent",
            "payload": {
                "twilio_message_sid": MessageSid,
                "from_number": From,
                "sender_name": sender_name,
                "body": Body,
                "num_media": int(NumMedia) if NumMedia else 0
            }
        }
    )

    logger.info(f"Published incoming SMS notification for {from_redacted}: {notification_id}")

    return Response(
        content=str(response),
        media_type="application/xml"
    )
