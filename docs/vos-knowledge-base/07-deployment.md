# Deployment and Infrastructure

## Quick Start

### 1. Prerequisites
- Docker & Docker Compose installed
- `.env` file configured (copy from `.env.example`)
- Ports available: 5432, 5672, 8000, 8080, 8081, 15672

### 2. Start Everything
```bash
docker-compose up -d
```

### 3. Verify Services
```bash
docker-compose ps  # All should be "Up"
docker-compose logs -f api_gateway  # Check logs
```

## Environment Variables

### Required in `.env`
```bash
# Gemini API for primary_agent
GEMINI_API_KEY=your_key_here

# Optional: Sentry monitoring
SENTRY_DSN=https://...@sentry.io/...
```

### Service URLs (Internal)
```bash
DATABASE_URL=postgresql://vos_user:vos_password@postgres:5432/vos_database
RABBITMQ_URL=amqp://vos_user:vos_password@rabbitmq:5672/vos_vhost
WEAVIATE_URL=http://weaviate:8080
API_GATEWAY_URL=http://api_gateway:8000
```

## Docker Compose Services

### Service Dependencies
```yaml
api_gateway:
  depends_on:
    postgres: {condition: service_healthy}
    rabbitmq: {condition: service_healthy}
    weaviate: {condition: service_healthy}

primary_agent:
  depends_on:
    rabbitmq: {condition: service_healthy}
    postgres: {condition: service_healthy}
```

### Health Checks
Every service has health checks:
- **PostgreSQL**: `pg_isready`
- **RabbitMQ**: `rabbitmq-diagnostics ping`
- **Weaviate**: HTTP ready endpoint
- **API Gateway**: HTTP health endpoint

### Persistent Volumes
```
./_data/
├── postgres/    # Database files
├── rabbitmq/    # Queue persistence
└── weaviate/    # Vector storage
```

## Network Architecture

### Docker Network
- Network name: `vos_network`
- Type: Bridge network
- All services on same network
- Internal DNS by service name

### Port Mappings
| Service | Internal | External | Purpose |
|---------|----------|----------|---------|
| PostgreSQL | 5432 | 5432 | Database |
| RabbitMQ | 5672 | 5672 | AMQP |
| RabbitMQ | 15672 | 15672 | Management UI |
| API Gateway | 8000 | 8000 | REST API |
| Weaviate | 8080 | 8080 | Vector API |
| Weaviate | 50051 | 50051 | gRPC |
| Adminer | 8080 | 8081 | DB UI |

## Common Operations

### View Logs
```bash
# Single service
docker-compose logs -f primary_agent

# All services
docker-compose logs -f

# Last 100 lines
docker-compose logs --tail=100 api_gateway
```

### Restart Services
```bash
# Single service
docker-compose restart primary_agent

# All services
docker-compose restart
```

### Scale Services
```bash
# Run multiple weather agents
docker-compose up -d --scale weather_agent=3
```

### Database Access
```bash
# Via Adminer UI
http://localhost:8081
# System: PostgreSQL
# Server: postgres
# Username: vos_user
# Password: vos_password
# Database: vos_database

# Via psql
docker exec -it vos_postgres psql -U vos_user -d vos_database
```

### RabbitMQ Management
```bash
# Management UI
http://localhost:15672
# Username: vos_user
# Password: vos_password

# Check queues
docker exec vos_rabbitmq rabbitmqctl list_queues
```

## Troubleshooting

### Service Won't Start
```bash
# Check logs
docker-compose logs service_name

# Common issues:
# - Port already in use
# - Missing environment variables
# - Health check failing
```

### Database Connection Issues
```bash
# Test connection
docker exec vos_postgres pg_isready -U vos_user

# Reset database
docker-compose down -v  # WARNING: Deletes data
docker-compose up -d
```

### Message Queue Issues
```bash
# Check queue status
docker exec vos_rabbitmq rabbitmqctl status

# Purge queue
docker exec vos_rabbitmq rabbitmqctl purge_queue primary_agent_queue
```

### Container Debugging
```bash
# Enter container shell
docker exec -it vos_primary_agent /bin/bash

# Run Python console in container
docker exec -it vos_primary_agent python
```

## Production Considerations

### Security
- Change default passwords
- Use secrets management
- Enable TLS for RabbitMQ
- Restrict port exposure

### Scaling
- Use Docker Swarm or Kubernetes
- External PostgreSQL cluster
- RabbitMQ clustering
- Load balancer for API Gateway

### Monitoring
- Configure Sentry DSN
- Add Prometheus metrics
- Set up log aggregation
- Health check automation