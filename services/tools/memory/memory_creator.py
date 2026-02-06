"""
Memory Creator Module for VOS agents.

Automatically analyzes conversations and decides whether to create memories.
Runs every N turns (configurable via .env).
"""

import os
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

from google import genai
from google.genai.types import GenerateContentConfig
from .weaviate_client import WeaviateClient, MemoryType, MemoryScope, MemorySource
from .embedding_service import get_embedding_service

logger = logging.getLogger(__name__)


def get_agent_metadata(db_client, agent_name: str) -> Dict[str, Any]:
    """Get agent metadata from agent_state table."""
    try:
        state_result = db_client.get_agent_state(agent_name)
        if state_result.status == "SUCCESS":
            state_data = state_result.result.get("result", {})
            return state_data.get("metadata", {})
        return {}
    except Exception as e:
        logger.warning(f"Failed to get agent metadata: {e}")
        return {}


def update_agent_metadata(db_client, agent_name: str, metadata: Dict[str, Any]) -> bool:
    """Update agent metadata in agent_state table using direct HTTP request."""
    try:
        import httpx
        # Use the db_client's base_url to make a direct API call
        url = f"{db_client.base_url}/api/v1/agents/{agent_name}/metadata"

        headers = {}
        if db_client.internal_api_key:
            headers["X-Internal-Key"] = db_client.internal_api_key

        with httpx.Client(timeout=10.0) as client:
            response = client.put(url, json=metadata, headers=headers)
            if response.status_code in (200, 201):
                return True
            else:
                logger.error(f"Failed to update metadata: HTTP {response.status_code} - {response.text}")
                return False
    except Exception as e:
        logger.error(f"Failed to update agent metadata: {e}")
        return False


