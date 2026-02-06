from contextlib import asynccontextmanager
from datetime import datetime, timezone
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo
import json
import logging
import uuid
import os

from fastapi import FastAPI, HTTPException, Path, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from prometheus_fastapi_instrumentator import Instrumentator
import sentry_sdk

from app.config import Settings
from app.rabbitmq_client import RabbitMQClient
from app.schemas import ChatMessage, Notification, NotificationCreate
from app.database import DatabaseClient, close_database
from app.routers import tasks, message_history, messages, memories, websocket, notifications, weather, webhooks, audio, tools, auth, transcription, memory_visualization, attachments, documents, apps, call_websocket, call_internal, system_prompts, agent_voices, twilio_admin
from app.middleware.auth import AuthMiddleware, create_jwt_token
from app.notification_publisher import initialize_notification_publisher
from app.notification_consumer import start_notification_consumer, stop_notification_consumer
from app.app_interaction_consumer import initialize_consumer as initialize_app_interaction_consumer
from app.services.call_manager import initialize_call_manager, get_call_manager
from pydantic import BaseModel

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Sentry
sentry_dsn = os.getenv("SENTRY_DSN")
if sentry_dsn and not sentry_dsn.startswith(("your_", "YOUR_", "https://YOUR_")):
    try:
        sentry_sdk.init(
            dsn=sentry_dsn,
            # Privacy-conscious: don't send PII like request headers, IP addresses, etc.
            # see https://docs.sentry.io/platforms/python/data-management/data-collected/ for more info
            send_default_pii=False,
        )
        logger.info("✅ Sentry initialized for API Gateway")
    except Exception as e:
        logger.warning(f"⚠️ Failed to initialize Sentry: {e} - Sentry disabled")
else:
    logger.warning("⚠️ Sentry DSN not configured - Sentry disabled")

# Global variables for shared resources
rabbitmq_client: RabbitMQClient = None
db_client: DatabaseClient = None
settings = Settings()


# Security Middleware Classes
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


class RequestSizeLimiter(BaseHTTPMiddleware):
    """Limit request body size to prevent DoS attacks."""
    async def dispatch(self, request: Request, call_next):
        max_size = 10 * 1024 * 1024  # 10MB
        if request.headers.get("content-length"):
            if int(request.headers["content-length"]) > max_size:
                return JSONResponse(
                    {"error": "Request too large"},
                    status_code=413
                )
        return await call_next(request)


# Custom Prometheus metrics
agent_notifications_total = Counter(
    'vos_agent_notifications_total',
    'Total agent notifications sent',
    ['agent_id', 'notification_type']
)

task_operations_total = Counter(
    'vos_task_operations_total',
    'Total task operations',
    ['operation']  # create, update, delete
)

active_agents = Gauge(
    'vos_active_agents',
    'Number of active agents',
    ['agent_id', 'status']
)

