"""
Task management tools for VOS agents.

Tools for creating, updating, and retrieving tasks.
"""

import os
import logging
import requests
from typing import Dict, Any, Optional
from datetime import datetime

from vos_sdk import BaseTool

logger = logging.getLogger(__name__)


class CreateTaskTool(BaseTool):
    """
    Creates a new task in the task management system.
    """

    def __init__(self):
        super().__init__(
            name="create_task",
            description="Creates a new task that can be assigned to agents"
        )
        self.api_gateway_url = os.environ.get("API_GATEWAY_URL", "http://localhost:8000")
        self.internal_api_key = self._load_internal_api_key()

    def _load_internal_api_key(self) -> Optional[str]:
        """Load internal API key from shared volume."""
        try:
            with open("/shared/internal_api_key", "r") as f:
                key = f.read().strip()
                if key:
                    logger.info(f"✅ CreateTaskTool loaded internal API key: {key[:8]}...")
                    return key
        except FileNotFoundError:
            logger.warning("⚠️ Internal API key file not found")
        except Exception as e:
            logger.error(f"❌ Failed to load internal API key: {e}")
        return None

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate task creation arguments."""
        if "title" not in arguments:
            return False, "Missing required argument: 'title'"

        if not isinstance(arguments["title"], str):
            return False, f"'title' must be a string"

        if not arguments["title"].strip():
            return False, "'title' cannot be empty"

        if "assignee_ids" in arguments:
            if not isinstance(arguments["assignee_ids"], list):
                return False, "'assignee_ids' must be a list"

            for assignee in arguments["assignee_ids"]:
                if not isinstance(assignee, str):
                    return False, "All assignee_ids must be strings"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "create_task",
            "description": "Creates a new task that can be assigned to agents",
            "parameters": [
                {
                    "name": "title",
                    "type": "str",
                    "description": "Task title",
                    "required": True
                },
                {
                    "name": "description",
                    "type": "str",
                    "description": "Task description",
                    "required": False
                },
                {
                    "name": "assignee_ids",
                    "type": "list[str]",
                    "description": "List of agent IDs to assign the task to",
                    "required": False
                },
                {
                    "name": "broadcast_updates",
                    "type": "bool",
                    "description": "Whether to send notifications to assignees when task status changes (default: false)",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Create a new task.

        Args:
            arguments: Must contain 'title', optionally 'description' and 'assignee_ids'
        """
        try:
            task_data = {
                "created_by": self.agent_name,
                "title": arguments["title"],
                "description": arguments.get("description", ""),
                "assignee_ids": arguments.get("assignee_ids", []),
                "broadcast_updates": arguments.get("broadcast_updates", False)
            }

            # Build headers with internal API key for authentication
            headers = {"Content-Type": "application/json"}
            if self.internal_api_key:
                headers["X-Internal-Key"] = self.internal_api_key

            response = requests.post(
                f"{self.api_gateway_url}/api/v1/tasks",
                json=task_data,
                headers=headers,
                timeout=10
            )

            if response.status_code in [200, 201]:
                task = response.json()
                self.send_result_notification(
                    status="SUCCESS",
                    result={
                        "task_id": task.get("id"),
                        "title": task.get("title"),
                        "status": task.get("status", "pending"),
                        "created_at": task.get("created_at")
                    }
                )
            else:
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Failed to create task: API returned {response.status_code}"
                )

        except requests.exceptions.RequestException as e:
            self.send_result_notification(
                status="FAILURE",
                error_message=f"API error: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Unexpected error creating task: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to create task: {str(e)}"
            )


