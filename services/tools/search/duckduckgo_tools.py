"""
DuckDuckGo search tools for the Search Agent.

Provides web search, news search, and book search capabilities
using the ddgs (DuckDuckGo Search) Python library.
"""

import logging
from typing import Dict, Any, Optional

from vos_sdk import BaseTool

logger = logging.getLogger(__name__)


class DDGTextSearchTool(BaseTool):
    """
    Searches the web using DuckDuckGo.

    Returns text-based search results with titles, snippets, and URLs.
    Fast and privacy-focused alternative to other search engines.
    """

    def __init__(self):
        super().__init__(
            name="ddg_text_search",
            description="Searches the web using DuckDuckGo and returns text results"
        )

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate that query is provided."""
        if "query" not in arguments:
            return False, "Missing required argument: 'query'"

        if not isinstance(arguments["query"], str):
            return False, f"'query' must be a string, got {type(arguments['query']).__name__}"

        if not arguments["query"].strip():
            return False, "'query' cannot be empty"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "ddg_text_search",
            "description": "Searches the web using DuckDuckGo (text results)",
            "parameters": [
                {
                    "name": "query",
                    "type": "str",
                    "description": "The search query",
                    "required": True
                },
                {
                    "name": "max_results",
                    "type": "int",
                    "description": "Maximum number of results to return (default: 10)",
                    "required": False
                },
                {
                    "name": "region",
                    "type": "str",
                    "description": "Region for localized results (e.g., 'us-en', 'uk-en', 'fr-fr')",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Search the web using DuckDuckGo.

        Args:
            arguments: Must contain 'query', optionally 'max_results' and 'region'
        """
        query = arguments["query"]
        max_results = arguments.get("max_results", 10)
        region = arguments.get("region", "wt-wt")  # Worldwide by default

        try:
            # Import here to avoid loading if not needed
            from ddgs import DDGS

            with DDGS() as ddgs:
                # Perform text search
                results = list(ddgs.text(
                    query,  # First positional argument
                    region=region,
                    max_results=max_results
                ))

            # Format results for LLM
            formatted_results = []
            for result in results:
                formatted_results.append({
                    "title": result.get("title"),
                    "url": result.get("href"),
                    "snippet": result.get("body"),
                    "source": result.get("source", "DuckDuckGo")
                })

            search_result = {
                "query": query,
                "total_results": len(formatted_results),
                "region": region,
                "results": formatted_results
            }

            self.send_result_notification(
                status="SUCCESS",
                result=search_result
            )

            logger.info(f"✅ DuckDuckGo text search for '{query}': {len(formatted_results)} results")

        except ImportError:
            self.send_result_notification(
                status="FAILURE",
                error_message="ddgs library not installed. Run: pip install ddgs"
            )
        except Exception as e:
            logger.error(f"Unexpected error in ddg_text_search: {e}", exc_info=True)
            self.send_result_notification(
                status="FAILURE",
                error_message=f"DuckDuckGo search error: {str(e)}"
            )


