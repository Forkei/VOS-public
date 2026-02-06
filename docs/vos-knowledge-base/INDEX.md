# VOS Knowledge Base Index

## Navigation Guide
This index helps you quickly find the information you need about the VOS system.

### Quick Task Reference

**"I need to create a new agent"**
→ Read: `03-agent-patterns.md`, `05-sdk-essentials.md`

**"I need to add a new API endpoint"**
→ Read: `02-services.md` (API Gateway section), `04-data-models.md`

**"I need to understand how agents communicate"**
→ Read: `03-agent-patterns.md`, `06-notification-types.md`

**"I need to work with the database"**
→ Read: `04-data-models.md`, `02-services.md` (PostgreSQL section)

**"I need to deploy or run the system"**
→ Read: `07-deployment.md`, `08-quick-reference.md`

**"I need to use the SDK"**
→ Read: `05-sdk-essentials.md` - SDK is now fully implemented

**"I need to debug message flow"**
→ Read: `06-notification-types.md`, `03-agent-patterns.md`

**"I need to implement security and monitoring (Phase 1)"**
→ Read: `10-phase1-security-monitoring.md` - Complete implementation guide with file paths and code

**"I need to work with the memory system"**
→ Read: `11-memory-system.md` - Complete memory system guide with tools, API, and patterns

---

## Documentation Files

### 01-architecture.md
**Core system design and component relationships**
- Microservices overview
- Technology stack
- Communication flow
- System boundaries

### 02-services.md
**Individual service specifications**
- API Gateway (port 8000)
- Primary Agent (Gemini LLM)
- Weather Agent
- PostgreSQL database
- RabbitMQ messaging
- Weaviate vector store

### 03-agent-patterns.md
**How agents work and communicate**
- Agent lifecycle
- Message handling patterns
- Tool execution
- Queue management

### 04-data-models.md
**Database schemas and data structures**
- Task management schema
- Notification models
- Message formats
- API contracts

### 05-sdk-essentials.md
**Current SDK state and future design**
- Existing SDK capabilities
- Planned architecture
- Implementation priorities
- Migration strategy

### 06-notification-types.md
**All notification types and payloads**
- User messages
- Agent messages
- Task assignments
- System alerts
- Tool results

### 07-deployment.md
**Infrastructure and deployment**
- Docker compose setup
- Environment variables
- Service dependencies
- Health checks

### 08-quick-reference.md
**Common commands and patterns**
- Service URLs
- Queue names
- Code examples
- Troubleshooting

### 10-phase1-security-monitoring.md
**Phase 1 Production Readiness Implementation Guide**
- Security hardening (authentication, secrets, TLS/SSL)
- Monitoring & observability (metrics, tracing, logging)
- Specific file paths and code examples
- Week-by-week implementation plan
- Testing & validation procedures

### 11-memory-system.md
**Complete Memory System Documentation**
- 8 memory types (user_preference, user_fact, etc.)
- Memory scopes (individual vs shared)
- Memory tools (create, search, get, update, delete)
- Weaviate integration with embeddings
- Usage patterns and best practices
- API endpoints and examples

---

## System Map

```
User Input → API Gateway → RabbitMQ → Agents
                ↓                        ↓
            PostgreSQL              Tools/LLM
                ↓                        ↓
            Response ← ← ← ← ← ← Results
```

## Key Principles
1. **Agents are autonomous** - Each agent has its own queue and lifecycle
2. **Messages are async** - All communication via RabbitMQ
3. **Tools return ToolResult** - Standardized response format
4. **SDK abstracts complexity** - Hide boilerplate, expose functionality
5. **Everything is logged** - Comprehensive logging for debugging