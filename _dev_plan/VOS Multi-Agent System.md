# Technical Specification: VOS Multi-Agent System

This document provides the complete technical blueprint for a proactive, multi-agent AI companion system operating within a Virtual Operating System (VOS). It is intended to be the single source of truth for all architectural and technological decisions.

## 1. Core Architectural Principles

The system is designed around the following core principles:

-   **Microservices Architecture:** Every component (each agent, API gateway, etc.) is an independent, containerized service. This promotes modularity, scalability, and independent deployment.
-   **Event-Driven:** The system is highly reactive and proactive. Services communicate asynchronously through a central message bus by publishing and subscribing to standardized events.
-   **API-First Design:** All communication protocols and data schemas are strictly defined before implementation.
-   **Infrastructure as Code (IaC):** The entire cloud infrastructure is defined and managed through version-controlled code, ensuring reproducibility and stability.
-   **Security by Design:** Secrets management and security best practices are integrated at the foundational level, not as an afterthought.

## 2. Technology Stack Summary

| Category                      | Technology / Tool                                     | Role & Purpose                                                                                                                                  |
| ----------------------------- | ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| **Backend & Agents**          | Python 3.11+ & FastAPI                               | For building high-performance, asynchronous microservices for each agent and the main API gateway.                                            |
| **Frontend (Cross-Platform)** | Flutter                                               | A single codebase for Web, Android, iOS, Windows, and macOS applications, ensuring a consistent UI/UX.                                        |
| **Frontend State Management** | Riverpod / Bloc                                       | To manage complex application state on the Flutter client in a predictable and scalable manner.                                                 |
| **Vector Database (Memory)**  | Weaviate                                              | Stores and retrieves agent memories and contextual information via semantic search. The core of the AI's long-term memory.                     |
| **Relational Database (Tasks)**| PostgreSQL                                            | Stores structured data: the shared agent task list, user accounts, system configuration, and application metadata.                            |
| **Event Bus (System Events)** | RabbitMQ                                              | The central nervous system for asynchronous communication. Guarantees message delivery between all services.                                    |
| **Internal Service Comms**    | gRPC                                                  | High-performance RPC framework for fast, low-latency, synchronous communication between internal microservices.                                 |
| **Cloud Hosting Provider**    | Google Cloud Platform (GCP)                           | The hosting environment for all services, databases, and infrastructure.                                                                        |
| **Containerization**          | Docker                                                | Every microservice will be packaged as a Docker image. A `Dockerfile` will be present in each service's repository.                             |
| **Container Orchestration**   | Google Kubernetes Engine (GKE)                        | To deploy, scale, and manage our containerized applications in a production environment.                                                        |
| **Deployment & CI/CD**        | GitHub & GitHub Actions                               | To automate the entire build, test, and deployment pipeline triggered by `git push`.                                                          |
| **Infrastructure as Code**    | Terraform                                             | To define and provision all GCP resources (GKE clusters, databases, networks) as code.                                                        |
| **Secrets Management**        | GCP Secret Manager                                    | Secure, centralized storage for all API keys, database credentials, and other secrets. Services will fetch secrets at runtime.                 |
| **Observability Suite**       | Cloud Logging, Prometheus, Grafana, OpenTelemetry     | A full suite for centralized logging, real-time metrics/dashboards, and distributed tracing to monitor system health and performance.             |

## 3. Communication Protocols

-   **External Communication (Client ↔ Backend):** The Flutter client will communicate with the VOS backend via a main **API Gateway** service.
    -   **Primary Protocol:** A standard **RESTful API** built with FastAPI for most requests (e.g., login, fetching user data).
    -   **Real-time Protocol:** **WebSockets** will be used for persistent, bidirectional communication for real-time updates to the GUI (e.g., chat messages, live application state changes).

-   **Internal Communication (Service ↔ Service):**
    -   **Asynchronous:** **RabbitMQ** is the default. Used for commands, tasks, and system-wide events where a response is not immediately required (e.g., Primary Agent assigning a task to the Search Agent).
    -   **Synchronous:** **gRPC** is used for fast, direct request/response calls between services where one service needs immediate data from another to proceed.

## 4. The VOS SDK (`vos_sdk.py`)

A mandatory internal Python library to be used by all backend services to ensure consistency and rapid development.

-   **Purpose:** To abstract away the complexity of inter-service communication, database connections, and agent boilerplate.
-   **Core Components:**
    -   `VOSClient`: A class that handles connections to RabbitMQ, PostgreSQL, and Weaviate.
    -   `BaseAgent`: An abstract class that all agents must inherit from. It will include pre-built methods for logging, memory management, and tool handling.
    -   `@tool` Decorator: A simple decorator to define a function as a tool available to an agent's LLM.

### SDK Example Usage:

```python
# Example of defining a new agent using the SDK
from vos_sdk import BaseAgent, tool
from vos_sdk.models import VOSEvent

class WeatherAgent(BaseAgent):
    agent_id = "agent_Weather"

    def setup(self):
        """Subscribe to relevant events when the agent starts."""
        self.client.subscribe("user.request.weather", self.on_weather_request)

    async def on_weather_request(self, event: VOSEvent):
        """Callback function to handle a weather request event."""
        location = event.payload.get("location")
        if location:
            weather_data = self.get_weather_for_location(location=location)
            # Further logic to create a document and notify the Primary Agent
            pass

    @tool(description="Fetches the current weather for a specific city.")
    def get_weather_for_location(self, location: str) -> dict:
        """A tool that can be called by the agent's LLM."""
        # Logic to call an external weather API
        # ...
        return {"location": location, "temp_c": 15, "condition": "Cloudy"}

```

## 5. Core Data Schema: The VOS Event

All events published to RabbitMQ MUST adhere to this JSON structure.

### Base Structure:

```json
{
"notification_id": "string (UUID)",
"timestamp": "string (ISO 8601 format)",
"recipient_agent_id": "string",
"notification_type": "string (Enum)",
"source": "string",
"payload": {}
}
```

### Event Examples:

**User Message:**
```json
"payload": {
"content": "What's the weather like in New York today?",
"content_type": "text", // Could also be "voice_transcript", "image_id", etc.
"session_id": "device_laptop_session_xyz" // Identifies the VOS session
}
```

**Agent to Agent Message:**
```json
"payload": {
"sender_agent_id": "primary_agent",
"content": "Please retrieve the latest weather forecast for New York.",
"attachments": [
// Could include document IDs for context
]
}
```

**Tool Result:**
```json
"payload": {
"tool_name": "weather_agent.get_forecast",
"status": "SUCCESS", // or "FAILURE"
"result": {
"document_id": "doc_weather_report_nyc_123"
},
"error_message": null
}
```

**System Alert:**
```json
"payload": {
"alert_type": "TIMER", // or "ALARM", "SCHEDULED_EVENT"
"alert_name": "Kitchen Timer",
"message": "Your 15-minute timer has finished."
}
```

**Event Subscription Event:**
```json
"payload": {
"subscription_id": "sub_music_on_arrival_home",
"triggered_event": {
"event_type": "device.phone.location.home.distance",
"value": "95m",
"timestamp": "2025-09-10T20:55:00Z"
},
"conditions_met": "User is near home and music app was opened."
}
```