"""
Primary Agent - VOS SDK Implementation

Central orchestrator of the VOS system that handles user interactions,
delegates tasks to specialized agents, and coordinates responses.
"""

import sys
import os
import logging
from dotenv import load_dotenv

# Add the tools directory to Python path
# In container, tools are at /app/tools
tools_path = '/app/tools' if os.path.exists('/app/tools') else os.path.join(os.path.dirname(__file__), '..', 'tools')
sys.path.append(tools_path)

# Add shared directory for metrics
shared_path = '/app/shared' if os.path.exists('/app/shared') else os.path.join(os.path.dirname(__file__), '..', 'shared')
sys.path.append(shared_path)

from vos_sdk import AgentConfig, VOSAgentImplementation
from tools import (
    SendUserMessageTool,
    SendAgentMessageTool,
    CreateTaskTool,
    UpdateTaskTool,
    GetTasksTool,
    AssignToTaskTool,
    UnassignFromTaskTool,
    SleepTool,
    CreateMemoryTool,
    SearchMemoryTool,
    GetMemoryTool,
    UpdateMemoryTool,
    DeleteMemoryTool,
    CreateDocumentTool,
    ReadDocumentTool,
    ListDocumentsTool,
    DeleteDocumentTool,
    GetAttachmentTool,
    ViewImageTool,
    ReadSystemPromptTool,
    EditSystemPromptTool,
    # Call tools for voice calls
    SpeakTool,
    AnswerCallTool,
    HangUpTool,
    TransferCallTool,
    RecallPhoneTool,
    CallUserTool,
    CallPhoneTool,
    SendSMSTool
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


class PrimaryAgent(VOSAgentImplementation):
    """
    Primary Agent implementation using the VOS SDK.

    Acts as the central orchestrator, receiving user messages,
    understanding intent, delegating to specialized agents,
    and synthesizing responses.
    """

    # Define all tools this agent should have access to
    # Primary agent gets all standard tools EXCEPT shutdown and calendar tools
    # Calendar tools (reminders, events) should be delegated to calendar_agent
    TOOLS = [
        SendUserMessageTool,
        SendAgentMessageTool,
        CreateTaskTool,
        UpdateTaskTool,
        GetTasksTool,
        AssignToTaskTool,
        UnassignFromTaskTool,
        SleepTool,
        CreateMemoryTool,
        SearchMemoryTool,
        GetMemoryTool,
        UpdateMemoryTool,
        DeleteMemoryTool,
        # Document tools for efficient data sharing
        CreateDocumentTool,
        ReadDocumentTool,
        ListDocumentsTool,
        DeleteDocumentTool,
        # Attachment tools for viewing images
        GetAttachmentTool,
        ViewImageTool,
        # System prompt tools for self-modification
        ReadSystemPromptTool,
        EditSystemPromptTool,
        # Call tools for voice calls (Primary Agent can initiate calls and transfer)
        SpeakTool,
        AnswerCallTool,
        HangUpTool,
        TransferCallTool,
        RecallPhoneTool,
        CallUserTool,  # Primary Agent only - can call user via in-app voice
        CallPhoneTool,  # Primary Agent only - can call user via Twilio phone
        SendSMSTool  # Primary Agent only - can send SMS to any phone number
    ]

    def __init__(self, config: AgentConfig):
        # Initialize with orchestrator description
        super().__init__(
            config,
            "Central orchestrator that handles user interactions and coordinates specialized agents"
        )

        # Generate system prompt with tools automatically populated
        # The SDK will look for system_prompt.txt in this agent's directory
        self.system_prompt = self.generate_system_prompt()

        # Update the context builder with the complete prompt
        self.context_builder.agent_description = self.system_prompt

        logger.info(f"Primary Agent initialized with {len(self.tools)} tools")
        logger.debug(f"Registered tools: {', '.join(sorted(self.tools.keys()))}")


def main():
    """Main entry point for the Primary Agent."""
    # Load environment variables from .env file
    load_dotenv()

    try:
        # Create agent configuration from environment
        config = AgentConfig.from_env(
            agent_name="primary_agent",
            agent_display_name="Primary Orchestrator"
        )

        # Start metrics server if available
        if METRICS_AVAILABLE:
            metrics_server = MetricsServer(port=8080)
            metrics_server.start()
            logger.info("✅ Metrics server started on port 8080")

        # Create and start the primary agent
        agent = PrimaryAgent(config)

        logger.info("=" * 60)
        logger.info("🎯 Primary Agent Starting")
        logger.info(f"Agent Name: {config.agent_name}")
        logger.info(f"Display Name: {config.agent_display_name}")
        logger.info(f"Queue: {config.queue_name}")
        logger.info(f"Tools Available: {len(agent.tools)}")
        logger.info("=" * 60)

        # Start the agent - this blocks until stopped
        agent.start()

    except KeyboardInterrupt:
        logger.info("🛑 Primary Agent shutdown requested by user")
    except Exception as e:
        logger.error(f"💥 Primary Agent startup error: {e}")
        raise
    finally:
        if 'agent' in locals():
            agent.stop()
        logger.info("👋 Primary Agent stopped")


if __name__ == "__main__":
    main()