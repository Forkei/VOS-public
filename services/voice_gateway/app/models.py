"""
Voice Gateway Data Models
Pydantic models for request/response validation and type safety
"""

from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime
from enum import Enum


class VoiceState(str, Enum):
    """Voice session states"""
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    ERROR = "error"


class AudioFormat(BaseModel):
    """Audio format specification"""
    codec: Literal["opus", "aac", "pcm", "wav"] = "opus"
    container: Literal["webm", "ogg", "m4a", "wav"] = "webm"
    sample_rate: int = Field(16000, ge=8000, le=48000)
    channels: int = Field(1, ge=1, le=2)
    bitrate: int = Field(16000, ge=8000, le=128000)


class StartSessionPayload(BaseModel):
    """Payload for start_session message"""
    platform: Literal["web", "ios", "android", "desktop"] = "web"
    audio_format: AudioFormat
    language: str = "en"
    voice_preference: str = "default"


class WebSocketMessage(BaseModel):
    """Base WebSocket message structure"""
    type: str
    payload: dict


class TranscriptionResult(BaseModel):
    """Transcription result from Deepgram"""
    text: str
    is_final: bool
    confidence: float
    timestamp: Optional[datetime] = None


class VoiceSessionInfo(BaseModel):
    """Voice session information"""
    session_id: str
    voice_session_id: str
    state: VoiceState
    platform: str
    audio_format: AudioFormat
    started_at: datetime
    last_activity: datetime


class StatusMessage(BaseModel):
    """Status message to client"""
    type: str
    payload: dict

    @classmethod
    def create(cls, event_type: str, **kwargs):
        """Create a status message with timestamp"""
        return cls(
            type=event_type,
            payload={
                **kwargs,
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
        )


class ErrorSeverity(str, Enum):
    """Error severity levels"""
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class ErrorMessage(BaseModel):
    """Error message structure"""
    code: str
    message: str
    severity: ErrorSeverity = ErrorSeverity.ERROR
    retry_possible: bool = True
    timestamp: datetime = Field(default_factory=datetime.utcnow)
