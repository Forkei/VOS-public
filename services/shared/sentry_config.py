import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration
import logging
import os


def init_sentry(service_name: str):
    """
    Initialize Sentry for error tracking and performance monitoring.
    Shared configuration for all VOS services.

    Args:
        service_name: Name of the service for identification in Sentry
    """
    sentry_dsn = os.getenv("SENTRY_DSN")
    environment = os.getenv("ENVIRONMENT", "development")

    # Skip if DSN is not configured or contains placeholder values
    if not sentry_dsn or sentry_dsn.startswith(("your_", "YOUR_", "https://YOUR_")):
        logging.warning(f"Sentry DSN not configured for {service_name}. Skipping Sentry initialization.")
        return False
    
    try:
        sentry_sdk.init(
            dsn=sentry_dsn,
            environment=environment,
            integrations=[
                LoggingIntegration(
                    level=logging.INFO,        # Capture info and above as breadcrumbs
                    event_level=logging.ERROR   # Send errors as events
                ),
            ],
            traces_sample_rate=1.0 if environment == "development" else 0.1,  # 100% in dev, 10% in prod
            profiles_sample_rate=1.0 if environment == "development" else 0.1,
            attach_stacktrace=True,
            send_default_pii=False,  # Don't send personally identifiable information
            before_send=before_send_filter,
            release=os.getenv("RELEASE_VERSION", "unknown"),
            server_name=service_name,
            max_breadcrumbs=50,
            debug=environment == "development",
        )
        
        # Set additional tags
        sentry_sdk.set_tag("service", service_name)
        sentry_sdk.set_tag("container", os.getenv("HOSTNAME", "unknown"))
        
        # Add service-specific context
        with sentry_sdk.configure_scope() as scope:
            scope.set_context("service_info", {
                "name": service_name,
                "environment": environment,
                "rabbitmq_configured": bool(os.getenv("RABBITMQ_URL")),
                "database_configured": bool(os.getenv("DATABASE_URL")),
            })
        
        logging.info(f"✅ Sentry initialized for {service_name} in {environment} environment")
        return True
        
    except Exception as e:
        logging.error(f"❌ Failed to initialize Sentry: {e}")
        return False


def before_send_filter(event, hint):
    """
    Filter events before sending to Sentry.
    
    Args:
        event: The event dictionary
        hint: Additional information about the event
    
    Returns:
        Modified event or None to drop the event
    """
    # Filter out health check errors
    if "request" in event and event["request"].get("url", "").endswith("/health"):
        return None
    
    # Add custom context
    if "extra" not in event:
        event["extra"] = {}
    
    event["extra"]["rabbitmq_connected"] = os.getenv("RABBITMQ_URL") is not None
    event["extra"]["database_connected"] = os.getenv("DATABASE_URL") is not None
    event["extra"]["weaviate_connected"] = os.getenv("WEAVIATE_URL") is not None
    
    return event


def capture_message(message: str, level: str = "info", **kwargs):
    """
    Capture a custom message to Sentry.
    
    Args:
        message: The message to capture
        level: The level of the message (debug, info, warning, error, fatal)
        **kwargs: Additional context to attach to the message
    """
    with sentry_sdk.push_scope() as scope:
        for key, value in kwargs.items():
            scope.set_extra(key, value)
        sentry_sdk.capture_message(message, level=level)


def capture_exception_with_context(exception: Exception, **context):
    """
    Capture an exception with additional context.
    
    Args:
        exception: The exception to capture
        **context: Additional context to attach to the exception
    """
    with sentry_sdk.push_scope() as scope:
        for key, value in context.items():
            scope.set_extra(key, value)
        sentry_sdk.capture_exception(exception)


def track_agent_message(agent_name: str, message_type: str, payload: dict):
    """
    Track agent message processing for debugging.
    
    Args:
        agent_name: Name of the agent processing the message
        message_type: Type of message being processed
        payload: Message payload
    """
    sentry_sdk.add_breadcrumb(
        category="agent_message",
        message=f"{agent_name} processing {message_type}",
        level="info",
        data={
            "agent": agent_name,
            "message_type": message_type,
            "payload_size": len(str(payload))
        }
    )


def track_llm_call(service: str, model: str, prompt_tokens: int = 0, completion_tokens: int = 0):
    """
    Track LLM API calls for monitoring costs and performance.
    
    Args:
        service: LLM service (e.g., "gemini", "openai")
        model: Model name
        prompt_tokens: Number of tokens in prompt
        completion_tokens: Number of tokens in completion
    """
    sentry_sdk.add_breadcrumb(
        category="llm_call",
        message=f"Called {service} {model}",
        level="info",
        data={
            "service": service,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens
        }
    )