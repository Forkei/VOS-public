"""
VOS SDK - Virtual Operating System Software Development Kit

A standardized toolkit for agent communication and tool interaction within the VOS ecosystem.
"""

__version__ = "0.1.0"
__author__ = "VOS Development Team"

from .schemas import ToolResult
from .core.config import AgentConfig
from .core.database import DatabaseClient, ProcessingState, AgentStatus
from .core.agent import VOSAgent, VOSAgentImplementation
from .tools.base import BaseTool

__all__ = [
    "ToolResult",
    "AgentConfig",
    "DatabaseClient",
    "ProcessingState",
    "AgentStatus",
    "VOSAgent",
    "VOSAgentImplementation",
    "BaseTool"
]