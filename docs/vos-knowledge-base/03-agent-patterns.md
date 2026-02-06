# Agent Communication Patterns

## Agent Lifecycle

### 1. Initialization
```python
AGENT_ID = 'my_agent'
QUEUE_NAME = f'{AGENT_ID}_queue'
RABBITMQ_URL = 'amqp://vos_user:vos_password@rabbitmq:5672/vos_vhost'
```

### 2. Queue Setup
```python
channel.queue_declare(queue=QUEUE_NAME, durable=True)
channel.basic_qos(prefetch_count=1)  # Process one at a time
channel.basic_consume(queue=QUEUE_NAME, on_message_callback=callback, auto_ack=False)
```

### 3. Message Processing
```python
def callback(ch, method, properties, body):
    notification = json.loads(body)

    # Process based on notification_type
    if notification['notification_type'] == 'user_message':
        handle_user_message(notification['payload'])

    # Always acknowledge
    ch.basic_ack(delivery_tag=method.delivery_tag)
```

## Message Flow Patterns

### Receiving Notifications
1. Agent listens on personal queue
2. Receives notification with type and payload
3. Processes based on notification_type
4. Acknowledges message (critical!)

### Sending to Other Agents
```python
# Via API Gateway
POST /api/v1/agents/{recipient_id}/notify
{
    "recipient_agent_id": "weather_agent",
    "notification_type": "agent_message",
    "source": "primary_agent",
    "payload": {...}
}
```

### Tool Execution Pattern
```python
# 1. Define tool
def my_tool(params):
    result = perform_operation(params)
    return ToolResult.success("my_tool", {"data": result})

# 2. Execute tool
result = my_tool({"param": "value"})

# 3. Handle result
if result.status == "SUCCESS":
    process_success(result.result)
else:
    handle_error(result.error_message)
```

## Standard Agent Template

```python
class StandardAgent:
    def __init__(self, agent_id):
        self.agent_id = agent_id
        self.queue_name = f'{agent_id}_queue'

    def start(self):
        # Connect to RabbitMQ
        # Declare queue
        # Start consuming

    def handle_notification(self, notification):
        handlers = {
            'user_message': self.handle_user_message,
            'agent_message': self.handle_agent_message,
            'task_assignment': self.handle_task,
        }
        handler = handlers.get(notification['notification_type'])
        if handler:
            handler(notification['payload'])
```

## Error Handling

### Message Acknowledgment Strategy
- **Success**: `basic_ack()` - Message processed
- **Malformed**: `basic_nack(requeue=False)` - Drop bad message
- **Temporary Error**: `basic_nack(requeue=True)` - Retry later

### Connection Resilience
```python
for attempt in range(max_retries):
    try:
        connection = pika.BlockingConnection(params)
        # Success - start consuming
        break
    except AMQPConnectionError:
        time.sleep(retry_delay * 2**attempt)
```

## Inter-Agent Communication Rules

1. **Always use agent_id convention**: `{purpose}_agent`
2. **Include source in messages**: Identify sender
3. **Use standard payload formats**: Follow schemas
4. **Handle unknown notification types**: Log and continue
5. **Never block on external calls**: Use timeouts