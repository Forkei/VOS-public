# VOS Memory System

## Overview

The VOS Memory System provides semantic memory storage and retrieval for agents using Weaviate as the vector database. It enables agents to learn from interactions, remember user preferences, store procedural knowledge, and build contextual understanding over time.

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│   Agents    │────▶│ Memory Tools │────▶│   Weaviate   │
└─────────────┘     └──────────────┘     └──────────────┘
      │                     │                     │
      └─────────────────────┴─────────────────────┘
              (create, search, get, update, delete)

                      ┌──────────────┐
                      │   Embedding  │
                      │   Service    │
                      │(nomic-embed) │
                      └──────────────┘
                            ↑
                            │
                      (generates 768-dim vectors)
```

## Memory Types

The system supports 8 distinct memory types:

### 1. user_preference
User settings, preferences, and choices
- **Examples**: "Prefers dark mode", "Uses metric units", "Likes jazz music"
- **Importance**: High (0.7-1.0)
- **Scope**: Shared (accessible to all agents)

### 2. user_fact
Facts about the user
- **Examples**: "Lives in New York", "Works as software engineer", "Has a dog named Max"
- **Importance**: High (0.7-1.0)
- **Scope**: Shared

### 3. conversation_context
Context from conversations
- **Examples**: "Discussing weekend plans", "Troubleshooting API error", "Planning trip to Paris"
- **Importance**: Medium (0.4-0.7)
- **Scope**: Individual or Shared

### 4. agent_procedure
Learned procedures and workflows
- **Examples**: "How to handle weather API timeout", "Steps to create task", "Error recovery process"
- **Importance**: High (0.7-1.0)
- **Scope**: Individual (agent-specific knowledge)

### 5. knowledge
General knowledge and facts
- **Examples**: "Paris is the capital of France", "Python uses indentation", "HTTP 404 means not found"
- **Importance**: Medium-Low (0.3-0.6)
- **Scope**: Shared

### 6. event_pattern
Recognized behavioral patterns
- **Examples**: "User asks for weather every morning", "Errors spike on weekends", "Peak usage at 9am"
- **Importance**: Medium (0.5-0.8)
- **Scope**: Shared

### 7. error_handling
Error handling strategies
- **Examples**: "Retry failed API calls 3 times", "Use fallback data on timeout", "Notify user of errors"
- **Importance**: High (0.8-1.0)
- **Scope**: Individual or Shared

### 8. proactive_action
Proactive behavior patterns
- **Examples**: "Suggest umbrella when rain forecast", "Remind about meetings 10 min before", "Check system health hourly"
- **Importance**: Medium-High (0.6-0.9)
- **Scope**: Shared

## Memory Scopes

### Individual Scope
- Agent-specific private memory
- Only accessible to the agent that created it
- Used for procedures, strategies, personal notes
- Example: Weather Agent's learned error handling

### Shared Scope
- Accessible to all agents in the system
- Used for user facts, preferences, knowledge
- Enables cross-agent personalization
- Example: User's preferred temperature units

## Memory Schema

### Core Fields

```python
{
    # Identification
    "memory_id": "uuid",                     # Unique identifier
    "agent_id": "primary_agent",             # Creating agent

    # Content
    "memory_type": "user_preference",        # Type enum
    "content": "User prefers Fahrenheit",    # Text content
    "embedding": [0.123, 0.456, ...],        # 768-dim vector

    # Metadata
    "scope": "shared",                       # individual|shared
    "source": "user_explicit",               # How created
    "importance": 0.9,                       # 0.0-1.0 weight
    "confidence": 0.95,                      # 0.0-1.0 certainty
    "tags": ["temperature", "units"],        # Searchable tags
    "related_memories": ["memory_uuid_2"],   # Connections

    # Temporal tracking
    "created_at": "2025-10-06T12:00:00Z",
    "updated_at": "2025-10-06T14:30:00Z",
    "last_accessed": "2025-10-06T16:15:00Z",
    "access_count": 12                        # Usage tracking
}
```

## Memory Tools

Agents interact with memory via 5 tools:

### 1. CreateMemoryTool
Store new memories with automatic embedding generation.

```python
# Tool call
{
    "name": "create_memory",
    "arguments": {
        "content": "User prefers dark mode interfaces",
        "memory_type": "user_preference",
        "scope": "shared",
        "importance": 0.8,
        "confidence": 0.9,
        "tags": ["ui", "preference", "theme"]
    }
}

# Result
{
    "status": "SUCCESS",
    "memory_id": "uuid-123",
    "message": "Memory created successfully"
}
```

### 2. SearchMemoryTool
Semantic search across memories using natural language.

```python
# Tool call
{
    "name": "search_memory",
    "arguments": {
        "query": "user interface preferences",
        "limit": 5,
        "filters": {
            "memory_type": "user_preference",
            "scope": "shared",
            "min_importance": 0.7
        }
    }
}

