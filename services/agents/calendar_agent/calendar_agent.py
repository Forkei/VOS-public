"""
Calendar Agent - VOS SDK Implementation

Provides comprehensive calendar, reminder, and scheduling capabilities.
"""

import sys
import os
import logging
from dotenv import load_dotenv

# Add the tools directory to Python path
tools_path = '/app/tools' if os.path.exists('/app/tools') else os.path.join(os.path.dirname(__file__), '..', '..', 'tools')
sys.path.append(tools_path)

# Add shared directory for metrics
shared_path = '/app/shared' if os.path.exists('/app/shared') else os.path.join(os.path.dirname(__file__), '..', 'shared')
sys.path.append(shared_path)

from vos_sdk import AgentConfig, VOSAgentImplementation

# Import standard tools
from tools import (
    SendAgentMessageTool,
    CreateTaskTool,
    UpdateTaskTool,
    GetTasksTool,
    AssignToTaskTool,
    UnassignFromTaskTool,
    SleepTool,
    ShutdownTool,
    CreateMemoryTool,
    SearchMemoryTool,
    GetMemoryTool,
    UpdateMemoryTool,
    DeleteMemoryTool,
    CreateDocumentTool,
    ReadDocumentTool,
    ListDocumentsTool,
    DeleteDocumentTool,
    # Call tools for voice calls (when call is transferred to this agent)
    SpeakTool,
    HangUpTool,
    TransferCallTool
)

# Import calendar-specific tools
from tools.calendar import (
    CreateCalendarEventTool,
    ListCalendarEventsTool,
    UpdateCalendarEventTool,
    DeleteCalendarEventTool,
    CreateReminderTool,
    ListRemindersTool,
    EditReminderTool,
    DeleteReminderTool
)

# Import metrics
try:
    from metrics import MetricsServer
    METRICS_AVAILABLE = True
except ImportError:
    logger = logging.getLogger(__name__)
    logger.warning("⚠️ Metrics module not available - running without Prometheus metrics")
    METRICS_AVAILABLE = False
    MetricsServer = None

logger = logging.getLogger(__name__)


class CalendarAgent(VOSAgentImplementation):
    """
    Calendar Agent implementation using the VOS SDK.

    Provides calendar management with virtual recurrence, reminders,
    and scheduling while maintaining all standard agent capabilities.
    """

    # Define all tools this agent should have access to
    TOOLS = [
        # Standard agent tools
        SendAgentMessageTool,
        CreateTaskTool,
        UpdateTaskTool,
        GetTasksTool,
        AssignToTaskTool,
        UnassignFromTaskTool,
        SleepTool,
        ShutdownTool,
        CreateMemoryTool,
        SearchMemoryTool,
        GetMemoryTool,
        UpdateMemoryTool,
        DeleteMemoryTool,

        # Calendar-specific tools (8 tools)
        CreateCalendarEventTool,
        ListCalendarEventsTool,
        UpdateCalendarEventTool,
        DeleteCalendarEventTool,
        CreateReminderTool,
        ListRemindersTool,
        EditReminderTool,
        DeleteReminderTool,

        # Document tools for efficient data sharing
        CreateDocumentTool,
        ReadDocumentTool,
        ListDocumentsTool,
        DeleteDocumentTool,

        # Call tools for voice calls (when call is transferred to this agent)
        SpeakTool,
        HangUpTool,
        TransferCallTool
    ]

    def __init__(self, config: AgentConfig):
        # Initialize with a simple description
        super().__init__(
            config,
            "Manages calendar events with virtual recurrence, reminders, and scheduling with conflict detection"
        )

        # Generate system prompt with tools automatically populated
        # The SDK will look for system_prompt.txt in this agent's directory
        self.system_prompt = self.generate_system_prompt()

        # Update the context builder with the complete prompt
        self.context_builder.agent_description = self.system_prompt

        logger.info(f"Calendar Agent initialized with {len(self.tools)} tools")
        logger.debug(f"Registered tools: {', '.join(sorted(self.tools.keys()))}")


def main():
    """Main entry point for the Calendar Agent."""
    # Load environment variables from .env file
    load_dotenv()

    try:
        # Create agent configuration from environment
        config = AgentConfig.from_env(
            agent_name="calendar_agent",
            agent_display_name="Calendar & Scheduling Service"
        )

        # Start metrics server if available
        if METRICS_AVAILABLE:
            metrics_server = MetricsServer(port=8080)
            metrics_server.start()
            logger.info("✅ Metrics server started on port 8080")

        # Create and start the calendar agent
        agent = CalendarAgent(config)

        logger.info("=" * 60)
        logger.info("📅 Calendar Agent Starting")
        logger.info(f"Agent Name: {config.agent_name}")
        logger.info(f"Display Name: {config.agent_display_name}")
        logger.info(f"Queue: {config.queue_name}")
        logger.info(f"Tools Available: {len(agent.tools)}")
        logger.info("=" * 60)

        # Start the agent - this blocks until stopped
        agent.start()

    except KeyboardInterrupt:
        logger.info("🛑 Calendar Agent shutdown requested by user")
    except Exception as e:
        logger.error(f"💥 Calendar Agent startup error: {e}")
        raise
    finally:
        if 'agent' in locals():
            agent.stop()
        logger.info("👋 Calendar Agent stopped")


if __name__ == "__main__":
    main()
