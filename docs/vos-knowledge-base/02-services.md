# VOS Services Specification

## API Gateway
**Port**: 8000
**Container**: vos_api_gateway
**Purpose**: Central HTTP API and routing

### Endpoints
- `POST /api/v1/chat` - User messaging (creates notification for primary_agent)
- `POST /api/v1/tasks/` - Create task
- `GET /api/v1/tasks/{id}` - Get task
- `PATCH /api/v1/tasks/{id}` - Update task
- `DELETE /api/v1/tasks/{id}` - Delete task
- `POST /api/v1/agents/{agent_id}/notify` - Send notification
- `POST /api/v1/memories/` - Create memory
- `GET /api/v1/memories/search` - Semantic memory search
- `GET /api/v1/memories/{memory_id}` - Get memory
- `PUT /api/v1/memories/{memory_id}` - Update memory
- `DELETE /api/v1/memories/{memory_id}` - Delete memory
- `GET /api/v1/message-history/{agent_id}` - Get agent message history

### Dependencies
- PostgreSQL for task storage
- RabbitMQ for message publishing
- Weaviate for vector operations

---

## Primary Agent
**Queue**: primary_agent_queue
**Container**: vos_primary_agent
**Purpose**: Main orchestrator with LLM capabilities

### Capabilities
- Process user messages via Gemini LLM (30s timeout)
- Delegate to specialized agents
- Task management and coordination
- Memory creation and retrieval
- Complex reasoning and orchestration

### Tools
- Messaging: send_user_message, send_agent_message
- Task Management: create_task, update_task, get_tasks
- Memory: create_memory, search_memory, get_memory, update_memory, delete_memory
- Lifecycle: sleep, set_timer, set_alarm

---

## Weather Agent
**Queue**: weather_agent_queue
**Container**: vos_weather_agent
**Purpose**: Handle weather-related requests

### Capabilities
- Current weather lookups (OpenWeatherMap API)
- Weather forecasts (5-day)
- Weather by coordinates (lat/lon)
- UV index data
- Air quality information

### Tools
- Weather: get_weather, get_forecast, get_weather_by_coordinates, get_uv_index, get_air_quality
- Messaging: send_agent_message
- Task Management: create_task, update_task, get_tasks
- Memory: create_memory, search_memory, get_memory, update_memory, delete_memory
- Lifecycle: sleep, set_timer, set_alarm, shutdown

---

## PostgreSQL Database
**Port**: 5432
**Container**: vos_postgres
**Database**: vos_database

### Tables
```sql
-- tasks table
id          UUID PRIMARY KEY
created_at  TIMESTAMP
creator_id  VARCHAR
title       VARCHAR
description TEXT
status      VARCHAR (pending|in_progress|completed|archived)

-- task_assignees table
task_id     UUID REFERENCES tasks(id)
assignee_id VARCHAR
PRIMARY KEY (task_id, assignee_id)
```

---

## RabbitMQ Message Broker
**Port**: 5672 (AMQP), 15672 (Management UI)
**Container**: vos_rabbitmq
**Virtual Host**: vos_vhost

### Queue Convention
- Agent queues: `{agent_id}_queue`
- Durable queues with persistent messages
- Manual acknowledgment required

### Management UI
- URL: http://localhost:15672
- Username: vos_user
- Password: vos_password

---

## Weaviate Vector Store
**Port**: 8080 (REST), 50051 (gRPC)
**Container**: vos_weaviate
**Version**: 1.28.1
**Purpose**: Semantic memory system with embeddings

### Configuration
- No default vectorizer (external embedding service)
- Using nomic-embed-text-v1.5 (768 dimensions)
- Auto-schema disabled for control
- Anonymous access enabled (TODO: re-enable API key auth)

### Memory Schema
- **8 Memory Types**: user_preference, user_fact, conversation_context, agent_procedure, knowledge, event_pattern, error_handling, proactive_action
- **Scopes**: individual (agent-specific), shared (cross-agent)
- **Metadata**: importance, confidence, source, tags, relationships, access tracking

---

## Prometheus Monitoring
**Port**: 9090
**Container**: vos_prometheus
**Purpose**: Metrics collection and visualization

### Agent Metrics Endpoints
- Primary Agent: http://localhost:8002/metrics
- Weather Agent: http://localhost:8003/metrics

### Collected Metrics
- Agent notifications processed (by type)
- LLM API calls and duration
- Tool executions (by status)
- Queue depth
- Processing loop duration
- Error counts (by type)

---

## Supporting Services

### Adminer (Database UI)
**Port**: 8081
**Purpose**: PostgreSQL web interface

### Health Checks
All services include health checks:
- API Gateway: `GET /health`
- PostgreSQL: `pg_isready`
- RabbitMQ: `rabbitmq-diagnostics ping`
- Weaviate: `GET /v1/.well-known/ready`