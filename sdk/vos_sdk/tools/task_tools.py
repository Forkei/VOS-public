"""
Task management tools for the VOS SDK.

These tools provide standardized interfaces for task creation, updates, and management
across the VOS ecosystem.
"""

import logging
from typing import List, Optional

import httpx

from ..schemas import ToolResult

logger = logging.getLogger(__name__)

# API Gateway configuration
API_GATEWAY_BASE_URL = "http://api_gateway:8000"
TASKS_ENDPOINT = f"{API_GATEWAY_BASE_URL}/api/v1/tasks/"


def create_task(
    creator_id: Optional[str] = None,
    title: str = None,
    description: Optional[str] = None,
    assignee_ids: Optional[List[str]] = None
) -> ToolResult:
    """
    Create a new task in the VOS task management system.
    
    Args:
        creator_id: ID of the agent or user creating the task
        title: Short, descriptive title for the task
        description: Detailed description of the task objective
        assignee_ids: List of agent IDs to assign the task to
        
    Returns:
        ToolResult: Standardized result with task creation outcome
    """
    tool_name = "create_task"
    
    # Validate required parameters
    if not title:
        return ToolResult.failure(
            tool_name=tool_name,
            error_message="Task title is required"
        )
    
    # Prepare request payload
    task_data = {
        "creator_id": creator_id,
        "title": title,
        "description": description,
        "assignee_ids": assignee_ids or []
    }
    
    try:
        # Make HTTP request to API Gateway
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                TASKS_ENDPOINT,
                json=task_data,
                headers={"Content-Type": "application/json"}
            )
        
        # Check if request was successful
        if response.status_code in (200, 201):
            task_result = response.json()
            logger.info(f"✅ Task created successfully: {task_result.get('id')}")
            
            return ToolResult.success(
                tool_name=tool_name,
                result={
                    "task": task_result,
                    "message": f"Task '{title}' created successfully",
                    "task_id": task_result.get("id")
                }
            )
        else:
            # API returned an error status
            error_detail = "Unknown error"
            try:
                error_response = response.json()
                error_detail = error_response.get("detail", str(error_response))
            except:
                error_detail = response.text or f"HTTP {response.status_code}"
            
            logger.error(f"❌ Task creation failed: {error_detail}")
            
            return ToolResult.failure(
                tool_name=tool_name,
                error_message=f"Failed to create task: {error_detail}"
            )
            
    except httpx.TimeoutException:
        error_msg = "Request timeout while creating task"
        logger.error(f"❌ {error_msg}")
        return ToolResult.failure(tool_name=tool_name, error_message=error_msg)
        
    except httpx.ConnectError:
        error_msg = "Cannot connect to API Gateway - service may be down"
        logger.error(f"❌ {error_msg}")
        return ToolResult.failure(tool_name=tool_name, error_message=error_msg)
        
    except Exception as e:
        error_msg = f"Unexpected error during task creation: {str(e)}"
        logger.error(f"❌ {error_msg}")
        return ToolResult.failure(tool_name=tool_name, error_message=error_msg)