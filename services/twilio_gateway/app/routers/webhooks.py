"""
Twilio Webhooks Router
Handles TwiML endpoints for incoming/outgoing calls and status callbacks
"""

import logging
import os
import uuid
from typing import Optional

from fastapi import APIRouter, Request, Form, HTTPException, Depends
from fastapi.responses import Response
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream
from twilio.request_validator import RequestValidator

from ..config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


def get_db_client():
    """Dependency to get database client from app state"""
    from ..main import db_client
    return db_client


def get_rabbitmq_client():
    """Dependency to get RabbitMQ client from app state"""
    from ..main import rabbitmq_client
    return rabbitmq_client


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
        logger.warning("⚠️ Twilio signature validation DISABLED - only use for local development!")
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


@router.post("/voice/incoming")
async def handle_incoming_call(
    request: Request,
    CallSid: str = Form(...),
    From: str = Form(...),
    To: str = Form(...),
    CallStatus: str = Form(...),
    Direction: str = Form(default="inbound"),
    CallerName: Optional[str] = Form(default=None),
    db_client=Depends(get_db_client),
    rabbitmq_client=Depends(get_rabbitmq_client),
    _: bool = Depends(verify_twilio_signature)
):
    """
    Handle incoming Twilio voice calls.

    1. Check concurrent call limit
    2. Check if caller is in allowed_phone_numbers whitelist
    3. If not allowed, reject the call
    4. If allowed, create VOS session and return TwiML with Media Streams
    """
    # Redact phone number for logging (PII protection)
    from_redacted = From[:4] + "****" + From[-2:] if len(From) > 6 else "***"
    logger.info(f"Incoming call: CallSid={CallSid}, From={from_redacted}, To={To}, Status={CallStatus}")

    # Create TwiML response
    response = VoiceResponse()

    # Check concurrent call limit to prevent resource exhaustion
    from ..main import active_streams
    if len(active_streams) >= settings.MAX_CONCURRENT_CALLS:
        logger.warning(f"Rejecting call - max concurrent calls ({settings.MAX_CONCURRENT_CALLS}) reached")
        response.say("We're experiencing high call volume. Please try again later.", voice="Polly.Matthew")
        response.hangup()
        return Response(
            content=str(response),
            media_type="application/xml"
        )

    # Check if phone number is allowed
    is_allowed = await db_client.is_phone_number_allowed(From)

    if not is_allowed:
        logger.warning(f"Rejecting call from non-whitelisted number: {From}")
        response.reject(reason="rejected")
        return Response(
            content=str(response),
            media_type="application/xml"
        )

    # Get caller info from whitelist
    caller_info = await db_client.get_allowed_number_info(From)
    caller_name = caller_info.get("display_name") if caller_info else CallerName

    # Generate VOS session ID for this call
    session_id = f"twilio_{CallSid}"
    call_id = str(uuid.uuid4())

    # Create a welcoming message while we set up the connection
    response.say(
        "Connecting you now. Please wait.",
        voice="Polly.Matthew"
    )

    # Use <Connect><Stream> for BIDIRECTIONAL audio (allows sending TTS back to caller)
    # The WebSocket URL must be publicly accessible via Cloudflare tunnel
    # Safely construct WebSocket URL from WEBHOOK_BASE_URL
    base_url = settings.WEBHOOK_BASE_URL
    if base_url.startswith("https://"):
        ws_host = base_url.replace("https://", "")
    elif base_url.startswith("http://"):
        ws_host = base_url.replace("http://", "")
    else:
        ws_host = base_url
    # Remove trailing slash if present
    ws_host = ws_host.rstrip("/")
    stream_url = f"wss://{ws_host}/twilio/media-stream/{session_id}"

    # <Connect><Stream> creates a bidirectional stream:
    # - We receive caller's audio (inbound track) for STT
    # - We can send audio back to caller (TTS) via WebSocket media messages
    # Note: The call stays connected until WebSocket closes - no pause needed
    connect = Connect()
    stream = Stream(url=stream_url)
    stream.parameter(name="session_id", value=session_id)
    stream.parameter(name="call_id", value=call_id)
    stream.parameter(name="twilio_call_sid", value=CallSid)
    stream.parameter(name="caller_phone_number", value=From)
    stream.parameter(name="caller_name", value=caller_name or "Unknown")
    connect.append(stream)
    response.append(connect)

    # NOTE: We do NOT publish incoming_call notification here anymore.
    # The notification is sent by CallManager when the call is properly registered
    # (via media_stream.py -> register_inbound_call_with_api_gateway).
    # Publishing here caused race conditions where the agent received the notification
    # before the call was registered, leading to 404 errors on call management endpoints.

    logger.info(f"Accepted incoming call: session={session_id}, call_id={call_id}")

    return Response(
        content=str(response),
        media_type="application/xml"
    )


