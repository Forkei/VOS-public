"""
Calculator Agent Implementation

Provides comprehensive mathematical calculation capabilities including:
- Basic arithmetic operations
- Advanced mathematical functions (trigonometry, logarithms, etc.)
- Statistical analysis
- Random number generation
- Number theory operations
- Linear algebra operations
- Unit conversions
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
    BasicCalculationTool,
    AdvancedMathTool,
    StatisticsTool,
    RandomNumberTool,
    NumberTheoryTool,
    LinearAlgebraTool,
    UnitConversionTool,
    CreateDocumentTool,
    ReadDocumentTool,
    ListDocumentsTool,
    DeleteDocumentTool,
    # Call tools for voice calls (when call is transferred to this agent)
    SpeakTool,
    HangUpTool,
    TransferCallTool
)


class CalculatorAgent(VOSAgentImplementation):
    """
    Calculator Agent for performing comprehensive mathematical operations.

    Capabilities:
    - Basic arithmetic (addition, subtraction, multiplication, division, powers, square roots)
    - Advanced math (trigonometry, logarithms, exponentials, hyperbolic functions)
    - Statistical analysis (mean, median, mode, standard deviation, variance)
    - Random number generation (uniform, normal distribution, sampling)
    - Number theory (GCD, LCM, prime checking, prime factorization, divisors)
    - Linear algebra (vectors, matrices, dot product, cross product, determinants)
    - Unit conversions (length, mass, temperature, volume, speed, time, area)
    """

    # Define all tools this agent can use
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

        # Calculator-specific tools
        BasicCalculationTool,
        AdvancedMathTool,
        StatisticsTool,
        RandomNumberTool,
        NumberTheoryTool,
        LinearAlgebraTool,
        UnitConversionTool,

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
        super().__init__(
            config,
            "Calculator Agent - Provides comprehensive mathematical calculation capabilities"
        )
        self.system_prompt = self.generate_system_prompt()

        # Update the context builder with the complete prompt
        self.context_builder.agent_description = self.system_prompt


def main():
    """Entry point for the calculator agent"""
    # Load environment variables from .env file
    load_dotenv()

    try:
        config = AgentConfig.from_env(
            agent_name="calculator_agent",
            agent_display_name="Calculator & Math Service"
        )

        # Start metrics server if available
        if METRICS_AVAILABLE:
            metrics_server = MetricsServer(port=8080)
            metrics_server.start()
            logger.info("✅ Metrics server started on port 8080")

        agent = CalculatorAgent(config)

        logger.info("=" * 60)
        logger.info("🧮 Calculator Agent Starting")
        logger.info(f"Agent Name: {config.agent_name}")
        logger.info(f"Display Name: {config.agent_display_name}")
        logger.info(f"Queue: {config.queue_name}")
        logger.info(f"Tools Available: {len(agent.tools)}")
        logger.info("=" * 60)

        agent.start()

    except KeyboardInterrupt:
        logger.info("🛑 Calculator Agent shutdown requested by user")
    except Exception as e:
        logger.error(f"💥 Calculator Agent startup error: {e}")
        raise
    finally:
        if 'agent' in locals():
            agent.stop()
        logger.info("👋 Calculator Agent stopped")


if __name__ == "__main__":
    main()
