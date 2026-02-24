"""RAG Query Engine — orchestrates retrieval, reasoning, and generation.

Implements:
  - Query analysis with LLM-powered decomposition
  - HyDE (Hypothetical Document Embeddings) for better recall
  - Adaptive thinking with streaming thinking blocks
  - Native citations via search_result content blocks
  - Web search tool (server-side, Anthropic API)
  - Memory tool (client-side, local file storage)
  - Fast mode (Haiku 4.5, no thinking, lower max_tokens for speed)
  - Prompt caching with cache_control
  - Token tracking from response usage
  - Session-based conversation history
  - Follow-up question generation
"""

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from webapp.rag.prompts import (
    QUERY_ANALYSIS_SYSTEM,
    QUERY_ANALYSIS_USER,
    ANSWER_SYNTHESIS_SYSTEM,
    ANSWER_SYNTHESIS_USER,
    FOLLOWUP_SYSTEM,
    PERSONA_MODIFIERS,
    LLM_KNOWLEDGE_SUPPLEMENT,
    WEB_SEARCH_SUPPLEMENT,
    CONVERSATION_CONTEXT_INSTRUCTION,
)
from webapp.rag.retriever import Retriever, RetrievedChunk

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent


@dataclass
class Citation:
    """A citation reference to a source chunk."""
    index: int  # 1-based for display
    chunk_id: str
    source_title: str
    source_url: str
    source_type: str
    credibility: str
    competitor: str
    primary_topic: str
    content_date: str
    text_preview: str  # first ~200 chars


@dataclass
class QueryResult:
    """Complete result of a RAG query."""
    query: str
    answer: str
    citations: list[Citation]
    follow_up_questions: list[str]
    metadata: dict  # timing, retrieval stats, model info, etc.


