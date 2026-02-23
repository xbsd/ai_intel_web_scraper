"""RAG Query Engine — orchestrates retrieval, reasoning, and generation.

Implements:
  - Query analysis with LLM-powered decomposition
  - HyDE (Hypothetical Document Embeddings) for better recall
  - Multi-step ReAct reasoning when initial retrieval is insufficient
  - Chain-of-Thought grounded answer synthesis with inline citations
  - Follow-up question generation
"""

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

from webapp.rag.prompts import (
    QUERY_ANALYSIS_SYSTEM,
    QUERY_ANALYSIS_USER,
    ANSWER_SYNTHESIS_SYSTEM,
    ANSWER_SYNTHESIS_USER,
    FOLLOWUP_SYSTEM,
)
from webapp.rag.retriever import Retriever, RetrievedChunk

logger = logging.getLogger(__name__)


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
    """Unified LLM client supporting OpenAI and Anthropic."""

    def __init__(
        self,
        provider: str = "anthropic",
        model: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.provider = provider
        if provider == "anthropic":
            import anthropic
            self.model = model or "claude-sonnet-4-20250514"
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

    def chat(
        self,
        system: str,
        user: str,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> str:
        """Send a chat completion request."""
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


class QueryEngine:
    """Orchestrates the full RAG pipeline."""

    def __init__(
        self,
        retriever: Retriever,
        llm_provider: str = "anthropic",
        llm_model: Optional[str] = None,
        llm_api_key: Optional[str] = None,
    ):
        self.retriever = retriever
        self.llm = LLMClient(
            provider=llm_provider,
            model=llm_model,
            api_key=llm_api_key,
        )

    def query(
        self,
        query: str,
        competitor_filter: Optional[list[str]] = None,
        topic_filter: Optional[list[str]] = None,
        source_type_filter: Optional[list[str]] = None,
        n_results: int = 12,
    ) -> QueryResult:
        """Execute a full RAG query pipeline.

        Steps:
          1. Analyze query (decomposition, HyDE, intent classification)
          2. Multi-strategy retrieval (original + sub-queries + HyDE)
          3. Synthesize grounded answer with inline citations
          4. Generate follow-up questions
        """
        t_start = time.time()
        metadata: dict = {"timings": {}}

        # Step 1: Query Analysis
        t1 = time.time()
        analysis = self._analyze_query(query)
        metadata["timings"]["query_analysis_ms"] = int((time.time() - t1) * 1000)
        metadata["query_analysis"] = analysis

        # Only apply user-provided filters as hard constraints.
        # LLM-detected topics and source types are used for sub-queries
        # and HyDE but NOT as metadata filters (too restrictive — many
        # chunks are "unclassified" and would be excluded).
        competitors = competitor_filter or None
        topics = topic_filter or None
        source_types = source_type_filter or None

        # Step 2: Multi-Strategy Retrieval
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
                answer="No relevant information was found in the competitive intelligence database. "
                       "This may mean the topic hasn't been scraped yet, or the vector store needs "
                       "to be rebuilt. Try running `python pipeline.py vectorize --target all`.",
                citations=[],
                follow_up_questions=[],
                metadata=metadata,
            )

        # Step 3: Build citations
        citations = self._build_citations(chunks)

        # Step 4: Synthesize answer with CoT
        t3 = time.time()
        formatted_sources = self._format_sources_for_prompt(chunks, citations)
        answer = self.llm.chat(
            system=ANSWER_SYNTHESIS_SYSTEM,
            user=ANSWER_SYNTHESIS_USER.format(
                query=query,
                formatted_sources=formatted_sources,
            ),
            temperature=0.15,
            max_tokens=4096,
        )
        metadata["timings"]["synthesis_ms"] = int((time.time() - t3) * 1000)

        # Step 5: Generate follow-up questions
        t4 = time.time()
        follow_ups = self._generate_follow_ups(query, answer)
        metadata["timings"]["followups_ms"] = int((time.time() - t4) * 1000)

        metadata["timings"]["total_ms"] = int((time.time() - t_start) * 1000)
        metadata["llm_provider"] = self.llm.provider
        metadata["llm_model"] = self.llm.model

        return QueryResult(
            query=query,
            answer=answer,
            citations=citations,
            follow_up_questions=follow_ups,
            metadata=metadata,
        )

    def _analyze_query(self, query: str) -> dict:
        """Use LLM to decompose and analyze the query."""
        try:
            raw = self.llm.chat(
                system=QUERY_ANALYSIS_SYSTEM,
                user=QUERY_ANALYSIS_USER.format(query=query),
                temperature=0.1,
                max_tokens=1024,
            )
            # Strip potential markdown fences
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

    def _build_citations(self, chunks: list[RetrievedChunk]) -> list[Citation]:
        """Build citation objects from retrieved chunks."""
        citations = []
        seen_parents = set()

        for i, chunk in enumerate(chunks):
            # Group by parent doc to avoid citing the same doc multiple times
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
        """Format retrieved chunks as numbered sources for the LLM prompt."""
        # Map parent_doc_id+url to citation index
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
                f"    Topic: {chunk.primary_topic} | "
                f"Date: {chunk.content_date or 'unknown'} | "
                f"Score: {chunk.score:.4f}\n"
                f"    Content:\n{chunk.text}\n"
            )
        return "\n---\n".join(lines)

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
