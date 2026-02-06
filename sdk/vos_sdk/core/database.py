"""
Database interaction functions for VOS agents.

These functions provide a clean interface for agents to interact with
the VOS database through the API Gateway.
"""

import json
import logging
import time
from typing import List, Dict, Any, Optional
from enum import Enum

import httpx

from .config import AgentConfig
from ..schemas import ToolResult

logger = logging.getLogger(__name__)


class ProcessingState(str, Enum):
    """Agent processing states"""
    IDLE = "idle"
    THINKING = "thinking"
    EXECUTING_TOOLS = "executing_tools"


class AgentStatus(str, Enum):
    """Overall agent status"""
    ACTIVE = "active"
    SLEEPING = "sleeping"
    OFF = "off"


class DatabaseClient:
    """
    Client for interacting with VOS database through API Gateway.

    Provides methods for managing agent state, processing state,
    and message history.
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        self.base_url = config.api_gateway_url
        self.timeout = 10.0

        # Load internal API key for agent authentication
        self.internal_api_key = self._load_internal_api_key()

    def _load_internal_api_key(self, max_retries: int = 10, initial_delay: float = 0.5) -> Optional[str]:
        """
        Load internal API key from shared volume with retry logic.

        Retries with exponential backoff to handle cases where the API gateway
        hasn't finished writing the key file yet.

        Args:
            max_retries: Maximum number of retry attempts (default: 10)
            initial_delay: Initial delay in seconds between retries (default: 0.5)

        Returns:
            The internal API key string, or None if loading fails after all retries
        """
        delay = initial_delay

        for attempt in range(max_retries):
            try:
                with open("/shared/internal_api_key", "r") as f:
                    key = f.read().strip()
                    if key:  # Ensure key is not empty
                        logger.info(f"✅ Loaded internal API key: {key[:8]}... (attempt {attempt + 1})")
                        return key
                    else:
                        logger.warning(f"⚠️ Internal API key file is empty (attempt {attempt + 1}/{max_retries})")
            except FileNotFoundError:
                logger.warning(f"⚠️ Internal API key file not found (attempt {attempt + 1}/{max_retries})")
            except Exception as e:
                logger.error(f"❌ Failed to load internal API key: {e} (attempt {attempt + 1}/{max_retries})")

            # If not the last attempt, wait before retrying
            if attempt < max_retries - 1:
                logger.info(f"Retrying in {delay:.1f} seconds...")
                time.sleep(delay)
                delay = min(delay * 2, 30)  # Exponential backoff, max 30 seconds

        logger.error("❌ Failed to load internal API key after all retry attempts - internal endpoints will fail")
        return None

    def reload_internal_api_key(self) -> bool:
        """
        Reload the internal API key from disk.

        Useful when the API gateway restarts and generates a new key.

        Returns:
            True if key was successfully reloaded, False otherwise
        """
        logger.info("Reloading internal API key...")
        new_key = self._load_internal_api_key(max_retries=3, initial_delay=1.0)
        if new_key:
            self.internal_api_key = new_key
            logger.info("✅ Internal API key reloaded successfully")
            return True
        else:
            logger.error("❌ Failed to reload internal API key")
            return False

    def _make_request(self, method: str, endpoint: str, data: Optional[Dict] = None, _retry: bool = True) -> ToolResult:
        """
        Make HTTP request to API Gateway.

        Args:
            method: HTTP method (GET, POST, PUT)
            endpoint: API endpoint path
            data: Request data for POST/PUT
            _retry: Internal flag to control retry behavior (default: True)

        Returns:
            ToolResult with success/failure status
        """
        url = f"{self.base_url}{endpoint}"

        # Build headers with internal API key for authentication
        headers = {}
        if self.internal_api_key:
            headers["X-Internal-Key"] = self.internal_api_key

        try:
            with httpx.Client(timeout=self.timeout) as client:
                if method == "GET":
                    response = client.get(url, headers=headers)
                elif method == "POST":
                    response = client.post(url, json=data, headers=headers)
                elif method == "PUT":
                    response = client.put(url, json=data, headers=headers)
                elif method == "DELETE":
                    response = client.delete(url, headers=headers)
                else:
                    return ToolResult.failure(
                        tool_name="database_request",
                        error_message=f"Unsupported HTTP method: {method}"
                    )

                if response.status_code in (200, 201):
                    result = response.json()
                    logger.debug(f"Database request successful: {method} {endpoint}")
                    return ToolResult.success("database_request", {"result": result})
                elif response.status_code == 401 and _retry:
                    # Authentication failed - try reloading the internal API key
                    logger.warning("⚠️ Request returned 401 Unauthorized - attempting to reload internal API key")
                    if self.reload_internal_api_key():
                        logger.info("Retrying request with new API key...")
                        return self._make_request(method, endpoint, data, _retry=False)
                    else:
                        return ToolResult.failure(
                            tool_name="database_request",
                            error_message="Authentication failed and could not reload API key"
                        )
                else:
                    error_detail = "Unknown error"
                    try:
                        error_response = response.json()
                        error_detail = error_response.get("detail", str(error_response))
                    except:
                        error_detail = response.text or f"HTTP {response.status_code}"

                    return ToolResult.failure(
                        tool_name="database_request",
                        error_message=f"API request failed: {error_detail}"
                    )

        except httpx.TimeoutException:
            return ToolResult.failure(
                tool_name="database_request",
                error_message="Request timeout"
            )
        except httpx.ConnectError:
            return ToolResult.failure(
                tool_name="database_request",
                error_message="Cannot connect to API Gateway"
            )
        except Exception as e:
            return ToolResult.failure(
                tool_name="database_request",
                error_message=f"Unexpected error: {str(e)}"
            )

    def get_processing_state(self, agent_name: str) -> ToolResult:
        """
        Get the current processing state of an agent.

        Args:
            agent_name: Name of the agent

        Returns:
            ToolResult with processing_state in result data
        """
        endpoint = f"/api/v1/agents/{agent_name}/processing-state"
        return self._make_request("GET", endpoint)

    def set_processing_state(self, agent_name: str, state: ProcessingState) -> ToolResult:
        """
        Set the processing state of an agent.

        Args:
            agent_name: Name of the agent
            state: New processing state

        Returns:
            ToolResult with updated agent state
        """
        endpoint = f"/api/v1/agents/{agent_name}/processing-state"
        data = {"processing_state": state.value}
        return self._make_request("PUT", endpoint, data)

    def get_agent_state(self, agent_name: str) -> ToolResult:
        """
        Get complete agent state (status, processing_state, etc.).

        Args:
            agent_name: Name of the agent

        Returns:
            ToolResult with full agent state
        """
        endpoint = f"/api/v1/agents/{agent_name}/state"
        return self._make_request("GET", endpoint)

    def update_agent_status(self, agent_name: str, status: AgentStatus) -> ToolResult:
        """
        Update the overall status of an agent.

        Args:
            agent_name: Name of the agent
            status: New agent status

        Returns:
            ToolResult with updated agent state
        """
        endpoint = f"/api/v1/agents/{agent_name}/status"
        data = {"status": status.value}
        return self._make_request("PUT", endpoint, data)

    def get_agent_status(self, agent_name: str) -> ToolResult:
        """
        Get the current status of an agent.

        Args:
            agent_name: Name of the agent

        Returns:
            ToolResult with agent status
        """
        endpoint = f"/api/v1/agents/{agent_name}/status"
        return self._make_request("GET", endpoint)

    def get_message_history(self, agent_name: str, limit: int = 100, offset: int = 0) -> ToolResult:
        """
        Get message history for an agent.

        Args:
            agent_name: Name of the agent
            limit: Maximum number of messages to retrieve
            offset: Number of messages to skip

        Returns:
            ToolResult with message history
        """
        endpoint = f"/api/v1/transcript/{agent_name}?limit={limit}&offset={offset}"
        return self._make_request("GET", endpoint)

    def append_message(self, agent_name: str, role: str, content: Dict[str, Any],
                      documents: Optional[List[str]] = None) -> ToolResult:
        """
        Append a single message to an agent's history.

        Args:
            agent_name: Name of the agent
            role: Message role (system, user, assistant)
            content: Message content as JSON
            documents: Optional list of document IDs

        Returns:
            ToolResult with updated message count
        """
        endpoint = "/api/v1/transcript/append"
        data = {
            "agent_id": agent_name,
            "message": {
                "role": role,
                "content": content,
                "documents": documents or []
            }
        }
        return self._make_request("POST", endpoint, data)

    def submit_full_transcript(self, agent_name: str, messages: List[Dict[str, Any]]) -> ToolResult:
        """
        Replace entire message history for an agent.

        Args:
            agent_name: Name of the agent
            messages: List of message objects

        Returns:
            ToolResult with transcript submission status
        """
        endpoint = "/api/v1/transcript/submit"
        data = {
            "agent_id": agent_name,
            "messages": messages
        }
        return self._make_request("POST", endpoint, data)

    def update_system_prompt(self, agent_name: str, content: str) -> ToolResult:
        """
        Update the system message in the agent's transcript.

        This updates only the system message without affecting the rest
        of the conversation history.

        Args:
            agent_name: Name of the agent
            content: New system prompt content

        Returns:
            ToolResult with update status
        """
        endpoint = f"/api/v1/transcript/{agent_name}/system-prompt"
        data = {"content": content}
        return self._make_request("PUT", endpoint, data)

    # ===========================================================================
    # System Prompt Management (Database-First)
    # ===========================================================================

    def get_active_prompt(self, agent_id: str) -> ToolResult:
        """
        Get the active system prompt for an agent from the database.

        This is the database-first approach for system prompts, allowing
        prompts to be managed through the API/web UI without code changes.

        Args:
            agent_id: The agent's identifier (e.g., "primary_agent")

        Returns:
            ToolResult with the active prompt data including:
            - content: The main prompt content
            - section_ids: Array of section IDs to include
            - tools_position: Where to inject tools ("start", "end", "none")
            - version: Current version number
        """
        endpoint = f"/api/v1/system-prompts/agents/{agent_id}/active"
        return self._make_request("GET", endpoint)

    def get_prompt_sections(self, section_ids: List[str]) -> ToolResult:
        """
        Get multiple prompt sections by their IDs.

        Args:
            section_ids: List of section IDs to fetch

        Returns:
            ToolResult with sections content
        """
        # Fetch sections - for now we fetch all and filter client-side
        # TODO: Add batch endpoint for efficiency
        endpoint = "/api/v1/system-prompts/sections"
        result = self._make_request("GET", endpoint)

        if result.status != "SUCCESS":
            return result

        # Filter to requested section IDs
        all_sections = result.result.get("result", [])
        requested_sections = [
            s for s in all_sections
            if s.get("section_id") in section_ids
        ]

        # Sort by display_order
        requested_sections.sort(key=lambda s: s.get("display_order", 0))

        return ToolResult.success("get_prompt_sections", {"sections": requested_sections})

    def get_full_prompt_content(self, agent_id: str) -> ToolResult:
        """
        Get the full system prompt content with sections expanded.

        This fetches the active prompt and all its sections, combines them,
        and returns the complete prompt content ready for use.

        Args:
            agent_id: The agent's identifier

        Returns:
            ToolResult with:
            - full_content: Complete prompt with sections
            - tools_position: Where to inject tools
            - version: Prompt version
        """
        # Get active prompt
        prompt_result = self.get_active_prompt(agent_id)
        if prompt_result.status != "SUCCESS":
            return prompt_result

        prompt = prompt_result.result.get("result", {})
        section_ids = prompt.get("section_ids", [])
        main_content = prompt.get("content", "")
        tools_position = prompt.get("tools_position", "end")
        version = prompt.get("version", 1)

        # Get sections if any
        sections_content = ""
        if section_ids:
            sections_result = self.get_prompt_sections(section_ids)
            if sections_result.status == "SUCCESS":
                sections = sections_result.result.get("sections", [])
                sections_content = "\n\n".join(s.get("content", "") for s in sections)

        # Combine sections and main content
        parts = []
        if sections_content:
            parts.append(sections_content)
        if main_content:
            parts.append(main_content)

        full_content = "\n\n".join(parts)

        return ToolResult.success("get_full_prompt_content", {
            "full_content": full_content,
            "tools_position": tools_position,
            "version": version
        })


# Convenience functions for direct use
def get_processing_state(config: AgentConfig, agent_name: str) -> ProcessingState:
    """
    Get processing state, returning the enum directly.

    Raises:
        RuntimeError: If the request fails
    """
    db = DatabaseClient(config)
    result = db.get_processing_state(agent_name)

    if result.status != "SUCCESS":
        raise RuntimeError(f"Failed to get processing state: {result.error_message}")

    state_str = result.result["result"]["processing_state"]
    return ProcessingState(state_str)


def set_processing_state(config: AgentConfig, agent_name: str, state: ProcessingState) -> None:
    """
    Set processing state, raising exception on failure.

    Raises:
        RuntimeError: If the request fails
    """
    db = DatabaseClient(config)
    result = db.set_processing_state(agent_name, state)

    if result.status != "SUCCESS":
        raise RuntimeError(f"Failed to set processing state: {result.error_message}")

    logger.info(f"Updated {agent_name} processing state to {state.value}")


def append_message_to_history(config: AgentConfig, agent_name: str,
                             role: str, content: Dict[str, Any]) -> None:
    """
    Append message to agent history, raising exception on failure.

    Raises:
        RuntimeError: If the request fails
    """
    db = DatabaseClient(config)
    result = db.append_message(agent_name, role, content)

    if result.status != "SUCCESS":
        raise RuntimeError(f"Failed to append message: {result.error_message}")

    logger.debug(f"Appended {role} message to {agent_name} history")