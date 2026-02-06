"""
Twilio Client Utility
Handles Twilio REST API operations for outbound calls
"""

import logging
from typing import Optional, Dict, Any

from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

from ..config import settings

logger = logging.getLogger(__name__)

# Global Twilio client
_twilio_client: Optional[Client] = None


def get_twilio_client() -> Client:
    """Get or create Twilio REST client"""
    global _twilio_client

    if _twilio_client is None:
        _twilio_client = Client(
            settings.TWILIO_ACCOUNT_SID,
            settings.TWILIO_AUTH_TOKEN
        )

    return _twilio_client


async def initiate_outbound_call(
    to_number: str,
    session_id: str,
    call_id: str,
    from_number: Optional[str] = None
) -> Dict[str, Any]:
    """
    Initiate an outbound phone call via Twilio.

    Args:
        to_number: Phone number to call (E.164 format)
        session_id: VOS session ID for the call
        call_id: VOS call ID
        from_number: Caller ID (defaults to configured Twilio number)

    Returns:
        Dict with call information including Twilio call SID
    """
    client = get_twilio_client()

    from_number = from_number or settings.TWILIO_PHONE_NUMBER

    # Build the webhook URL for when the call is answered
    # Include session_id and call_id as query parameters - Twilio will include them in the webhook
    from urllib.parse import urlencode
    params = urlencode({"session_id": session_id, "call_id": call_id})
    outbound_url = f"{settings.WEBHOOK_BASE_URL}/twilio/voice/outbound?{params}"
    # Include call_id in status callback URL so we can notify api_gateway when call is answered
    status_params = urlencode({"session_id": session_id, "call_id": call_id})
    status_url = f"{settings.WEBHOOK_BASE_URL}/twilio/voice/status?{status_params}"

    try:
        call = client.calls.create(
            to=to_number,
            from_=from_number,
            url=outbound_url,
            status_callback=status_url,
            status_callback_event=["initiated", "ringing", "answered", "completed"],
            status_callback_method="POST"
        )

        logger.info(f"Initiated outbound call: sid={call.sid}, to={to_number}")

        return {
            "success": True,
            "twilio_call_sid": call.sid,
            "to_number": to_number,
            "from_number": from_number,
            "session_id": session_id,
            "call_id": call_id,
            "status": call.status
        }

    except TwilioRestException as e:
        logger.error(f"Twilio error initiating call: {e}")
        return {
            "success": False,
            "error": str(e),
            "error_code": e.code,
            "to_number": to_number
        }

    except Exception as e:
        logger.error(f"Error initiating outbound call: {e}")
        return {
            "success": False,
            "error": str(e),
            "to_number": to_number
        }


async def end_call(twilio_call_sid: str) -> bool:
    """
    End an active Twilio call.

    Args:
        twilio_call_sid: Twilio call SID

    Returns:
        True if successful
    """
    client = get_twilio_client()

    try:
        call = client.calls(twilio_call_sid).update(status="completed")
        logger.info(f"Ended Twilio call: {twilio_call_sid}")
        return True

    except TwilioRestException as e:
        logger.error(f"Twilio error ending call: {e}")
        return False

    except Exception as e:
        logger.error(f"Error ending call: {e}")
        return False


async def get_call_status(twilio_call_sid: str) -> Optional[Dict[str, Any]]:
    """
    Get the current status of a Twilio call.

    Args:
        twilio_call_sid: Twilio call SID

    Returns:
        Call status information or None
    """
    client = get_twilio_client()

    try:
        call = client.calls(twilio_call_sid).fetch()

        return {
            "twilio_call_sid": call.sid,
            "status": call.status,
            "direction": call.direction,
            "from_number": call.from_,
            "to_number": call.to,
            "duration": call.duration,
            "start_time": call.start_time.isoformat() if call.start_time else None,
            "end_time": call.end_time.isoformat() if call.end_time else None
        }

    except TwilioRestException as e:
        logger.error(f"Twilio error getting call status: {e}")
        return None

    except Exception as e:
        logger.error(f"Error getting call status: {e}")
        return None


async def send_sms(
    to_number: str,
    body: str,
    from_number: Optional[str] = None
) -> Dict[str, Any]:
    """
    Send an SMS message via Twilio.

    Args:
        to_number: Phone number to send SMS to (E.164 format)
        body: Message body to send
        from_number: Sender phone number (defaults to configured Twilio number)

    Returns:
        Dict with success status, message SID, and status
    """
    client = get_twilio_client()

    from_number = from_number or settings.TWILIO_PHONE_NUMBER

    try:
        message = client.messages.create(
            to=to_number,
            from_=from_number,
            body=body
        )

        logger.info(f"Sent SMS: sid={message.sid}, to={to_number}")

        return {
            "success": True,
            "twilio_message_sid": message.sid,
            "to_number": to_number,
            "status": message.status
        }

    except TwilioRestException as e:
        logger.error(f"Twilio error sending SMS: {e}")
        return {
            "success": False,
            "error": str(e),
            "error_code": e.code,
            "to_number": to_number
        }

    except Exception as e:
        logger.error(f"Error sending SMS: {e}")
        return {
            "success": False,
            "error": str(e),
            "to_number": to_number
        }


async def send_dtmf(twilio_call_sid: str, digits: str) -> bool:
    """
    Send DTMF tones to an active call.

    Args:
        twilio_call_sid: Twilio call SID
        digits: DTMF digits to send (0-9, *, #, w for wait)

    Returns:
        True if successful
    """
    import re
    from twilio.twiml.voice_response import VoiceResponse

    # Validate digits to prevent injection - only allow valid DTMF characters
    # Valid: 0-9, *, #, w (wait), W (longer wait)
    if not re.match(r'^[0-9*#wW]+$', digits):
        logger.error(f"Invalid DTMF digits (potential injection attempt): {digits!r}")
        return False

    client = get_twilio_client()

    try:
        # Use Twilio SDK's TwiML builder for safe XML generation
        response = VoiceResponse()
        response.play(digits=digits)
        twiml = str(response)

        client.calls(twilio_call_sid).update(twiml=twiml)
        logger.info(f"Sent DTMF to call {twilio_call_sid}: {digits}")
        return True

    except TwilioRestException as e:
        logger.error(f"Twilio error sending DTMF: {e}")
        return False

    except Exception as e:
        logger.error(f"Error sending DTMF: {e}")
        return False
