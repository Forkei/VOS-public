# VOS - Virtual Operating System

A multi-agent orchestration system where specialized AI agents collaborate through event-driven communication.

## Architecture

```
User → API Gateway → Primary Agent → Specialized Agents
          ↓                 ↓                   ↓
          ↓←←←←←←←←←←←←←←←←←↓←←←←←←←←←←←←←←←←←←↓
                (via RabbitMQ queues)
```

## Key Components

- **VOS SDK** (`/sdk`): Python framework for building agents with tools
- **Primary Agent**: Orchestrates user requests and delegates to specialists
- **Weather Agent**: Handles weather-related queries
- **API Gateway**: REST interface for user interactions
- **RabbitMQ**: Message broker for agent communication
- **PostgreSQL**: Stores message history and agent state
- **Weaviate**: Vector database for semantic memory system

## Quick Start

```bash
# Setup environment
cp .env.example .env
# Add your GEMINI_API_KEY to .env

# Start everything
docker-compose up --build

# Test weather delegation
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"text": "What is the weather in New York?"}'
```

## Agent Development

Agents are built using the VOS SDK:

```python
from vos_sdk import AgentConfig, VOSAgentImplementation
from tools import AGENT_TOOLS, WEATHER_TOOLS

class WeatherAgent(VOSAgentImplementation):
    TOOLS = AGENT_TOOLS + WEATHER_TOOLS

    def __init__(self, config):
        super().__init__(config, "Weather information provider")

if __name__ == "__main__":
    config = AgentConfig.from_env("weather_agent")
    agent = WeatherAgent(config)
    agent.start()
```

## Tools System

Tools are autodiscoverable and provide metadata:

```python
class GetWeatherTool(BaseTool):
    def get_tool_info(self):
        return {
            "description": "Fetches current weather",
            "parameters": {
                "location": {"type": "string", "required": True}
            }
        }

    def execute(self, arguments):
        # Tool logic here
        self.send_result_notification("SUCCESS", result)
```

## Status

✅ **Working**: Weather delegation flow (User → Primary → Weather → Primary → User)

See [docs/NEXT_STEPS.md](docs/NEXT_STEPS.md) for detailed status and known issues.
