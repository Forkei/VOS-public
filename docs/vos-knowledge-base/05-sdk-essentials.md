# SDK Essentials

## SDK Location
`/sdk/vos_sdk/`

## SDK Architecture (Production-Ready)

The VOS SDK is fully implemented and operational. Agents built with the SDK are autonomous, LLM-powered services that handle notifications, execute tools, and manage their own state.

### Core Components

```
vos_sdk/
├── __init__.py                 # Exports VOSAgentImplementation, AgentConfig, BaseTool
├── core/
│   ├── agent.py               # VOSAgent and VOSAgentImplementation
│   ├── config.py              # AgentConfig with environment loading
│   ├── context.py             # ContextBuilder for LLM formatting
│   ├── database.py            # DatabaseClient for message history
│   └── __init__.py
├── tools/
│   ├── base.py                # BaseTool abstract class
│   ├── task_tools.py          # Task management tools (legacy)
│   └── __init__.py
└── schemas.py                 # ToolResult and core data models
```

## Using the SDK

### 1. Create an Agent

```python
from vos_sdk import AgentConfig, VOSAgentImplementation
from tools.standard import AGENT_TOOLS
from tools.weather import WEATHER_TOOLS

class WeatherAgent(VOSAgentImplementation):
    # Define tools this agent can use
    TOOLS = AGENT_TOOLS + WEATHER_TOOLS

    def __init__(self, config):
        super().__init__(config, "Weather information provider")

# Start the agent
if __name__ == "__main__":
    config = AgentConfig.from_env("weather_agent")
    agent = WeatherAgent(config)
    agent.start()
```

That's it! The SDK handles:
- RabbitMQ connection and queue management
- Notification consumption with retry logic
- LLM integration (Gemini) with tool definitions
- Tool discovery and registration
- Message history management
- State transitions (idle → thinking → executing_tools → idle)
- Prometheus metrics (if available)
- Error handling and logging

### 2. Create a Tool

Tools send notifications instead of returning values (event-driven pattern):

```python
from vos_sdk.tools import BaseTool

class GetWeatherTool(BaseTool):
    def __init__(self):
        super().__init__(
            name="get_weather",
            description="Get current weather for a location"
        )

    def get_tool_info(self):
        """Return tool metadata for LLM."""
        return {
            "description": self.description,
            "parameters": {
                "location": {
                    "type": "string",
                    "description": "City name or location",
                    "required": True
                },
                "units": {
                    "type": "string",
                    "description": "Temperature units (celsius/fahrenheit)",
                    "required": False
                }
            }
        }

    def validate_arguments(self, arguments):
        """Validate input arguments."""
        if "location" not in arguments:
            return False, "Missing required argument: 'location'"
        if not arguments["location"].strip():
            return False, "Location cannot be empty"
        return True, None

    def execute(self, arguments):
        """Execute the tool and send result notification."""
        location = arguments["location"]
        units = arguments.get("units", "celsius")

        try:
            # Fetch weather data
            weather_data = self.fetch_weather_api(location, units)

            # Send success notification (NOT return!)
            self.send_result_notification("SUCCESS", {
                "location": location,
                "temperature": weather_data["temp"],
                "conditions": weather_data["conditions"],
                "units": units
            })
        except Exception as e:
            # Send failure notification
            self.send_result_notification("FAILURE", {
                "error": str(e),
                "location": location
            })
```

## Key SDK Features

### VOSAgentImplementation

The base class for all agents:

```python
class VOSAgentImplementation(VOSAgent):
    """
    Base class for agent implementations.

    Subclass this and define TOOLS as a class attribute.
    """
    TOOLS = []  # Override in subclass

    def __init__(self, config: AgentConfig, description: str):
        super().__init__(config)
        self.description = description
        # Automatically registers all tools in TOOLS list
```

**Features**:
- Autonomous notification processing loop
- Automatic tool registration from TOOLS list
- LLM integration with Gemini
- Message history management with automatic trimming
- State management (idle/thinking/executing_tools)
- Prometheus metrics collection (optional)

### AgentConfig

Environment-based configuration:

```python
config = AgentConfig.from_env("agent_name")

# Automatically loads:
# - RabbitMQ connection (RABBITMQ_HOST, RABBITMQ_PORT, etc.)
# - Database connection (DATABASE_HOST, DATABASE_PORT, etc.)
# - Weaviate connection (WEAVIATE_HOST, WEAVIATE_PORT)
# - API Gateway URL (API_GATEWAY_HOST, API_GATEWAY_PORT)
# - Agent settings (MAX_CONVERSATION_MESSAGES, etc.)
```

### BaseTool

Event-driven tool framework:

