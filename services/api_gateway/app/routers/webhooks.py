"""
Webhooks router for VOS API Gateway.

Handles incoming webhooks from external services like Firecrawl.
"""

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request, Header
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


class FirecrawlWebhookPayload(BaseModel):
    """Schema for Firecrawl webhook payload"""
    success: bool
    type: str  # "crawl.started", "crawl.page", "crawl.completed", "crawl.failed"
    id: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


@router.post("/webhooks/firecrawl")
async def firecrawl_webhook(
    request: Request,
    payload: Dict[str, Any]
):
    """
    Receive webhook notifications from Firecrawl crawl operations.

    Firecrawl sends webhooks for:
    - crawl.started: When a crawl job begins
    - crawl.page: For each page crawled (real-time progress)
    - crawl.completed: When the crawl finishes successfully
    - crawl.failed: When the crawl fails

    This endpoint converts the webhook into a tool_result notification
    and sends it to the search_agent queue so the agent can process
    the crawl results.

    Args:
        request: FastAPI request object
        payload: Webhook payload from Firecrawl

    Returns:
        Acknowledgment response
    """
    # Import here to avoid circular dependency
    from app.main import rabbitmq_client

    if not rabbitmq_client:
        logger.error("RabbitMQ client not initialized")
        raise HTTPException(status_code=500, detail="RabbitMQ client not available")

    try:
        # Extract webhook data
        webhook_type = payload.get("type", "unknown")
        success = payload.get("success", False)
        crawl_id = payload.get("id")
        data = payload.get("data", {})
        error = payload.get("error")

        logger.info(f"📥 Received Firecrawl webhook: type={webhook_type}, id={crawl_id}, success={success}")

        # Determine tool result status based on webhook type
        if webhook_type == "crawl.failed" or not success:
            tool_status = "FAILURE"
            tool_result = {
                "crawl_id": crawl_id,
                "error": error or "Crawl failed",
                "webhook_type": webhook_type
            }
        elif webhook_type == "crawl.completed":
            tool_status = "SUCCESS"
            tool_result = {
                "crawl_id": crawl_id,
                "webhook_type": webhook_type,
                "data": data,
                "message": "Crawl completed successfully"
            }
        elif webhook_type == "crawl.page":
            # For page-by-page updates, send as SUCCESS with progress info
            tool_status = "SUCCESS"
            tool_result = {
                "crawl_id": crawl_id,
                "webhook_type": webhook_type,
                "page_data": data,
                "message": "Page crawled"
            }
        elif webhook_type == "crawl.started":
            # Crawl started - informational
            tool_status = "SUCCESS"
            tool_result = {
                "crawl_id": crawl_id,
                "webhook_type": webhook_type,
                "message": "Crawl started"
            }
        else:
            # Unknown webhook type
            logger.warning(f"⚠️ Unknown webhook type: {webhook_type}")
            tool_status = "SUCCESS"
            tool_result = {
                "crawl_id": crawl_id,
                "webhook_type": webhook_type,
                "data": data
            }

        # Create tool_result notification for search_agent
        notification = {
            "notification_id": str(uuid.uuid4()),
            "timestamp": datetime.utcnow().isoformat(),
            "recipient_agent_id": "search_agent",
            "notification_type": "tool_result",
            "source": "firecrawl_webhook",
            "payload": {
                "tool_name": "firecrawl_crawl",
                "status": tool_status,
                "result": tool_result,
                "error_message": error if tool_status == "FAILURE" else None
            }
        }

        # Publish to search_agent queue
        success = rabbitmq_client.publish_message("search_agent_queue", notification)

        if not success:
            logger.error("Failed to publish webhook notification to search_agent queue")
            raise HTTPException(
                status_code=500,
                detail="Failed to forward webhook to search agent"
            )

        logger.info(f"✅ Firecrawl webhook forwarded to search_agent: {webhook_type} (crawl_id: {crawl_id})")

        return {
            "status": "received",
            "webhook_type": webhook_type,
            "crawl_id": crawl_id,
            "forwarded_to": "search_agent"
        }

    except Exception as e:
        logger.error(f"Error processing Firecrawl webhook: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process webhook: {str(e)}"
        )
