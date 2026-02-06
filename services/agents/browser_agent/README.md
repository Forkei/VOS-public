# Browser Agent

AI-powered browser automation service for the VOS platform using the browser-use library.

## Overview

The Browser Agent provides intelligent web browsing capabilities through natural language task descriptions. It uses Google Gemini AI to understand tasks and autonomously navigate websites, interact with elements, fill forms, and extract information.

## Features

- **AI-Powered Automation**: Uses LLM to intelligently navigate and interact with websites
- **Natural Language Tasks**: Describe what you want in plain English
- **Screenshot Capture**: Automatically captures screenshots of results
- **Session Management**: Maintain browser state across multiple related tasks
- **Form Filling**: Automatically fill out web forms
- **Data Extraction**: Extract structured data from websites
- **Element Interaction**: Click buttons, links, and interact with dynamic content

## Tools Available

### 1. `browser_use` - AI-Powered Browser Automation

Intelligently automates browser interactions using LLM decision-making.

**Parameters:**
- `task` (required): Natural language description of what to do
  - Example: "Go to example.com and find the contact email"
  - Example: "Fill out the search form and extract the top 5 results"
- `session_id` (optional): Session ID to maintain browser state
- `max_steps` (optional): Maximum number of actions (default: 10, max: 50)
- `capture_screenshot` (optional): Capture final screenshot (default: true)

**Usage Example:**
```
Ask primary agent: "Use the browser to go to github.com/browser-use/browser-use and tell me how many stars it has"
```

### 2. `browser_navigate` - Simple URL Navigation

Direct navigation to a URL and screenshot capture without intelligent interaction.

**Parameters:**
- `url` (required): URL to navigate to (must start with http:// or https://)
- `wait_ms` (optional): Milliseconds to wait for page load (default: 3000)
- `full_page` (optional): Capture full page screenshot (default: false)

**Usage Example:**
```
Ask primary agent: "Navigate to https://example.com and show me what it looks like"
```

## Architecture

```
User Request → Primary Agent → Browser Agent
                                    ↓
                              Browser-Use Library
                                    ↓
                            Playwright + Chromium
                                    ↓
                              Screenshot (base64)
                                    ↓
                            Sent back to user
```

## Technologies

- **browser-use**: AI agent library for browser automation
- **Playwright**: Browser automation framework
- **Chromium**: Headless browser (pre-installed in Docker image)
- **Google Gemini 2.0 Flash**: LLM for intelligent decision-making
- **langchain-google-genai**: LangChain integration for Gemini

## Configuration

### Environment Variables

See `.env.example` for required configuration:

- `GEMINI_API_KEY`: Google Gemini API key (required)
- `RABBITMQ_*`: RabbitMQ connection settings
- `DATABASE_*`: PostgreSQL connection settings
- `WEAVIATE_*`: Weaviate vector database settings
- `BROWSER_AGENT_MAX_CONVERSATION_MESSAGES`: Message history limit (default: 15)

## Docker Configuration

The Browser Agent uses Microsoft's official Playwright Docker image which includes:
- Pre-installed Chromium, Firefox, and WebKit browsers
- All necessary system dependencies
- Optimized for headless browser automation

**Key Docker Settings:**
- Base image: `mcr.microsoft.com/playwright/python:v1.48.0-noble`
- Shared memory: 2GB (required for Chromium)
- Non-root user: `browser_user` for security

## Building and Running

### With Docker Compose (Recommended)

```bash
# Build and start browser agent
docker compose up browser_agent --build

# View logs
docker compose logs -f browser_agent

# Stop
docker compose stop browser_agent
```

### Standalone (Development)

```bash
# Install dependencies
cd services/agents/browser_agent
pip install -r requirements.txt
pip install ../../sdk

# Install Playwright browsers
playwright install chromium

# Run agent
python main.py
```

## Usage Examples

### Example 1: Web Research
```
User: "Go to Wikipedia and find the population of Tokyo"

Browser Agent will:
1. Navigate to wikipedia.org
2. Search for "Tokyo"
3. Find the population information
4. Extract and return the data
5. Provide screenshot as proof
```

### Example 2: Form Interaction
```
User: "Go to example.com/contact and fill out the form with name 'Test User' and email 'test@example.com'"

Browser Agent will:
1. Navigate to the contact page
2. Identify form fields
3. Fill in the specified information
4. Capture screenshot of filled form
```

### Example 3: Multi-Step Task
```
User: "Search GitHub for 'browser automation' repositories and tell me the top 3 by stars"

Browser Agent will:
1. Navigate to github.com
2. Use the search functionality
3. Sort by stars
4. Extract top 3 results
5. Return formatted data with screenshots
```

## Performance

- **Startup Time**: ~5-10 seconds (browser initialization)
- **Simple Navigation**: ~3-5 seconds
- **Complex Tasks**: Varies by task complexity (10-60 seconds typical)
- **Max Steps**: Limited to 50 actions per task for safety

## Security Considerations

- Browser runs in headless mode (no visible UI)
- Non-root user execution
- No real credentials should be used without explicit permission
- Respects robots.txt when appropriate
- Sandboxed browser environment
- 2GB shared memory limit prevents resource exhaustion

## Troubleshooting

### Browser won't start
- Check that `shm_size: '2gb'` is set in docker-compose.yml
- Ensure container has enough memory allocated
- Check logs for Chromium-specific errors

### Tasks timing out
- Increase `max_steps` parameter
- Break complex tasks into smaller steps
- Check network connectivity from container

### Screenshots not captured
- Verify `capture_screenshot: true` in task parameters
- Check browser is running in headless mode correctly
- Review agent logs for screenshot errors

## Limitations

- Cannot handle CAPTCHAs automatically
- May struggle with heavily JavaScript-dependent sites
- File downloads require special handling
- Cookie consent banners may interfere with automation
- Maximum 50 steps per task to prevent runaway execution

## Development

### Adding New Browser Tools

1. Create tool class inheriting from `BaseTool`
2. Add to `services/tools/browser/browser_use_tools.py`
3. Export in `services/tools/browser/__init__.py`
4. Update `BROWSER_TOOLS` list
5. Tool automatically appears in system prompt

### Testing

```bash
# Test browser agent locally
python -m pytest tests/agents/browser_agent/

# Test specific tool
python -c "from tools.browser import BrowserUseTool; print(BrowserUseTool().get_tool_info())"
```

## Metrics

Exposed on port 8007 (Prometheus format):
- `vos_browser_tasks_total` - Total browser tasks executed
- `vos_browser_task_duration_seconds` - Task execution time
- `vos_browser_screenshots_total` - Screenshots captured
- `vos_browser_errors_total` - Errors encountered

## License

Part of the VOS (Virtual Operating System) project.

## Support

For issues specific to browser automation:
- Check browser-use documentation: https://github.com/browser-use/browser-use
- Review Playwright docs: https://playwright.dev/python/
- VOS project issues: [Your issue tracker]