class UpdateTaskTool(BaseTool):
    """
    Updates an existing task's status or details.
    """

    def __init__(self):
        super().__init__(
            name="update_task",
            description="Updates the status or details of an existing task"
        )
        self.api_gateway_url = os.environ.get("API_GATEWAY_URL", "http://localhost:8000")
        self.internal_api_key = self._load_internal_api_key()

    def _load_internal_api_key(self) -> Optional[str]:
        """Load internal API key from shared volume."""
        try:
            with open("/shared/internal_api_key", "r") as f:
                return f.read().strip()
        except Exception as e:
            logger.warning(f"Failed to load internal API key: {e}")
            return None

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate task update arguments."""
        if "task_id" not in arguments:
            return False, "Missing required argument: 'task_id'"

        if not isinstance(arguments["task_id"], str):
            return False, f"'task_id' must be a string"

        if "status" in arguments:
            valid_statuses = ["pending", "in_progress", "completed", "archived"]
            if arguments["status"] not in valid_statuses:
                return False, f"'status' must be one of: {', '.join(valid_statuses)}"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "update_task",
            "description": "Updates the status or details of an existing task",
            "parameters": [
                {
                    "name": "task_id",
                    "type": "str",
                    "description": "ID of the task to update",
                    "required": True
                },
                {
                    "name": "status",
                    "type": "str",
                    "description": "New status (pending/in_progress/completed/archived)",
                    "required": False
                },
                {
                    "name": "description",
                    "type": "str",
                    "description": "Updated task description",
                    "required": False
                },
                {
                    "name": "assignee_ids",
                    "type": "list[str]",
                    "description": "Updated list of assignee agent IDs",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Update a task.

        Args:
            arguments: Must contain 'task_id', optionally 'status', 'description', etc.
        """
        task_id = arguments["task_id"]

        try:
            update_data = {}

            if "status" in arguments:
                update_data["status"] = arguments["status"]

            if "description" in arguments:
                update_data["description"] = arguments["description"]

            if "assignee_ids" in arguments:
                update_data["assignee_ids"] = arguments["assignee_ids"]

            # Build headers with internal API key for authentication
            headers = {"Content-Type": "application/json"}
            if self.internal_api_key:
                headers["X-Internal-Key"] = self.internal_api_key

            response = requests.patch(
                f"{self.api_gateway_url}/api/v1/tasks/{task_id}",
                json=update_data,
                headers=headers,
                timeout=10
            )

            if response.status_code == 200:
                task = response.json()
                self.send_result_notification(
                    status="SUCCESS",
                    result={
                        "task_id": task_id,
                        "updated": True,
                        "new_status": task.get("status"),
                        "updated_at": datetime.now().isoformat()
                    }
                )
            else:
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Failed to update task: API returned {response.status_code}"
                )

        except requests.exceptions.RequestException as e:
            self.send_result_notification(
                status="FAILURE",
                error_message=f"API error: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Unexpected error updating task: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to update task: {str(e)}"
            )


class GetTasksTool(BaseTool):
    """
    Retrieves tasks from the task management system.
    """

    def __init__(self):
        super().__init__(
            name="get_tasks",
            description="Retrieves tasks, optionally filtered by status or assignee"
        )
        self.api_gateway_url = os.environ.get("API_GATEWAY_URL", "http://localhost:8000")
        self.internal_api_key = self._load_internal_api_key()

    def _load_internal_api_key(self) -> Optional[str]:
        """Load internal API key from shared volume."""
        try:
            with open("/shared/internal_api_key", "r") as f:
                return f.read().strip()
        except Exception as e:
            logger.warning(f"Failed to load internal API key: {e}")
            return None

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate get tasks arguments."""
        if "status" in arguments:
            valid_statuses = ["pending", "in_progress", "completed", "archived"]
            if arguments["status"] not in valid_statuses:
                return False, f"'status' must be one of: {', '.join(valid_statuses)}"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "get_tasks",
            "description": "Retrieves tasks, optionally filtered by status or assignee",
            "parameters": [
                {
                    "name": "status",
                    "type": "str",
                    "description": "Filter by status (pending/in_progress/completed/archived)",
                    "required": False
                },
                {
                    "name": "assignee_id",
                    "type": "str",
                    "description": "Filter by assignee agent ID",
                    "required": False
                },
                {
                    "name": "limit",
                    "type": "int",
                    "description": "Maximum number of tasks to return",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Retrieve tasks.

        Args:
            arguments: Optionally 'status', 'assignee_id', 'limit'
        """
        try:
            params = {}

            if "status" in arguments:
                params["status"] = arguments["status"]

            if "assignee_id" in arguments:
                params["assignee_id"] = arguments["assignee_id"]

            if "limit" in arguments:
                params["limit"] = arguments["limit"]

            # Build headers with internal API key for authentication
            headers = {}
            if self.internal_api_key:
                headers["X-Internal-Key"] = self.internal_api_key

            response = requests.get(
                f"{self.api_gateway_url}/api/v1/tasks",
                params=params,
                headers=headers,
                timeout=10
            )

            if response.status_code == 200:
                tasks = response.json()
                self.send_result_notification(
                    status="SUCCESS",
                    result={
                        "tasks": tasks,
                        "count": len(tasks)
                    }
                )
            else:
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Failed to retrieve tasks: API returned {response.status_code}"
                )

        except requests.exceptions.RequestException as e:
            self.send_result_notification(
                status="FAILURE",
                error_message=f"API error: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Unexpected error getting tasks: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to get tasks: {str(e)}"
            )


