"""
Browser-Use tools for the Browser Agent.

Provides AI-powered browser automation using the browser-use library.
Enables agents to navigate websites, interact with elements, and extract information
using natural language commands with LLM-powered decision making.
"""

import os
import logging
import asyncio
import base64
from typing import Dict, Any, Optional
from datetime import datetime
import json

from vos_sdk import BaseTool

logger = logging.getLogger(__name__)

# Global browser session storage (keyed by session_id)
_browser_sessions = {}


class BrowserUseTool(BaseTool):
    """
    AI-powered browser automation tool using browser-use library.

    Uses an LLM to intelligently navigate and interact with websites based on
    natural language task descriptions. Supports screenshots, element interaction,
    form filling, and data extraction.
    """

    def __init__(self):
        super().__init__(
            name="browser_use",
            description="Automate browser interactions using AI - navigate, click, fill forms, extract data"
        )
        self.gemini_api_key = os.environ.get("GEMINI_API_KEY")

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate that required arguments are provided."""
        if "task" not in arguments:
            return False, "Missing required argument: 'task' (natural language description of what to do)"

        if not isinstance(arguments["task"], str):
            return False, f"'task' must be a string, got {type(arguments['task']).__name__}"

        if not arguments["task"].strip():
            return False, "'task' cannot be empty"

        if not self.gemini_api_key:
            return False, "GEMINI_API_KEY environment variable not set"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "browser_use",
            "description": "Automate browser interactions using AI - navigate websites, click elements, fill forms, extract data using natural language instructions",
            "parameters": [
                {
                    "name": "task",
                    "type": "str",
                    "description": "Natural language description of the browser task (e.g., 'Go to example.com and find the contact email', 'Fill out the login form with username test@example.com')",
                    "required": True
                },
                {
                    "name": "session_id",
                    "type": "str",
                    "description": "Session ID to maintain browser state across multiple tasks (optional, creates new session if not provided)",
                    "required": False
                },
                {
                    "name": "max_steps",
                    "type": "int",
                    "description": "Maximum number of browser actions to take (default: 10, max: 50)",
                    "required": False
                },
                {
                    "name": "capture_screenshot",
                    "type": "bool",
                    "description": "Capture screenshot of final result (default: true)",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Execute browser automation task using browser-use library.

        Args:
            arguments: Must contain 'task', optionally 'session_id', 'max_steps', 'capture_screenshot'
        """
        task = arguments["task"]
        session_id = arguments.get("session_id")
        max_steps = min(arguments.get("max_steps", 10), 50)  # Cap at 50 for safety
        capture_screenshot = arguments.get("capture_screenshot", True)

        try:
            # Import browser-use dependencies
            try:
                from browser_use import Agent
                from langchain_google_genai import ChatGoogleGenerativeAI
                from playwright.async_api import async_playwright
            except ImportError as e:
                self.send_result_notification(
                    status="FAILURE",
                    error_message=f"Browser-use dependencies not installed: {str(e)}. Install with: pip install browser-use langchain-google-genai playwright"
                )
                return

            # Run async task
            result = asyncio.run(self._run_browser_task(
                task=task,
                session_id=session_id,
                max_steps=max_steps,
                capture_screenshot=capture_screenshot
            ))

            if result["status"] == "SUCCESS":
                self.send_result_notification(
                    status="SUCCESS",
                    result=result["data"]
                )
            else:
                self.send_result_notification(
                    status="FAILURE",
                    error_message=result.get("error", "Unknown error occurred")
                )

        except Exception as e:
            logger.error(f"Unexpected error in browser_use tool: {e}", exc_info=True)
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Browser automation error: {str(e)}"
            )

    async def _run_browser_task(
        self,
        task: str,
        session_id: Optional[str],
        max_steps: int,
        capture_screenshot: bool
    ) -> Dict[str, Any]:
        """
        Run browser automation task asynchronously.

        Returns:
            Dict with 'status' and either 'data' or 'error'
        """
        from browser_use import Agent, Browser, BrowserConfig
        from langchain_google_genai import ChatGoogleGenerativeAI

        browser = None
        page = None

        try:
            # Initialize LLM
            llm = ChatGoogleGenerativeAI(
                model="gemini-2.0-flash-exp",
                google_api_key=self.gemini_api_key,
                temperature=0.0  # Deterministic for browser automation
            )

            # Create or reuse browser session
            browser_config = BrowserConfig(
                headless=True,
                disable_security=False,  # Keep security enabled
                extra_chromium_args=[
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu'
                ]
            )

            browser = Browser(config=browser_config)

            # Create agent
            agent = Agent(
                task=task,
                llm=llm,
                browser=browser,
                max_steps=max_steps
            )

            # Run the task
            logger.info(f"🌐 Starting browser task: {task}")
            history = await agent.run()

            # Extract final result
            final_result = history.final_result() if hasattr(history, 'final_result') else str(history)

            # Get current page for screenshot
            page = await browser.get_current_page()

            # Capture screenshot if requested
            screenshot_data = None
            if capture_screenshot and page:
                try:
                    screenshot_bytes = await page.screenshot(type='png', full_page=False)
                    screenshot_data = base64.b64encode(screenshot_bytes).decode('utf-8')
                except Exception as e:
                    logger.warning(f"Failed to capture screenshot: {e}")

            # Get current URL
            current_url = page.url if page else None

            return {
                "status": "SUCCESS",
                "data": {
                    "task": task,
                    "result": final_result,
                    "current_url": current_url,
                    "screenshot": screenshot_data,
                    "steps_taken": len(history.history) if hasattr(history, 'history') else max_steps,
                    "timestamp": datetime.utcnow().isoformat()
                }
            }

        except Exception as e:
            logger.error(f"Browser task execution error: {e}", exc_info=True)
            return {
                "status": "FAILURE",
                "error": str(e)
            }
        finally:
            # Clean up browser
            if browser:
                try:
                    await browser.close()
                except Exception as e:
                    logger.warning(f"Error closing browser: {e}")


