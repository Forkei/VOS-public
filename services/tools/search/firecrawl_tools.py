"""
Firecrawl-specific tools for the Search Agent.

Provides web scraping, crawling, mapping, searching, and extraction capabilities
using the Firecrawl API v2.
Requires FIRECRAWL_API_KEY environment variable.
"""

import os
import logging
import requests
from typing import Dict, Any, Optional, List

from vos_sdk import BaseTool

logger = logging.getLogger(__name__)


def deduplicate_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deduplicate metadata by removing og: prefixed keys when they have the same value
    as their non-prefixed counterparts.

    Example: If og:description and description have the same value, keep only description.
    """
    result = {}
    seen_values = {}

    # First pass: collect non-og keys and their values
    for key, value in metadata.items():
        if not key.startswith('og'):
            seen_values[key] = str(value)
            result[key] = value

    # Second pass: add og: keys only if they're unique
    for key, value in metadata.items():
        if key.startswith('og'):
            # Try to find corresponding non-og key
            # og:description -> description, ogDescription -> description
            clean_key = key.replace('og:', '').replace('og', '', 1).lower()

            # Check if we already have this value
            value_str = str(value)
            is_duplicate = False

            for seen_key, seen_val in seen_values.items():
                if seen_key.lower() == clean_key or seen_val == value_str:
                    is_duplicate = True
                    break

            if not is_duplicate:
                result[key] = value

    return result


class FirecrawlScrapeTool(BaseTool):
    """
    Scrapes a single webpage using Firecrawl API.

    Supports multiple output formats: summary, markdown, html, rawHtml, links, images.
    Returns clean, formatted content optimized for LLM consumption.
    """

    def __init__(self):
        super().__init__(
            name="firecrawl_scrape",
            description="Scrapes content from a single webpage with format selection (summary, markdown, html, links, images)"
        )
        self.api_key = os.environ.get("FIRECRAWL_API_KEY")
        self.base_url = "https://api.firecrawl.dev/v2/scrape"

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate that url is provided and API key is configured."""
        if "url" not in arguments:
            return False, "Missing required argument: 'url'"

        if not isinstance(arguments["url"], str):
            return False, f"'url' must be a string, got {type(arguments['url']).__name__}"

        if not arguments["url"].strip():
            return False, "'url' cannot be empty"

        # Validate format if provided
        if "format" in arguments:
            valid_formats = ["summary", "markdown", "html", "rawHtml", "links", "images"]
            if arguments["format"] not in valid_formats:
                return False, f"'format' must be one of {valid_formats}, got '{arguments['format']}'"

        if not self.api_key:
            return False, "FIRECRAWL_API_KEY environment variable not set"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "firecrawl_scrape",
            "description": "Scrapes clean content from a webpage with format selection",
            "parameters": [
                {
                    "name": "url",
                    "type": "str",
                    "description": "The URL to scrape",
                    "required": True
                },
                {
                    "name": "format",
                    "type": "str",
                    "description": "Output format (default: 'summary'). Options: 'summary' (AI summary), 'markdown' (clean markdown), 'html' (cleaned HTML), 'rawHtml' (original HTML), 'links' (all links), 'images' (all images)",
                    "required": False
                },
                {
                    "name": "onlyMainContent",
                    "type": "bool",
                    "description": "Extract only main content, excluding headers/navs/footers (default: false)",
                    "required": False
                },
                {
                    "name": "timeout",
                    "type": "int",
                    "description": "Request timeout in milliseconds (default: 30000)",
                    "required": False
                },
                {
                    "name": "mobile",
                    "type": "bool",
                    "description": "Emulate mobile device (default: false). Useful for bypassing paywalls that differ on mobile",
                    "required": False
                },
                {
                    "name": "headers",
                    "type": "dict",
                    "description": "Custom HTTP headers (e.g., cookies, user-agent). Useful for authenticated access or bypassing restrictions",
                    "required": False
                },
                {
                    "name": "skipTlsVerification",
                    "type": "bool",
                    "description": "Skip TLS certificate verification (default: false)",
                    "required": False
                },
                {
                    "name": "waitFor",
                    "type": "int",
                    "description": "Delay in milliseconds before fetching content (default: 0). Useful for dynamic content loading",
                    "required": False
                },
                {
                    "name": "proxy",
                    "type": "str",
                    "description": "Proxy mode: 'basic' (standard), 'stealth' (advanced anti-bot bypass), 'auto' (retry with stealth if basic fails). Default: 'auto'",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Scrape content from the specified URL.

        Args:
            arguments: Must contain 'url', optionally 'format', 'onlyMainContent', 'timeout'
        """
        url = arguments["url"]
        format_type = arguments.get("format", "summary")

        # Build formats array for API
        payload = {
            "url": url,
            "formats": [format_type],
            "onlyMainContent": arguments.get("onlyMainContent", False),
            "location": {
                "country": "US",
                "languages": ["en-US"]
            },
            "removeBase64Images": True,
            "blockAds": True,
            "timeout": arguments.get("timeout", 30000),
            "proxy": arguments.get("proxy", "auto"),
            "mobile": arguments.get("mobile", False),
            "skipTlsVerification": arguments.get("skipTlsVerification", False),
            "waitFor": arguments.get("waitFor", 0)
        }

        # Add custom headers if provided
        if "headers" in arguments:
            payload["headers"] = arguments["headers"]

        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }

            response = requests.post(
                self.base_url,
                json=payload,
                headers=headers,
                timeout=60  # HTTP request timeout
            )

            if response.status_code == 404:
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"URL '{url}' not found (404)"
                )
                return

            if response.status_code == 403:
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Access forbidden to '{url}' (403)"
                )
                return

            response.raise_for_status()
            data = response.json()

            # Check if scrape was successful
            if not data.get("success", False):
                error_msg = data.get("error", "Unknown error")
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Firecrawl scrape failed: {error_msg}"
                )
                return

            # Extract content based on format
            scrape_data = data.get("data", {})
            raw_metadata = scrape_data.get("metadata", {})

            # Deduplicate metadata
            metadata = deduplicate_metadata(raw_metadata)

            # Keep only essential metadata for LLM
            clean_metadata = {
                "title": metadata.get("title"),
                "description": metadata.get("description"),
                "keywords": metadata.get("keywords"),
                "language": metadata.get("language"),
                "sourceURL": metadata.get("sourceURL"),
                "statusCode": metadata.get("statusCode", 200)
            }
            # Remove None values
            clean_metadata = {k: v for k, v in clean_metadata.items() if v is not None}

            # Build result based on format
            result = {
                "url": url,
                "format": format_type,
                "metadata": clean_metadata
            }

            # Add content field based on format type
            if format_type == "summary":
                result["content"] = scrape_data.get("summary")
            elif format_type == "markdown":
                result["content"] = scrape_data.get("markdown")
            elif format_type == "html":
                result["content"] = scrape_data.get("html")
            elif format_type == "rawHtml":
                result["content"] = scrape_data.get("rawHtml")
            elif format_type == "links":
                result["links"] = scrape_data.get("links", [])
            elif format_type == "images":
                result["images"] = scrape_data.get("images", [])

            self.send_result_notification(
                status="SUCCESS",
                result=result
            )

            logger.info(f"✅ Successfully scraped {url} (format: {format_type})")

        except requests.exceptions.Timeout:
            self.send_result_notification(
                status="FAILURE",
                error_message="Firecrawl API request timed out"
            )
        except requests.exceptions.RequestException as e:
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Firecrawl API error: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Unexpected error in firecrawl_scrape: {e}", exc_info=True)
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Unexpected error: {str(e)}"
            )


class FirecrawlCrawlTool(BaseTool):
    """
    Crawls an entire website using Firecrawl API.

    This is an async operation that triggers webhooks as it progresses.
    The tool returns immediately with a job_id, and results are sent
    via webhook to the search agent.
    """

    def __init__(self):
        super().__init__(
            name="firecrawl_crawl",
            description="Crawls multiple pages on a website (async with webhook notifications)"
        )
        self.api_key = os.environ.get("FIRECRAWL_API_KEY")
        self.base_url = "https://api.firecrawl.dev/v2/crawl"
        # Get webhook base URL from environment
        self.webhook_base_url = os.environ.get(
            "WEBHOOK_BASE_URL",
            os.environ.get("API_GATEWAY_URL", "http://api_gateway:8000")
        )

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate that url is provided and API key is configured."""
        if "url" not in arguments:
            return False, "Missing required argument: 'url'"

        if not isinstance(arguments["url"], str):
            return False, f"'url' must be a string, got {type(arguments['url']).__name__}"

        if not arguments["url"].strip():
            return False, "'url' cannot be empty"

        if not self.api_key:
            return False, "FIRECRAWL_API_KEY environment variable not set"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "firecrawl_crawl",
            "description": "Crawls multiple pages on a website (async with webhook updates)",
            "parameters": [
                {
                    "name": "url",
                    "type": "str",
                    "description": "The starting URL to crawl",
                    "required": True
                },
                {
                    "name": "limit",
                    "type": "int",
                    "description": "Maximum number of pages to crawl (default: 10)",
                    "required": False
                },
                {
                    "name": "deluxe",
                    "type": "bool",
                    "description": "Enable deluxe mode: crawl entire domain and subdomains (default: false)",
                    "required": False
                },
                {
                    "name": "format",
                    "type": "str",
                    "description": "Format for scraped pages (default: 'summary'). Options: 'summary', 'markdown', 'html'",
                    "required": False
                },
                {
                    "name": "includePaths",
                    "type": "list",
                    "description": "URL patterns to include (e.g., ['/blog/*'])",
                    "required": False
                },
                {
                    "name": "excludePaths",
                    "type": "list",
                    "description": "URL patterns to exclude (e.g., ['/admin/*'])",
                    "required": False
                },
                {
                    "name": "mobile",
                    "type": "bool",
                    "description": "Emulate mobile device (default: false). Useful for bypassing paywalls that differ on mobile",
                    "required": False
                },
                {
                    "name": "headers",
                    "type": "dict",
                    "description": "Custom HTTP headers (e.g., cookies, user-agent). Useful for authenticated access or bypassing restrictions",
                    "required": False
                },
                {
                    "name": "skipTlsVerification",
                    "type": "bool",
                    "description": "Skip TLS certificate verification (default: false)",
                    "required": False
                },
                {
                    "name": "waitFor",
                    "type": "int",
                    "description": "Delay in milliseconds before fetching content (default: 0). Useful for dynamic content loading",
                    "required": False
                },
                {
                    "name": "proxy",
                    "type": "str",
                    "description": "Proxy mode: 'basic' (standard), 'stealth' (advanced anti-bot bypass), 'auto' (retry with stealth if basic fails). Default: 'auto'",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Start a crawl job.

        This returns immediately with a job_id. Firecrawl will send webhook
        notifications as the crawl progresses.

        Args:
            arguments: Must contain 'url', optionally other parameters
        """
        url = arguments["url"]
        deluxe = arguments.get("deluxe", False)
        format_type = arguments.get("format", "summary")

        # Build payload with defaults (v2 API compliant)
        payload = {
            "url": url,
            "limit": arguments.get("limit", 10),
            "webhook": {
                "url": f"{self.webhook_base_url}/api/v1/webhooks/firecrawl",
                "events": ["started", "page", "completed", "failed"]  # v2 event names
            },
            "scrapeOptions": {
                "formats": [format_type],
                "onlyMainContent": True,
                "removeBase64Images": True,
                "blockAds": True,
                "proxy": arguments.get("proxy", "auto"),
                "mobile": arguments.get("mobile", False),
                "skipTlsVerification": arguments.get("skipTlsVerification", False),
                "waitFor": arguments.get("waitFor", 0)
            }
        }

        # Add custom headers if provided
        if "headers" in arguments:
            payload["scrapeOptions"]["headers"] = arguments["headers"]

        # Apply deluxe mode settings
        if deluxe:
            payload["crawlEntireDomain"] = True
            payload["allowSubdomains"] = True

        # Add optional parameters
        if "includePaths" in arguments:
            payload["includePaths"] = arguments["includePaths"]

        if "excludePaths" in arguments:
            payload["excludePaths"] = arguments["excludePaths"]

        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }

            logger.debug(f"Firecrawl crawl request payload: {payload}")

            response = requests.post(
                self.base_url,
                json=payload,
                headers=headers,
                timeout=30
            )

            # Parse JSON response safely
            data = {}
            try:
                data = response.json()
            except ValueError:
                # If JSON parsing fails, we'll handle it based on status code
                pass

            # Handle HTTP errors with detailed Firecrawl error messages
            if response.status_code == 400:
                error_msg = data.get("error", "Bad Request - Invalid crawl parameters")
                # Provide helpful context about why crawls typically fail
                context_msg = "Common reasons: (1) Site has limited internal links and isn't suitable for crawling - use firecrawl_scrape instead for single pages, (2) Invalid URL patterns or excludePaths, (3) Site blocks crawlers"
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Firecrawl Crawl failed: {error_msg}. {context_msg}"
                )
                logger.warning(f"Firecrawl crawl rejected for {url}: {error_msg}")
                return

            response.raise_for_status()

            # Extract job information
            if data.get("success"):
                crawl_result = {
                    "job_id": data.get("id"),
                    "url": url,
                    "status": "started",
                    "webhook_url": payload["webhook"]["url"],
                    "limit": payload["limit"],
                    "format": format_type,
                    "message": "Crawl job started. Results will be sent via webhook as pages are crawled."
                }

                self.send_result_notification(
                    status="SUCCESS",
                    result=crawl_result
                )

                logger.info(f"✅ Started Firecrawl crawl job: {data.get('id')} for {url}")
            else:
                error_msg = data.get("error", "Unknown error")
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Failed to start crawl: {error_msg}"
                )

        except requests.exceptions.Timeout:
            self.send_result_notification(
                status="FAILURE",
                error_message="Firecrawl API request timed out"
            )
        except requests.exceptions.RequestException as e:
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Firecrawl API error: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Unexpected error in firecrawl_crawl: {e}", exc_info=True)
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Unexpected error: {str(e)}"
            )


class FirecrawlMapTool(BaseTool):
    """
    Maps a website's structure using Firecrawl API.

    Discovers and returns all URLs on a website without scraping content.
    Useful for understanding site structure before crawling.
    """

    def __init__(self):
        super().__init__(
            name="firecrawl_map",
            description="Maps a website's structure and discovers all URLs"
        )
        self.api_key = os.environ.get("FIRECRAWL_API_KEY")
        self.base_url = "https://api.firecrawl.dev/v2/map"

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate that url is provided and API key is configured."""
        if "url" not in arguments:
            return False, "Missing required argument: 'url'"

        if not isinstance(arguments["url"], str):
            return False, f"'url' must be a string, got {type(arguments['url']).__name__}"

        if not arguments["url"].strip():
            return False, "'url' cannot be empty"

        if not self.api_key:
            return False, "FIRECRAWL_API_KEY environment variable not set"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "firecrawl_map",
            "description": "Maps a website's structure and discovers all URLs without scraping content",
            "parameters": [
                {
                    "name": "url",
                    "type": "str",
                    "description": "The website URL to map",
                    "required": True
                },
                {
                    "name": "search",
                    "type": "str",
                    "description": "Search query to filter discovered URLs",
                    "required": False
                },
                {
                    "name": "limit",
                    "type": "int",
                    "description": "Maximum number of URLs to return (default: 5000)",
                    "required": False
                },
                {
                    "name": "includeSubdomains",
                    "type": "bool",
                    "description": "Include subdomains in mapping (default: false)",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Map the website structure.

        Args:
            arguments: Must contain 'url', optionally other parameters
        """
        url = arguments["url"]

        # Build payload with defaults
        payload = {
            "url": url,
            "limit": arguments.get("limit", 5000),
            "includeSubdomains": arguments.get("includeSubdomains", False),
            "ignoreQueryParameters": True,
            "location": {
                "country": "US",
                "languages": ["en-US"]
            }
        }

        # Add optional search parameter
        if "search" in arguments:
            payload["search"] = arguments["search"]

        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }

            response = requests.post(
                self.base_url,
                json=payload,
                headers=headers,
                timeout=60
            )

            response.raise_for_status()
            data = response.json()

            if data.get("success"):
                links = data.get("links", [])

                map_result = {
                    "url": url,
                    "total_urls": len(links),
                    "links": links  # Array of {url, title, description}
                }

                self.send_result_notification(
                    status="SUCCESS",
                    result=map_result
                )

                logger.info(f"✅ Mapped {url}: found {map_result['total_urls']} URLs")
            else:
                error_msg = data.get("error", "Unknown error")
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Failed to map website: {error_msg}"
                )

        except requests.exceptions.Timeout:
            self.send_result_notification(
                status="FAILURE",
                error_message="Firecrawl API request timed out"
            )
        except requests.exceptions.RequestException as e:
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Firecrawl API error: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Unexpected error in firecrawl_map: {e}", exc_info=True)
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Unexpected error: {str(e)}"
            )


class FirecrawlSearchTool(BaseTool):
    """
    Searches the web and scrapes results using Firecrawl API.

    Combines search with automatic scraping of results, returning
    clean content from the top search results.
    """

    def __init__(self):
        super().__init__(
            name="firecrawl_search",
            description="Searches the web and automatically scrapes result content"
        )
        self.api_key = os.environ.get("FIRECRAWL_API_KEY")
        self.base_url = "https://api.firecrawl.dev/v2/search"

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate that query is provided and API key is configured."""
        if "query" not in arguments:
            return False, "Missing required argument: 'query'"

        if not isinstance(arguments["query"], str):
            return False, f"'query' must be a string, got {type(arguments['query']).__name__}"

        if not arguments["query"].strip():
            return False, "'query' cannot be empty"

        if not self.api_key:
            return False, "FIRECRAWL_API_KEY environment variable not set"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "firecrawl_search",
            "description": "Searches the web and automatically scrapes content from results",
            "parameters": [
                {
                    "name": "query",
                    "type": "str",
                    "description": "The search query",
                    "required": True
                },
                {
                    "name": "limit",
                    "type": "int",
                    "description": "Number of results to scrape (default: 10)",
                    "required": False
                },
                {
                    "name": "format",
                    "type": "str",
                    "description": "Format for scraped results (default: 'summary'). Options: 'summary', 'markdown', 'html'",
                    "required": False
                },
                {
                    "name": "categories",
                    "type": "list",
                    "description": "Filter by categories: 'github', 'research', 'pdf' (optional)",
                    "required": False
                },
                {
                    "name": "tbs",
                    "type": "str",
                    "description": "Time-based filter (e.g., 'qdr:h' for past hour, 'qdr:d' for past day, 'qdr:w' for past week)",
                    "required": False
                },
                {
                    "name": "location",
                    "type": "str",
                    "description": "Location for localized search results",
                    "required": False
                },
                {
                    "name": "mobile",
                    "type": "bool",
                    "description": "Emulate mobile device (default: false). Useful for bypassing paywalls that differ on mobile",
                    "required": False
                },
                {
                    "name": "headers",
                    "type": "dict",
                    "description": "Custom HTTP headers (e.g., cookies, user-agent). Useful for authenticated access or bypassing restrictions",
                    "required": False
                },
                {
                    "name": "skipTlsVerification",
                    "type": "bool",
                    "description": "Skip TLS certificate verification (default: false)",
                    "required": False
                },
                {
                    "name": "waitFor",
                    "type": "int",
                    "description": "Delay in milliseconds before fetching content (default: 0). Useful for dynamic content loading",
                    "required": False
                },
                {
                    "name": "proxy",
                    "type": "str",
                    "description": "Proxy mode: 'basic' (standard), 'stealth' (advanced anti-bot bypass), 'auto' (retry with stealth if basic fails). Default: 'auto'",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Search the web and scrape results.

        Args:
            arguments: Must contain 'query', optionally other parameters
        """
        query = arguments["query"]
        format_type = arguments.get("format", "summary")

        # Build payload with defaults
        payload = {
            "query": query,
            "limit": arguments.get("limit", 10),
            "sources": [{"type": "web"}],
            "country": "US",
            "timeout": 60000,
            "scrapeOptions": {
                "formats": [format_type],
                "onlyMainContent": True,
                "removeBase64Images": True,
                "blockAds": True,
                "proxy": arguments.get("proxy", "auto"),
                "mobile": arguments.get("mobile", False),
                "skipTlsVerification": arguments.get("skipTlsVerification", False),
                "waitFor": arguments.get("waitFor", 0)
            }
        }

        # Add custom headers if provided
        if "headers" in arguments:
            payload["scrapeOptions"]["headers"] = arguments["headers"]

        # Add optional categories
        if "categories" in arguments:
            categories = []
            for cat in arguments["categories"]:
                if cat.lower() in ["github", "research", "pdf"]:
                    categories.append({"type": cat.lower()})
            if categories:
                payload["categories"] = categories

        # Add optional time-based search
        if "tbs" in arguments:
            payload["tbs"] = arguments["tbs"]

        # Add optional location
        if "location" in arguments:
            payload["location"] = arguments["location"]

        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }

            response = requests.post(
                self.base_url,
                json=payload,
                headers=headers,
                timeout=120  # Search + scraping can take longer
            )

            response.raise_for_status()
            data = response.json()

            if data.get("success"):
                web_results = data.get("data", {}).get("web", [])

                # Process results to include only relevant content
                processed_results = []
                for item in web_results:
                    result_item = {
                        "title": item.get("title"),
                        "description": item.get("description"),
                        "url": item.get("url")
                    }

                    # Add content based on format
                    if format_type == "summary" and item.get("markdown"):
                        # For summary, we might not get explicit summary field
                        # Use markdown if available
                        result_item["content"] = item.get("markdown")
                    elif format_type == "markdown":
                        result_item["content"] = item.get("markdown")
                    elif format_type == "html":
                        result_item["content"] = item.get("html")

                    # Add metadata if available
                    if "metadata" in item:
                        result_item["statusCode"] = item["metadata"].get("statusCode")

                    processed_results.append(result_item)

                search_result = {
                    "query": query,
                    "total_results": len(processed_results),
                    "format": format_type,
                    "results": processed_results
                }

                self.send_result_notification(
                    status="SUCCESS",
                    result=search_result
                )

                logger.info(f"✅ Firecrawl search for '{query}': {search_result['total_results']} results")
            else:
                error_msg = data.get("error", "Unknown error")
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Firecrawl search failed: {error_msg}"
                )

        except requests.exceptions.Timeout:
            self.send_result_notification(
                status="FAILURE",
                error_message="Firecrawl API request timed out"
            )
        except requests.exceptions.RequestException as e:
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Firecrawl API error: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Unexpected error in firecrawl_search: {e}", exc_info=True)
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Unexpected error: {str(e)}"
            )


class FirecrawlExtractTool(BaseTool):
    """
    Extracts structured data from websites using Firecrawl API.

    Uses LLM-powered extraction to pull specific data fields from one or more URLs
    based on a natural language prompt or JSON schema.
    """

    def __init__(self):
        super().__init__(
            name="firecrawl_extract",
            description="Extracts structured data from websites using LLM and optional schema"
        )
        self.api_key = os.environ.get("FIRECRAWL_API_KEY")
        self.base_url = "https://api.firecrawl.dev/v2/extract"

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate that urls and prompt are provided."""
        if "urls" not in arguments:
            return False, "Missing required argument: 'urls'"

        if not isinstance(arguments["urls"], list):
            return False, f"'urls' must be a list, got {type(arguments['urls']).__name__}"

        if not arguments["urls"]:
            return False, "'urls' cannot be empty"

        if "prompt" not in arguments:
            return False, "Missing required argument: 'prompt'"

        if not self.api_key:
            return False, "FIRECRAWL_API_KEY environment variable not set"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "firecrawl_extract",
            "description": "Extracts structured data from websites using LLM and optional schema",
            "parameters": [
                {
                    "name": "urls",
                    "type": "list",
                    "description": "List of URLs to extract data from (can use glob patterns)",
                    "required": True
                },
                {
                    "name": "prompt",
                    "type": "str",
                    "description": "Natural language prompt describing what to extract",
                    "required": True
                },
                {
                    "name": "schema",
                    "type": "dict",
                    "description": "Optional JSON schema to structure the extracted data",
                    "required": False
                },
                {
                    "name": "enableWebSearch",
                    "type": "bool",
                    "description": "Use web search to find additional data (default: false)",
                    "required": False
                },
                {
                    "name": "includeSubdomains",
                    "type": "bool",
                    "description": "Include subdomains when extracting (default: true)",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Extract structured data from URLs.

        Args:
            arguments: Must contain 'urls' and 'prompt', optionally 'schema' and other parameters
        """
        urls = arguments["urls"]
        prompt = arguments["prompt"]

        # Build payload
        payload = {
            "urls": urls,
            "prompt": prompt,
            "enableWebSearch": arguments.get("enableWebSearch", False),
            "includeSubdomains": arguments.get("includeSubdomains", True),
            "ignoreSitemap": False,
            "ignoreInvalidURLs": True,
            "scrapeOptions": {
                "formats": ["markdown"],
                "onlyMainContent": True,
                "removeBase64Images": True,
                "blockAds": True
            }
        }

        # Add optional schema
        if "schema" in arguments:
            payload["schema"] = arguments["schema"]

        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }

            response = requests.post(
                self.base_url,
                json=payload,
                headers=headers,
                timeout=30  # Just starting the job - returns quickly
            )

            response.raise_for_status()
            data = response.json()

            if data.get("success"):
                job_id = data.get("id")
                extract_result = {
                    "job_id": job_id,
                    "urls": urls,
                    "prompt": prompt,
                    "invalid_urls": data.get("invalidURLs", []),
                    "message": f"Extraction job started (job_id: {job_id}). Use firecrawl_extract_status with this job_id to check progress and retrieve results."
                }

                self.send_result_notification(
                    status="SUCCESS",
                    result=extract_result
                )

                logger.info(f"✅ Started Firecrawl extract job: {job_id} for {len(urls)} URLs")
            else:
                error_msg = data.get("error", "Unknown error")
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Firecrawl extract failed: {error_msg}"
                )

        except requests.exceptions.Timeout:
            self.send_result_notification(
                status="FAILURE",
                error_message="Firecrawl API request timed out"
            )
        except requests.exceptions.RequestException as e:
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Firecrawl API error: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Unexpected error in firecrawl_extract: {e}", exc_info=True)
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Unexpected error: {str(e)}"
            )


class FirecrawlExtractStatusTool(BaseTool):
    """
    Checks the status of a Firecrawl extraction job and retrieves results.

    Use this tool after starting an extraction job with firecrawl_extract to:
    - Check if the extraction is still processing
    - Retrieve results when completed
    - Get progress information
    """

    def __init__(self):
        super().__init__(
            name="firecrawl_extract_status",
            description="Checks status and retrieves results of a Firecrawl extraction job"
        )
        self.api_key = os.environ.get("FIRECRAWL_API_KEY")
        self.base_url = "https://api.firecrawl.dev/v2/extract"

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate that job_id is provided and API key is configured."""
        if "job_id" not in arguments:
            return False, "Missing required argument: 'job_id'"

        if not isinstance(arguments["job_id"], str):
            return False, f"'job_id' must be a string, got {type(arguments['job_id']).__name__}"

        if not arguments["job_id"].strip():
            return False, "'job_id' cannot be empty"

        if not self.api_key:
            return False, "FIRECRAWL_API_KEY environment variable not set"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "firecrawl_extract_status",
            "description": "Checks the status and retrieves results of a Firecrawl extraction job",
            "parameters": [
                {
                    "name": "job_id",
                    "type": "str",
                    "description": "The job ID returned from firecrawl_extract",
                    "required": True
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Check extraction job status and retrieve results if completed.

        Args:
            arguments: Must contain 'job_id'
        """
        job_id = arguments["job_id"]

        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }

            response = requests.get(
                f"{self.base_url}/{job_id}",
                headers=headers,
                timeout=30
            )

            response.raise_for_status()
            data = response.json()

            if data.get("success"):
                status = data.get("status", "unknown")

                result = {
                    "job_id": job_id,
                    "status": status,
                    "expiresAt": data.get("expiresAt")
                }

                # Add status-specific information
                if status == "completed":
                    result["data"] = data.get("data", {})
                    result["tokensUsed"] = data.get("tokensUsed")
                    result["message"] = "Extraction completed successfully"
                elif status == "processing":
                    result["message"] = "Extraction is still processing. Check again in a few moments."
                elif status == "failed":
                    result["message"] = "Extraction job failed"
                    result["error"] = data.get("error")
                elif status == "cancelled":
                    result["message"] = "Extraction job was cancelled"
                else:
                    result["message"] = f"Unknown status: {status}"

                self.send_result_notification(
                    status="SUCCESS",
                    result=result
                )

                logger.info(f"✅ Extract job {job_id} status: {status}")
            else:
                error_msg = data.get("error", "Unknown error")
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Failed to get extract status: {error_msg}"
                )

        except requests.exceptions.Timeout:
            self.send_result_notification(
                status="FAILURE",
                error_message="Firecrawl API request timed out"
            )
        except requests.exceptions.RequestException as e:
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Firecrawl API error: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Unexpected error in firecrawl_extract_status: {e}", exc_info=True)
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Unexpected error: {str(e)}"
            )


class FirecrawlCrawlStatusTool(BaseTool):
    """
    Checks the status of a Firecrawl crawl job and retrieves results.

    Use this tool after starting a crawl job with firecrawl_crawl to:
    - Check if the crawl is still in progress
    - Retrieve results when completed
    - Get progress information (completed/total pages)
    """

    def __init__(self):
        super().__init__(
            name="firecrawl_crawl_status",
            description="Checks status and retrieves results of a Firecrawl crawl job"
        )
        self.api_key = os.environ.get("FIRECRAWL_API_KEY")
        self.base_url = "https://api.firecrawl.dev/v2/crawl"

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate that job_id is provided and API key is configured."""
        if "job_id" not in arguments:
            return False, "Missing required argument: 'job_id'"

        if not isinstance(arguments["job_id"], str):
            return False, f"'job_id' must be a string, got {type(arguments['job_id']).__name__}"

        if not arguments["job_id"].strip():
            return False, "'job_id' cannot be empty"

        if not self.api_key:
            return False, "FIRECRAWL_API_KEY environment variable not set"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "firecrawl_crawl_status",
            "description": "Checks the status and retrieves results of a Firecrawl crawl job",
            "parameters": [
                {
                    "name": "job_id",
                    "type": "str",
                    "description": "The job ID returned from firecrawl_crawl",
                    "required": True
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Check crawl job status and retrieve results if completed.

        Args:
            arguments: Must contain 'job_id'
        """
        job_id = arguments["job_id"]

        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }

            response = requests.get(
                f"{self.base_url}/{job_id}",
                headers=headers,
                timeout=30
            )

            response.raise_for_status()
            data = response.json()

            if data.get("success"):
                status = data.get("status", "unknown")
                total = data.get("total", 0)
                completed = data.get("completed", 0)
                credits_used = data.get("creditsUsed", 0)
                expires_at = data.get("expiresAt")
                has_next = data.get("next") is not None

                result = {
                    "job_id": job_id,
                    "status": status,
                    "total_pages": total,
                    "completed_pages": completed,
                    "credits_used": credits_used,
                    "expires_at": expires_at
                }

                # Add status-specific information
                if status == "completed":
                    crawl_data = data.get("data", [])
                    result["data"] = crawl_data
                    result["pages_count"] = len(crawl_data)
                    result["has_more_pages"] = has_next
                    result["message"] = f"Crawl completed successfully. Retrieved {len(crawl_data)} pages."
                    if has_next:
                        result["message"] += " More pages available via pagination."
                elif status == "scraping":
                    result["message"] = f"Crawl in progress: {completed}/{total} pages completed. Check again in a few moments."
                elif status == "failed":
                    result["message"] = "Crawl job failed"
                    result["error"] = data.get("error")
                else:
                    result["message"] = f"Crawl status: {status}"

                self.send_result_notification(
                    status="SUCCESS",
                    result=result
                )

                logger.info(f"✅ Crawl job {job_id} status: {status} ({completed}/{total} pages)")
            else:
                error_msg = data.get("error", "Unknown error")
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Failed to get crawl status: {error_msg}"
                )

        except requests.exceptions.Timeout:
            self.send_result_notification(
                status="FAILURE",
                error_message="Firecrawl API request timed out"
            )
        except requests.exceptions.RequestException as e:
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Firecrawl API error: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Unexpected error in firecrawl_crawl_status: {e}", exc_info=True)
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Unexpected error: {str(e)}"
            )


# Export all Firecrawl tools
FIRECRAWL_TOOLS = [
    FirecrawlScrapeTool,
    FirecrawlCrawlTool,
    FirecrawlCrawlStatusTool,
    FirecrawlMapTool,
    FirecrawlSearchTool,
    FirecrawlExtractTool,
    FirecrawlExtractStatusTool
]
