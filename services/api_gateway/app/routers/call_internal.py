"""
Call Internal Router

Handles internal callback endpoints from Voice Gateway.
Used for routing TTS audio and STT transcriptions back to the user's WebSocket.
"""

from fastapi import APIRouter, HTTPException, Header, Body, Request, status
from pydantic import BaseModel
from typing import Optional
import logging
import base64
import os

from app.routers.call_websocket import send_tts_to_user, send_transcription_to_user

router = APIRouter(tags=["calls_internal"])
logger = logging.getLogger(__name__)

# Security: Load internal API key
INTERNAL_API_KEY = None

def get_internal_api_key():
    global INTERNAL_API_KEY
    if INTERNAL_API_KEY:
        return INTERNAL_API_KEY
        
    try:
        with open("/shared/internal_api_key", "r") as f:
            INTERNAL_API_KEY = f.read().strip()
            return INTERNAL_API_KEY
    except Exception:
        # Fallback to env var if shared file not found
        key = os.getenv("INTERNAL_API_KEY")
        if not key:
            logger.error("No internal API key found: /shared/internal_api_key missing and INTERNAL_API_KEY env var not set")
        return key

class TTSAudioRequest(BaseModel):
    session_id: str
    call_id: str
    audio_b64: str
    text: Optional[str] = None
    agent_id: Optional[str] = None

class TranscriptionRequest(BaseModel):
    session_id: str
    call_id: str
    text: str
    is_final: bool = False
    confidence: Optional[float] = None

@router.post("/calls/internal/tts-audio")
async def internal_tts_audio(
    request: TTSAudioRequest,
    x_internal_key: str = Header(..., alias="X-Internal-Key")
):
    """Receive TTS audio from voice_gateway and forward to user"""
    expected_key = get_internal_api_key()
    if x_internal_key != expected_key:
        logger.warning(f"Internal auth failed. Expected: {expected_key[:4]}... Got: {x_internal_key[:4]}...")
        raise HTTPException(status_code=403, detail="Invalid internal key")

    try:
        # Decode base64 audio
        audio_data = base64.b64decode(request.audio_b64)
        
        # Forward to user
        await send_tts_to_user(request.session_id, audio_data, request.text)
        
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error processing internal TTS: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/calls/internal/transcription")
async def internal_transcription(
    request: TranscriptionRequest,
    x_internal_key: str = Header(..., alias="X-Internal-Key")
):
    """Receive transcription from voice_gateway and forward to user"""
    expected_key = get_internal_api_key()
    if x_internal_key != expected_key:
        raise HTTPException(status_code=403, detail="Invalid internal key")

    try:
        await send_transcription_to_user(
            request.session_id, 
            request.text, 
            request.is_final, 
            request.confidence
        )
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error processing internal transcription: {e}")
        raise HTTPException(status_code=500, detail=str(e))
