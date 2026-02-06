# Data Models and Schemas

## Task Management

### TaskCreate (Input)
```python
{
    "creator_id": "primary_agent",      # Optional
    "title": "Process user request",    # Required
    "description": "Details...",        # Optional
    "assignee_ids": ["weather_agent"]   # Optional
}
```

### Task (Database/Output)
```python
{
    "id": "uuid",
    "created_at": "2024-01-01T00:00:00Z",
    "creator_id": "primary_agent",
    "title": "Process user request",
    "description": "Details...",
    "status": "pending",  # pending|in_progress|completed|archived
    "assignee_ids": ["weather_agent"]
}
```

## Notification Schema

### Base Notification
```python
{
    "notification_id": "uuid",
    "timestamp": "2024-01-01T00:00:00Z",
    "recipient_agent_id": "weather_agent",
    "notification_type": "user_message",
    "source": "api_gateway",
    "payload": {...}  # Type-specific
}
```

### Notification Types & Payloads

#### user_message
```python
{
    "content": "What's the weather?",
    "content_type": "text",  # text|voice_transcript|image_id
    "session_id": "device_laptop_xyz"
}
```

#### agent_message
```python
{
    "sender_agent_id": "primary_agent",
    "content": "Process this request",
    "attachments": ["doc_id_123"]
}
```

#### task_assignment
```python
{
    "task_id": "uuid",
    "task_description": "Get weather for NYC",
    "priority": "normal",  # low|normal|high|critical
    "deadline": "2024-01-02T00:00:00Z",
    "context": {...}
}
```

#### tool_result
```python
{
    "tool_name": "weather_agent.get_forecast",
    "status": "SUCCESS",  # SUCCESS|FAILURE
    "result": {"temperature": 72},
    "error_message": null
}
```

#### system_alert
```python
{
    "alert_type": "TIMER",  # TIMER|ALARM|SCHEDULED_EVENT
    "alert_name": "daily_sync",
    "message": "Time for daily sync"
}
```

#### status_update
```python
{
    "agent_id": "weather_agent",
    "status": "busy",  # idle|busy|offline|error
    "current_task": "Fetching forecast",
    "capacity": 0.8,  # 0.0-1.0
    "message": "Processing request"
}
```

#### capability_broadcast
```python
{
    "agent_id": "weather_agent",
    "capabilities": ["get_weather", "get_forecast"],
    "version": "1.0.0",
    "status": "available"
}
```

## Tool Result Standard

### ToolResult Schema
```python
class ToolResult:
    tool_name: str           # Tool identifier
    status: str             # SUCCESS or FAILURE
    result: Dict[str, Any]  # Success data
    error_message: str      # Failure reason
```

### Success Example
```python
ToolResult(
    tool_name="create_task",
    status="SUCCESS",
    result={"task_id": "uuid", "message": "Task created"},
    error_message=None
)
```

### Failure Example
```python
ToolResult(
    tool_name="create_task",
    status="FAILURE",
    result=None,
    error_message="Database connection failed"
)
```

## Database Schema

### SQL Tables
```sql
-- Main task table
CREATE TABLE tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMP DEFAULT NOW(),
    creator_id VARCHAR(255),
    title VARCHAR(500) NOT NULL,
    description TEXT,
    status VARCHAR(50) DEFAULT 'pending'
);

-- Many-to-many assignees
CREATE TABLE task_assignees (
    task_id UUID REFERENCES tasks(id) ON DELETE CASCADE,
    assignee_id VARCHAR(255) NOT NULL,
    PRIMARY KEY (task_id, assignee_id)
);

-- Message history (agent conversations)
CREATE TABLE messages (
    id SERIAL PRIMARY KEY,
    agent_id VARCHAR(255) NOT NULL,
    conversation_id VARCHAR(255),
    role VARCHAR(50) NOT NULL,  -- user|assistant|system|tool
    content TEXT,
    tool_calls TEXT,  -- JSON array of tool calls
    tool_results TEXT,  -- JSON array of tool results
    timestamp TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_messages_agent_id ON messages(agent_id);
CREATE INDEX idx_messages_conversation_id ON messages(conversation_id);
CREATE INDEX idx_messages_timestamp ON messages(timestamp);

-- Agent state tracking
CREATE TABLE agent_state (
    agent_id VARCHAR(255) PRIMARY KEY,
    status VARCHAR(50) DEFAULT 'idle',  -- idle|thinking|executing_tools|offline
    processing_state VARCHAR(50) DEFAULT 'idle',
    last_heartbeat TIMESTAMP DEFAULT NOW(),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

## Memory System Schema (Weaviate)

### Memory Object
```python
{
    "memory_id": "uuid",
    "memory_type": "user_preference",  # See MemoryType enum
    "content": "User prefers dark mode interfaces",
    "embedding": [0.123, ...],  # 768-dim vector (nomic-embed-text)
    "scope": "shared",  # individual|shared
    "source": "primary_agent",
    "agent_id": "primary_agent",
    "importance": 0.8,  # 0.0-1.0
    "confidence": 0.9,  # 0.0-1.0
    "tags": ["preference", "ui", "theme"],
    "related_memories": ["memory_id_2"],
    "created_at": "2025-10-06T12:00:00Z",
    "updated_at": "2025-10-06T12:00:00Z",
    "last_accessed": "2025-10-06T14:30:00Z",
    "access_count": 5
}
```

### Memory Types (Enum)
- `user_preference`: User settings and preferences
- `user_fact`: Facts about the user
- `conversation_context`: Context from conversations
- `agent_procedure`: Learned procedures for agents
- `knowledge`: General knowledge and facts
- `event_pattern`: Recognized event patterns
- `error_handling`: Error handling strategies
- `proactive_action`: Proactive behavior patterns

## API Response Formats

### Success Response
```json
{
    "status": "success",
    "data": {...},
    "message": "Operation completed"
}
```

### Error Response
```json
{
    "status": "error",
    "error": "ValidationError",
    "message": "Title is required",
    "details": {...}
}