# Result
{
    "status": "SUCCESS",
    "memories": [
        {
            "memory_id": "uuid-123",
            "content": "User prefers dark mode interfaces",
            "distance": 0.15,  # Semantic similarity
            "importance": 0.8
        },
        ...
    ]
}
```

### 3. GetMemoryTool
Retrieve specific memory by ID.

```python
# Tool call
{
    "name": "get_memory",
    "arguments": {
        "memory_id": "uuid-123"
    }
}

# Result
{
    "status": "SUCCESS",
    "memory": {
        "memory_id": "uuid-123",
        "content": "User prefers dark mode interfaces",
        ...
    }
}
```

### 4. UpdateMemoryTool
Modify existing memories (updates access tracking automatically).

```python
# Tool call
{
    "name": "update_memory",
    "arguments": {
        "memory_id": "uuid-123",
        "updates": {
            "content": "User strongly prefers dark mode in all apps",
            "importance": 0.95,
            "tags": ["ui", "preference", "theme", "accessibility"]
        }
    }
}

# Result
{
    "status": "SUCCESS",
    "message": "Memory updated successfully"
}
```

### 5. DeleteMemoryTool
Remove memories from the system.

```python
# Tool call
{
    "name": "delete_memory",
    "arguments": {
        "memory_id": "uuid-123"
    }
}

# Result
{
    "status": "SUCCESS",
    "message": "Memory deleted successfully"
}
```

## API Endpoints

Direct REST API access to memory system via Swagger UI at `http://localhost:8000/docs`

### Authentication
All endpoints require authentication. In Swagger UI:
1. Click the green **"Authorize"** button
2. Enter API key: `YOUR_API_KEY_HERE`
3. Click "Authorize" and "Close"

### Create Memory
```http
POST /api/v1/memories/
Content-Type: application/json
X-API-Key: YOUR_API_KEY_HERE

{
    "content": "User prefers Fahrenheit for temperature",
    "memory_type": "user_preference",
    "agent_id": "primary_agent",
    "scope": "shared",
    "importance": 0.9,
    "confidence": 0.95,
    "tags": ["temperature", "units", "preference"]
}
```

### Search Memories (GET-based)
```http
GET /api/v1/memories/search?query=temperature+preferences&limit=5&min_importance=0.7
X-API-Key: YOUR_API_KEY_HERE

# Parameters (all optional):
# - query: Text query for semantic search
# - memory_type: Filter by type (user_preference, user_fact, etc.)
# - scope: Filter by scope (individual/shared)
# - agent_id: Filter by agent
# - tags: Comma-separated tags (e.g., "temperature,weather")
# - min_importance: Minimum importance (0.0-1.0)
# - min_confidence: Minimum confidence (0.0-1.0)
# - limit: Max results (default: 10, max: 100)
```

### List All Memories
```http
GET /api/v1/memories/?agent_id=primary_agent&limit=50&offset=0
X-API-Key: YOUR_API_KEY_HERE

# Supports all filters from /search plus pagination:
# - offset: Skip N results for pagination
```

### Get Memory
```http
GET /api/v1/memories/{memory_id}
X-API-Key: YOUR_API_KEY_HERE
```

### Update Memory
```http
PATCH /api/v1/memories/{memory_id}
Content-Type: application/json
X-API-Key: YOUR_API_KEY_HERE

{
    "importance": 0.95,
    "tags": ["temperature", "units", "preference", "weather"]
}
```

### Delete Memory
```http
DELETE /api/v1/memories/{memory_id}
X-API-Key: YOUR_API_KEY_HERE
```

### Agent Metadata (for module state)
```http
# Get metadata
GET /api/v1/agents/{agent_id}/metadata
X-API-Key: YOUR_API_KEY_HERE

# Update metadata
PUT /api/v1/agents/{agent_id}/metadata
Content-Type: application/json
X-API-Key: YOUR_API_KEY_HERE

{
    "memory_creator_wait_topic": "Planning trip to Tokyo",
    "custom_data": {...}
}
```

## Embedding Service

Memories are converted to semantic vectors using **nomic-embed-text-v1.5**:
- **Dimensions**: 768
- **Model**: nomic-ai/nomic-embed-text-v1.5 (via HuggingFace)
- **Purpose**: Enables semantic similarity search
- **Performance**: Fast inference, good quality embeddings

## Usage Patterns

### Pattern 1: Learning User Preferences

