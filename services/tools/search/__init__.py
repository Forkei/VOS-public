"""
Search Tools Module

Provides comprehensive web search and scraping capabilities through:
- Firecrawl: Advanced scraping, crawling, mapping, searching, and extraction
- DuckDuckGo: Fast, privacy-focused web, news, and books search
"""

from .firecrawl_tools import (
    FirecrawlScrapeTool,
    FirecrawlCrawlTool,
    FirecrawlMapTool,
    FirecrawlSearchTool,
    FirecrawlExtractTool,
    FIRECRAWL_TOOLS
)

from .duckduckgo_tools import (
    DDGTextSearchTool,
    DDGNewsSearchTool,
    DDGBooksSearchTool,
    DUCKDUCKGO_TOOLS
)

# Combined list of all search tools
SEARCH_TOOLS = FIRECRAWL_TOOLS + DUCKDUCKGO_TOOLS

__all__ = [
    # Firecrawl tools
    'FirecrawlScrapeTool',
    'FirecrawlCrawlTool',
    'FirecrawlMapTool',
    'FirecrawlSearchTool',
    'FirecrawlExtractTool',
    'FIRECRAWL_TOOLS',

    # DuckDuckGo tools
    'DDGTextSearchTool',
    'DDGNewsSearchTool',
    'DDGBooksSearchTool',
    'DUCKDUCKGO_TOOLS',

    # Combined
    'SEARCH_TOOLS',
]
