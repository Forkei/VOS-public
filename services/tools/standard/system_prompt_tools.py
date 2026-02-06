"""
System prompt tools for agent self-modification.

Tools for agents to read and edit their own system prompts.
"""

import os
import logging
from typing import Dict, Any

from vos_sdk import BaseTool

logger = logging.getLogger(__name__)


class ReadSystemPromptTool(BaseTool):
    """
    Reads the agent's current system prompt from disk.

    This allows agents to see their own instructions and configuration.
    Note: The {tools} section is dynamically generated and won't appear in the raw file.
    """

    def __init__(self):
        super().__init__(
            name="read_system_prompt",
            description=(
                "Reads your current system prompt from disk. This shows the raw template "
                "including the {tools} placeholder (which gets replaced with tool descriptions "
                "at runtime). Use this to understand your current instructions before making changes."
            )
        )
        self.prompt_path = os.environ.get("SYSTEM_PROMPT_PATH", "/app/system_prompt.txt")

    def get_parameters(self):
        return []  # No parameters needed

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": self.name,
            "description": self.description,
            "parameters": []
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        """Read and return the current system prompt."""
        try:
            if not os.path.exists(self.prompt_path):
                return {
                    "status": "FAILURE",
                    "result": None,
                    "error_message": f"System prompt file not found at {self.prompt_path}"
                }

            with open(self.prompt_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # Get file stats
            stats = os.stat(self.prompt_path)

            return {
                "status": "SUCCESS",
                "result": {
                    "content": content,
                    "path": self.prompt_path,
                    "size_bytes": stats.st_size,
                    "note": "The {tools} placeholder in the prompt is replaced with actual tool descriptions at runtime."
                }
            }

        except Exception as e:
            logger.error(f"Failed to read system prompt: {e}")
            return {
                "status": "FAILURE",
                "result": None,
                "error_message": str(e)
            }


class EditSystemPromptTool(BaseTool):
    """
    Edits the agent's system prompt.

    Changes take effect immediately on the next LLM call.
    IMPORTANT: Preserve the {tools} placeholder or tools won't be available.
    """

    def __init__(self):
        super().__init__(
            name="edit_system_prompt",
            description=(
                "Edits your system prompt. Changes take effect immediately on the next LLM call. "
                "You can either replace the entire prompt or use find/replace for targeted edits. "
                "CRITICAL: You MUST preserve the {tools} placeholder somewhere in the prompt, "
                "otherwise you won't have access to any tools!"
            )
        )
        self.prompt_path = os.environ.get("SYSTEM_PROMPT_PATH", "/app/system_prompt.txt")

    def get_parameters(self):
        return [
            {
                "name": "new_content",
                "type": "string",
                "description": "The complete new system prompt content. Must include {tools} placeholder.",
                "required": False
            },
            {
                "name": "find",
                "type": "string",
                "description": "Text to find for replacement (use with 'replace' parameter for targeted edits)",
                "required": False
            },
            {
                "name": "replace",
                "type": "string",
                "description": "Text to replace the found text with",
                "required": False
            },
            {
                "name": "append",
                "type": "string",
                "description": "Text to append to the end of the prompt (before {tools} if present)",
                "required": False
            }
        ]

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": self.name,
            "description": self.description,
            "parameters": [
                {
                    "name": "new_content",
                    "type": "str",
                    "description": "Complete new system prompt content (must include {tools})",
                    "required": False
                },
                {
                    "name": "find",
                    "type": "str",
                    "description": "Text to find for replacement",
                    "required": False
                },
                {
                    "name": "replace",
                    "type": "str",
                    "description": "Text to replace the found text with",
                    "required": False
                },
                {
                    "name": "append",
                    "type": "str",
                    "description": "Text to append (before {tools})",
                    "required": False
                }
            ]
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        """Edit the system prompt."""
        new_content = kwargs.get("new_content")
        find_text = kwargs.get("find")
        replace_text = kwargs.get("replace")
        append_text = kwargs.get("append")

        try:
            # Read current content
            if not os.path.exists(self.prompt_path):
                return {
                    "status": "FAILURE",
                    "result": None,
                    "error_message": f"System prompt file not found at {self.prompt_path}"
                }

            with open(self.prompt_path, 'r', encoding='utf-8') as f:
                current_content = f.read()

            # Determine the new content based on operation
            if new_content is not None:
                # Full replacement
                final_content = new_content
                operation = "full_replace"
            elif find_text is not None and replace_text is not None:
                # Find and replace
                if find_text not in current_content:
                    return {
                        "status": "FAILURE",
                        "result": None,
                        "error_message": f"Text to find not found in system prompt: '{find_text[:100]}...'"
                    }
                final_content = current_content.replace(find_text, replace_text)
                operation = "find_replace"
            elif append_text is not None:
                # Append text
                # Try to append before {tools} if it exists
                if "{tools}" in current_content:
                    final_content = current_content.replace("{tools}", f"{append_text}\n\n{{tools}}")
                else:
                    final_content = current_content + "\n\n" + append_text
                operation = "append"
            else:
                return {
                    "status": "FAILURE",
                    "result": None,
                    "error_message": "Must provide either 'new_content', 'find'+'replace', or 'append' parameter"
                }

            # Validate that {tools} placeholder is preserved
            if "{tools}" not in final_content:
                return {
                    "status": "FAILURE",
                    "result": None,
                    "error_message": (
                        "REJECTED: The {tools} placeholder is missing from the new content! "
                        "You MUST include {tools} in your system prompt or you will lose access to all tools. "
                        "Please add {tools} where you want the tool descriptions to appear."
                    )
                }

            # Write the new content
            with open(self.prompt_path, 'w', encoding='utf-8') as f:
                f.write(final_content)

            logger.info(f"System prompt updated via {operation} ({len(final_content)} chars)")

            return {
                "status": "SUCCESS",
                "result": {
                    "operation": operation,
                    "new_size_bytes": len(final_content.encode('utf-8')),
                    "path": self.prompt_path,
                    "note": "Changes will take effect on the next LLM call."
                }
            }

        except Exception as e:
            logger.error(f"Failed to edit system prompt: {e}")
            return {
                "status": "FAILURE",
                "result": None,
                "error_message": str(e)
            }