```python
# Primary Agent processes: "I prefer Celsius for temperature"

# 1. Create memory
create_memory(
    content="User prefers Celsius for temperature displays",
    memory_type="user_preference",
    scope="shared",
    importance=0.9,
    tags=["temperature", "units", "preference"]
)

# 2. Weather Agent searches before responding
memories = search_memory(
    query="temperature unit preference",
    limit=1,
    filters={"memory_type": "user_preference"}
)

# 3. Use preference in response
# "The temperature is 22°C" (not "72°F")
```

### Pattern 2: Agent Learning Procedures

```python
# Weather Agent learns from successful pattern

# Store procedure
create_memory(
    content="When OpenWeatherMap API times out, wait 5 seconds and retry with fallback to cached data",
    memory_type="agent_procedure",
    scope="individual",
    agent_id="weather_agent",
    importance=0.85,
    tags=["error_handling", "api", "timeout", "retry"]
)

# Retrieve when needed
procedures = search_memory(
    query="handling API timeout errors",
    filters={
        "memory_type": "agent_procedure",
        "agent_id": "weather_agent"
    }
)
```

### Pattern 3: Cross-Agent Knowledge Sharing

```python
# Primary Agent learns something useful

# Store knowledge
create_memory(
    content="User's work schedule: Mon-Fri 9am-5pm EST",
    memory_type="user_fact",
    scope="shared",
    importance=0.8,
    tags=["schedule", "work", "timezone"]
)

# Any agent can retrieve
# Notes Agent creating reminder:
context = search_memory(
    query="user work schedule timing",
    filters={"scope": "shared"}
)
# Creates reminder during work hours
```

## Best Practices

### For Agents

1. **Set Appropriate Importance**
   - Critical info (user facts, preferences): 0.8-1.0
   - Contextual info: 0.5-0.7
   - Ephemeral info: 0.3-0.5

2. **Use Descriptive Tags**
   - Helps with future retrieval
   - 3-5 tags per memory
   - Use consistent tag vocabulary

3. **Update Rather Than Duplicate**
   - Search first to check if memory exists
   - Update existing rather than create new
   - Maintains clean memory space

4. **Set Confidence Appropriately**
   - User-stated: 0.9-1.0
   - Inferred: 0.6-0.8
   - Uncertain: 0.3-0.5

5. **Choose Correct Scope**
   - Shared: User info, preferences, facts
   - Individual: Agent-specific procedures, strategies

### Memory Hygiene

- **Deprecate Old Info**: Update confidence to 0.0 for outdated memories
- **Consolidate**: Merge similar memories to reduce duplication
- **Prune Low-Importance**: Periodically remove low-importance, low-access memories
- **Track Access**: Use `access_count` to identify valuable memories

## Autonomous Memory Modules

The VOS Memory System includes two autonomous background modules that run automatically during agent conversations.

### Memory Creator Module

**Purpose**: Analyzes conversations and autonomously decides when to create memories

**How It Works**:
1. Runs every N turns (configurable via `MEMORY_CREATOR_RUN_EVERY_N_TURNS`)
2. Analyzes recent conversation context (last 10 messages by default)
3. Uses Gemini 2.5 Flash Lite LLM to make intelligent decisions
4. Checks last 3 created memories to avoid duplicates
5. Makes one of three decisions:
   - **CREATE_NOW**: Complete information available → creates memory objects immediately
   - **WAIT**: Topic started but incomplete → saves topic to database for next run
   - **IGNORE**: Not significant → moves on without action

**Configuration** (.env):
```bash
# Global settings
MEMORY_CREATOR_RUN_EVERY_N_TURNS=1        # How often to run (default: every turn)
MEMORY_CREATOR_CONTEXT_MESSAGES=10        # How many messages to analyze

# Per-agent toggle
PRIMARY_AGENT_MEMORY_CREATOR_ENABLED=true
WEATHER_AGENT_MEMORY_CREATOR_ENABLED=false
```

**WAIT State Persistence**:
- Saves incomplete topics to `agent_state.metadata` JSONB field
- Persists across agent restarts
- Continues waiting for information on next runs
- Clears WAIT state after CREATE_NOW or IGNORE decision

**Example**:
```
User: "I'm planning a trip to Japan but haven't decided on dates yet."

Memory Creator LLM Decision:
{
  "decision": "WAIT",
  "topic": "User planning trip to Japan, awaiting date decision"
}

→ Saved to database, will check again on future turns

User (later): "I think I'll go in April next year."

Memory Creator LLM Decision:
{
  "decision": "CREATE_NOW",
  "memories": [
    {
      "content": "User is planning a trip to Japan in April next year",
      "memory_type": "user_fact",
      "importance": 0.8,
      "tags": ["travel", "japan", "planning", "april"]
    }
  ]
}

→ Memory created, WAIT state cleared
```

### Memory Retriever Module

**Purpose**: Proactively searches for and injects relevant memories into agent context

