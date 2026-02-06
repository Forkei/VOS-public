from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    text: str
    session_id: Optional[str] = "user_session_default"  # Session identifier for conversation tracking
    user_timezone: Optional[str] = None  # User's IANA timezone name (e.g., "America/New_York")
    attachment_ids: Optional[List[str]] = None  # List of attachment IDs for images (vision support)


class TaskCreate(BaseModel):
    """Schema for creating a new task."""
    created_by: Optional[str] = None  # ID of the agent or user who created the task
    title: str  # Short, human-readable title for the task
    description: Optional[str] = None  # Detailed description of the task's objective
    assignee_ids: Optional[List[str]] = None  # List of agent IDs to assign the task to
    broadcast_updates: Optional[bool] = False  # Whether to notify assignees of task status changes


class TaskUpdate(BaseModel):
    """Schema for updating an existing task."""
    title: Optional[str] = None  # Updated title
    description: Optional[str] = None  # Updated description
    status: Optional[str] = None  # Updated status: 'pending', 'in_progress', 'completed', 'archived'
    assignee_ids: Optional[List[str]] = None  # Updated list of assignee agent IDs
    broadcast_updates: Optional[bool] = None  # Whether to notify assignees of task status changes


class Task(BaseModel):
    """Schema for reading a task from the database."""
    id: int  # Task ID
    created_at: datetime  # Task creation timestamp
    created_by: Optional[str] = None  # Agent or user who created the task
    title: str  # Task title
    description: Optional[str] = None  # Task description
    status: str  # Task status
    assignee_ids: List[str] = []  # List of assigned agent IDs from task_assignees table
    broadcast_updates: bool = False  # Whether to notify assignees of task status changes

    class Config:
        from_attributes = True  # Enable ORM mode for database compatibility


# ============================================
# Notification System Models (VOS Event Schema)
# ============================================

class NotificationType(str, Enum):
    """Enumeration of notification types in the VOS system."""
    USER_MESSAGE = "user_message"
    AGENT_MESSAGE = "agent_message"
    TOOL_RESULT = "tool_result"
    SYSTEM_ALERT = "system_alert"
    EVENT_SUBSCRIPTION = "event_subscription"
    TASK_ASSIGNMENT = "task_assignment"
    STATUS_UPDATE = "status_update"
    CAPABILITY_BROADCAST = "capability_broadcast"


# ============================================
# Payload Models for Each Notification Type
# ============================================

class UserMessagePayload(BaseModel):
    """Payload for user messages."""
    content: str
    content_type: str = Field(default="text", description="Type: text, voice_transcript, image_id, etc.")
    session_id: str = Field(description="Identifies the VOS session (e.g., device_laptop_session_xyz)")


class AgentMessagePayload(BaseModel):
    """Payload for agent-to-agent messages."""
    sender_agent_id: str
    content: str
    attachments: List[str] = Field(default_factory=list, description="Document IDs or other context")


class ToolResultPayload(BaseModel):
    """Payload for tool execution results."""
    tool_name: str = Field(description="Format: agent_name.tool_name")
    status: str = Field(description="SUCCESS or FAILURE")
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None


class SystemAlertPayload(BaseModel):
    """Payload for system alerts and timers."""
    alert_type: str = Field(description="TIMER, ALARM, SCHEDULED_EVENT, etc.")
    alert_name: str
    message: str


class EventSubscriptionPayload(BaseModel):
    """Payload for event subscription triggers."""
    subscription_id: str
    triggered_event: Dict[str, Any] = Field(description="The event that triggered this subscription")
    conditions_met: str = Field(description="Description of conditions that were met")


class TaskAssignmentPayload(BaseModel):
    """Payload for task assignments between agents."""
    task_id: int
    task_description: str
    priority: Optional[str] = Field(default="normal", description="Priority: low, normal, high, critical")
    deadline: Optional[datetime] = None
    context: Optional[Dict[str, Any]] = None


class StatusUpdatePayload(BaseModel):
    """Payload for agent status updates."""
    agent_id: str
    status: str = Field(description="idle, busy, offline, error, etc.")
    current_task: Optional[str] = None
    capacity: Optional[float] = Field(None, description="0.0 to 1.0 representing current load")
    message: Optional[str] = None


class CapabilityBroadcastPayload(BaseModel):
    """Payload for agent capability announcements."""
    agent_id: str
    capabilities: List[str] = Field(description="List of capabilities/tools this agent provides")
    version: str
    status: str = Field(default="available")


# ============================================
# Base Notification Model
# ============================================

class Notification(BaseModel):
    """
    Base notification model for the VOS event system.
    All events published to RabbitMQ must adhere to this structure.
    """
    notification_id: UUID = Field(description="Unique identifier for this notification")
    timestamp: datetime = Field(description="ISO 8601 formatted timestamp")
    recipient_agent_id: str = Field(description="Target agent for this notification")
    notification_type: NotificationType
    source: str = Field(description="Source agent or system component")
    payload: Dict[str, Any] = Field(description="Notification-specific payload data")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            UUID: lambda v: str(v),
        }


class NotificationCreate(BaseModel):
    """Schema for creating a new notification (auto-generates ID and timestamp)."""
    recipient_agent_id: str
    notification_type: NotificationType
    source: str
    payload: Dict[str, Any]


# ============================================
# Conversation Message Models
# ============================================

class SenderType(str, Enum):
    """Enumeration of sender types for conversation messages."""
    USER = "user"
    AGENT = "agent"


class ConversationMessageCreate(BaseModel):
    """Schema for creating a new conversation message."""
    session_id: str
    sender_type: SenderType
    sender_id: Optional[str] = None  # NULL for user messages, agent_id for agent messages
    content: str
    metadata: Optional[Dict[str, Any]] = None


class ConversationMessage(BaseModel):
    """Schema for reading a conversation message from the database."""
    id: int
    session_id: str
    sender_type: SenderType
    sender_id: Optional[str] = None
    content: str
    timestamp: datetime
    metadata: Optional[Dict[str, Any]] = None
    input_mode: Optional[str] = 'text'  # 'text' or 'voice'
    voice_message_id: Optional[int] = None
    audio_file_path: Optional[str] = None  # From joined voice_messages table
    audio_url: Optional[str] = None  # Signed URL for audio playback
    audio_duration_ms: Optional[int] = None  # From joined voice_messages table

    class Config:
        from_attributes = True


class ConversationHistoryResponse(BaseModel):
    """Response schema for conversation history retrieval."""
    session_id: str
    messages: List[ConversationMessage]
    total_messages: int
    timestamp: datetime