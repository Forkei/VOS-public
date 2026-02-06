"""
Frontend notification schemas for WebSocket real-time updates.

Supports multiple notification types for flexible frontend updates:
- new_message: Agent messages to users
- timer_alert: Timer/alarm notifications
- agent_status: Agent status changes
- app_interaction: Agent interactions with applications
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
from uuid import UUID, uuid4


class FrontendNotificationType(str, Enum):
    """Types of notifications that can be sent to the frontend."""
    NEW_MESSAGE = "new_message"                   # Agent sent a message to user
    TIMER_ALERT = "timer_alert"                   # Timer/alarm triggered
    AGENT_STATUS = "agent_status"                 # Agent status changed (idle/active/sleeping)
    AGENT_ACTION_STATUS = "agent_action_status"   # Agent action description (e.g. "Searching...")
    APP_INTERACTION = "app_interaction"           # Agent interacted with an app
    SYSTEM_ALERT = "system_alert"                 # System-level alerts
    BROWSER_SCREENSHOT = "browser_screenshot"     # Browser agent screenshot


class NewMessagePayload(BaseModel):
    """Payload for new_message notification type."""
    session_id: str = Field(..., description="Conversation session ID")
    message_id: int = Field(..., description="Database message ID")
    agent_id: str = Field(..., description="Agent that sent the message")
    content: str = Field(..., description="Message content")
    content_type: str = Field(default="text", description="Content type (text, code, etc.)")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Message timestamp")
    # Audio/voice metadata (optional fields for voice responses)
    input_mode: Optional[str] = Field(default="text", description="Input mode: 'text' or 'voice'")
    voice_message_id: Optional[int] = Field(default=None, description="Voice message ID if audio exists")
    audio_file_path: Optional[str] = Field(default=None, description="Path to audio file")
    audio_url: Optional[str] = Field(default=None, description="Signed URL for audio playback")
    audio_duration_ms: Optional[int] = Field(default=None, description="Audio duration in milliseconds")
    # Attachment and document references
    attachment_ids: Optional[list[str]] = Field(default=None, description="Image attachment IDs")
    document_ids: Optional[list[str]] = Field(default=None, description="Document reference IDs")


class TimerAlertPayload(BaseModel):
    """Payload for timer_alert notification type."""
    timer_id: str = Field(..., description="Timer/alarm ID")
    agent_id: str = Field(..., description="Agent that set the timer")
    message: str = Field(..., description="Alert message")
    timer_type: str = Field(..., description="Type: timer, alarm, or reminder")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class AgentStatusPayload(BaseModel):
    """Payload for agent_status notification type."""
    agent_id: str = Field(..., description="Agent ID")
    status: str = Field(..., description="New status: active, sleeping, off")
    processing_state: str = Field(..., description="Processing state: idle, thinking, executing_tools")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class AppInteractionPayload(BaseModel):
    """Payload for app_interaction notification type."""
    agent_id: str = Field(..., description="Agent performing interaction")
    app_name: str = Field(..., description="Application name")
    action: str = Field(..., description="Action performed")
    result: Optional[Dict[str, Any]] = Field(default=None, description="Interaction result")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class SystemAlertPayload(BaseModel):
    """Payload for system_alert notification type."""
    alert_level: str = Field(..., description="Level: info, warning, error, critical")
    message: str = Field(..., description="Alert message")
    source: str = Field(..., description="Source of the alert")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class FrontendNotification(BaseModel):
    """
    Standardized notification structure for frontend WebSocket updates.

    This flexible schema supports multiple notification types while maintaining
    a consistent structure for frontend consumption.
    """
    notification_id: UUID = Field(default_factory=uuid4, description="Unique notification ID")
    notification_type: FrontendNotificationType = Field(..., description="Type of notification")
    session_id: Optional[str] = Field(default=None, description="Session ID (for routing)")
    user_id: Optional[str] = Field(default=None, description="User ID (for multi-user support)")
    payload: Dict[str, Any] = Field(..., description="Type-specific payload")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Notification timestamp")
    delivery_id: Optional[UUID] = Field(default=None, description="Delivery tracking ID")

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            UUID: lambda v: str(v)
        }


class PendingNotification(BaseModel):
    """Database model for tracking undelivered notifications."""
    id: UUID = Field(default_factory=uuid4)
    session_id: str = Field(..., description="Target session")
    notification: FrontendNotification = Field(..., description="The notification to deliver")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    delivered_at: Optional[datetime] = Field(default=None)
    delivery_attempts: int = Field(default=0, description="Number of delivery attempts")


class WebSocketMessage(BaseModel):
    """Message format for WebSocket communication."""
    type: str = Field(..., description="Message type: notification, ping, pong, ack")
    data: Optional[Dict[str, Any]] = Field(default=None, description="Message data")
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class BrowserScreenshotPayload(BaseModel):
    """Payload for browser_screenshot notification type."""
    agent_id: str = Field(..., description="Browser agent ID")
    session_id: Optional[str] = Field(default=None, description="Session ID")
    screenshot_base64: str = Field(..., description="Base64 encoded PNG screenshot")
    current_url: Optional[str] = Field(default=None, description="Current browser URL")
    task: Optional[str] = Field(default=None, description="Task being performed")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
