"""
Calendar and Reminder Tools

This module contains all calendar-related tools for the VOS calendar agent.
"""

# Calendar Event Tools
from .calendar_event_tools import (
    CreateCalendarEventTool,
    ListCalendarEventsTool,
    UpdateCalendarEventTool,
    DeleteCalendarEventTool
)

# Reminder Tools
from .reminder_tools import (
    CreateReminderTool,
    ListRemindersTool,
    EditReminderTool,
    DeleteReminderTool
)

__all__ = [
    # Calendar Event Tools (4)
    'CreateCalendarEventTool',
    'ListCalendarEventsTool',
    'UpdateCalendarEventTool',
    'DeleteCalendarEventTool',
    # Reminder Tools (4)
    'CreateReminderTool',
    'ListRemindersTool',
    'EditReminderTool',
    'DeleteReminderTool',
]
