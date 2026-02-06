# VOS Quick Reference

## Service URLs

### External Access
```
API Gateway:    http://localhost:8000
RabbitMQ UI:    http://localhost:15672
Database UI:    http://localhost:8081
Weaviate:       http://localhost:8080
```

### Internal (Container-to-Container)
```
API Gateway:    http://api_gateway:8000
PostgreSQL:     postgres:5432
RabbitMQ:       rabbitmq:5672
Weaviate:       weaviate:8080
```

## Queue Names
```
primary_agent_queue
weather_agent_queue
{agent_id}_queue  # Pattern
```

## Common Commands

### Docker Operations
```bash
# Start system
docker-compose up -d

# Stop system
docker-compose down

# View logs
docker-compose logs -f [service_name]

# Restart service
docker-compose restart [service_name]

# Execute command in container
docker exec -it vos_primary_agent python
```

### Testing API Endpoints
```bash
# Create task
curl -X POST http://localhost:8000/api/v1/tasks/ \
  -H "Content-Type: application/json" \
  -d '{"title": "Test task", "creator_id": "test"}'

# Get task
curl http://localhost:8000/api/v1/tasks/{task_id}

# Send notification
curl -X POST http://localhost:8000/api/v1/agents/primary_agent/notify \
  -H "Content-Type: application/json" \
  -d '{
    "recipient_agent_id": "primary_agent",
    "notification_type": "user_message",
    "source": "test",
    "payload": {
      "content": "Hello",
      "content_type": "text",
      "session_id": "test_session"
    }
  }'
```

## Code Snippets

### Create Agent with SDK
```python
from vos_sdk import AgentConfig, VOSAgentImplementation
from tools import AGENT_TOOLS

class MyAgent(VOSAgentImplementation):
    TOOLS = AGENT_TOOLS

    def __init__(self, config):
        super().__init__(config, "My agent description")

config = AgentConfig.from_env("my_agent", "My Agent")
agent = MyAgent(config)
agent.start()
```

### Test Weather Delegation
```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"text": "What is the weather in New York?"}'
```

### Send Agent Message
```python
import requests

def send_message(recipient_id, content):
    url = f"http://localhost:8000/api/v1/agents/{recipient_id}/notify"
    payload = {
        "recipient_agent_id": recipient_id,
        "notification_type": "agent_message",
        "source": "my_agent",
        "payload": {
            "sender_agent_id": "my_agent",
            "content": content,
            "attachments": []
        }
    }
    response = requests.post(url, json=payload)
    return response.json()
```

## Database Queries

### Connect to Database
```python
import psycopg2

conn = psycopg2.connect(
    "postgresql://vos_user:vos_password@localhost:5432/vos_database"
)
```

### Common Queries
```sql
-- Get all tasks
SELECT * FROM tasks ORDER BY created_at DESC;

-- Get task with assignees
SELECT t.*, array_agg(ta.assignee_id) as assignees
FROM tasks t
LEFT JOIN task_assignees ta ON t.id = ta.task_id
GROUP BY t.id;

-- Update task status
UPDATE tasks SET status = 'completed' WHERE id = 'uuid';

-- Get pending tasks for agent
SELECT t.* FROM tasks t
JOIN task_assignees ta ON t.id = ta.task_id
WHERE ta.assignee_id = 'weather_agent'
AND t.status = 'pending';
```

## Error Handling Patterns

### RabbitMQ Connection
```python
max_retries = 10
retry_delay = 5

for attempt in range(max_retries):
    try:
        connection = pika.BlockingConnection(params)
        break
    except AMQPConnectionError:
        time.sleep(retry_delay)
        retry_delay = min(retry_delay * 2, 60)
```

### Tool Execution
```python
try:
    result = tool.execute(params)
    if result.status == "SUCCESS":
        return result.result
    else:
        logger.error(result.error_message)
        return None
except Exception as e:
    logger.error(f"Tool failed: {e}")
    return None
```

## Debugging Tips

### Check Message Flow
1. Send notification via API
2. Check RabbitMQ UI for queue depth
3. Check agent logs for processing
4. Verify acknowledgment in RabbitMQ

### Common Issues
- **Queue not created**: Agent hasn't started
- **Messages stuck**: No acknowledgment
- **Connection refused**: Service not healthy
- **Task not found**: UUID format issue

### Log Locations
```
docker-compose logs api_gateway    # API logs
docker-compose logs primary_agent  # Agent logs
docker exec vos_postgres tail -f /var/log/postgresql/*  # DB logs
```

## Environment Variables Quick Reference
```bash
# Required
GEMINI_API_KEY=your_key

# Optional
SENTRY_DSN=your_sentry_dsn
ENVIRONMENT=development|production

# Internal (auto-configured)
DATABASE_URL=postgresql://...
RABBITMQ_URL=amqp://...
WEAVIATE_URL=http://...
```