class MemoryCreatorModule:
    """
    Autonomous memory creation module.

    Analyzes conversation context and decides whether to create memories.
    """

    def __init__(self, agent_name: str, gemini_api_key: str, db_client):
        """
        Initialize Memory Creator Module.

        Args:
            agent_name: Name of the agent using this module
            gemini_api_key: Gemini API key for LLM calls
            db_client: Database client for persistent state
        """
        self.agent_name = agent_name
        self.genai_client = genai.Client(api_key=gemini_api_key)
        self.weaviate_url = os.getenv("WEAVIATE_URL", "http://weaviate:8080")
        self.db_client = db_client

        # Configuration from .env - per-agent toggles
        agent_prefix = agent_name.upper()
        self.enabled = os.getenv(f"{agent_prefix}_MEMORY_CREATOR_ENABLED", "true").lower() == "true"
        self.run_every_n_turns = int(os.getenv("MEMORY_CREATOR_RUN_EVERY_N_TURNS", "1"))
        self.context_messages = int(os.getenv("MEMORY_CREATOR_CONTEXT_MESSAGES", "10"))

        logger.info(f"Memory Creator initialized for {agent_name} (enabled={self.enabled}, every_n_turns={self.run_every_n_turns})")

    def should_run(self, turn_number: int) -> bool:
        """
        Check if the module should run on this turn.

        Args:
            turn_number: Current turn number from agent_state.total_messages

        Returns:
            True if should run this turn
        """
        if not self.enabled:
            return False

        return turn_number % self.run_every_n_turns == 0

    def _get_past_5_memories(self) -> List[Dict[str, Any]]:
        """
        Get the last 5 created memories to avoid duplicates.

        Returns:
            List of recent memories (full objects), sorted by creation time (newest first)
        """
        try:
            with WeaviateClient(self.weaviate_url) as client:
                # Search for memories created by this agent, sorted by created_at descending
                memories = client.search_memories(
                    agent_id=self.agent_name,
                    limit=5,
                    sort_by_created=True
                )
                return memories
        except Exception as e:
            logger.warning(f"Failed to get past memories: {e}")
            return []

    def _get_wait_state(self) -> Optional[str]:
        """
        Get WAIT state topic from agent metadata in database.

        Returns:
            Wait topic string or None
        """
        try:
            metadata = get_agent_metadata(self.db_client, self.agent_name)
            return metadata.get("memory_creator_wait_topic")
        except Exception as e:
            logger.warning(f"Failed to get WAIT state: {e}")
            return None

    def _set_wait_state(self, topic: Optional[str]) -> None:
        """
        Set WAIT state topic in agent metadata in database.

        Args:
            topic: Wait topic string or None to clear
        """
        try:
            metadata = get_agent_metadata(self.db_client, self.agent_name)
            if topic is None:
                # Clear WAIT state
                metadata.pop("memory_creator_wait_topic", None)
            else:
                # Set WAIT state
                metadata["memory_creator_wait_topic"] = topic
            update_agent_metadata(self.db_client, self.agent_name, metadata)
        except Exception as e:
            logger.error(f"Failed to set WAIT state: {e}")

    def _build_system_prompt(self) -> str:
        """Build system prompt for memory creator."""
        return """You are the agent's subconscious memory system. Your job is to identify and store important information that will be valuable in future conversations.

BE HIGHLY SELECTIVE. Only create memories when truly necessary.

CREATE memories ONLY for:
- Explicit user preferences or corrections ("I prefer...", "Don't do...", "Always...")
- Personal facts about the user (name, job, location, relationships, interests)
- Significant project context or goals that will matter in future sessions
- Procedures that worked well or failed in notable ways

NEVER create memories for:
- General knowledge or facts (the agent can look these up)
- Trivial or routine exchanges ("hi", "thanks", small talk)
- Information already captured in recent memories (CHECK THE PAST 5 MEMORIES CAREFULLY)
- Information that is similar to or overlaps with a recent memory
- Temporary context that won't matter in future conversations
- Things the user mentioned casually without emphasis

DUPLICATE PREVENTION (CRITICAL):
- Before deciding CREATE_NOW, check if ANY of the past 5 memories already cover this topic
- If a recent memory exists on the same subject, IGNORE unless there's genuinely NEW information
- Don't create a memory just because the user mentioned something - only if it's important AND not already stored
- When in doubt, IGNORE. It's better to miss a memory than to spam duplicates.

MEMORY TYPES:
- user_preference: How the user wants things done
- user_fact: Who the user is (name, job, location, relationships, interests)
- conversation_context: Important ongoing topics, projects, or goals
- agent_procedure: What worked/failed for this agent
- error_handling: How to handle specific errors
- proactive_action: When to act without being asked

DECISIONS:
- CREATE_NOW: You have complete, valuable, NEW information not covered by recent memories
- WAIT: User started sharing something important but hasn't finished
- IGNORE: Nothing significant OR already covered by recent memories (this should be your most common decision)

OUTPUT (JSON):
{
  "reflection": "<brief reasoning, including why this isn't a duplicate>",
  "decision": "CREATE_NOW" | "WAIT" | "IGNORE",
  "memories": [  // only for CREATE_NOW
    {
      "content": "<clear, searchable description>",
      "memory_type": "<type>",
      "importance": <0.0-1.0>,
      "tags": ["<searchable>", "<terms>"],
      "scope": "shared" | "individual"
    }
  ],
  "topic": "<description>"  // only for WAIT
}

Write memory content as clear, standalone statements that will make sense months later without context.
You see the past 5 created memories - USE THEM to avoid duplicates."""

    def _call_llm(self, context: str) -> str:
        """
        Call Gemini LLM for memory creation decision.

        Args:
            context: Context string with messages and past memories

        Returns:
            LLM response JSON string
        """
        try:
            response = self.genai_client.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=context,
                config=GenerateContentConfig(
                    system_instruction=self._build_system_prompt()
                )
            )

            if not response or not response.text:
                raise RuntimeError("Empty response from Gemini LLM")

            return response.text.strip()
        except Exception as e:
            logger.error(f"Memory Creator LLM call failed: {e}")
            raise

    def _format_messages_for_context(self, messages: List[Dict[str, Any]]) -> str:
        """
        Format messages for LLM context.

        Args:
            messages: List of message objects with role and content

        Returns:
            Formatted string
        """
        formatted = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            # Handle different content formats
            if isinstance(content, dict):
                content_str = json.dumps(content, indent=2)
            else:
                content_str = str(content)

            formatted.append(f"{role.upper()}: {content_str}")

        return "\n\n".join(formatted)

    def _parse_llm_response(self, response_text: str) -> Dict[str, Any]:
        """
        Parse LLM response JSON.

        Args:
            response_text: Raw LLM response

        Returns:
            Parsed decision dict
        """
        try:
            # Remove markdown code blocks if present
            if "```json" in response_text:
                start = response_text.find("```json") + 7
                end = response_text.find("```", start)
                response_text = response_text[start:end].strip()
            elif "```" in response_text:
                start = response_text.find("```") + 3
                end = response_text.find("```", start)
                response_text = response_text[start:end].strip()

            parsed = json.loads(response_text)

            # Validate required fields
            if "decision" not in parsed:
                raise ValueError("Missing 'decision' field")

            if parsed["decision"] not in ["CREATE_NOW", "WAIT", "IGNORE"]:
                raise ValueError(f"Invalid decision: {parsed['decision']}")

            return parsed
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response: {e}\nResponse: {response_text}")
            raise ValueError(f"Invalid JSON response: {e}")

    def _create_memory_in_weaviate(self, memory_data: Dict[str, Any]) -> str:
        """
        Create a single memory in Weaviate.

        Args:
            memory_data: Memory parameters

        Returns:
            Memory UUID
        """
        try:
            # Generate embedding
            embedding_service = get_embedding_service()
            vector = embedding_service.embed_memory(memory_data["content"])

            # Parse enums
            memory_type = MemoryType(memory_data["memory_type"])
            scope = MemoryScope(memory_data.get("scope", "shared"))

            # Create memory
            with WeaviateClient(self.weaviate_url) as client:
                memory_id = client.create_memory(
                    content=memory_data["content"],
                    memory_type=memory_type,
                    scope=scope,
                    vector=vector,
                    agent_id=self.agent_name,
                    tags=memory_data.get("tags", []),
                    importance=memory_data.get("importance", 0.5),
                    confidence=memory_data.get("confidence", 1.0),
                    source=MemorySource.PROACTIVE_AGENT,
                    related_event_types=memory_data.get("related_event_types"),
                    related_tools=memory_data.get("related_tools")
                )

            logger.info(f"Created memory {memory_id}: {memory_data['content'][:50]}...")
            return memory_id
        except Exception as e:
            logger.error(f"Failed to create memory in Weaviate: {e}")
            raise

    def run(self, messages: List[Dict[str, Any]]) -> None:
        """
        Run the memory creator module.

        Args:
            messages: Recent conversation messages (user/assistant only, last N)
        """
        try:
            # Get past 5 memories
            past_memories = self._get_past_5_memories()

            # Build context
            context_parts = []

            # Add conversation
            context_parts.append("# Recent Conversation")
            context_parts.append(self._format_messages_for_context(messages[-self.context_messages:]))

            # Add past memories
            if past_memories:
                context_parts.append("\n# Past 5 Created Memories (check these to avoid duplicates)")
                for mem in past_memories:
                    context_parts.append(f"- [{mem['memory_type']}] {mem['content']}")

            # Get and add WAIT state from database if exists
            wait_topic = self._get_wait_state()
            if wait_topic:
                context_parts.append(f"\n# WAIT State Topic: {wait_topic}")

            context = "\n\n".join(context_parts)

            # Call LLM
            logger.debug("Calling Memory Creator LLM...")
            response_text = self._call_llm(context)

            # Parse decision
            try:
                decision = self._parse_llm_response(response_text)
                logger.debug(f"Memory Creator decision: {decision['decision']}")
            except Exception as e:
                logger.warning(f"Memory Creator LLM response parsing failed, interpreting as IGNORE: {e}")
                decision = {"decision": "IGNORE", "reflection": "Parse error"}

            # Execute decision
            if decision["decision"] == "CREATE_NOW":
                memories = decision.get("memories", [])
                logger.info(f"🧠 Memory Creator: CREATE_NOW - {len(memories)} memories")
                logger.info(f"   Reflection: {decision.get('reflection', 'N/A')[:100]}")

                for mem_data in memories:
                    try:
                        memory_id = self._create_memory_in_weaviate(mem_data)
                        logger.info(f"   ✅ Created [{mem_data.get('memory_type')}]: {mem_data.get('content', '')[:80]}...")
                    except Exception as e:
                        logger.error(f"   ❌ Failed to create memory: {e}")

                # Clear WAIT state in database
                self._set_wait_state(None)

            elif decision["decision"] == "WAIT":
                topic = decision.get("topic", "Unknown topic")
                logger.info(f"🧠 Memory Creator: WAIT - {topic}")
                logger.info(f"   Reflection: {decision.get('reflection', 'N/A')[:100]}")
                # Save WAIT state to database
                self._set_wait_state(topic)

            else:  # IGNORE
                logger.info(f"🧠 Memory Creator: IGNORE")
                logger.debug(f"   Reflection: {decision.get('reflection', 'N/A')[:100]}")
                # Clear WAIT state in database
                self._set_wait_state(None)

        except Exception as e:
            logger.error(f"Memory Creator run failed: {e}")
            # Don't raise - this is a background process
