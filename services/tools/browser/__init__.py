"""
Browser Tools Module

Provides AI-powered browser automation capabilities through:
- Browser-Use: LLM-powered browser interaction and task automation
- Direct Navigation: Simple URL navigation and screenshot capture
"""

from .browser_use_tools import (
    BrowserUseTool,
    BrowserNavigateTool,
    BROWSER_TOOLS
)

__all__ = [
    # Individual browser tools
    'BrowserUseTool',
    'BrowserNavigateTool',

    # Combined
    'BROWSER_TOOLS',
]
