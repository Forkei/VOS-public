# VOS - Virtual Operating System

A proactive, multi-agent AI operating system. VOS orchestrates specialized AI agents that collaborate through event-driven messaging to handle everything from weather lookups to web browsing to phone calls — all through a single conversational interface.

```
                        +---------------------+
                        |    Flutter Client    |
                        |  (Mobile / Desktop)  |
                        +---------+-----------+
                                  |
                          REST / WebSocket
                                  |
                        +---------v-----------+
                        |    API Gateway       |
                        |  Auth, Routing, WS   |
                        +---------+-----------+
                                  |
                            RabbitMQ
                     (event-driven messaging)
                                  |
            +---------------------+---------------------+
            |                     |                     |
   +--------v-------+   +--------v-------+   +---------v------+
   | Primary Agent   |   | Voice Gateway  |   | Twilio Gateway  |
   | (Orchestrator)  |   | (STT / TTS)    |   | (Phone Calls)   |
   +--------+-------+   +----------------+   +----------------+
            |
     Delegates to...
            |
   +--------v-------------------------------------------+
   |  Weather | Search | Calendar | Notes | Browser | ...  |
   +---------------------------------------------------+
            |                     |
      +-----v------+      +------v------+
      | PostgreSQL  |      |  Weaviate   |
      | (State, DB) |      |  (Memory)   |
      +------------+      +-------------+
```

## What Can VOS Do?

- **Chat** — talk to VOS through a Flutter app, ask it anything, and it delegates to the right specialist agent
- **Phone Calls** — call VOS on a real phone number via Twilio, with real-time speech-to-text and streaming text-to-speech
- **Web Search** — search the web and scrape pages via DuckDuckGo and Firecrawl
- **Browser Automation** — VOS can control a headless browser to interact with websites
- **Calendar & Notes** — manage events, reminders, and notes with persistent storage
- **Weather** — get current weather and forecasts for any location
- **Semantic Memory** — VOS remembers context across conversations using vector search (Weaviate)
- **Proactive Scheduling** — schedule tasks and reminders that trigger agent actions automatically

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Flutter (mobile + desktop) |
| API | FastAPI (Python) |
| Messaging | RabbitMQ (event-driven) |
| LLM | Google Gemini |
| Database | PostgreSQL |
| Vector Memory | Weaviate |
| Voice (STT) | AssemblyAI |
| Voice (TTS) | ElevenLabs / Cartesia |
| Phone | Twilio |
| Web Scraping | Firecrawl |
| Monitoring | Prometheus, Sentry, Jaeger |
| Deployment | Docker Compose, Cloudflare Tunnel |

## Quick Start

### Prerequisites

- Docker & Docker Compose
- API keys for: Gemini, OpenWeatherMap, and optionally AssemblyAI, ElevenLabs, Twilio, Firecrawl

### Setup

```bash
# 1. Clone the repo
git clone https://github.com/Forkei/VOS-public.git
cd VOS-public

# 2. Create your environment file
cp .env.example .env
# Edit .env and add your API keys (at minimum: GEMINI_API_KEY)

# 3. Generate secure credentials
python -c "import secrets; print('JWT_SECRET=' + secrets.token_hex(64))"
python -c "import secrets; print('API_KEYS=' + secrets.token_hex(32))"
# Paste the output into your .env

# 4. Start everything
docker-compose up --build
```

The API gateway will be available at `http://localhost:8000`.

### Test It

```bash
# Send a chat message
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{"text": "What is the weather in New York?"}'
```

## Project Structure

```
VOS/
├── sdk/                        # VOS SDK — framework for building agents
│   └── vos_sdk/
│       ├── core/               # Agent base class, config, context, database
│       └── tools/              # Base tool class, task tools
├── services/
│   ├── agents/                 # Specialist agents
│   │   ├── primary_agent/      # Orchestrator — routes requests to specialists
│   │   ├── weather_agent/      # Weather lookups (OpenWeatherMap)
│   │   ├── search_agent/       # Web search (DuckDuckGo + Firecrawl)
│   │   ├── calendar_agent/     # Calendar events & reminders
│   │   ├── notes_agent/        # Note management
│   │   ├── calculator_agent/   # Math and calculations
│   │   └── browser_agent/      # Headless browser automation
│   ├── api_gateway/            # FastAPI — REST API, WebSocket, auth
│   ├── voice_gateway/          # Speech-to-text & text-to-speech pipeline
│   ├── twilio_gateway/         # Twilio phone call handling
│   ├── tools/                  # Shared tool implementations
│   ├── scheduler_service/      # Task scheduling & reminders
│   └── app_registry/           # Agent/app registration
├── docker-compose.yml          # Full stack deployment
├── docker-compose.backend-only.yml  # Backend without frontend
└── .env.example                # Environment variable template
```

## How It Works

1. **User sends a message** (via Flutter app, WebSocket, REST API, or phone call)
2. **API Gateway** authenticates the request and publishes it to RabbitMQ
3. **Primary Agent** picks up the message, analyzes intent, and delegates to the right specialist
4. **Specialist Agent** executes its tools (API calls, DB queries, browser actions, etc.)
5. **Results flow back** through RabbitMQ to the Primary Agent, then to the user
6. **Memory system** stores conversation context in Weaviate for future reference

For phone calls, the Twilio Gateway handles the phone connection, the Voice Gateway manages real-time speech-to-text (AssemblyAI) and text-to-speech (ElevenLabs/Cartesia), and the same agent pipeline processes the conversation.

## Building an Agent

Agents are built with the VOS SDK:

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

Each agent runs as its own Docker container, communicates via RabbitMQ, and can use any combination of tools. See the existing agents in `services/agents/` for examples.

## License

This project uses a custom source-available license. Free for non-commercial use with attribution. Commercial use requires written permission. See [LICENSE](LICENSE) for details.
