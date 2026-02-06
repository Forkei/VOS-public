import json
import logging
import os
from typing import Dict, Any

import requests

logger = logging.getLogger(__name__)

# Configuration
API_GATEWAY_URL = os.environ.get('API_GATEWAY_URL', 'http://api_gateway:8000')


def send_message_to_agent(recipient_agent_id: str, content: str) -> Dict[str, Any]:
    """
    Send an agent message to another agent via the API Gateway notification system.
    
    Args:
        recipient_agent_id: The ID of the agent to send the message to (must end with '_agent')
        content: The message content to send
        
    Returns:
        Dictionary containing the response from the API Gateway
        
    Raises:
        requests.RequestException: If the HTTP request fails
        ValueError: If the recipient_agent_id doesn't follow naming convention
    """
    # Validate agent ID follows naming convention
    if not recipient_agent_id.endswith('_agent'):
        raise ValueError(
            f"Invalid recipient_agent_id: '{recipient_agent_id}'. "
            f"Agent IDs must end with '_agent' (e.g., 'weather_agent', 'search_agent')"
        )
    
    # Create the notification payload for agent message
    notification_payload = {
        "recipient_agent_id": recipient_agent_id,
        "notification_type": "agent_message",
        "source": "primary_agent",  # This tool is being called by primary_agent
        "payload": {
            "sender_agent_id": "primary_agent",
            "content": content,
            "attachments": []  # No attachments for this simple message
        }
    }
    
    # Construct the API Gateway URL
    api_url = f"{API_GATEWAY_URL}/api/v1/agents/{recipient_agent_id}/notify"
    
    logger.info(f"🚀 Sending message to agent '{recipient_agent_id}'")
    logger.info(f"   URL: {api_url}")
    logger.info(f"   Content: {content}")
    logger.debug(f"   Full payload: {json.dumps(notification_payload, indent=2)}")
    
    try:
        # Make the HTTP POST request
        response = requests.post(
            api_url,
            json=notification_payload,
            headers={
                'Content-Type': 'application/json',
                'User-Agent': 'primary_agent/1.0'
            },
            timeout=10  # 10 second timeout
        )
        
        # Check if the request was successful
        response.raise_for_status()
        
        # Parse the response
        response_data = response.json()
        
        logger.info(f"✅ Message sent successfully to '{recipient_agent_id}'")
        logger.info(f"   Notification ID: {response_data.get('notification_id', 'unknown')}")
        logger.info(f"   Queue: {response_data.get('queue', 'unknown')}")
        
        return response_data
        
    except requests.exceptions.Timeout:
        error_msg = f"Timeout sending message to '{recipient_agent_id}'"
        logger.error(f"❌ {error_msg}")
        raise requests.RequestException(error_msg)
        
    except requests.exceptions.ConnectionError:
        error_msg = f"Connection error sending message to '{recipient_agent_id}' - API Gateway may be down"
        logger.error(f"❌ {error_msg}")
        raise requests.RequestException(error_msg)
        
    except requests.exceptions.HTTPError as e:
        error_msg = f"HTTP error sending message to '{recipient_agent_id}': {e.response.status_code}"
        logger.error(f"❌ {error_msg}")
        if e.response.status_code == 400:
            logger.error(f"   Response: {e.response.text}")
        raise requests.RequestException(error_msg)
        
    except Exception as e:
        error_msg = f"Unexpected error sending message to '{recipient_agent_id}': {e}"
        logger.error(f"❌ {error_msg}")
        raise requests.RequestException(error_msg)


def send_user_message_to_agent(recipient_agent_id: str, content: str, session_id: str = "test_session") -> Dict[str, Any]:
    """
    Send a user message to an agent via the API Gateway notification system.
    
    Args:
        recipient_agent_id: The ID of the agent to send the message to
        content: The user message content
        session_id: The session ID for the user (default: "test_session")
        
    Returns:
        Dictionary containing the response from the API Gateway
    """
    # Create the notification payload for user message
    notification_payload = {
        "recipient_agent_id": recipient_agent_id,
        "notification_type": "user_message",
        "source": "primary_agent",
        "payload": {
            "content": content,
            "content_type": "text",
            "session_id": session_id
        }
    }
    
    # Construct the API Gateway URL
    api_url = f"{API_GATEWAY_URL}/api/v1/agents/{recipient_agent_id}/notify"
    
    logger.info(f"🚀 Sending user message to agent '{recipient_agent_id}'")
    logger.info(f"   Content: {content}")
    
    try:
        response = requests.post(
            api_url,
            json=notification_payload,
            headers={'Content-Type': 'application/json'},
            timeout=10
        )
        
        response.raise_for_status()
        response_data = response.json()
        
        logger.info(f"✅ User message sent successfully to '{recipient_agent_id}'")
        return response_data
        
    except Exception as e:
        error_msg = f"Error sending user message to '{recipient_agent_id}': {e}"
        logger.error(f"❌ {error_msg}")
        raise requests.RequestException(error_msg)