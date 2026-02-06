# VOS Telemetry Implementation Guide

## Overview
Telemetry provides observability into the VOS system, tracking event flows, performance metrics, and system health across all services and agents.

## Core Telemetry Use Cases

### 1. Event Flow Tracing
Track a request's complete journey through the system:
- User input → API Gateway → RabbitMQ → Agent → Tool → Response
- Each step gets a unique trace_id that follows the request
- Parent-child relationships for spawned operations

### 2. Performance Monitoring
- **Latency tracking**: Time between request and response
- **Queue depth**: Messages waiting in each agent queue
- **Processing time**: How long each agent takes
- **Tool execution duration**: Performance of individual tools
- **Database query timing**: Slow query identification

### 3. System Health Metrics
- **Agent availability**: Up/down status, capacity
- **Error rates**: Failures per service/endpoint
- **Resource usage**: CPU, memory, connections
- **Message throughput**: Messages/second per queue
- **Success/failure ratios**: Task completion rates

### 4. Business Intelligence
- **User behavior**: Most requested features
- **Agent utilization**: Which agents are busiest
- **Task patterns**: Common task types and flows
- **Peak usage times**: Load patterns
- **Feature adoption**: Which tools are actually used

## Implementation Architecture

### Distributed Tracing with OpenTelemetry

```python
# Base telemetry setup
from opentelemetry import trace, metrics
from opentelemetry.exporter.otlp.proto.grpc import (
    trace_exporter, metrics_exporter
)

class TelemetryManager:
    def __init__(self, service_name: str):
        self.service_name = service_name
        self.tracer = trace.get_tracer(service_name)
        self.meter = metrics.get_meter(service_name)

        # Create metrics
        self.request_counter = self.meter.create_counter(
            "requests_total",
            description="Total requests processed"
        )

        self.processing_time = self.meter.create_histogram(
            "processing_duration_ms",
            description="Request processing time"
        )
```

### Event Flow Tracking

```python
# In API Gateway
@router.post("/api/v1/agents/{agent_id}/notify")
async def notify_agent(agent_id: str, notification: NotificationCreate):
    # Start a new trace
    with tracer.start_as_current_span("notify_agent") as span:
        # Generate trace_id for this request
        trace_id = span.get_span_context().trace_id

        # Add to notification for propagation
        notification_with_trace = {
            **notification.dict(),
            "trace_id": format(trace_id, '032x'),
            "span_id": format(span.get_span_context().span_id, '016x'),
            "timestamp": datetime.utcnow().isoformat()
        }

        # Set span attributes
        span.set_attribute("agent.id", agent_id)
        span.set_attribute("notification.type", notification.notification_type)
        span.set_attribute("source", notification.source)

        # Publish to RabbitMQ
        success = rabbitmq_client.publish_message(
            f"{agent_id}_queue",
            notification_with_trace
        )

        # Record metrics
        request_counter.add(1, {"agent": agent_id, "type": notification.notification_type})

        return {"trace_id": notification_with_trace["trace_id"]}
```

### Agent Telemetry Integration

```python
# In Agent base class
class VOSAgent:
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.telemetry = TelemetryManager(agent_id)

    def handle_notification(self, notification: dict):
        # Continue trace from parent
        trace_id = notification.get("trace_id")
        parent_span_id = notification.get("span_id")

        # Create child span
        with self.telemetry.tracer.start_as_current_span(
            "handle_notification",
            context=self._create_context(trace_id, parent_span_id)
        ) as span:

            start_time = time.time()

            try:
                # Process notification
                result = self._process(notification)

                # Record success metrics
                span.set_status(Status(StatusCode.OK))
                self.telemetry.success_counter.add(1)

            except Exception as e:
                # Record failure
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                self.telemetry.error_counter.add(1)
                raise

            finally:
                # Record timing
                duration = (time.time() - start_time) * 1000
                self.telemetry.processing_time.record(
                    duration,
                    {"notification_type": notification.get("notification_type")}
                )
```

## Telemetry Data Model

### Core Metrics to Track

```yaml
# Service Level Metrics
service_metrics:
  - service_name: string
  - uptime_seconds: gauge
  - memory_usage_mb: gauge
  - cpu_usage_percent: gauge
  - active_connections: gauge

# Request Metrics
request_metrics:
  - endpoint: string
  - method: string
  - status_code: int
  - duration_ms: histogram
  - request_size_bytes: histogram
  - response_size_bytes: histogram

# Queue Metrics
queue_metrics:
  - queue_name: string
  - messages_published: counter
  - messages_consumed: counter
  - messages_failed: counter
  - queue_depth: gauge
  - processing_time_ms: histogram

# Agent Metrics
agent_metrics:
  - agent_id: string
  - notifications_received: counter
  - notifications_processed: counter
  - tool_executions: counter
  - llm_calls: counter
  - capacity_percent: gauge

# Task Metrics
task_metrics:
  - task_id: uuid
  - creator_id: string
  - assignee_ids: array
  - creation_time: timestamp
  - completion_time: timestamp
  - status_changes: array
  - duration_seconds: gauge
```

## Storage and Visualization

### Time-Series Database (InfluxDB/Prometheus)

