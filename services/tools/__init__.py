"""
VOS Tools Module

Centralized imports for all VOS agent tools organized by category.
Makes it easy for agents to import the tools they need.
"""

from .weather import WEATHER_TOOLS
from .standard import STANDARD_TOOLS, AGENT_TOOLS
from .search import SEARCH_TOOLS
from .browser import BROWSER_TOOLS
from .call import CALL_TOOLS

# Calculator tools are optional - only import if numexpr is available
try:
    from .calculator import (
        BasicCalculationTool,
        AdvancedMathTool,
        StatisticsTool,
        RandomNumberTool,
        NumberTheoryTool,
        LinearAlgebraTool,
        UnitConversionTool
    )
    CALCULATOR_AVAILABLE = True
except ImportError:
    # Calculator tools not available (missing numexpr dependency)
    CALCULATOR_AVAILABLE = False
    BasicCalculationTool = None
    AdvancedMathTool = None
    StatisticsTool = None
    RandomNumberTool = None
    NumberTheoryTool = None
    LinearAlgebraTool = None
    UnitConversionTool = None

# Note: MEMORY_TOOLS not imported at package level to avoid circular imports
# Individual memory tools can still be imported directly

# Export individual tool classes for direct import
from .weather import (
    GetWeatherTool,
    GetForecastTool,
    GetWeatherByCoordinatesTool,
    GetUVIndexTool,
    GetAirQualityTool
)

from .standard import (
    SendUserMessageTool,
    SendAgentMessageTool,
    CreateTaskTool,
    UpdateTaskTool,
    GetTasksTool,
    AssignToTaskTool,
    UnassignFromTaskTool,
    SleepTool,
    ShutdownTool,
    ReadSystemPromptTool,
    EditSystemPromptTool,
    MESSAGING_TOOLS,
    TASK_TOOLS,
    TIMER_TOOLS,
    SYSTEM_PROMPT_TOOLS
)

# Import individual memory tools directly from memory_tools to avoid circular import
from .memory.memory_tools import (
    CreateMemoryTool,
    SearchMemoryTool,
    GetMemoryTool,
    UpdateMemoryTool,
    DeleteMemoryTool,
    MEMORY_TOOLS
)

# Import search tools
from .search import (
    FirecrawlScrapeTool,
    FirecrawlCrawlTool,
    FirecrawlMapTool,
    FirecrawlSearchTool,
    FirecrawlExtractTool,
    DDGTextSearchTool,
    DDGNewsSearchTool,
    DDGBooksSearchTool,
    FIRECRAWL_TOOLS,
    DUCKDUCKGO_TOOLS
)

# Import calendar tools (timers/alarms are now reminders, subscriptions removed)
from .calendar import (
    CreateCalendarEventTool,
    ListCalendarEventsTool,
    UpdateCalendarEventTool,
    DeleteCalendarEventTool,
    CreateReminderTool,
    ListRemindersTool,
    EditReminderTool,
    DeleteReminderTool
)

# Import notes tools
from .notes import (
    CreateNoteTool,
    ListNotesTool,
    GetNoteTool,
    UpdateNoteTool,
    DeleteNoteTool,
    SearchNotesTool,
    ArchiveNoteTool,
    PinNoteTool
)

# Import browser tools
from .browser import (
    BrowserUseTool,
    BrowserNavigateTool
)

# Import document tools
from .documents import (
    CreateDocumentTool,
    ReadDocumentTool,
    ListDocumentsTool,
    DeleteDocumentTool
)

# Import attachment tools (for agent image handling)
from .attachments import (
    DownloadImageTool,
    CreateAttachmentTool,
    GetAttachmentTool,
    ViewImageTool,
    ATTACHMENT_TOOLS
)

# Import call tools (for voice call interactions)
from .call import (
    SpeakTool,
    AnswerCallTool,
    HangUpTool,
    TransferCallTool,
    RecallPhoneTool,
    CallUserTool,
    CallPhoneTool,
    SendSMSTool,
    CALL_TOOLS
)

# Calendar tool collections for easy agent import
CALENDAR_EVENT_TOOLS = [
    CreateCalendarEventTool,
    ListCalendarEventsTool,
    UpdateCalendarEventTool,
    DeleteCalendarEventTool
]

CALENDAR_REMINDER_TOOLS = [
    CreateReminderTool,
    ListRemindersTool,
    EditReminderTool,
    DeleteReminderTool
]

CALENDAR_TOOLS = CALENDAR_EVENT_TOOLS + CALENDAR_REMINDER_TOOLS