```python
class BaseTool:
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.channel = None  # Set by agent during registration

    def get_tool_info(self) -> dict:
        """Return metadata for LLM tool definitions."""

    def validate_arguments(self, arguments: dict) -> tuple[bool, str]:
        """Validate arguments before execution."""

    def execute(self, arguments: dict):
        """Execute tool and send notification (DON'T RETURN!)."""

    def send_result_notification(self, status: str, data: dict):
        """Send result back to agent via RabbitMQ."""
```

## Tool Collections

Pre-built tool sets for common functionality:

### Standard Tools (All Agents)
```python
from tools.standard import AGENT_TOOLS

# Includes:
# - SendAgentMessageTool: Send messages to other agents
# - CreateTaskTool, UpdateTaskTool, GetTasksTool: Task management
# - SleepTool, SetTimerTool, SetAlarmTool: Lifecycle management
# - CreateMemoryTool, SearchMemoryTool, etc.: Memory system
```

### Primary Agent Tools
```python
from tools.standard import STANDARD_TOOLS

# Includes AGENT_TOOLS plus:
# - SendUserMessageTool: Send messages to users (only primary agent)
```

### Weather Tools
```python
from tools.weather import WEATHER_TOOLS

# Includes:
# - GetWeatherTool: Current weather
# - GetForecastTool: Weather forecast
# - GetWeatherByCoordinatesTool: Weather by lat/lon
# - GetUVIndexTool: UV index data
# - GetAirQualityTool: Air quality data
```

## Processing Flow

1. **Agent starts** and connects to RabbitMQ queue
2. **Receives notification** from queue
3. **Checks processing state** (must be idle)
4. **Builds context** from notification + message history
5. **Calls LLM** (Gemini) with tool definitions
6. **Parses response** for tool calls
7. **Executes tools** (tools send notifications)
8. **Waits for tool results** (via tool_result notifications)
9. **Updates state** back to idle
10. **Acknowledges message** to RabbitMQ
11. **Repeats** autonomous loop

## Error Handling

- **Parse errors**: Show raw LLM response for debugging
- **Tool errors**: Send FAILURE notification with error details
- **Transient errors**: Requeue message for retry (max 3 attempts)
- **Fatal errors**: Log error, send to Sentry, nack message
- **Processing always returns to idle** state

## Message History Management

Automatic conversation trimming prevents context overflow:

```bash
# Environment variables
MAX_CONVERSATION_MESSAGES=0  # Global default (0 = unlimited)
PRIMARY_AGENT_MAX_CONVERSATION_MESSAGES=20  # Per-agent override
WEATHER_AGENT_MAX_CONVERSATION_MESSAGES=10   # Per-agent override
```

The SDK automatically:
- Stores all messages in PostgreSQL
- Loads recent messages into LLM context
- Trims old messages based on configured limits
- Keeps conversation focused and efficient

## Agent Example: Complete Weather Agent

```python
# services/agents/weather_agent/weather_agent.py
from vos_sdk import AgentConfig, VOSAgentImplementation
from tools.standard import AGENT_TOOLS
from tools.weather import WEATHER_TOOLS

class WeatherAgent(VOSAgentImplementation):
    """
    Specialized agent for weather-related queries.

    Handles:
    - Current weather lookups
    - Weather forecasts
    - UV index and air quality
    """

    # Define available tools
    TOOLS = AGENT_TOOLS + WEATHER_TOOLS

    def __init__(self, config: AgentConfig):
        super().__init__(
            config,
            "Weather information provider using OpenWeatherMap API"
        )

# Entry point
if __name__ == "__main__":
    config = AgentConfig.from_env("weather_agent")
    agent = WeatherAgent(config)
    agent.start()  # Runs forever, processing notifications
```

That's ~20 lines of code for a fully functional agent!

## Metrics (Optional)

If `prometheus_client` is installed, the SDK automatically tracks:

- `agent_notifications_processed_total`: Notifications processed by type
- `agent_llm_calls_total`: LLM API calls and status
- `agent_llm_call_duration_seconds`: LLM response time
- `agent_tool_executions_total`: Tool executions by status
- `agent_notification_queue_depth`: Pending notifications
- `agent_processing_loop_duration_seconds`: Processing cycle time
- `agent_errors_total`: Errors by type

Access metrics at agent endpoints (e.g., `http://localhost:8002/metrics`)

## Next Steps

1. **Create a new agent**: Copy `weather_agent/` as template
2. **Define tools**: Create tool classes in `/services/tools/`
3. **Set TOOLS list**: Add tools to agent's TOOLS class attribute
4. **Add to docker-compose**: Deploy agent as service
5. **Test delegation**: Send messages through Primary Agent

See `docs/vos-knowledge-base/03-agent-patterns.md` for communication patterns.
