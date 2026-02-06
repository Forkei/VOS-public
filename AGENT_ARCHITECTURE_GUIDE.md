# VOS Backend Architecture Analysis Report
## Focus: Creating a Notes/Document Agent

Based on my analysis of the calendar_agent service and the overall VOS architecture, here's a comprehensive breakdown:

## 🏗️ **Overall Architecture Pattern**

The VOS system uses a **microservices architecture** with:
- **RabbitMQ** for asynchronous message passing between agents
- **PostgreSQL** for persistent data storage
- **VOS SDK** as a common framework for all agents
- **API Gateway** as the central communication hub
- **Individual agent services** that are self-contained, containerized applications

---

## 📦 **Agent Structure (Using calendar_agent as Template)**

Each agent service consists of these key files:

### **1. Core Files**
```
services/agents/calendar_agent/
├── calendar_agent.py       # Main agent implementation class
├── main.py                 # Entry point (just imports and runs main())
├── system_prompt.txt       # Agent's system prompt/instructions
├── requirements.txt        # Python dependencies specific to this agent
└── Dockerfile             # Container definition
```

### **2. calendar_agent.py Structure**
```python
class CalendarAgent(VOSAgentImplementation):
    # Define tools this agent has access to
    TOOLS = [
        # Standard agent tools (messaging, tasks, memory, sleep, shutdown)
        SendAgentMessageTool, CreateTaskTool, UpdateTaskTool, ...

        # Domain-specific tools (calendar-specific)
        CreateCalendarEventTool, ListCalendarEventsTool, ...
    ]

    def __init__(self, config: AgentConfig):
        super().__init__(config, "agent description")
        self.system_prompt = self.generate_system_prompt()

def main():
    config = AgentConfig.from_env(
        agent_name="calendar_agent",
        agent_display_name="Calendar & Scheduling Service"
    )
    agent = CalendarAgent(config)
    agent.start()  # Blocks and runs the agent
```

### **3. Dockerfile Pattern**
```dockerfile
FROM python:3.11-slim
WORKDIR /app

# Install system dependencies (gcc for compiling)
# Copy requirements FIRST (for caching)
# Install VOS SDK from /sdk directory
# Copy tools directory (shared across agents)
# Copy agent-specific files
# Create non-root user for security
# Set CMD to run main.py
```

---

## 🔧 **VOS SDK Framework**

The SDK (`/sdk/vos_sdk/`) provides:

### **VOSAgentImplementation Base Class**
- Handles RabbitMQ connection and message consumption
- Manages agent processing state (idle → thinking → executing_tools)
- Calls Gemini LLM with context to decide tool usage
- Executes tools and updates message history
- Automatic tool registration from TOOLS list

### **Key SDK Components**
- `AgentConfig`: Loads configuration from environment variables
- `DatabaseClient`: PostgreSQL connection for state/history
- `ContextBuilder`: Builds LLM context from notifications + history
- `BaseTool`: Base class all tools must inherit from
- `ToolResult`: Standard return format for tool execution

---

## 🛠️ **Tools Architecture**

Tools are organized in `/services/tools/`:

```
tools/
├── __init__.py              # Central exports
├── standard/                # Common tools (messaging, tasks, sleep, shutdown)
├── memory/                  # Memory storage/retrieval tools
├── weather/                 # Weather-specific tools
├── search/                  # Search-specific tools
└── calendar/                # Calendar-specific tools
    ├── __init__.py
    ├── calendar_event_tools.py
    └── reminder_tools.py
```

### **Tool Implementation Pattern**
Each tool inherits from `BaseTool`:
```python
class CreateCalendarEventTool(BaseTool):
    name = "create_calendar_event"
    description = "Creates a new calendar event"
    parameters = {...}  # JSON schema for parameters

    def execute(self, context: Dict[str, Any]) -> ToolResult:
        # 1. Extract parameters from context
        # 2. Connect to database directly via psycopg2
        # 3. Perform business logic
        # 4. Publish notifications if needed (app interactions)
        # 5. Return ToolResult(success=True/False, data=...)
```

**Key insight**: Tools connect **directly to PostgreSQL** - they don't go through the API Gateway for data access.

---

## 💾 **Database Schema**

Calendar agent uses these tables (from `/services/api_gateway/app/sql/vos_sdk_schema.sql`):

```sql
CREATE TABLE calendar_events (
    id SERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    all_day BOOLEAN DEFAULT false,
    location VARCHAR(255),
    recurrence_rule TEXT,
    exception_dates JSONB DEFAULT '[]'::jsonb,
    auto_reminders JSONB DEFAULT '[]'::jsonb,
    created_by VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE reminders (
    id SERIAL PRIMARY KEY,
    title VARCHAR(255),
    description TEXT,
    trigger_time TIMESTAMPTZ NOT NULL,
    event_id INTEGER REFERENCES calendar_events(id) ON DELETE CASCADE,
    recurrence_rule TEXT,
    target_agents TEXT[] DEFAULT ARRAY['primary_agent'],
    created_by VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'::jsonb
);
```

---

## 🔄 **Agent Communication Flow**

1. **User sends message** → API Gateway receives it
2. **API Gateway** publishes notification to `primary_agent_queue` via RabbitMQ
3. **Primary Agent** receives notification, processes it
4. **Primary Agent** may delegate to specialist agents using `send_agent_message` tool
   - Publishes to `calendar_agent_queue`, `weather_agent_queue`, etc.
5. **Specialist agent** processes request, executes tools
6. **Specialist agent** reports back to `primary_agent` using `send_agent_message`
7. **Primary Agent** aggregates results and responds to user