class LLMClient:
    """Unified LLM client supporting OpenAI and Anthropic with advanced features.

    Anthropic features: adaptive thinking, native citations, web search tool,
    memory tool, fast mode, prompt caching, token tracking.
    """

    def __init__(
        self,
        provider: str = "anthropic",
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        fast_mode: bool = False,
    ):
        self.provider = provider
        self.fast_mode = fast_mode

        if provider == "anthropic":
            import anthropic
            self.model = model or "claude-sonnet-4-6"
            if fast_mode:
                self.model = "claude-haiku-4-5-20251001"
            self.client = anthropic.Anthropic(
                api_key=api_key or os.getenv("ANTHROPIC_API_KEY")
            )
        elif provider == "openai":
            from openai import OpenAI
            self.model = model or "gpt-4o"
            self.client = OpenAI(
                api_key=api_key or os.getenv("OPENAI_API_KEY")
            )
        else:
            raise ValueError(f"Unsupported provider: {provider}")

    # ------------------------------------------------------------------
    # Simple chat (for query analysis, follow-ups — backward compatible)
    # ------------------------------------------------------------------

    def chat(
        self,
        system: str,
        user: str,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> str:
        """Send a simple chat completion request."""
        if self.provider == "anthropic":
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return response.content[0].text
        else:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            return response.choices[0].message.content

    # ------------------------------------------------------------------
    # Simple streaming (for OpenAI fallback)
    # ------------------------------------------------------------------

    def chat_stream(
        self,
        system: str,
        user: str,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ):
        """Yield text chunks from a streaming chat completion (text-only)."""
        if self.provider == "anthropic":
            with self.client.messages.stream(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                messages=[{"role": "user", "content": user}],
            ) as stream:
                for text in stream.text_stream:
                    yield text
        else:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

    # ------------------------------------------------------------------
    # Advanced Anthropic streaming with raw event handling
    # ------------------------------------------------------------------

    def _build_system_blocks(self, system_text: str) -> list[dict]:
        """Convert system prompt to content blocks with prompt caching."""
        block = {"type": "text", "text": system_text}
        if self.provider == "anthropic":
            block["cache_control"] = {"type": "ephemeral"}
        return [block]

    def _build_anthropic_params(
        self,
        system_blocks: list[dict],
        messages: list[dict],
        max_tokens: int = 16000,
        thinking: bool = True,
        tools: Optional[list[dict]] = None,
    ) -> dict:
        """Build kwargs for Anthropic messages.create/stream."""
        params = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system_blocks,
            "messages": messages,
        }
        if thinking:
            params["thinking"] = {"type": "adaptive"}
            # Cannot use temperature with thinking enabled
        else:
            params["temperature"] = 0.15

        if tools:
            params["tools"] = tools

        return params

    def chat_stream_raw(
        self,
        system: str,
        messages: list[dict],
        thinking: bool = True,
        tools: Optional[list[dict]] = None,
        max_tokens: int = 16000,
    ):
        """Low-level Anthropic streaming that yields (event_type, data) tuples.

        Event types:
          - ("thinking", str) — thinking delta text
          - ("text", str) — text delta content
          - ("citation", dict) — citation delta object
          - ("server_tool_use_start", dict) — web search starting
          - ("web_search_result", dict) — web search results
          - ("tool_use_start", dict) — memory tool call starting
          - ("tool_use_end", dict) — memory tool call complete with input
          - ("usage", dict) — token usage data
          - ("stop", dict) — end of message with stop_reason
          - ("response", obj) — the final message object
        """
        system_blocks = self._build_system_blocks(system)
        params = self._build_anthropic_params(
            system_blocks, messages, max_tokens, thinking, tools
        )

        stream_ctx = self.client.messages.stream(**params)

        current_block_type = None
        current_tool_use = None
        tool_input_json = ""

        with stream_ctx as stream:
            for event in stream:
                etype = getattr(event, "type", None)

                if etype == "content_block_start":
                    block = event.content_block
                    btype = getattr(block, "type", None)
                    current_block_type = btype

                    if btype == "thinking":
                        pass  # deltas will follow
                    elif btype == "text":
                        pass  # deltas will follow
                    elif btype == "server_tool_use":
                        name = getattr(block, "name", "")
                        yield ("server_tool_use_start", {"name": name, "id": getattr(block, "id", "")})
                    elif btype == "web_search_tool_result":
                        pass  # will capture in stop
                    elif btype == "tool_use":
                        current_tool_use = {
                            "id": getattr(block, "id", ""),
                            "name": getattr(block, "name", ""),
                        }
                        tool_input_json = ""
                        yield ("tool_use_start", current_tool_use.copy())

                elif etype == "content_block_delta":
                    delta = event.delta
                    dtype = getattr(delta, "type", None)

                    if dtype == "thinking_delta":
                        yield ("thinking", getattr(delta, "thinking", ""))
                    elif dtype == "text_delta":
                        yield ("text", getattr(delta, "text", ""))
                    elif dtype == "citations_delta":
                        citation = getattr(delta, "citation", None)
                        if citation:
                            yield ("citation", _serialize_citation(citation))
                    elif dtype == "input_json_delta":
                        tool_input_json += getattr(delta, "partial_json", "")

                elif etype == "content_block_stop":
                    if current_block_type == "tool_use" and current_tool_use:
                        try:
                            parsed_input = json.loads(tool_input_json) if tool_input_json else {}
                        except json.JSONDecodeError:
                            parsed_input = {"raw": tool_input_json}
                        current_tool_use["input"] = parsed_input
                        yield ("tool_use_end", current_tool_use.copy())
                        current_tool_use = None
                        tool_input_json = ""
                    current_block_type = None

                elif etype == "message_delta":
                    stop_reason = getattr(event.delta, "stop_reason", None)
                    usage = getattr(event.usage, "__dict__", {}) if hasattr(event, "usage") else {}
                    if not usage and hasattr(event, "usage"):
                        usage = {
                            "output_tokens": getattr(event.usage, "output_tokens", 0),
                        }
                    yield ("stop", {"reason": stop_reason})

                elif etype == "message_start":
                    msg = getattr(event, "message", None)
                    if msg and hasattr(msg, "usage"):
                        u = msg.usage
                        yield ("usage", {
                            "input_tokens": getattr(u, "input_tokens", 0),
                            "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0),
                            "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0),
                        })

            # Get final message for complete usage
            try:
                final = stream.get_final_message()
                if final:
                    u = final.usage
                    yield ("usage", {
                        "input_tokens": getattr(u, "input_tokens", 0),
                        "output_tokens": getattr(u, "output_tokens", 0),
                        "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0),
                        "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0),
                    })
                    yield ("response", final)
            except Exception as e:
                logger.warning("Could not get final message: %s", e)


