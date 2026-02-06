"""
API Gateway client for scheduler service.
Handles app_interaction notifications via HTTP.
"""

import requests
import os
from typing import Dict, Any


class APIClient:
    def __init__(self):
        self.api_gateway_url = os.getenv('API_GATEWAY_URL', 'http://api_gateway:8000')
        self.internal_api_key = os.getenv('INTERNAL_API_KEY', '')

    def send_app_interaction(self, agent_id: str, app_name: str, action: str, result: Dict[Any, Any], session_id: str = None):
        """Send app_interaction notification to frontend via API Gateway"""
        url = f"{self.api_gateway_url}/api/v1/notifications/app-interaction"

        payload = {
            'agent_id': agent_id,
            'app_name': app_name,
            'action': action,
            'result': result,
            'session_id': session_id
        }

        headers = {
            'Content-Type': 'application/json',
            'X-Internal-Key': self.internal_api_key
        }

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=5)
            response.raise_for_status()
            print(f"  → App interaction sent: {agent_id}/{app_name}/{action}")
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"  ✗ Failed to send app interaction: {e}")
            # Don't raise - we don't want to crash the scheduler if API Gateway is down
            return None

    def send_system_alert(self, alert_type: str, payload: Dict[Any, Any]):
        """Send system alert notification to frontend"""
        url = f"{self.api_gateway_url}/api/v1/notifications/system-alert"

        notification = {
            'alert_type': alert_type,
            'payload': payload
        }

        headers = {
            'Content-Type': 'application/json',
            'X-Internal-Key': self.internal_api_key
        }

        try:
            response = requests.post(url, json=notification, headers=headers, timeout=5)
            response.raise_for_status()
            print(f"  → System alert sent: {alert_type}")
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"  ✗ Failed to send system alert: {e}")
            return None