llm_call_duration = Histogram(
    'vos_llm_call_duration_seconds',
    'LLM call duration in seconds',
    ['agent_id', 'model']
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    global rabbitmq_client, db_client

    # Generate or load internal API key for agent authentication
    import secrets
    os.makedirs("/shared", exist_ok=True)

    # Only generate a new key if it doesn't exist (persist across restarts)
    key_file_path = "/shared/internal_api_key"
    if os.path.exists(key_file_path):
        with open(key_file_path, "r") as f:
            internal_api_key = f.read().strip()
        logger.info(f"🔑 Loaded existing internal API key: {internal_api_key[:8]}...")
    else:
        internal_api_key = secrets.token_hex(32)
        logger.info(f"🔑 Generated new internal API key: {internal_api_key[:8]}...")
        with open(key_file_path, "w") as f:
            f.write(internal_api_key)
        logger.info("✅ Internal API key written to /shared/internal_api_key")

    # Initialize RabbitMQ client
    rabbitmq_client = RabbitMQClient(settings.rabbitmq_url)
    if not rabbitmq_client.connect():
        raise RuntimeError("Failed to connect to RabbitMQ")

    # Initialize Database client
    try:
        db_client = DatabaseClient(settings.database_url)
        logger.info("✅ Database client initialized successfully")
    except Exception as e:
        logger.error(f"❌ Failed to initialize database client: {e}")
        raise RuntimeError(f"Failed to connect to database: {e}")

    # Initialize notification publisher (for API endpoints to publish to RabbitMQ)
    try:
        initialize_notification_publisher(settings.rabbitmq_url)
        logger.info("✅ Notification publisher initialized")
    except Exception as e:
        logger.error(f"❌ Failed to initialize notification publisher: {e}")
        raise RuntimeError(f"Failed to initialize notification publisher: {e}")

    # Start notification consumer (listens to RabbitMQ and delivers via WebSocket)
    try:
        start_notification_consumer(settings.rabbitmq_url, db_client)
        logger.info("✅ Notification consumer started")
    except Exception as e:
        logger.error(f"❌ Failed to start notification consumer: {e}")
        raise RuntimeError(f"Failed to start notification consumer: {e}")

    # Start app interaction consumer (for calendar, reminders, timers, etc.)
    try:
        app_interaction_consumer = initialize_app_interaction_consumer(settings.rabbitmq_url)
        await app_interaction_consumer.start()
        logger.info("✅ App interaction consumer started")
    except Exception as e:
        logger.error(f"❌ Failed to start app interaction consumer: {e}")
        raise RuntimeError(f"Failed to start app interaction consumer: {e}")

    # Initialize Call Manager for voice calls
    try:
        call_manager = initialize_call_manager(db_client, settings.rabbitmq_url)
        call_manager.start_timeout_monitor()
        logger.info("✅ Call Manager initialized with timeout monitoring")
    except Exception as e:
        logger.error(f"❌ Failed to initialize Call Manager: {e}")
        # Non-fatal - call features will be unavailable but other features work

    logger.info("🚀 VOS API Gateway fully initialized with WebSocket support")

    yield

    # Shutdown
    logger.info("🛑 Shutting down VOS API Gateway...")

    # Stop app interaction consumer
    try:
        from app.app_interaction_consumer import app_interaction_consumer
        if app_interaction_consumer:
            await app_interaction_consumer.stop()
            logger.info("✅ App interaction consumer stopped")
    except Exception as e:
        logger.warning(f"⚠️ Error stopping app interaction consumer: {e}")

    # Stop notification consumer
    try:
        stop_notification_consumer()
        logger.info("✅ Notification consumer stopped")
    except Exception as e:
        logger.warning(f"⚠️ Error stopping notification consumer: {e}")

    # Stop call manager timeout monitor
    try:
        call_manager = get_call_manager()
        if call_manager:
            call_manager.stop_timeout_monitor()
            logger.info("✅ Call Manager timeout monitor stopped")
    except Exception as e:
        logger.warning(f"⚠️ Error stopping call manager: {e}")

    if rabbitmq_client:
        rabbitmq_client.close()

    if db_client:
        db_client.close()
        logger.info("Database client closed")

    logger.info("👋 VOS API Gateway shutdown complete")


app = FastAPI(
    title="VOS API Gateway",
    description="Virtual Operating System API Gateway",
    version="1.0.0",
    lifespan=lifespan,
    swagger_ui_parameters={
        "persistAuthorization": True  # Remember auth between page reloads
    }
)

# Add OpenAPI security schemes for Swagger UI auth
from fastapi.openapi.utils import get_openapi

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title="VOS API Gateway",
        version="1.0.0",
        description="Virtual Operating System API Gateway",
        routes=app.routes,
    )

    # Add security schemes
    openapi_schema["components"]["securitySchemes"] = {
        "APIKeyHeader": {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
            "description": "API Key for regular endpoints"
        },
        "InternalKeyHeader": {
            "type": "apiKey",
            "in": "header",
            "name": "X-Internal-Key",
            "description": "Internal API Key for agent-only endpoints"
        }
    }

    # Apply API Key auth to all endpoints by default (can be overridden per endpoint)
    openapi_schema["security"] = [{"APIKeyHeader": []}]

    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi

# Prometheus metrics instrumentation
instrumentator = Instrumentator(
    should_group_status_codes=False,
    should_ignore_untemplated=True,
    should_respect_env_var=True,
    should_instrument_requests_inprogress=True,
    excluded_handlers=["/metrics", "/health"],
    env_var_name="ENABLE_METRICS",
    inprogress_name="vos_http_requests_inprogress",
    inprogress_labels=True,
)

instrumentator.instrument(app)
logger.info("✅ Prometheus metrics instrumentation enabled")

# Add Security Middleware (order matters - applied in reverse)
# Authentication middleware (applied first to check auth before other processing)
app.middleware("http")(AuthMiddleware())