# Notes tool collection for easy agent import
NOTES_TOOLS = [
    CreateNoteTool,
    ListNotesTool,
    GetNoteTool,
    UpdateNoteTool,
    DeleteNoteTool,
    SearchNotesTool,
    ArchiveNoteTool,
    PinNoteTool
]

# Calculator tool collection for easy agent import
if CALCULATOR_AVAILABLE:
    CALCULATOR_TOOLS = [
        BasicCalculationTool,
        AdvancedMathTool,
        StatisticsTool,
        RandomNumberTool,
        NumberTheoryTool,
        LinearAlgebraTool,
        UnitConversionTool
    ]
else:
    CALCULATOR_TOOLS = []

# Document tool collection for easy agent import
DOCUMENT_TOOLS = [
    CreateDocumentTool,
    ReadDocumentTool,
    ListDocumentsTool,
    DeleteDocumentTool
]

__all__ = [
    # Tool collections
    'WEATHER_TOOLS',
    'STANDARD_TOOLS',
    'AGENT_TOOLS',
    'MESSAGING_TOOLS',
    'TASK_TOOLS',
    'TIMER_TOOLS',
    'MEMORY_TOOLS',
    'SEARCH_TOOLS',
    'FIRECRAWL_TOOLS',
    'DUCKDUCKGO_TOOLS',
    'CALENDAR_TOOLS',
    'CALENDAR_EVENT_TOOLS',
    'CALENDAR_REMINDER_TOOLS',
    'NOTES_TOOLS',
    'CALCULATOR_TOOLS',
    'BROWSER_TOOLS',
    'DOCUMENT_TOOLS',

    # Individual weather tools
    'GetWeatherTool',
    'GetForecastTool',
    'GetWeatherByCoordinatesTool',
    'GetUVIndexTool',
    'GetAirQualityTool',

    # Individual standard tools
    'SendUserMessageTool',
    'SendAgentMessageTool',
    'CreateTaskTool',
    'UpdateTaskTool',
    'GetTasksTool',
    'AssignToTaskTool',
    'UnassignFromTaskTool',
    'SleepTool',
    'ShutdownTool',
    'ReadSystemPromptTool',
    'EditSystemPromptTool',
    'SYSTEM_PROMPT_TOOLS',

    # Individual memory tools
    'CreateMemoryTool',
    'SearchMemoryTool',
    'GetMemoryTool',
    'UpdateMemoryTool',
    'DeleteMemoryTool',

    # Individual search tools
    'FirecrawlScrapeTool',
    'FirecrawlCrawlTool',
    'FirecrawlMapTool',
    'FirecrawlSearchTool',
    'FirecrawlExtractTool',
    'DDGTextSearchTool',
    'DDGNewsSearchTool',
    'DDGBooksSearchTool',

    # Individual calendar tools (8 total)
    'CreateCalendarEventTool',
    'ListCalendarEventsTool',
    'UpdateCalendarEventTool',
    'DeleteCalendarEventTool',
    'CreateReminderTool',
    'ListRemindersTool',
    'EditReminderTool',
    'DeleteReminderTool',

    # Individual notes tools (8 total)
    'CreateNoteTool',
    'ListNotesTool',
    'GetNoteTool',
    'UpdateNoteTool',
    'DeleteNoteTool',
    'SearchNotesTool',
    'ArchiveNoteTool',
    'PinNoteTool',

    # Individual calculator tools (7 total)
    'BasicCalculationTool',
    'AdvancedMathTool',
    'StatisticsTool',
    'RandomNumberTool',
    'NumberTheoryTool',
    'LinearAlgebraTool',
    'UnitConversionTool',

    # Individual browser tools (2 total)
    'BrowserUseTool',
    'BrowserNavigateTool',

    # Individual document tools (4 total)
    'CreateDocumentTool',
    'ReadDocumentTool',
    'ListDocumentsTool',
    'DeleteDocumentTool',

    # Attachment tools collection
    'ATTACHMENT_TOOLS',

    # Individual attachment tools (4 total)
    'DownloadImageTool',
    'CreateAttachmentTool',
    'GetAttachmentTool',
    'ViewImageTool',

    # Call tools collection
    'CALL_TOOLS',

    # Individual call tools (8 total)
    'SpeakTool',
    'AnswerCallTool',
    'HangUpTool',
    'TransferCallTool',
    'RecallPhoneTool',
    'CallUserTool',
    'CallPhoneTool',
    'SendSMSTool',
]