```yaml
# docker-compose addition
influxdb:
  image: influxdb:2.7-alpine
  container_name: vos_influxdb
  ports:
    - "8086:8086"
  environment:
    - DOCKER_INFLUXDB_INIT_MODE=setup
    - DOCKER_INFLUXDB_INIT_USERNAME=vos_admin
    - DOCKER_INFLUXDB_INIT_PASSWORD=vos_password
    - DOCKER_INFLUXDB_INIT_ORG=vos
    - DOCKER_INFLUXDB_INIT_BUCKET=telemetry
  volumes:
    - ./_data/influxdb:/var/lib/influxdb2
```

### Grafana Dashboards

```yaml
grafana:
  image: grafana/grafana:latest
  container_name: vos_grafana
  ports:
    - "3000:3000"
  environment:
    - GF_SECURITY_ADMIN_PASSWORD=admin
  volumes:
    - ./_data/grafana:/var/lib/grafana
    - ./grafana/dashboards:/etc/grafana/provisioning/dashboards
```

## Telemetry Patterns

### 1. Correlation IDs
Every request gets a unique ID that follows it everywhere:

```python
class CorrelationMiddleware:
    async def __call__(self, request: Request, call_next):
        correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
        request.state.correlation_id = correlation_id

        # Add to all logs
        logger.bind(correlation_id=correlation_id)

        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        return response
```

### 2. Structured Logging

```python
import structlog

logger = structlog.get_logger()

# Rich context logging
logger.info(
    "task_created",
    task_id=task.id,
    creator_id=task.creator_id,
    assignee_count=len(task.assignee_ids),
    trace_id=trace_id,
    duration_ms=duration
)
```

### 3. Custom Metrics

```python
# Track business metrics
class BusinessMetrics:
    def __init__(self):
        self.weather_requests = meter.create_counter("weather_requests_total")
        self.llm_tokens_used = meter.create_counter("llm_tokens_total")
        self.task_completion_time = meter.create_histogram("task_completion_seconds")

    def record_weather_request(self, location: str):
        self.weather_requests.add(1, {"location": location})

    def record_llm_usage(self, tokens: int, model: str):
        self.llm_tokens_used.add(tokens, {"model": model})
```

### 4. Health Check Telemetry

```python
@router.get("/health")
async def health_check():
    checks = {
        "database": check_database(),
        "rabbitmq": check_rabbitmq(),
        "weaviate": check_weaviate()
    }

    # Record health status
    for service, status in checks.items():
        health_gauge.set(1 if status else 0, {"service": service})

    return {
        "status": "healthy" if all(checks.values()) else "degraded",
        "checks": checks,
        "uptime": get_uptime(),
        "version": get_version()
    }
```

## Implementation Phases

### Phase 1: Basic Metrics (Week 1)
- Add OpenTelemetry to all services
- Implement request/response logging
- Set up InfluxDB for storage
- Create basic Grafana dashboards

### Phase 2: Distributed Tracing (Week 2)
- Implement trace propagation
- Add span creation in agents
- Set up Jaeger for trace visualization
- Create trace-based alerts

### Phase 3: Business Metrics (Week 3)
- Define custom business KPIs
- Implement metric collection
- Create business dashboards
- Set up alerting rules

### Phase 4: Advanced Analytics (Week 4)
- Implement anomaly detection
- Add predictive metrics
- Create automated reports
- Build debugging tools

## Query Examples

### Find Slow Operations
```sql
SELECT
    agent_id,
    notification_type,
    AVG(duration_ms) as avg_duration,
    MAX(duration_ms) as max_duration,
    COUNT(*) as count
FROM agent_metrics
WHERE time > now() - 1h
GROUP BY agent_id, notification_type
HAVING AVG(duration_ms) > 1000
ORDER BY avg_duration DESC
```

### Track Error Rates
```sql
SELECT
    time_bucket('5m', time) as window,
    service_name,
    SUM(errors) / SUM(requests) * 100 as error_rate
FROM service_metrics
WHERE time > now() - 24h
GROUP BY window, service_name
HAVING error_rate > 5
```

### Message Flow Analysis
```sql
WITH message_flow AS (
    SELECT
        trace_id,
        MIN(timestamp) as start_time,
        MAX(timestamp) as end_time,
        COUNT(DISTINCT service_name) as services_touched,
        ARRAY_AGG(service_name ORDER BY timestamp) as path
    FROM spans
    WHERE time > now() - 1h
    GROUP BY trace_id
)
SELECT
    path,
    COUNT(*) as frequency,
    AVG(EXTRACT(EPOCH FROM (end_time - start_time))) as avg_duration_seconds
FROM message_flow
GROUP BY path
ORDER BY frequency DESC
```

## Benefits of Comprehensive Telemetry

1. **Debugging**: Trace exact path of failed requests
2. **Performance**: Identify bottlenecks and slow operations
3. **Capacity Planning**: Understand load patterns and scale appropriately
4. **User Experience**: Monitor and improve response times
5. **Cost Optimization**: Track resource usage and optimize
6. **Compliance**: Audit trail for all operations
7. **Proactive Monitoring**: Detect issues before users report them
8. **Business Intelligence**: Data-driven decision making

## Security Considerations

- **PII Filtering**: Don't log sensitive user data
- **Sampling**: Use sampling for high-volume metrics
- **Retention**: Define data retention policies
- **Access Control**: Restrict telemetry data access
- **Encryption**: Encrypt telemetry in transit and at rest