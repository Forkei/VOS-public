# VOS System Architecture

## Core Design
VOS is a **microservices-based virtual operating system** where autonomous agents communicate asynchronously to handle tasks.

## Technology Stack
- **Language**: Python 3.x
- **Message Queue**: RabbitMQ (AMQP)
- **Database**: PostgreSQL 15
- **Vector Store**: Weaviate
- **API Framework**: FastAPI
- **LLM Integration**: Google Gemini
- **Monitoring**: Sentry
- **Containerization**: Docker & Docker Compose

## Component Architecture

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│   Clients   │────▶│  API Gateway │────▶│   RabbitMQ   │
└─────────────┘     └──────────────┘     └──────────────┘
                            │                     │
                            ▼                     ▼
                    ┌──────────────┐     ┌──────────────┐
                    │  PostgreSQL  │     │    Agents    │
                    └──────────────┘     └──────────────┘
                            │                     │
                    ┌──────────────┐     ┌──────────────┐
                    │   Weaviate   │     │  Tools/LLM   │
                    └──────────────┘     └──────────────┘
```

## Communication Flow
1. **Client → API Gateway**: HTTP REST requests
2. **API Gateway → RabbitMQ**: Publishes notifications to agent queues
3. **RabbitMQ → Agents**: Agents consume from personal queues
4. **Agents → Tools**: Execute tools and get results
5. **Agents → API Gateway**: Can make HTTP calls for operations
6. **All Services → PostgreSQL**: Persistent storage

## Key Design Decisions

### Asynchronous Messaging
- Each agent has a dedicated queue (`{agent_id}_queue`)
- Messages are durable and persistent
- Manual acknowledgment ensures no message loss

### Service Isolation
- Each service runs in its own Docker container
- Services communicate only through defined interfaces
- No shared state except database

### Scalability
- Agents can be scaled horizontally
- Queue-based architecture prevents bottlenecks
- Database connection pooling for efficiency

## Directory Structure
```
VOS/
├── services/
│   ├── api_gateway/     # Central API service
│   ├── primary_agent/   # Main orchestrator
│   ├── weather_agent/   # Specialized agent
│   └── shared/          # Shared utilities
├── sdk/                 # Python SDK
├── docs/                # Documentation
└── _data/              # Persistent volumes
```