import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
import logging
import os


def init_sentry(service_name: str = "api_gateway"):
    """
    Initialize Sentry for error tracking and performance monitoring.
    
    Args:
        service_name: Name of the service for identification in Sentry
    """
    sentry_dsn = os.getenv("SENTRY_DSN")
    environment = os.getenv("ENVIRONMENT", "development")
    
    if not sentry_dsn:
        logging.warning(f"Sentry DSN not configured for {service_name}. Skipping Sentry initialization.")
        return False
    
    try:
        sentry_sdk.init(
            dsn=sentry_dsn,
            environment=environment,
            integrations=[
                FastApiIntegration(
                    transaction_style="endpoint",
                    failed_request_status_codes=[400, 401, 403, 404, 405, 500, 502, 503, 504],
                ),
                StarletteIntegration(
                    transaction_style="endpoint",
                ),
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
    
    # Filter out specific non-critical errors
    if "exception" in event:
        exc_info = hint.get("exc_info")
        if exc_info:
            exc_type, exc_value, tb = exc_info
            # Add custom filtering logic here if needed
            pass
    
    # Add custom context
    if "extra" not in event:
        event["extra"] = {}
    
    event["extra"]["rabbitmq_connected"] = os.getenv("RABBITMQ_URL") is not None
    event["extra"]["database_connected"] = os.getenv("DATABASE_URL") is not None
    
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