class BrowserNavigateTool(BaseTool):
    """
    Simple browser navigation tool for direct URL access.

    Unlike browser_use which uses AI, this tool directly navigates to a URL
    and captures a screenshot without any intelligent interaction.
    """

    def __init__(self):
        super().__init__(
            name="browser_navigate",
            description="Navigate directly to a URL and capture screenshot"
        )

    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate that URL is provided."""
        if "url" not in arguments:
            return False, "Missing required argument: 'url'"

        if not isinstance(arguments["url"], str):
            return False, f"'url' must be a string, got {type(arguments['url']).__name__}"

        if not arguments["url"].strip():
            return False, "'url' cannot be empty"

        # Basic URL validation
        url = arguments["url"].strip()
        if not url.startswith(('http://', 'https://')):
            return False, "'url' must start with http:// or https://"

        return True, None

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "browser_navigate",
            "description": "Navigate directly to a URL and capture screenshot (simple navigation without AI interaction)",
            "parameters": [
                {
                    "name": "url",
                    "type": "str",
                    "description": "URL to navigate to (must start with http:// or https://)",
                    "required": True
                },
                {
                    "name": "wait_ms",
                    "type": "int",
                    "description": "Milliseconds to wait for page to load (default: 3000)",
                    "required": False
                },
                {
                    "name": "full_page",
                    "type": "bool",
                    "description": "Capture full page screenshot (default: false)",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Navigate to URL and capture screenshot.

        Args:
            arguments: Must contain 'url', optionally 'wait_ms', 'full_page'
        """
        url = arguments["url"].strip()
        wait_ms = arguments.get("wait_ms", 3000)
        full_page = arguments.get("full_page", False)

        try:
            # Run async navigation
            result = asyncio.run(self._navigate_and_screenshot(url, wait_ms, full_page))

            if result["status"] == "SUCCESS":
                self.send_result_notification(
                    status="SUCCESS",
                    result=result["data"]
                )
            else:
                self.send_result_notification(
                    status="FAILURE",
                    error_message=result.get("error", "Unknown error occurred")
                )

        except Exception as e:
            logger.error(f"Unexpected error in browser_navigate: {e}", exc_info=True)
            self.send_result_notification(
                status="FAILURE",
                error_message=f"Navigation error: {str(e)}"
            )

    async def _navigate_and_screenshot(
        self,
        url: str,
        wait_ms: int,
        full_page: bool
    ) -> Dict[str, Any]:
        """Navigate to URL and capture screenshot asynchronously."""
        from playwright.async_api import async_playwright

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=['--no-sandbox', '--disable-dev-shm-usage']
                )

                page = await browser.new_page(
                    viewport={'width': 1280, 'height': 720}
                )

                # Navigate to URL
                logger.info(f"🌐 Navigating to: {url}")
                await page.goto(url, wait_until='networkidle', timeout=30000)

                # Wait for specified time
                await asyncio.sleep(wait_ms / 1000.0)

                # Capture screenshot
                screenshot_bytes = await page.screenshot(
                    type='png',
                    full_page=full_page
                )
                screenshot_data = base64.b64encode(screenshot_bytes).decode('utf-8')

                # Get page title
                title = await page.title()

                await browser.close()

                return {
                    "status": "SUCCESS",
                    "data": {
                        "url": url,
                        "title": title,
                        "screenshot": screenshot_data,
                        "timestamp": datetime.utcnow().isoformat()
                    }
                }

        except Exception as e:
            logger.error(f"Navigation error: {e}", exc_info=True)
            return {
                "status": "FAILURE",
                "error": str(e)
            }


# Export all browser tools
BROWSER_TOOLS = [
    BrowserUseTool,
    BrowserNavigateTool
]