class DDGNewsSearchTool(BaseTool):
    """
    Searches for news using DuckDuckGo.

    Returns news articles with titles, snippets, URLs, dates, and sources.
    Supports time-based filtering for recent news.
    """

    def __init__(self):
        super().__init__(
            name="ddg_news_search",
            description="Searches for news articles using DuckDuckGo"
        )

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate that query is provided."""
        if "query" not in arguments:
            return False, "Missing required argument: 'query'"

        if not isinstance(arguments["query"], str):
            return False, f"'query' must be a string, got {type(arguments['query']).__name__}"

        if not arguments["query"].strip():
            return False, "'query' cannot be empty"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "ddg_news_search",
            "description": "Searches for news articles using DuckDuckGo",
            "parameters": [
                {
                    "name": "query",
                    "type": "str",
                    "description": "The search query",
                    "required": True
                },
                {
                    "name": "max_results",
                    "type": "int",
                    "description": "Maximum number of results to return (default: 10)",
                    "required": False
                },
                {
                    "name": "timelimit",
                    "type": "str",
                    "description": "Time filter: 'd' (day), 'w' (week), 'm' (month)",
                    "required": False
                },
                {
                    "name": "region",
                    "type": "str",
                    "description": "Region for localized news (e.g., 'us-en', 'uk-en')",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Search for news using DuckDuckGo.

        Args:
            arguments: Must contain 'query', optionally 'max_results', 'timelimit', 'region'
        """
        query = arguments["query"]
        max_results = arguments.get("max_results", 10)
        timelimit = arguments.get("timelimit")  # None, 'd', 'w', or 'm'
        region = arguments.get("region", "wt-wt")

        try:
            # Import here to avoid loading if not needed
            from ddgs import DDGS

            with DDGS() as ddgs:
                # Perform news search
                kwargs = {
                    "region": region,
                    "max_results": max_results
                }

                # Add timelimit if provided
                if timelimit:
                    kwargs["timelimit"] = timelimit

                results = list(ddgs.news(query, **kwargs))

            # Format results for LLM
            formatted_results = []
            for result in results:
                # Convert date to ISO format string if it's a datetime object
                date_value = result.get("date")
                if hasattr(date_value, 'isoformat'):
                    date_value = date_value.isoformat()

                formatted_results.append({
                    "title": result.get("title"),
                    "url": result.get("url"),
                    "snippet": result.get("body"),
                    "date": date_value,
                    "source": result.get("source"),
                    "image": result.get("image")
                })

            search_result = {
                "query": query,
                "total_results": len(formatted_results),
                "region": region,
                "timelimit": timelimit,
                "results": formatted_results
            }

            self.send_result_notification(
                status="SUCCESS",
                result=search_result
            )

            logger.info(f"✅ DuckDuckGo news search for '{query}': {len(formatted_results)} results")

        except ImportError:
            self.send_result_notification(
                status="FAILURE",
                error_message="ddgs library not installed. Run: pip install ddgs"
            )
        except Exception as e:
            logger.error(f"Unexpected error in ddg_news_search: {e}", exc_info=True)
            self.send_result_notification(
                status="FAILURE",
                error_message=f"DuckDuckGo news search error: {str(e)}"
            )


class DDGBooksSearchTool(BaseTool):
    """
    Searches for books using DuckDuckGo.

    Returns book results with titles, descriptions, URLs, and metadata.
    Useful for finding book information, reviews, and purchase links.
    """

    def __init__(self):
        super().__init__(
            name="ddg_books_search",
            description="Searches for books using DuckDuckGo"
        )

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate that query is provided."""
        if "query" not in arguments:
            return False, "Missing required argument: 'query'"

        if not isinstance(arguments["query"], str):
            return False, f"'query' must be a string, got {type(arguments['query']).__name__}"

        if not arguments["query"].strip():
            return False, "'query' cannot be empty"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "ddg_books_search",
            "description": "Searches for books using DuckDuckGo",
            "parameters": [
                {
                    "name": "query",
                    "type": "str",
                    "description": "The book search query (title, author, ISBN, etc.)",
                    "required": True
                },
                {
                    "name": "max_results",
                    "type": "int",
                    "description": "Maximum number of results to return (default: 10)",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Search for books using DuckDuckGo.

        Args:
            arguments: Must contain 'query', optionally 'max_results'
        """
        query = arguments["query"]
        max_results = arguments.get("max_results", 10)

        try:
            # Import here to avoid loading if not needed
            from ddgs import DDGS

            with DDGS() as ddgs:
                # Perform books search
                results = list(ddgs.books(
                    query,  # First positional argument
                    max_results=max_results
                ))

            # Format results for LLM (ddgs books returns different structure)
            formatted_results = []
            for result in results:
                formatted_results.append({
                    "title": result.get("title"),
                    "url": result.get("url"),
                    "description": result.get("description"),
                    "author": result.get("author"),
                    "image": result.get("image")
                })

            search_result = {
                "query": query,
                "total_results": len(formatted_results),
                "results": formatted_results
            }

            self.send_result_notification(
                status="SUCCESS",
                result=search_result
            )

            logger.info(f"✅ DuckDuckGo books search for '{query}': {len(formatted_results)} results")

        except ImportError:
            self.send_result_notification(
                status="FAILURE",
                error_message="ddgs library not installed. Run: pip install ddgs"
            )
        except Exception as e:
            logger.error(f"Unexpected error in ddg_books_search: {e}", exc_info=True)
            self.send_result_notification(
                status="FAILURE",
                error_message=f"DuckDuckGo books search error: {str(e)}"
            )


# Export all DuckDuckGo tools
DUCKDUCKGO_TOOLS = [
    DDGTextSearchTool,
    DDGNewsSearchTool,
    DDGBooksSearchTool
]