# Security headers, rate limiting, etc.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestSizeLimiter)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=os.getenv("ALLOWED_HOSTS", "*").split(",")
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth.router, prefix="/api/v1")  # Authentication endpoints
app.include_router(tasks.router, prefix="/api/v1")
app.include_router(message_history.router, prefix="/api/v1")
app.include_router(messages.router, prefix="/api/v1")
app.include_router(memories.router, prefix="/api/v1")
app.include_router(memory_visualization.router, prefix="/api/v1")  # Memory visualization endpoints
app.include_router(websocket.router, prefix="/api/v1")  # WebSocket endpoints
app.include_router(notifications.router, prefix="/api/v1")  # Notification endpoints
app.include_router(weather.router, prefix="/api/v1")  # Weather endpoints
app.include_router(webhooks.router, prefix="/api/v1")  # Webhook endpoints
app.include_router(audio.router, prefix="/api/v1")  # Audio file serving
app.include_router(tools.router, prefix="/api/v1")  # Tools execution
app.include_router(transcription.router, prefix="/api/v1")  # Batch transcription
app.include_router(attachments.router, prefix="/api/v1")  # Image/file attachments
app.include_router(documents.router, prefix="/api/v1")  # Document management
app.include_router(apps.router, prefix="/api/v1")  # App registry proxy
app.include_router(call_websocket.router, prefix="/api/v1")  # Call WebSocket endpoints
app.include_router(call_internal.router, prefix="/api/v1")  # Call Internal endpoints
app.include_router(system_prompts.router)  # System prompt management (already has /api/v1 prefix)
app.include_router(agent_voices.router)  # Agent voice settings (already has /api/v1 prefix)
app.include_router(twilio_admin.router)  # Twilio phone integration (already has /api/v1 prefix)


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "api_gateway"}


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/sentry-debug")
async def trigger_error():
    """Sentry debug endpoint to test error tracking."""
    division_by_zero = 1 / 0


@app.post("/api/v1/chat")
async def chat(message: ChatMessage):
    """Process chat message and send to primary agent queue."""
    if not rabbitmq_client:
        raise HTTPException(status_code=500, detail="RabbitMQ client not initialized")

    if not db_client:
        raise HTTPException(status_code=500, detail="Database client not initialized")

    # Fetch images if attachment_ids provided (for vision support)
    images_data = []
    if message.attachment_ids:
        try:
            from app.routers.attachments import get_attachments_for_message
            images_data = await get_attachments_for_message(message.attachment_ids)
            logger.info(f"Fetched {len(images_data)} images for chat message")
        except Exception as e:
            logger.error(f"Error fetching attachments: {e}")
            # Continue without images - don't fail the request

    # Store user message in conversation_messages table
    try:
        insert_query = """
        INSERT INTO conversation_messages (session_id, sender_type, sender_id, content, metadata)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id, timestamp;
        """

        # Include attachment info in metadata
        metadata = {"content_type": "text"}
        if message.attachment_ids:
            metadata["attachment_ids"] = message.attachment_ids
            metadata["content_type"] = "multimodal"

        result = db_client.execute_query(
            insert_query,
            (message.session_id, "user", None, message.text, json.dumps(metadata))
        )

        message_id = result[0][0] if result else None
        message_timestamp = result[0][1] if result else datetime.utcnow()

        logger.info(f"Stored user message {message_id} in conversation_messages for session {message.session_id}")
    except Exception as e:
        logger.error(f"Failed to store user message in database: {e}")
        # Continue even if database storage fails - message should still be sent to agent

    # Get current time in UTC (timezone-aware)
    utc_now = datetime.now(timezone.utc)

    # Build notification payload
    payload = {
        "content": message.text,
        "content_type": "multimodal" if images_data else "text",
        "session_id": message.session_id,
        "user_timezone": message.user_timezone  # Forward user's timezone
    }

    # Include images if present (for vision analysis)
    if images_data:
        payload["images"] = images_data
        logger.info(f"Including {len(images_data)} images in notification payload for vision analysis")

    # Create a proper Notification object for user message
    notification = Notification(
        notification_id=uuid.uuid4(),
        timestamp=utc_now,  # Use timezone-aware UTC
        recipient_agent_id="primary_agent",  # Send to primary agent
        notification_type="user_message",
        source="api_gateway",
        payload=payload
    )

    # Convert the notification to a dictionary for JSON serialization
    notification_dict = notification.model_dump()

    # Convert UUID and datetime to strings for JSON serialization
    notification_dict["notification_id"] = str(notification_dict["notification_id"])

    # Convert timestamp to user's local time if timezone provided
    ts = notification_dict["timestamp"]
    if message.user_timezone:
        try:
            user_tz = ZoneInfo(message.user_timezone)
            local_ts = ts.astimezone(user_tz)
            notification_dict["timestamp"] = local_ts.isoformat()
        except Exception as e:
            logger.warning(f"Could not convert to timezone {message.user_timezone}: {e}")
            notification_dict["timestamp"] = ts.isoformat()
    else:
        notification_dict["timestamp"] = ts.isoformat()

    logger.info(f"Sending user message to primary_agent via primary_agent_queue")
    logger.debug(f"Notification content: {json.dumps(notification_dict, indent=2)}")

    # Publish to the primary agent's queue
    success = rabbitmq_client.publish_message("primary_agent_queue", notification_dict)

    if not success:
        logger.error("Failed to send user message to primary_agent_queue")
        raise HTTPException(status_code=500, detail="Failed to send message to queue")

    logger.info(f"Successfully sent user message {notification.notification_id} to primary_agent")

    return {
        "status": "message sent",
        "notification_id": str(notification.notification_id),
        "recipient": "primary_agent",
        "queue": "primary_agent_queue",
        "session_id": message.session_id
    }


