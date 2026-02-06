# Notification Types Reference

## Quick Lookup

| Type | Purpose | Source | Key Fields |
|------|---------|--------|------------|
| `user_message` | User input | API/UI | content, session_id |
| `agent_message` | Inter-agent | Agents | sender_agent_id, content |
| `task_assignment` | Assign work | Any | task_id, priority |
| `tool_result` | Tool output | Tools | tool_name, status, result |
| `system_alert` | Timers/Events | System | alert_type, message |
| `status_update` | Agent status | Agents | status, capacity |
| `capability_broadcast` | Announce skills | Agents | capabilities list |

## Detailed Specifications

### user_message
**When**: User sends input through UI/API
**Handler Priority**: High
**Required Response**: Yes

```python
def handle_user_message(payload):
    content = payload['content']
    session_id = payload['session_id']

    # Process with LLM if available
    if self.llm_client:
        response = self.llm_client.get_response(content)

    # Or route to appropriate agent
    if 'weather' in content.lower():
        self.send_to_agent('weather_agent', content)
```

### agent_message
**When**: Agent needs another agent's help
**Handler Priority**: Medium
**Required Response**: Optional

```python
def handle_agent_message(payload):
    sender = payload['sender_agent_id']
    content = payload['content']
    attachments = payload.get('attachments', [])

    # Process based on sender and content
    result = self.process_request(content)

    # Reply if needed
    self.send_message(sender, result)
```

### task_assignment
**When**: Task delegated to agent
**Handler Priority**: High
**Required Response**: Status updates

```python
def handle_task_assignment(payload):
    task_id = payload['task_id']
    priority = payload.get('priority', 'normal')

    # Update status to in_progress
    self.update_task_status(task_id, 'in_progress')

    # Execute task
    result = self.execute_task(payload)

    # Update status to completed
    self.update_task_status(task_id, 'completed')
```

### tool_result
**When**: Tool execution completes
**Handler Priority**: Medium
**Required Response**: No

```python
def handle_tool_result(payload):
    tool_name = payload['tool_name']
    status = payload['status']

    if status == 'SUCCESS':
        self.process_result(payload['result'])
    else:
        self.handle_error(payload['error_message'])
```

### system_alert
**When**: Scheduled events, timers
**Handler Priority**: Variable
**Required Response**: No

```python
def handle_system_alert(payload):
    alert_type = payload['alert_type']

    if alert_type == 'TIMER':
        self.execute_scheduled_task()
    elif alert_type == 'ALARM':
        self.trigger_alarm_action()
```

### status_update
**When**: Agent status changes
**Handler Priority**: Low
**Required Response**: No

```python
def handle_status_update(payload):
    agent_id = payload['agent_id']
    status = payload['status']

    # Track agent availability
    self.agent_registry[agent_id] = {
        'status': status,
        'capacity': payload.get('capacity', 1.0)
    }
```

### capability_broadcast
**When**: Agent announces capabilities
**Handler Priority**: Low
**Required Response**: No

```python
def handle_capability_broadcast(payload):
    agent_id = payload['agent_id']
    capabilities = payload['capabilities']

    # Update capability registry
    self.capability_registry[agent_id] = capabilities
```

## Notification Flow

### Publishing a Notification
```python
POST /api/v1/agents/{agent_id}/notify

{
    "recipient_agent_id": "weather_agent",
    "notification_type": "agent_message",
    "source": "primary_agent",
    "payload": {
        "sender_agent_id": "primary_agent",
        "content": "Get weather for NYC"
    }
}
```

### Receiving and Processing
```python
def callback(ch, method, properties, body):
    notification = json.loads(body)

    # Route to appropriate handler
    handlers = {
        'user_message': self.handle_user_message,
        'agent_message': self.handle_agent_message,
        'task_assignment': self.handle_task_assignment,
        # ... etc
    }

    handler = handlers.get(notification['notification_type'])
    if handler:
        handler(notification['payload'])
    else:
        logger.warning(f"Unknown type: {notification['notification_type']}")

    # Always acknowledge
    ch.basic_ack(delivery_tag=method.delivery_tag)
```

## Best Practices

1. **Always validate payload** - Check required fields
2. **Handle unknown types** - Log and continue
3. **Acknowledge quickly** - Process async if needed
4. **Include source** - Always identify sender
5. **Use appropriate priority** - Set based on type