@router.post("/voice/outbound")
async def handle_outbound_call(
    request: Request,
    CallSid: str = Form(...),
    To: str = Form(...),
    From: str = Form(...),
    CallStatus: str = Form(...),
    _: bool = Depends(verify_twilio_signature)
):
    """
    Handle outbound call connection (when called party answers).
    Returns TwiML to start Media Streams.
    """
    # Get custom parameters from query string (passed via URL when creating the call)
    session_id = request.query_params.get("session_id")
    call_id = request.query_params.get("call_id")

    logger.info(f"Outbound call answered: CallSid={CallSid}, To={To}, Status={CallStatus}, session_id={session_id}")

    response = VoiceResponse()

    # Use the session_id we passed, or generate one
    if not session_id:
        session_id = f"twilio_out_{CallSid}"
    if not call_id:
        call_id = str(uuid.uuid4())

    # Brief message to the called party
    response.say(
        "Please hold while we connect your call.",
        voice="Polly.Matthew"
    )

    # Use <Connect><Stream> for BIDIRECTIONAL audio (allows sending TTS back to caller)
    # Safely construct WebSocket URL from WEBHOOK_BASE_URL
    base_url = settings.WEBHOOK_BASE_URL
    if base_url.startswith("https://"):
        ws_host = base_url.replace("https://", "")
    elif base_url.startswith("http://"):
        ws_host = base_url.replace("http://", "")
    else:
        ws_host = base_url
    ws_host = ws_host.rstrip("/")
    stream_url = f"wss://{ws_host}/twilio/media-stream/{session_id}"

    # <Connect><Stream> creates a bidirectional stream:
    # - We receive caller's audio (inbound track) for STT
    # - We can send audio back to caller (TTS) via WebSocket media messages
    # Note: The call stays connected until WebSocket closes - no pause needed
    connect = Connect()
    stream = Stream(url=stream_url)
    stream.parameter(name="session_id", value=session_id)
    stream.parameter(name="call_id", value=call_id)
    stream.parameter(name="twilio_call_sid", value=CallSid)
    stream.parameter(name="caller_phone_number", value=To)
    stream.parameter(name="direction", value="outbound")
    connect.append(stream)
    response.append(connect)

    logger.info(f"Started media stream for outbound call: session={session_id}")

    return Response(
        content=str(response),
        media_type="application/xml"
    )


@router.post("/voice/status")
async def handle_call_status(
    request: Request,
    CallSid: str = Form(...),
    CallStatus: str = Form(...),
    CallDuration: Optional[str] = Form(default=None),
    From: Optional[str] = Form(default=None),
    To: Optional[str] = Form(default=None),
    db_client=Depends(get_db_client),
    rabbitmq_client=Depends(get_rabbitmq_client),
    _: bool = Depends(verify_twilio_signature)
):
    """
    Handle Twilio call status callbacks.

    Status values: queued, ringing, in-progress, completed, busy, failed, no-answer, canceled
    """
    logger.info(f"Call status update: CallSid={CallSid}, Status={CallStatus}, Duration={CallDuration}")

    # Map Twilio status to VOS call status
    status_map = {
        "queued": "ringing_inbound",
        "ringing": "ringing_inbound",
        "in-progress": "connected",
        "completed": "ended",
        "busy": "missed",
        "failed": "ended",
        "no-answer": "missed",
        "canceled": "ended"
    }

    vos_status = status_map.get(CallStatus, CallStatus)

    # First try to get call_id from query params (for outbound calls we pass these directly)
    call_id = request.query_params.get("call_id")
    session_id = request.query_params.get("session_id")

    # Fall back to database lookup if not in query params
    if not call_id or not session_id:
        call_record = await db_client.get_call_by_twilio_sid(CallSid)
        if call_record:
            call_id = call_record["call_id"]
            session_id = call_record["session_id"]

    if call_id:
        # When call is answered (in-progress), notify api_gateway to transition to CONNECTED
        # This prevents the 30-second RINGING_TIMEOUT from ending the call prematurely
        if CallStatus == "in-progress":
            logger.info(f"Call answered - notifying api_gateway: call_id={call_id}, CallSid={CallSid}")
            await _notify_call_answered(call_id, CallSid)

        # Publish status update for ended calls
        if CallStatus in ["completed", "busy", "failed", "no-answer", "canceled"]:
            await rabbitmq_client.publish_call_event(
                event_type="call_ended",
                session_id=session_id or f"twilio_{CallSid}",
                call_id=call_id,
                twilio_call_sid=CallSid,
                phone_number=From,
                metadata={
                    "final_status": CallStatus,
                    "duration": CallDuration,
                    "to_number": To
                }
            )
    else:
        logger.warning(f"Could not find call_id for CallSid={CallSid}, cannot notify api_gateway")

    return {"status": "received"}


async def _notify_call_answered(call_id: str, twilio_call_sid: str):
    """
    Notify api_gateway that the Twilio call was answered.
    This transitions the call from RINGING to CONNECTED state.
    """
    import httpx

    api_gateway_url = os.getenv("API_GATEWAY_URL", "http://api_gateway:8000")

    try:
        # Read internal API key
        try:
            with open("/shared/internal_api_key", "r") as f:
                internal_key = f.read().strip()
        except FileNotFoundError:
            logger.warning("Internal API key not found for call answered notification")
            internal_key = ""

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{api_gateway_url}/api/v1/calls/{call_id}/answer",
                json={
                    "answered_by": "user",  # The called party answered
                    "twilio_call_sid": twilio_call_sid
                },
                headers={"X-Internal-Key": internal_key},
                timeout=10.0
            )

            if response.status_code == 200:
                logger.info(f"Notified api_gateway that call {call_id} was answered")
            else:
                logger.warning(f"Failed to notify call answered: HTTP {response.status_code} - {response.text}")

    except Exception as e:
        logger.error(f"Error notifying call answered: {e}")
