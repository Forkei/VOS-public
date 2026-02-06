"""
Notifications router for VOS API Gateway.

Handles notification publishing from agents to the frontend.
"""

import logging
from datetime import datetime
from typing import Optional, Dict, Any

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

from app.notification_publisher import get_notification_publisher

logger = logging.getLogger(__name__)

router = APIRouter()


class ActionStatusNotification(BaseModel):
    """Schema for action_status notification from agents."""
    agent_id: str
    session_id: str
    action_description: str
    timestamp: Optional[str] = None


class AppInteractionNotification(BaseModel):
    """Schema for app_interaction notification from agents."""
    agent_id: str
    app_name: str
    action: str
    result: Dict[str, Any]
    session_id: Optional[str] = None


@router.post("/notifications/action-status")
async def publish_action_status(
    notification: ActionStatusNotification,
    x_internal_key: Optional[str] = Header(None)
):
    """
    Receive action_status notification from agents and publish to RabbitMQ.

    This endpoint is called by agents (via SDK) when they have a user-facing
    status update to display, like "Searching weather data..." or
    "Contacting Weather Agent..."

    Args:
        notification: Action status notification data
        x_internal_key: Internal API key for authentication

    Returns:
        Success status
    """
    try:
        # Verify internal API key
        try:
            with open("/shared/internal_api_key", "r") as f:
                expected_key = f.read().strip()

            if x_internal_key != expected_key:
                raise HTTPException(status_code=401, detail="Invalid internal API key")
        except FileNotFoundError:
            raise HTTPException(status_code=500, detail="Internal API key not configured")

        # Get notification publisher
        publisher = get_notification_publisher()
        if not publisher:
            raise HTTPException(status_code=500, detail="Notification publisher not initialized")

        # Publish to RabbitMQ (session_id=None broadcasts to all sessions)
        success = publisher.publish_agent_action_status(
            agent_id=notification.agent_id,
            session_id=None,  # Broadcast to all sessions
            action_description=notification.action_description
        )

        if not success:
            raise HTTPException(status_code=500, detail="Failed to publish notification")

        logger.info(f"📤 Published action_status from {notification.agent_id}: '{notification.action_description}'")

        return {
            "status": "success",
            "message": "Action status notification published",
            "agent_id": notification.agent_id,
            "session_id": notification.session_id
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error publishing action_status notification: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to publish notification: {str(e)}")


@router.post("/notifications/app-interaction")
async def publish_app_interaction(
    notification: AppInteractionNotification,
    x_internal_key: Optional[str] = Header(None)
):
    """
    Receive app_interaction notification from agents and publish to RabbitMQ.

    This endpoint is called by agents (via tools) when they interact with
    frontend applications, like sending weather data to the weather app.

    Args:
        notification: App interaction notification data
        x_internal_key: Internal API key for authentication

    Returns:
        Success status
    """
    try:
        # Verify internal API key
        try:
            with open("/shared/internal_api_key", "r") as f:
                expected_key = f.read().strip()

            if x_internal_key != expected_key:
                raise HTTPException(status_code=401, detail="Invalid internal API key")
        except FileNotFoundError:
            raise HTTPException(status_code=500, detail="Internal API key not configured")

        # Get notification publisher
        publisher = get_notification_publisher()
        if not publisher:
            raise HTTPException(status_code=500, detail="Notification publisher not initialized")

        # Publish to RabbitMQ
        success = publisher.publish_app_interaction(
            agent_id=notification.agent_id,
            app_name=notification.app_name,
            action=notification.action,
            result=notification.result,
            session_id=notification.session_id
        )

        if not success:
            raise HTTPException(status_code=500, detail="Failed to publish notification")

        logger.info(f"📤 Published app_interaction from {notification.agent_id} to {notification.app_name}: {notification.action}")

        return {
            "status": "success",
            "message": "App interaction notification published",
            "agent_id": notification.agent_id,
            "app_name": notification.app_name,
            "session_id": notification.session_id
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error publishing app_interaction notification: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to publish notification: {str(e)}")


class BrowserScreenshotNotification(BaseModel):
    """Schema for browser_screenshot notification from browser agent."""
    agent_id: str
    session_id: Optional[str] = None
    screenshot_base64: str
    current_url: Optional[str] = None
    task: Optional[str] = None


@router.post("/notifications/browser-screenshot")
async def publish_browser_screenshot(
    notification: BrowserScreenshotNotification,
    x_internal_key: Optional[str] = Header(None)
):
    """
    Receive browser screenshot from browser agent and publish to frontend.

    Args:
        notification: Browser screenshot notification data
        x_internal_key: Internal API key for authentication

    Returns:
        Success status
    """
    try:
        # Verify internal API key
        try:
            with open("/shared/internal_api_key", "r") as f:
                expected_key = f.read().strip()

            if x_internal_key != expected_key:
                raise HTTPException(status_code=401, detail="Invalid internal API key")
        except FileNotFoundError:
            raise HTTPException(status_code=500, detail="Internal API key not configured")

        # Get notification publisher
        publisher = get_notification_publisher()
        if not publisher:
            raise HTTPException(status_code=500, detail="Notification publisher not initialized")

        # Publish to RabbitMQ
        success = publisher.publish_browser_screenshot(
            agent_id=notification.agent_id,
            screenshot_base64=notification.screenshot_base64,
            current_url=notification.current_url,
            task=notification.task,
            session_id=notification.session_id
        )

        if not success:
            raise HTTPException(status_code=500, detail="Failed to publish notification")

        logger.info(f"📸 Published browser_screenshot from {notification.agent_id} (url: {notification.current_url})")

        return {
            "status": "success",
            "message": "Browser screenshot notification published",
            "agent_id": notification.agent_id
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error publishing browser_screenshot notification: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to publish notification: {str(e)}")