### **Message Format** (Notifications)
```python
{
    "notification_id": "uuid",
    "notification_type": "user_message" | "agent_message" | "tool_result" | ...,
    "timestamp": "ISO-8601",
    "payload": {
        "text": "message content",
        "sender_agent_id": "primary_agent",
        "recipient_agent_id": "calendar_agent",
        "session_id": "uuid",
        "user_timezone": "America/New_York",  # Important for time handling
        # ... other fields
    }
}
```

---

## 🌟 **Key Patterns & Best Practices**

### **1. Agent Registration**
Agents are registered in docker-compose with:
- Environment variables for config (RABBITMQ_HOST, DATABASE_HOST, etc.)
- Agent name and display name
- Dependencies on infrastructure services (rabbitmq, postgres, api_gateway)

### **2. System Prompt**
- Stored in `system_prompt.txt` in agent directory
- Contains `{tools}` placeholder that SDK auto-populates
- Defines agent behavior, communication protocols, output schema

### **3. Shutdown Behavior**
- Agents should call `shutdown_agent` tool when idle to save resources
- Only use `sleep` if waiting for scheduled task
- Always report results before shutting down

### **4. Metrics**
- Optional Prometheus metrics via `/app/shared/metrics.py`
- Exposes metrics on port 8080

### **5. Environment Configuration**
Common env vars for agents:
```bash
AGENT_NAME=calendar_agent
AGENT_DISPLAY_NAME=Calendar & Scheduling Service
RABBITMQ_HOST=rabbitmq
DATABASE_HOST=postgres
API_GATEWAY_HOST=api_gateway
WEAVIATE_HOST=weaviate
MAX_CONVERSATION_MESSAGES=15  # Message history limit
```

---

## 📝 **Template for Notes/Document Agent**

### **What You'll Need to Create:**

#### **1. Agent Service Files**
```
services/agents/notes_agent/
├── notes_agent.py           # Main implementation
├── main.py                  # Entry point
├── system_prompt.txt        # Agent instructions
├── requirements.txt         # Dependencies
└── Dockerfile              # Container definition
```

#### **2. Domain Tools**
```
services/tools/notes/
├── __init__.py
├── note_tools.py           # CreateNote, ListNotes, UpdateNote, DeleteNote
└── document_tools.py       # CreateDocument, GetDocument, etc.
```

#### **3. Database Schema** (add to vos_sdk_schema.sql)
```sql
CREATE TABLE notes (
    id SERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    content TEXT,
    tags TEXT[],
    folder VARCHAR(255),
    created_by VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE documents (
    id SERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    content TEXT,
    document_type VARCHAR(50),  -- markdown, txt, rich_text
    folder VARCHAR(255),
    tags TEXT[],
    created_by VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'::jsonb
);
```

#### **4. Docker Compose Entry**
Add to both `docker-compose.yml` and `docker-compose.backend-only.yml`:
```yaml
notes_agent:
  build:
    context: .
    dockerfile: ./services/agents/notes_agent/Dockerfile
  container_name: vos_notes_agent
  env_file:
    - ./services/.env
  environment:
    - RABBITMQ_HOST=rabbitmq
    - DATABASE_HOST=postgres
    - AGENT_NAME=notes_agent
    - AGENT_DISPLAY_NAME=Notes & Documents Service
    # ... other standard env vars
  ports:
    - "8006:8080"  # Metrics endpoint
  depends_on:
    rabbitmq:
      condition: service_healthy
    postgres:
      condition: service_healthy
    api_gateway:
      condition: service_healthy
  restart: unless-stopped
  networks:
    - vos_network
```

#### **5. Tools to Implement**
- `CreateNoteTool` - Create a new note
- `ListNotesTool` - List notes with filtering (by tag, folder, search)
- `GetNoteTool` - Get a specific note by ID
- `UpdateNoteTool` - Update note content/metadata
- `DeleteNoteTool` - Delete a note
- `CreateDocumentTool` - Create longer-form document
- `ListDocumentsTool` - List documents
- `GetDocumentTool` - Retrieve document
- `UpdateDocumentTool` - Update document
- `DeleteDocumentTool` - Delete document
- `SearchNotesTool` - Full-text search across notes/documents

---

## 🎯 **Implementation Steps**

1. **Copy calendar_agent as template** - rename files and classes
2. **Create database schema** - add tables to vos_sdk_schema.sql
3. **Implement tools** - create notes/ directory with tool classes
4. **Update agent class** - define TOOLS list with standard + notes tools
5. **Write system_prompt.txt** - define agent behavior and capabilities
6. **Create Dockerfile** - follow calendar_agent pattern
7. **Add to docker-compose** - add service definition
8. **Update tools/__init__.py** - export new notes tools
9. **Test** - start service and test via API Gateway

---

## 🔑 **Critical Insights**

1. **Tools are self-contained** - They connect directly to DB, no API calls
2. **Agent registration is automatic** - Just name queue correctly (`{agent_name}_queue`)
3. **VOS SDK handles everything** - Message loop, LLM calls, tool execution
4. **System prompt is key** - Defines agent personality and behavior
5. **Always include standard tools** - Messaging, tasks, memory, shutdown
6. **Use metadata JSONB** - For extensibility without schema changes
7. **Timezone awareness** - Handle `user_timezone` from payload for timestamps

---

This architecture makes it very straightforward to create new agents - you're essentially just defining the tools and system prompt, then letting the SDK handle all the infrastructure!