class AssignToTaskTool(BaseTool):
    """
    Assigns an agent to an existing task.
    """

    def __init__(self):
        super().__init__(
            name="assign_to_task",
            description="Assigns an agent to a task"
        )
        self.api_gateway_url = os.environ.get("API_GATEWAY_URL", "http://localhost:8000")
        self.internal_api_key = self._load_internal_api_key()

    def _load_internal_api_key(self) -> Optional[str]:
        """Load internal API key from shared volume."""
        try:
            with open("/shared/internal_api_key", "r") as f:
                return f.read().strip()
        except Exception as e:
            logger.warning(f"Failed to load internal API key: {e}")
            return None

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate assign to task arguments."""
        if "task_id" not in arguments:
            return False, "Missing required argument: 'task_id'"

        if "agent_id" not in arguments:
            return False, "Missing required argument: 'agent_id'"

        if not isinstance(arguments["task_id"], (str, int)):
            return False, "'task_id' must be a string or integer"

        if not isinstance(arguments["agent_id"], str):
            return False, "'agent_id' must be a string"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "assign_to_task",
            "description": "Assigns an agent to an existing task (adds to assignee list)",
            "parameters": [
                {
                    "name": "task_id",
                    "type": "str",
                    "description": "ID of the task",
                    "required": True
                },
                {
                    "name": "agent_id",
                    "type": "str",
                    "description": "Agent ID to assign to the task",
                    "required": True
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Assign an agent to a task.

        Args:
            arguments: Must contain 'task_id' and 'agent_id'
        """
        task_id = str(arguments["task_id"])
        agent_id = arguments["agent_id"]

        try:
            # First, get the current task to see existing assignees
            headers = {}
            if self.internal_api_key:
                headers["X-Internal-Key"] = self.internal_api_key

            response = requests.get(
                f"{self.api_gateway_url}/api/v1/tasks/{task_id}",
                headers=headers,
                timeout=10
            )

            if response.status_code != 200:
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Failed to get task: API returned {response.status_code}"
                )
                return

            task = response.json()
            current_assignees = task.get("assignee_ids", [])

            # Add the new agent if not already assigned
            if agent_id in current_assignees:
                self.send_result_notification(
                    status="SUCCESS",
                    result={
                        "task_id": task_id,
                        "agent_id": agent_id,
                        "message": f"Agent {agent_id} is already assigned to task {task_id}",
                        "assignee_ids": current_assignees
                    }
                )
                return

            new_assignees = current_assignees + [agent_id]

            # Update the task with new assignee list
            headers["Content-Type"] = "application/json"
            update_response = requests.patch(
                f"{self.api_gateway_url}/api/v1/tasks/{task_id}",
                json={"assignee_ids": new_assignees},
                headers=headers,
                timeout=10
            )

            if update_response.status_code == 200:
                updated_task = update_response.json()
                self.send_result_notification(
                    status="SUCCESS",
                    result={
                        "task_id": task_id,
                        "agent_id": agent_id,
                        "assigned": True,
                        "assignee_ids": updated_task.get("assignee_ids", [])
                    }
                )
            else:
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Failed to assign agent: API returned {update_response.status_code}"
                )

        except requests.exceptions.RequestException as e:
            self.send_result_notification(
                status="FAILURE",
                error_message=f"API error: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Unexpected error assigning to task: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to assign to task: {str(e)}"
            )


