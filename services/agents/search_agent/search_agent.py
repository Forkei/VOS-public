"""
Search Agent - VOS SDK Implementation

Provides comprehensive web search and scraping capabilities using Firecrawl and DuckDuckGo.
Combines search tools with standard agent capabilities for a powerful research agent.
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
    DOCUMENT_TOOLS,
    DownloadImageTool,
    CreateAttachmentTool,
    GetAttachmentTool,
    ATTACHMENT_TOOLS,
    # Call tools for voice calls (when call is transferred to this agent)
    SpeakTool,
    HangUpTool,
    TransferCallTool
)

# Import search tools
from search import SEARCH_TOOLS

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


class SearchAgent(VOSAgentImplementation):
    """
    Search Agent implementation using the VOS SDK.

    Provides comprehensive web search and scraping capabilities through:
    - Firecrawl: Advanced scraping, crawling, mapping, searching, and structured extraction
    - DuckDuckGo: Fast, privacy-focused web, news, and books search

    Maintains all standard agent capabilities for task management,
    communication, memory, and scheduling.
    """

    # Define all tools this agent should have access to
    # Combines standard agent tools with search-specific tools
    TOOLS = [
        # Standard agent tools (excludes send_user_message - only primary agent talks to users)
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
        # Call tools for voice calls (when call is transferred to this agent)
        SpeakTool,
        HangUpTool,
        TransferCallTool,
    ] + SEARCH_TOOLS + DOCUMENT_TOOLS + ATTACHMENT_TOOLS  # Add search, document, and image tools

    def __init__(self, config: AgentConfig):
        # Initialize with a description
        super().__init__(
            config,
            "Provides comprehensive web search, scraping, crawling, and data extraction capabilities"
        )

        # Generate system prompt with tools automatically populated
        self.system_prompt = self.generate_system_prompt()

        # Update the context builder with the complete prompt
        self.context_builder.agent_description = self.system_prompt

        logger.info(f"Search Agent initialized with {len(self.tools)} tools")
        logger.debug(f"Registered tools: {', '.join(sorted(self.tools.keys()))}")


def main():
    """Main entry point for the Search Agent."""
    # Load environment variables from .env file
    load_dotenv()

    try:
        # Create agent configuration from environment
        config = AgentConfig.from_env(
            agent_name="search_agent",
            agent_display_name="Search & Research Service"
        )

        # Start metrics server if available
        if METRICS_AVAILABLE:
            metrics_server = MetricsServer(port=8080)
            metrics_server.start()
            logger.info("✅ Metrics server started on port 8080")

        # Create and start the search agent
        agent = SearchAgent(config)

        logger.info("=" * 60)
        logger.info("🔍 Search Agent Starting")
        logger.info(f"Agent Name: {config.agent_name}")
        logger.info(f"Display Name: {config.agent_display_name}")
        logger.info(f"Queue: {config.queue_name}")
        logger.info(f"Tools Available: {len(agent.tools)}")
        logger.info("=" * 60)

        # Start the agent - this blocks until stopped
        agent.start()

    except KeyboardInterrupt:
        logger.info("🛑 Search Agent shutdown requested by user")
    except Exception as e:
        logger.error(f"💥 Search Agent startup error: {e}")
        raise
    finally:
        if 'agent' in locals():
            agent.stop()
        logger.info("👋 Search Agent stopped")


if __name__ == "__main__":
    main()
