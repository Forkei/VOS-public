# Phase 1: MVP - The Reactive Assistant

**Objective:** To build the simplest possible end-to-end version of the system: a single, reactive agent that can respond to user text input by using basic, hard-coded tools.

**Key Outcomes by End of Phase:**
- A runnable Primary Agent service.
- Two functional, directly callable tools (Math & Search).
- A simple command-line or basic web interface for interaction.

---

### Tasks

#### 1. Build the VOS API Gateway (v1)
- **Status:** [x] Complete
- **Task:** Implement the core logic for receiving user messages.
- **Sub-Tasks:**
    - [x] Create a `/api/v1/chat` POST endpoint in the FastAPI service.
    - [x] Endpoint publishes notifications to `primary_agent_queue`.
    - [x] Additional endpoints:
        - [x] `/api/v1/tasks/*` - Task management CRUD
        - [x] `/api/v1/memories/*` - Memory system CRUD
        - [x] `/api/v1/message-history/*` - Message history
- **Notes:** Full API Gateway operational with multiple routers

#### 2. Implement the Primary Agent (v1)
- **Status:** [x] Complete
- **Task:** Create a standalone Python service for the Primary Agent.
- **Sub-Tasks:**
    - [x] Create its own `Dockerfile`.
    - [x] Connect to RabbitMQ and consume from `primary_agent_queue`.
    - [x] Implement the autonomous agent loop using VOS SDK:
        1. [x] Receive notification
        2. [x] Build context with message history
        3. [x] Call Gemini LLM with tool definitions
        4. [x] Parse LLM response for tool calls
        5. [x] Execute tools via event-driven pattern
        6. [x] Update state and acknowledge message
    - [x] Integrated with VOS SDK (60 lines vs 311 originally planned)
- **Notes:** Uses VOSAgentImplementation base class, fully autonomous

#### 3. Create the First Tools
- **Status:** [x] Partial
- **Task:** Implement basic tools as BaseTool subclasses.
- **Sub-Tasks:**
    - [x] Create `/services/tools/` directory structure.
    - [x] Implement weather tools (5 tools):
        - GetWeatherTool, GetForecastTool, GetWeatherByCoordinatesTool
        - GetUVIndexTool, GetAirQualityTool
    - [x] Implement standard tools:
        - SendUserMessageTool, SendAgentMessageTool
        - CreateTaskTool, UpdateTaskTool, GetTasksTool
        - SleepTool, SetTimerTool, SetAlarmTool, ShutdownTool
    - [x] Implement memory tools:
        - CreateMemoryTool, SearchMemoryTool, GetMemoryTool
        - UpdateMemoryTool, DeleteMemoryTool
    - [ ] Math tool (not yet implemented)
    - [ ] Search tool (not yet implemented)
- **Notes:** Tools use event-driven pattern, send notifications not return values

#### 4. Develop a Simple User Interface
- **Status:** [ ] Not Started
- **Task:** Create a minimal interface to talk to the agent.
- **Sub-Tasks:**
    - [ ] **Option A (Easiest):** Create a `cli.py` script that takes user input in a loop and makes HTTP requests to the API Gateway.
    - [ ] **Option B (Better):** Create a single `index.html` file with basic JavaScript to create a chat box that communicates with the API Gateway.
- **Notes:** Currently testing via curl commands. GUI deferred to Phase 5 (VOS Applications).