class UnassignFromTaskTool(BaseTool):
    """
    Unassigns an agent from an existing task.
    """

    def __init__(self):
        super().__init__(
            name="unassign_from_task",
            description="Unassigns an agent from a task"
        )
        self.api_gateway_url = os.environ.get("API_GATEWAY_URL", "http://localhost:8000")
        self.internal_api_key = self._load_internal_api_key()

    def _load_internal_api_key(self) -> Optional[str]:
        """Load internal API key from shared volume."""
        try:
            with open("/shared/internal_api_key", "r") as f:
                return f.read().strip()
        except Exception as e:
            logger.warning(f"Failed to load internal API key: {e}")
            return None

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate unassign from task arguments."""
        if "task_id" not in arguments:
            return False, "Missing required argument: 'task_id'"

        if not isinstance(arguments["task_id"], (str, int)):
            return False, "'task_id' must be a string or integer"

        # agent_id is optional (defaults to self)
        if "agent_id" in arguments and not isinstance(arguments["agent_id"], str):
            return False, "'agent_id' must be a string"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "unassign_from_task",
            "description": "Unassigns an agent from a task (removes from assignee list). If agent_id not provided, unassigns self.",
            "parameters": [
                {
                    "name": "task_id",
                    "type": "str",
                    "description": "ID of the task",
                    "required": True
                },
                {
                    "name": "agent_id",
                    "type": "str",
                    "description": "Agent ID to unassign (defaults to self if not provided)",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Unassign an agent from a task.

        Args:
            arguments: Must contain 'task_id', optionally 'agent_id'
        """
        task_id = str(arguments["task_id"])
        agent_id = arguments.get("agent_id", self.agent_name)  # Default to self

        try:
            # First, get the current task to see existing assignees
            headers = {}
            if self.internal_api_key:
                headers["X-Internal-Key"] = self.internal_api_key

            response = requests.get(
                f"{self.api_gateway_url}/api/v1/tasks/{task_id}",
                headers=headers,
                timeout=10
            )

            if response.status_code != 200:
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Failed to get task: API returned {response.status_code}"
                )
                return

            task = response.json()
            current_assignees = task.get("assignee_ids", [])

            # Remove the agent if assigned
            if agent_id not in current_assignees:
                self.send_result_notification(
                    status="SUCCESS",
                    result={
                        "task_id": task_id,
                        "agent_id": agent_id,
                        "message": f"Agent {agent_id} is not assigned to task {task_id}",
                        "assignee_ids": current_assignees
                    }
                )
                return

            new_assignees = [a for a in current_assignees if a != agent_id]

            # Update the task with new assignee list
            headers["Content-Type"] = "application/json"
            update_response = requests.patch(
                f"{self.api_gateway_url}/api/v1/tasks/{task_id}",
                json={"assignee_ids": new_assignees},
                headers=headers,
                timeout=10
            )

            if update_response.status_code == 200:
                updated_task = update_response.json()
                self.send_result_notification(
                    status="SUCCESS",
                    result={
                        "task_id": task_id,
                        "agent_id": agent_id,
                        "unassigned": True,
                        "assignee_ids": updated_task.get("assignee_ids", [])
                    }
                )
            else:
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Failed to unassign agent: API returned {update_response.status_code}"
                )

        except requests.exceptions.RequestException as e:
            self.send_result_notification(
                status="FAILURE",
                error_message=f"API error: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Unexpected error unassigning from task: {e}")
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Failed to unassign from task: {str(e)}"
            )


# Export task management tools
TASK_TOOLS = [
    CreateTaskTool,
    UpdateTaskTool,
    GetTasksTool,
    AssignToTaskTool,
    UnassignFromTaskTool
]