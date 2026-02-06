"""
Standard tools package for VOS agents.

Centralized imports for all standard agent tools organized by category.
"""

from .messaging_tools import MESSAGING_TOOLS, SendUserMessageTool, SendAgentMessageTool
from .task_tools import TASK_TOOLS, CreateTaskTool, UpdateTaskTool, GetTasksTool, AssignToTaskTool, UnassignFromTaskTool
from .timer_tools import TIMER_TOOLS, SleepTool, ShutdownTool
from .system_prompt_tools import ReadSystemPromptTool, EditSystemPromptTool

# System prompt tools collection
SYSTEM_PROMPT_TOOLS = [ReadSystemPromptTool, EditSystemPromptTool]

# Combined collection of all standard tools
STANDARD_TOOLS = MESSAGING_TOOLS + TASK_TOOLS + TIMER_TOOLS + SYSTEM_PROMPT_TOOLS

# Tools for non-primary agents (excludes send_user_message)
AGENT_TOOLS = [SendAgentMessageTool] + TASK_TOOLS + TIMER_TOOLS + SYSTEM_PROMPT_TOOLS

__all__ = [
    # Tool collections
    'STANDARD_TOOLS',
    'AGENT_TOOLS',
    'MESSAGING_TOOLS',
    'TASK_TOOLS',
    'TIMER_TOOLS',
    'SYSTEM_PROMPT_TOOLS',

    # Individual messaging tools
    'SendUserMessageTool',
    'SendAgentMessageTool',

    # Individual task tools
    'CreateTaskTool',
    'UpdateTaskTool',
    'GetTasksTool',
    'AssignToTaskTool',
    'UnassignFromTaskTool',

    # Individual timer tools
    'SleepTool',
    'ShutdownTool',

    # Individual system prompt tools
    'ReadSystemPromptTool',
    'EditSystemPromptTool'
]