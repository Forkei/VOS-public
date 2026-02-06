"""
Memory Retriever Module for VOS agents.

Automatically retrieves relevant memories from past conversations.
Runs every N turns (configurable via .env).
"""

import os
import json
import logging
from typing import Dict, Any, List, Optional

from google import genai
from google.genai.types import GenerateContentConfig
from .weaviate_client import WeaviateClient
from .embedding_service import get_embedding_service

logger = logging.getLogger(__name__)


class MemoryRetrieverModule:
    """
    Autonomous memory retrieval module.

    Searches for relevant memories based on conversation context using iterative refinement.
    """

    def __init__(self, agent_name: str, gemini_api_key: str, db_client):
        """
        Initialize Memory Retriever Module.

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
        self.enabled = os.getenv(f"{agent_prefix}_MEMORY_RETRIEVER_ENABLED", "true").lower() == "true"
        self.run_every_n_turns = int(os.getenv("MEMORY_RETRIEVER_RUN_EVERY_N_TURNS", "1"))
        self.context_messages = int(os.getenv("MEMORY_RETRIEVER_CONTEXT_MESSAGES", "10"))
        self.max_iterations = int(os.getenv("MEMORY_RETRIEVER_MAX_ITERATIONS", "3"))

        logger.info(f"Memory Retriever initialized for {agent_name} (enabled={self.enabled}, every_n_turns={self.run_every_n_turns}, max_iterations={self.max_iterations})")

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

    def _get_past_provided_memories(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get the last N provided memories to avoid re-providing.

        Uses last_accessed_at as a proxy for when memories were provided,
        since accessing a memory updates this timestamp.

        Args:
            limit: Number of past provided memories to retrieve (default 10)

        Returns:
            List of recently provided memories (full objects), sorted by access time (newest first)
        """
        try:
            with WeaviateClient(self.weaviate_url) as client:
                # Get recently accessed memories as proxy for recently provided
                # Sort by last_accessed_at descending to get most recently accessed
                memories = client.search_memories(
                    limit=limit,
                    sort_by_accessed=True
                )
                return memories
        except Exception as e:
            logger.warning(f"Failed to get past provided memories: {e}")
            return []

    def _build_system_prompt(self) -> str:
        """Build system prompt for memory retriever."""
        return f"""You are the agent's subconscious memory system. Your job is to surface relevant memories that would help the current conversation.

SEARCH when the user:
- Asks about themselves (identity, name, preferences, facts about them)
- References past conversations or decisions
- Asks questions that would benefit from personalization
- Needs context from previous interactions

IGNORE when:
- It's a purely factual/informational request unrelated to the user personally
- The conversation already has all needed context
- The memories you would provide are already in the "Past 10 Provided Memories" list
- The topic hasn't changed significantly since the last retrieval

CRITICAL RULES:
1. ONLY return 1-2 memories maximum. Never more than 2.
2. NEVER return similar/redundant memories - if multiple memories say the same thing, pick only the BEST one.
3. Check the "Past 10 Provided Memories" list - DO NOT re-provide any of them.
4. If all relevant memories were already provided recently, return IGNORE.
5. Quality over quantity - one perfect memory is better than multiple redundant ones.

PROCESS (max {self.max_iterations} iterations):
1. Generate focused search queries (1-3) for user identity, preferences, or relevant context
2. Optionally add filters to narrow results (time range, memory type, importance)
3. Review results and FILTER OUT duplicates/similar memories - keep only the best version
4. Select AT MOST 1-2 memories that are relevant AND not recently provided

DECISIONS:
- GET_MEMORIES: Search needed → provide 1-3 focused queries with optional filters
- GIVE_MEMORIES: Found 1-2 relevant memories NOT in past 5 provided → provide memory IDs
- IGNORE: No memories needed OR all relevant memories already provided recently

OUTPUT (JSON):
{{
  "reflection": "<brief reasoning, note if you're filtering out similar memories>",
  "decision": "GET_MEMORIES" | "GIVE_MEMORIES" | "IGNORE",
  "queries": [  // only for GET_MEMORIES - can be simple strings or objects with filters
    "simple text query",
    {{
      "text": "query with filters",
      "filters": {{
        "memory_type": "user_preference",  // optional: user_preference, user_fact, conversation_context, agent_procedure, error_handling, proactive_action
        "min_importance": 0.7,  // optional: 0.0-1.0
        "created_after": "2024-01-01T00:00:00Z",  // optional: ISO timestamp
        "created_before": "2024-12-31T23:59:59Z",  // optional: ISO timestamp
        "tags": ["tag1", "tag2"]  // optional: filter by tags
      }}
    }}
  ],
  "memory_ids": ["<uuid>"]  // only for GIVE_MEMORIES - MAX 2 IDs, must NOT be in past 10 provided
}}

FILTER EXAMPLES:
- User asks "what did I tell you last week?" → use created_after/created_before for last 7 days
- User asks about preferences → use memory_type: "user_preference"
- User asks about important things → use min_importance: 0.7

REMEMBER: Maximum 1-2 memories. Never return similar/duplicate memories. One perfect memory beats multiple redundant ones."""

    def _call_llm(self, context: str) -> str:
        """
        Call Gemini LLM for memory retrieval decision.

        Args:
            context: Context string with messages, past provided memories, and search results

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
            logger.error(f"Memory Retriever LLM call failed: {e}")
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

            if parsed["decision"] not in ["GET_MEMORIES", "GIVE_MEMORIES", "IGNORE"]:
                raise ValueError(f"Invalid decision: {parsed['decision']}")

            return parsed
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response: {e}\nResponse: {response_text}")
            raise ValueError(f"Invalid JSON response: {e}")

    def _search_memories(self, queries: List) -> List[Dict[str, Any]]:
        """
        Search Weaviate for memories using queries.

        Args:
            queries: List of search queries - can be strings or dicts with text and filters:
                     - "simple query string"
                     - {"text": "query", "filters": {"memory_type": "user_preference", ...}}

        Returns:
            Combined list of unique memories (3 per query)
        """
        try:
            from .weaviate_client import MemoryType
            embedding_service = get_embedding_service()
            all_memories = []
            seen_ids = set()

            with WeaviateClient(self.weaviate_url) as client:
                for query_item in queries:
                    # Parse query - can be string or dict with filters
                    if isinstance(query_item, str):
                        query_text = query_item
                        filters = {}
                    elif isinstance(query_item, dict):
                        query_text = query_item.get("text", "")
                        filters = query_item.get("filters", {})
                    else:
                        logger.warning(f"Invalid query format: {query_item}")
                        continue

                    if not query_text:
                        continue

                    # Generate query embedding
                    query_vector = embedding_service.embed_query(query_text)

                    # Build filter kwargs
                    search_kwargs = {
                        "query_vector": query_vector,
                        "limit": 3  # 3 memories per query
                    }

                    # Apply filters if provided
                    if filters.get("memory_type"):
                        try:
                            search_kwargs["memory_type"] = MemoryType(filters["memory_type"])
                        except ValueError:
                            logger.warning(f"Invalid memory_type: {filters['memory_type']}")

                    if filters.get("min_importance") is not None:
                        search_kwargs["min_importance"] = float(filters["min_importance"])

                    if filters.get("created_after"):
                        search_kwargs["created_after"] = filters["created_after"]

                    if filters.get("created_before"):
                        search_kwargs["created_before"] = filters["created_before"]

                    if filters.get("tags"):
                        search_kwargs["tags"] = filters["tags"]

                    # Search for memories
                    memories = client.search_memories(**search_kwargs)

                    # Add to results if not already seen
                    for memory in memories:
                        if memory["id"] not in seen_ids:
                            all_memories.append(memory)
                            seen_ids.add(memory["id"])

            logger.info(f"Found {len(all_memories)} unique memories for {len(queries)} queries")
            return all_memories

        except Exception as e:
            logger.error(f"Failed to search memories: {e}")
            return []

    def _format_memories_for_context(self, memories: List[Dict[str, Any]]) -> str:
        """
        Format memories for LLM context.

        Args:
            memories: List of memory objects

        Returns:
            Formatted string
        """
        if not memories:
            return "No memories found."

        formatted = []
        for mem in memories:
            formatted.append(
                f"ID: {mem['id']}\n"
                f"Type: {mem['memory_type']}\n"
                f"Content: {mem['content']}\n"
                f"Importance: {mem['importance']}\n"
                f"Tags: {', '.join(mem.get('tags', []))}"
            )

        return "\n\n".join(formatted)

    def _deduplicate_memories(self, memories: List[Dict[str, Any]], similarity_threshold: float = 0.85) -> List[Dict[str, Any]]:
        """
        Deduplicate memories with similar content.

        Uses semantic similarity to group memories and keeps only the most important
        one from each group. This prevents the agent from receiving 6 nearly
        identical memories about the same topic.

        Args:
            memories: List of memory objects with 'content', 'importance', 'created_at'
            similarity_threshold: Cosine similarity threshold for considering memories as duplicates (0.0-1.0)

        Returns:
            Deduplicated list of memories
        """
        if len(memories) <= 1:
            return memories

        try:
            embedding_service = get_embedding_service()

            # Get embeddings for all memory contents
            contents = [mem['content'] for mem in memories]
            embeddings = [embedding_service.embed_query(content) for content in contents]

            # Group memories by similarity
            groups = []  # List of lists of indices
            used = set()

            for i in range(len(memories)):
                if i in used:
                    continue

                # Start a new group with this memory
                group = [i]
                used.add(i)

                # Find all similar memories
                for j in range(i + 1, len(memories)):
                    if j in used:
                        continue

                    # Calculate cosine similarity
                    dot_product = sum(a * b for a, b in zip(embeddings[i], embeddings[j]))
                    norm_i = sum(a * a for a in embeddings[i]) ** 0.5
                    norm_j = sum(b * b for b in embeddings[j]) ** 0.5
                    similarity = dot_product / (norm_i * norm_j) if norm_i > 0 and norm_j > 0 else 0

                    if similarity >= similarity_threshold:
                        group.append(j)
                        used.add(j)

                groups.append(group)

            # Select best memory from each group (highest importance, then most recent)
            deduplicated = []
            for group in groups:
                if len(group) == 1:
                    deduplicated.append(memories[group[0]])
                else:
                    # Sort by importance (desc), then by created_at (desc)
                    group_memories = [memories[idx] for idx in group]
                    best = max(
                        group_memories,
                        key=lambda m: (m.get('importance', 0), m.get('created_at', ''))
                    )
                    deduplicated.append(best)
                    logger.debug(f"Deduplicated {len(group)} similar memories, keeping: {best['content'][:50]}...")

            if len(deduplicated) < len(memories):
                logger.info(f"📚 Deduplicated {len(memories)} memories → {len(deduplicated)} unique memories")

            return deduplicated

        except Exception as e:
            logger.warning(f"Memory deduplication failed, returning original list: {e}")
            return memories

    def run(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Run the memory retriever module.

        Args:
            messages: Recent conversation messages (user/assistant only, last N)

        Returns:
            List of retrieved memory objects to inject into agent context
        """
        try:
            # Get past 10 provided memories to avoid re-providing
            past_provided = self._get_past_provided_memories()
            past_provided_ids = {mem["id"] for mem in past_provided}

            # Build initial context
            context_parts = []

            # Add conversation
            context_parts.append("# Recent Conversation")
            context_parts.append(self._format_messages_for_context(messages[-self.context_messages:]))

            # Add past provided memories
            if past_provided:
                context_parts.append("\n# Past 10 Provided Memories (DO NOT re-provide these)")
                for mem in past_provided:
                    context_parts.append(f"- [ID: {mem['id']}] [{mem['memory_type']}] {mem['content']}")

            context = "\n\n".join(context_parts)

            # Iterative retrieval loop
            iteration = 1
            search_results = []

            while iteration <= self.max_iterations:
                logger.debug(f"Memory Retriever iteration {iteration}/{self.max_iterations}")

                # Call LLM
                response_text = self._call_llm(context)

                # Parse decision
                try:
                    decision = self._parse_llm_response(response_text)
                    logger.debug(f"Memory Retriever decision: {decision['decision']}")
                except Exception as e:
                    logger.warning(f"Memory Retriever LLM response parsing failed, interpreting as IGNORE: {e}")
                    return []  # Return empty list on parse error

                # Execute decision
                if decision["decision"] == "GET_MEMORIES":
                    queries = decision.get("queries", [])
                    if not queries or len(queries) > 5:
                        logger.warning(f"Invalid queries count: {len(queries)}, treating as IGNORE")
                        return []

                    logger.info(f"🔍 Memory Retriever: GET_MEMORIES (iteration {iteration}/{self.max_iterations})")
                    # Log queries with filter info
                    for i, q in enumerate(queries):
                        if isinstance(q, str):
                            logger.info(f"   Query {i+1}: \"{q}\"")
                        elif isinstance(q, dict):
                            logger.info(f"   Query {i+1}: \"{q.get('text', '')}\" with filters: {q.get('filters', {})}")
                    search_results = self._search_memories(queries)
                    logger.info(f"   Found {len(search_results)} memories")

                    # Add search results to context for next iteration
                    context_parts.append(f"\n# Search Results (Iteration {iteration})")
                    context_parts.append(self._format_memories_for_context(search_results))
                    context = "\n\n".join(context_parts)

                    iteration += 1

                    # Force final decision on last iteration
                    if iteration > self.max_iterations:
                        logger.info("   Max iterations reached, must decide now")

                elif decision["decision"] == "GIVE_MEMORIES":
                    memory_ids = decision.get("memory_ids", [])
                    logger.info(f"🔍 Memory Retriever: GIVE_MEMORIES - {len(memory_ids)} memories")
                    logger.info(f"   Reflection: {decision.get('reflection', 'N/A')[:100]}")

                    # Filter search results to only include selected IDs
                    selected_memories = [
                        mem for mem in search_results
                        if mem["id"] in memory_ids
                    ]

                    # Deduplicate similar memories to avoid redundant context
                    selected_memories = self._deduplicate_memories(selected_memories)

                    for mem in selected_memories:
                        logger.info(f"   📝 [{mem.get('memory_type')}]: {mem.get('content', '')[:60]}...")

                    # Mark these memories as provided (update last_accessed_at)
                    # This is used by _get_past_provided_memories to avoid re-providing
                    try:
                        with WeaviateClient(self.weaviate_url) as client:
                            provided_ids = [mem["id"] for mem in selected_memories]
                            client.mark_memories_provided(provided_ids)
                    except Exception as e:
                        logger.warning(f"Failed to mark memories as provided: {e}")

                    return selected_memories

                else:  # IGNORE
                    logger.info(f"🔍 Memory Retriever: IGNORE")
                    logger.debug(f"   Reflection: {decision.get('reflection', 'N/A')[:100]}")
                    return []

            # If we exit the loop without returning, means max iterations reached
            logger.warning("Memory Retriever max iterations reached without GIVE_MEMORIES decision")
            return []

        except Exception as e:
            logger.error(f"Memory Retriever run failed: {e}")
            return []  # Return empty list on error
