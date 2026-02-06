"""
Weather Agent - VOS SDK Implementation

Provides comprehensive weather information using the VOS agent framework.
Combines weather-specific tools with standard agent capabilities.
"""

import sys
import os
import logging
from dotenv import load_dotenv

# Add the tools directory to Python path
# In container, tools are at /app/tools
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
    GetWeatherTool,
    GetForecastTool,
    GetWeatherByCoordinatesTool,
    GetUVIndexTool,
    GetAirQualityTool,
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


class WeatherAgent(VOSAgentImplementation):
    """
    Weather Agent implementation using the VOS SDK.

    Provides weather information, forecasts, and related services while
    maintaining all standard agent capabilities for task management,
    communication, and scheduling.
    """

    # Define all tools this agent should have access to
    # Explicit tool list - excludes send_user_message (only primary agent should talk to users)
    TOOLS = [
        SendAgentMessageTool,
        CreateTaskTool,
        UpdateTaskTool,
        GetTasksTool,
        AssignToTaskTool,
        UnassignFromTaskTool,
        SleepTool,
        ShutdownTool,
        GetWeatherTool,
        GetForecastTool,
        GetWeatherByCoordinatesTool,
        GetUVIndexTool,
        GetAirQualityTool,
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
        # Call tools for voice calls (when call is transferred to this agent)
        SpeakTool,
        HangUpTool,
        TransferCallTool
    ]

    def __init__(self, config: AgentConfig):
        # Initialize with a simple description
        super().__init__(
            config,
            "Provides comprehensive weather information, forecasts, and meteorological data"
        )

        # Generate system prompt with tools automatically populated
        # The SDK will look for system_prompt.txt in this agent's directory
        self.system_prompt = self.generate_system_prompt()

        # Update the context builder with the complete prompt
        self.context_builder.agent_description = self.system_prompt

        logger.info(f"Weather Agent initialized with {len(self.tools)} tools")
        logger.debug(f"Registered tools: {', '.join(sorted(self.tools.keys()))}")


def main():
    """Main entry point for the Weather Agent."""
    # Load environment variables from .env file
    load_dotenv()

    try:
        # Create agent configuration from environment
        config = AgentConfig.from_env(
            agent_name="weather_agent",
            agent_display_name="Weather Service"
        )

        # Start metrics server if available
        if METRICS_AVAILABLE:
            metrics_server = MetricsServer(port=8080)
            metrics_server.start()
            logger.info("✅ Metrics server started on port 8080")

        # Create and start the weather agent
        agent = WeatherAgent(config)

        logger.info("=" * 60)
        logger.info("🌤️  Weather Agent Starting")
        logger.info(f"Agent Name: {config.agent_name}")
        logger.info(f"Display Name: {config.agent_display_name}")
        logger.info(f"Queue: {config.queue_name}")
        logger.info(f"Tools Available: {len(agent.tools)}")
        logger.info("=" * 60)

        # Start the agent - this blocks until stopped
        agent.start()

    except KeyboardInterrupt:
        logger.info("🛑 Weather Agent shutdown requested by user")
    except Exception as e:
        logger.error(f"💥 Weather Agent startup error: {e}")
        raise
    finally:
        if 'agent' in locals():
            agent.stop()
        logger.info("👋 Weather Agent stopped")


if __name__ == "__main__":
    main()