**How It Works**:
1. Runs every N turns BEFORE agent processing starts (configurable)
2. Analyzes recent conversation context
3. Uses Gemini 2.5 Flash Lite to decide if memories are needed
4. Performs iterative semantic search (up to 3 iterations):
   - **GET_MEMORIES**: Generates search queries, retrieves 3 results per query
   - **GIVE_MEMORIES**: Selects most relevant memories to return (up to 9 total)
   - **IGNORE**: No relevant memories needed
5. Selected memories added to agent's context automatically

**Configuration** (.env):
```bash
# Global settings
MEMORY_RETRIEVER_RUN_EVERY_N_TURNS=1      # How often to run
MEMORY_RETRIEVER_CONTEXT_MESSAGES=10      # Messages to analyze
MEMORY_RETRIEVER_MAX_ITERATIONS=3         # Max search refinement rounds

# Per-agent toggle
PRIMARY_AGENT_MEMORY_RETRIEVER_ENABLED=true
WEATHER_AGENT_MEMORY_RETRIEVER_ENABLED=false
```

**Iterative Refinement**:
```
Iteration 1:
  Decision: GET_MEMORIES
  Queries: ["user temperature preferences", "weather display settings"]
  Results: 6 memories retrieved (3 per query)

Iteration 2:
  Decision: GET_MEMORIES (refining search)
  Queries: ["celsius fahrenheit unit preference"]
  Results: 3 more memories retrieved

Iteration 3:
  Decision: GIVE_MEMORIES
  Selected: [memory_id_1, memory_id_5, memory_id_7]
  → Returns 3 most relevant memories to agent
```

**Example**:
```
User: "What's the weather like today?"

Memory Retriever (before Primary Agent processes):
  → Searches for relevant memories about weather preferences
  → Finds: "User prefers Celsius", "User likes detailed forecasts"
  → Adds to Primary Agent's context

Primary Agent (with memory context):
  → Delegates to Weather Agent with preference info
  → Weather Agent returns forecast in Celsius
  → User gets personalized response
```

### Integration with Agents

Both modules integrate seamlessly with the VOS SDK:

```python
# In sdk/vos_sdk/core/agent.py

# Initialize modules
self.memory_creator = MemoryCreatorModule(
    agent_name=self.agent_name,
    gemini_api_key=config.gemini_api_key,
    db_client=self.db
)

self.memory_retriever = MemoryRetrieverModule(
    agent_name=self.agent_name,
    gemini_api_key=config.gemini_api_key,
    db_client=self.db
)

# Memory Retriever runs BEFORE processing
if self.memory_retriever and self.memory_retriever.should_run(turn_number):
    relevant_memories = self.memory_retriever.run(recent_messages)
    # Memories added to context automatically

# Agent processes notification...

# Memory Creator runs AFTER processing complete
if self.memory_creator and self.memory_creator.should_run(turn_number):
    self.memory_creator.run(recent_messages)
```

### Module State Management

**Agent Metadata Endpoints**:
```http
GET /api/v1/agents/{agent_id}/metadata
PUT /api/v1/agents/{agent_id}/metadata

# Example metadata storage
{
  "memory_creator_wait_topic": "User planning vacation to Tokyo",
  "custom_agent_data": {...}
}
```

**Access via Database Client**:
```python
# Get metadata
metadata = get_agent_metadata(db_client, "primary_agent")
wait_topic = metadata.get("memory_creator_wait_topic")

# Update metadata
metadata["memory_creator_wait_topic"] = "New topic"
update_agent_metadata(db_client, "primary_agent", metadata)
```

## Future Enhancements

### Advanced Features (Future)

- **Memory Consolidation**: Merge similar memories automatically
- **Temporal Decay**: Reduce importance of old, unused memories
- **Relationship Graphs**: Visualize memory connections
- **Memory Versioning**: Track changes to memories over time
- **Conflict Resolution**: Handle contradictory memories
- **Privacy Controls**: User-managed memory permissions

## Technical Details

### Weaviate Configuration
- **Version**: 1.28.1
- **Collection**: `Memory`
- **Vectorizer**: External (nomic-embed-text-v1.5)
- **Distance Metric**: Cosine similarity
- **Authentication**: Anonymous (TODO: Enable API key)

### Performance
- **Write Speed**: ~50ms per memory (including embedding)
- **Search Speed**: ~20-50ms for semantic search
- **Scalability**: Tested up to 10K memories per agent

### Data Persistence
- Weaviate data stored in: `./_data/weaviate`
- Automatic backups: Not yet implemented (future)
- Export format: JSON with embeddings

## See Also

- [02-services.md](02-services.md) - Weaviate service configuration
- [04-data-models.md](04-data-models.md) - Memory schema details
- [05-sdk-essentials.md](05-sdk-essentials.md) - Using memory tools in agents