def _serialize_citation(citation) -> dict:
    """Convert an Anthropic citation object to a serializable dict."""
    result = {"type": getattr(citation, "type", "unknown")}
    for attr in ["source", "title", "cited_text", "document_index",
                 "document_title", "start_char_index", "end_char_index",
                 "start_block_index", "end_block_index",
                 "search_result_index", "url", "encrypted_index", "page_number"]:
        val = getattr(citation, attr, None)
        if val is not None:
            result[attr] = val
    return result


class QueryEngine:
    """Orchestrates the full RAG pipeline with advanced Claude API features."""

    def __init__(
        self,
        retriever: Retriever,
        llm_provider: str = "anthropic",
        llm_model: Optional[str] = None,
        llm_api_key: Optional[str] = None,
        fast_mode: bool = False,
    ):
        self.retriever = retriever
        self.llm = LLMClient(
            provider=llm_provider,
            model=llm_model,
            api_key=llm_api_key,
            fast_mode=fast_mode,
        )

    # ------------------------------------------------------------------
    # Non-streaming query (backward compatible, simpler path)
    # ------------------------------------------------------------------

    def query(
        self,
        query: str,
        competitor_filter: Optional[list[str]] = None,
        topic_filter: Optional[list[str]] = None,
        source_type_filter: Optional[list[str]] = None,
        n_results: int = 12,
        persona: Optional[str] = None,
        use_llm_knowledge: bool = False,
        use_web_search: bool = False,
        session_id: Optional[str] = None,
        username: Optional[str] = None,
        use_thinking: bool = True,
    ) -> QueryResult:
        """Execute a full RAG query pipeline (non-streaming)."""
        t_start = time.time()
        metadata: dict = {"timings": {}}

        t1 = time.time()
        analysis = self._analyze_query(query)
        metadata["timings"]["query_analysis_ms"] = int((time.time() - t1) * 1000)
        metadata["query_analysis"] = analysis

        competitors = competitor_filter or None
        topics = topic_filter or None
        source_types = source_type_filter or None

        t2 = time.time()
        chunks = self.retriever.retrieve(
            query=query,
            sub_queries=analysis.get("sub_queries"),
            hyde_passage=analysis.get("hyde_passage"),
            competitors=competitors,
            topics=topics,
            source_types=source_types,
            n_results=n_results,
        )
        metadata["timings"]["retrieval_ms"] = int((time.time() - t2) * 1000)
        metadata["chunks_retrieved"] = len(chunks)

        if not chunks:
            metadata["llm_provider"] = self.llm.provider
            metadata["llm_model"] = self.llm.model
            metadata["timings"]["total_ms"] = int((time.time() - t_start) * 1000)
            return QueryResult(
                query=query,
                answer="No relevant information was found in the competitive intelligence database.",
                citations=[],
                follow_up_questions=[],
                metadata=metadata,
            )

        citations = self._build_citations(chunks)

        t3 = time.time()
        system_prompt = self._build_system_prompt(persona, use_llm_knowledge, use_web_search)

        if self.llm.provider == "anthropic":
            # Use native citations via search_result blocks
            search_blocks = self._build_search_result_blocks(chunks)
            messages, _ = self._build_messages(query, search_blocks, session_id)
            thinking_enabled = use_thinking and not self.llm.fast_mode
            params = self.llm._build_anthropic_params(
                self.llm._build_system_blocks(system_prompt),
                messages, max_tokens=16000, thinking=thinking_enabled,
            )
            response = self.llm.client.messages.create(**params)
            answer = ""
            for block in response.content:
                if getattr(block, "type", None) == "text":
                    answer += block.text
        else:
            formatted_sources = self._format_sources_for_prompt(chunks, citations)
            answer = self.llm.chat(
                system=system_prompt,
                user=ANSWER_SYNTHESIS_USER.format(query=query),
                temperature=0.15, max_tokens=4096,
            )

        metadata["timings"]["synthesis_ms"] = int((time.time() - t3) * 1000)

        t4 = time.time()
        follow_ups = self._generate_follow_ups(query, answer)
        metadata["timings"]["followups_ms"] = int((time.time() - t4) * 1000)

        metadata["timings"]["total_ms"] = int((time.time() - t_start) * 1000)
        metadata["llm_provider"] = self.llm.provider
        metadata["llm_model"] = self.llm.model

        return QueryResult(
            query=query, answer=answer, citations=citations,
            follow_up_questions=follow_ups, metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Streaming query with full Claude API features
    # ------------------------------------------------------------------

    def query_stream(
        self,
        query: str,
        competitor_filter: Optional[list[str]] = None,
        topic_filter: Optional[list[str]] = None,
        source_type_filter: Optional[list[str]] = None,
        n_results: int = 12,
        persona: Optional[str] = None,
        use_llm_knowledge: bool = False,
        use_web_search: bool = False,
        session_id: Optional[str] = None,
        username: Optional[str] = None,
        use_thinking: bool = True,
    ):
        """Generator yielding SSE-formatted strings with full Claude API features."""
        import json as _json
        from dataclasses import asdict

        t_start = time.time()
        metadata: dict = {"timings": {}}

        def sse(event: str, data) -> str:
            return f"event: {event}\ndata: {_json.dumps(data)}\n\n"

        # Step 1: Query Analysis
        yield sse("status", {"step": "analyzing", "message": "Analyzing query..."})
        t1 = time.time()
        analysis = self._analyze_query(query)
        metadata["timings"]["query_analysis_ms"] = int((time.time() - t1) * 1000)
        metadata["query_analysis"] = analysis
        yield sse("status", {"step": "analyzing_done", "ms": metadata["timings"]["query_analysis_ms"]})

        competitors = competitor_filter or None
        topics = topic_filter or None
        source_types = source_type_filter or None

        # Step 2: Retrieval
        yield sse("status", {"step": "retrieving", "message": "Searching vector database..."})
        t2 = time.time()
        chunks = self.retriever.retrieve(
            query=query,
            sub_queries=analysis.get("sub_queries"),
            hyde_passage=analysis.get("hyde_passage"),
            competitors=competitors,
            topics=topics,
            source_types=source_types,
            n_results=n_results,
        )
        metadata["timings"]["retrieval_ms"] = int((time.time() - t2) * 1000)
        metadata["chunks_retrieved"] = len(chunks)
        yield sse("status", {"step": "retrieving_done", "ms": metadata["timings"]["retrieval_ms"], "chunks": len(chunks)})

        if not chunks:
            metadata["llm_provider"] = self.llm.provider
            metadata["llm_model"] = self.llm.model
            metadata["timings"]["total_ms"] = int((time.time() - t_start) * 1000)
            yield sse("token", {"text": "No relevant information was found in the competitive intelligence database."})
            yield sse("metadata", metadata)
            yield sse("done", {})
            return

        # Step 3: Build citations and emit source metadata
        citations = self._build_citations(chunks)
        yield sse("citations_sources", [asdict(c) for c in citations])

        # Step 4: Stream synthesis
        yield sse("status", {"step": "synthesizing", "message": "Thinking and synthesizing..."})
        t3 = time.time()
        system_prompt = self._build_system_prompt(
            persona, use_llm_knowledge, use_web_search,
            has_conversation_history=bool(session_id),
        )

        usage_data = {}
        history_count = 0

        if self.llm.provider == "anthropic":
            # Build tools list
            tools = []
            if use_web_search:
                tools.append({
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 5,
                })
            if username:
                tools.append({
                    "type": "memory_20250818",
                    "name": "memory",
                })

            # Build messages with search_result blocks for native citations
            search_blocks = self._build_search_result_blocks(chunks)
            messages, history_count = self._build_messages(query, search_blocks, session_id)

            # Tool use loop (for memory tool)
            max_iterations = 5
            full_answer_parts = []

            for iteration in range(max_iterations):
                stop_reason = None
                assistant_content_blocks = []

                use_thinking = use_thinking and not self.llm.fast_mode
                stream_max_tokens = 8000 if self.llm.fast_mode else 16000
                for event_type, data in self.llm.chat_stream_raw(
                    system=system_prompt,
                    messages=messages,
                    thinking=use_thinking,
                    tools=tools if tools else None,
                    max_tokens=stream_max_tokens,
                ):
                    if event_type == "thinking":
                        yield sse("thinking", {"text": data})
                    elif event_type == "text":
                        full_answer_parts.append(data)
                        yield sse("token", {"text": data})
                    elif event_type == "citation":
                        yield sse("citation_delta", data)
                    elif event_type == "server_tool_use_start":
                        yield sse("status", {"step": "web_searching", "message": "Searching the web..."})
                    elif event_type == "web_search_result":
                        yield sse("web_search_result", data)
                    elif event_type == "tool_use_start":
                        yield sse("status", {"step": "memory_tool", "message": f"Using memory: {data.get('name', '')}..."})
                    elif event_type == "tool_use_end":
                        assistant_content_blocks.append(data)
                    elif event_type == "usage":
                        usage_data.update(data)
                    elif event_type == "stop":
                        stop_reason = data.get("reason")
                    elif event_type == "response":
                        # Capture the full response for tool use continuation
                        if hasattr(data, "content"):
                            assistant_content_blocks = data.content

                if stop_reason != "tool_use":
                    break

                # Execute memory tool calls and continue
                tool_results = []
                raw_blocks = []
                for block in assistant_content_blocks:
                    if hasattr(block, "type"):
                        raw_blocks.append(block)
                        if block.type == "tool_use" and block.name == "memory":
                            result_text = self._execute_memory_tool(block.input, username)
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result_text,
                            })
                    elif isinstance(block, dict) and block.get("name") == "memory":
                        result_text = self._execute_memory_tool(block.get("input", {}), username)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.get("id", ""),
                            "content": result_text,
                        })

                if tool_results:
                    messages.append({"role": "assistant", "content": raw_blocks})
                    messages.append({"role": "user", "content": tool_results})
                    yield sse("status", {"step": "memory_done", "message": "Memory updated"})
                else:
                    break

        else:
            # OpenAI fallback: use simple streaming with [N] citation prompt
            formatted_sources = self._format_sources_for_prompt(chunks, citations)
            full_answer_parts = []
            for text_chunk in self.llm.chat_stream(
                system=system_prompt,
                user=ANSWER_SYNTHESIS_USER.format(query=query),
                temperature=0.15,
                max_tokens=4096,
            ):
                full_answer_parts.append(text_chunk)
                yield sse("token", {"text": text_chunk})

        metadata["timings"]["synthesis_ms"] = int((time.time() - t3) * 1000)
        yield sse("status", {"step": "synthesizing_done", "ms": metadata["timings"]["synthesis_ms"]})

        # Step 5: Follow-up questions
        yield sse("status", {"step": "followups", "message": "Generating follow-ups..."})
        t4 = time.time()
        answer = "".join(full_answer_parts)
        follow_ups = self._generate_follow_ups(query, answer)
        metadata["timings"]["followups_ms"] = int((time.time() - t4) * 1000)
        yield sse("followups", follow_ups)

        # Usage data
        if usage_data:
            yield sse("usage", usage_data)

        # Final metadata
        metadata["timings"]["total_ms"] = int((time.time() - t_start) * 1000)
        metadata["llm_provider"] = self.llm.provider
        metadata["llm_model"] = self.llm.model
        metadata["history_messages_included"] = history_count
        yield sse("metadata", metadata)
        yield sse("done", {})

    # ------------------------------------------------------------------
    # Search result blocks for native citations
    # ------------------------------------------------------------------

    def _build_search_result_blocks(self, chunks: list[RetrievedChunk]) -> list[dict]:
        """Convert retrieved chunks to Anthropic search_result content blocks."""
        blocks = []
        for chunk in chunks:
            blocks.append({
                "type": "search_result",
                "source": chunk.source_url or f"vectordb://{chunk.chunk_id}",
                "title": f"{chunk.source_title} ({chunk.source_type}, {chunk.credibility})",
                "content": [{"type": "text", "text": chunk.text}],
                "citations": {"enabled": True},
            })
        return blocks

    def _build_messages(
        self,
        query: str,
        search_blocks: list[dict],
        session_id: Optional[str] = None,
        max_history_tokens: int = 4000,
    ) -> tuple[list[dict], int]:
        """Build the messages array with token-aware conversation history.

        Returns (messages, history_count) where history_count is the number
        of history messages actually included after compaction.
        """
        messages = []
        history_count = 0

        # Load conversation history if we have a session (with token-aware compaction)
        if session_id:
            try:
                from webapp.sessions import SessionManager
                mgr = SessionManager()
                recent = mgr.get_recent_messages(session_id, limit=10)
                # Token-aware trimming: estimate ~4 chars per token, include newest first
                token_budget = max_history_tokens
                included = []
                for msg in reversed(recent):  # newest first
                    est_tokens = len(msg["content"]) // 4
                    if token_budget - est_tokens < 0 and included:
                        break
                    token_budget -= est_tokens
                    included.insert(0, msg)  # maintain chronological order
                for msg in included:
                    messages.append({
                        "role": msg["role"],
                        "content": msg["content"],
                    })
                history_count = len(included)
                # Add cache_control to last history message for prompt caching
                if messages:
                    last = messages[-1]
                    if isinstance(last["content"], str):
                        messages[-1]["content"] = [{
                            "type": "text",
                            "text": last["content"],
                            "cache_control": {"type": "ephemeral"},
                        }]
            except Exception as e:
                logger.warning("Failed to load conversation history: %s", e)

        # Current query with search result blocks
        content = [
            *search_blocks,
            {"type": "text", "text": ANSWER_SYNTHESIS_USER.format(query=query)},
        ]
        messages.append({"role": "user", "content": content})

        return messages, history_count

    # ------------------------------------------------------------------
    # Memory tool execution
    # ------------------------------------------------------------------

    def _execute_memory_tool(self, tool_input: dict, username: str) -> str:
        """Execute a memory tool command with path traversal protection."""
        if not username or not re.match(r'^[a-zA-Z0-9_\-]+$', username):
            return "Error: invalid username"

        memory_dir = PROJECT_ROOT / "data" / "memories" / username

        command = tool_input.get("command", "")
        file_path = tool_input.get("path", "")

        def safe_resolve(rel_path: str) -> Optional[Path]:
            """Resolve path safely within memory_dir."""
            if not rel_path:
                return memory_dir
            # Strip leading /memories/ prefix that Claude may include
            rel_path = re.sub(r'^/?memories/?', '', rel_path)
            resolved = (memory_dir / rel_path).resolve()
            if not str(resolved).startswith(str(memory_dir.resolve())):
                return None
            return resolved

        try:
            if command == "view":
                target = safe_resolve(file_path)
                if target is None:
                    return "Error: path traversal detected"
                if not target.exists():
                    if target == memory_dir:
                        return "No memories stored yet."
                    return f"File not found: {file_path}"
                if target.is_dir():
                    files = [f.name for f in target.iterdir() if f.is_file()]
                    return "\n".join(files) if files else "Directory is empty."
                content = target.read_text()
                return content[:10000]  # Limit to 10KB

            elif command == "create":
                memory_dir.mkdir(parents=True, exist_ok=True)
                target = safe_resolve(file_path)
                if target is None:
                    return "Error: path traversal detected"
                # Check limits
                if memory_dir.exists():
                    existing = list(memory_dir.glob("*"))
                    if len(existing) >= 100:
                        return "Error: memory limit reached (100 files max)"
                file_text = tool_input.get("file_text", "")
                if len(file_text) > 10000:
                    return "Error: file too large (10KB max)"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(file_text)
                return f"Created {file_path}"

            elif command == "str_replace":
                target = safe_resolve(file_path)
                if target is None:
                    return "Error: path traversal detected"
                if not target.exists():
                    return f"File not found: {file_path}"
                old_str = tool_input.get("old_str", "")
                new_str = tool_input.get("new_str", "")
                text = target.read_text()
                if old_str not in text:
                    return f"String not found in {file_path}"
                target.write_text(text.replace(old_str, new_str, 1))
                return f"Updated {file_path}"

            elif command == "delete":
                target = safe_resolve(file_path)
                if target is None:
                    return "Error: path traversal detected"
                if not target.exists():
                    return f"File not found: {file_path}"
                if target.is_file():
                    target.unlink()
                    return f"Deleted {file_path}"
                return "Cannot delete directories"

            else:
                return f"Unknown command: {command}"

        except Exception as e:
            logger.warning("Memory tool error: %s", e)
            return f"Error: {str(e)}"

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    def _build_system_prompt(
        self,
        persona: Optional[str] = None,
        use_llm_knowledge: bool = False,
        use_web_search: bool = False,
        has_conversation_history: bool = False,
    ) -> str:
        """Build the synthesis system prompt with persona and augmentation modifiers."""
        prompt = ANSWER_SYNTHESIS_SYSTEM
        if persona and persona in PERSONA_MODIFIERS:
            prompt += "\n\n" + PERSONA_MODIFIERS[persona]
        if use_llm_knowledge:
            prompt += LLM_KNOWLEDGE_SUPPLEMENT
        if use_web_search:
            prompt += WEB_SEARCH_SUPPLEMENT
        if has_conversation_history:
            prompt += CONVERSATION_CONTEXT_INSTRUCTION
        return prompt

    # ------------------------------------------------------------------
    # Query analysis
    # ------------------------------------------------------------------

    def _analyze_query(self, query: str) -> dict:
        """Use LLM to decompose and analyze the query."""
        try:
            raw = self.llm.chat(
                system=QUERY_ANALYSIS_SYSTEM,
                user=QUERY_ANALYSIS_USER.format(query=query),
                temperature=0.1,
                max_tokens=1024,
            )
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned.rsplit("```", 1)[0]
            cleaned = cleaned.strip()
            return json.loads(cleaned)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("Query analysis failed, using defaults: %s", e)
            return {
                "intent": "exploratory",
                "competitors_mentioned": [],
                "topics": [],
                "sub_queries": [query],
                "hyde_passage": None,
                "source_type_hints": [],
                "reasoning": "Analysis failed, using original query",
            }

    # ------------------------------------------------------------------
    # Citations
    # ------------------------------------------------------------------

    def _build_citations(self, chunks: list[RetrievedChunk]) -> list[Citation]:
        """Build citation objects from retrieved chunks."""
        citations = []
        seen_parents = set()

        for i, chunk in enumerate(chunks):
            key = (chunk.parent_doc_id, chunk.source_url)
            if key in seen_parents:
                continue
            seen_parents.add(key)

            preview = chunk.text[:200].replace("\n", " ").strip()
            if len(chunk.text) > 200:
                preview += "..."

            citations.append(Citation(
                index=len(citations) + 1,
                chunk_id=chunk.chunk_id,
                source_title=chunk.source_title,
                source_url=chunk.source_url,
                source_type=chunk.source_type,
                credibility=chunk.credibility,
                competitor=chunk.competitor,
                primary_topic=chunk.primary_topic,
                content_date=chunk.content_date,
                text_preview=preview,
            ))

        return citations

    def _format_sources_for_prompt(
        self,
        chunks: list[RetrievedChunk],
        citations: list[Citation],
    ) -> str:
        """Format retrieved chunks as numbered sources (OpenAI fallback)."""
        citation_map: dict[tuple, int] = {}
        for c in citations:
            key = (
                next((ch.parent_doc_id for ch in chunks if ch.chunk_id == c.chunk_id), ""),
                c.source_url,
            )
            citation_map[key] = c.index

        lines = []
        for chunk in chunks:
            key = (chunk.parent_doc_id, chunk.source_url)
            idx = citation_map.get(key, "?")
            lines.append(
                f"[{idx}] **{chunk.source_title}** "
                f"({chunk.source_type}, {chunk.credibility}, {chunk.competitor})\n"
                f"    URL: {chunk.source_url}\n"
                f"    Content:\n{chunk.text}\n"
            )
        return "\n---\n".join(lines)

    # ------------------------------------------------------------------
    # Follow-up generation
    # ------------------------------------------------------------------

    def _generate_follow_ups(self, query: str, answer: str) -> list[str]:
        """Generate follow-up questions based on the conversation."""
        try:
            raw = self.llm.chat(
                system=FOLLOWUP_SYSTEM,
                user=f"Original question: {query}\n\nAnswer provided:\n{answer[:1500]}",
                temperature=0.5,
                max_tokens=512,
            )
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned.rsplit("```", 1)[0]
            cleaned = cleaned.strip()
            return json.loads(cleaned)
        except Exception as e:
            logger.warning("Follow-up generation failed: %s", e)
            return []