@app.post("/api/v1/agents/{agent_id}/notify")
async def send_notification_to_agent(
    agent_id: str = Path(..., description="The ID of the recipient agent (must end with '_agent')"),
    notification_data: NotificationCreate = None
):
    """
    Send a notification to a specific agent via their dedicated queue.

    The agent_id must follow the naming convention: {purpose}_agent
    Examples: primary_agent, weather_agent, search_agent, memory_agent

    The notification will be published to a queue named {agent_id}_queue.
    For example, a request to /api/v1/agents/weather_agent/notify will
    publish to the queue 'weather_agent_queue'.

    Args:
        agent_id: The ID of the recipient agent (from URL path)
        notification_data: The notification to send (request body)

    Returns:
        Success status with notification ID and queue name
    """
    if not rabbitmq_client:
        raise HTTPException(status_code=500, detail="RabbitMQ client not initialized")

    # Validate agent_id follows naming convention
    if not agent_id.endswith("_agent"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid agent_id format: '{agent_id}'. "
                   f"Agent IDs must end with '_agent' (e.g., 'weather_agent', 'search_agent'). "
                   f"This ensures consistent queue naming across the system."
        )

    # Additional validation: agent_id should not be empty before _agent
    agent_purpose = agent_id[:-6]  # Remove '_agent' suffix
    if not agent_purpose:
        raise HTTPException(
            status_code=400,
            detail="Invalid agent_id: cannot be just '_agent'. "
                   "Must have a purpose prefix (e.g., 'primary_agent', 'weather_agent')"
        )

    # Generate the dynamic queue name from the agent_id
    queue_name = f"{agent_id}_queue"

    # Create a full Notification object with generated ID and timestamp
    notification = Notification(
        notification_id=uuid.uuid4(),
        timestamp=datetime.utcnow(),
        recipient_agent_id=agent_id,  # Use the agent_id from the URL
        notification_type=notification_data.notification_type,
        source=notification_data.source,
        payload=notification_data.payload
    )

    # Convert the notification to a dictionary for JSON serialization
    notification_dict = notification.model_dump()

    # Convert UUID and datetime to strings for JSON serialization
    notification_dict["notification_id"] = str(notification_dict["notification_id"])
    notification_dict["timestamp"] = notification_dict["timestamp"].isoformat()

    logger.info(f"Sending notification to agent '{agent_id}' via queue '{queue_name}'")
    logger.debug(f"Notification content: {json.dumps(notification_dict, indent=2)}")

    # Publish to the agent's specific queue
    success = rabbitmq_client.publish_message(queue_name, notification_dict)

    if not success:
        logger.error(f"Failed to send notification to queue '{queue_name}'")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send notification to agent '{agent_id}'"
        )

    logger.info(f"Successfully sent notification {notification.notification_id} to queue '{queue_name}'")

    return {
        "status": "notification sent",
        "notification_id": str(notification.notification_id),
        "agent_id": agent_id,
        "queue": queue_name,
        "timestamp": notification.timestamp.isoformat()
    }

