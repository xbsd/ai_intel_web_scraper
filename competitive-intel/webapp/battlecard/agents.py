"""Intelligence gathering agents for battle card generation.

Each agent specializes in a different data source and returns structured
intelligence that the orchestrator synthesizes into a battle card.
"""

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent


@dataclass
class AgentResult:
    """Result from an intelligence gathering agent."""

    agent_name: str
    data: dict
    sources_count: int = 0
    error: Optional[str] = None


class InternalKBAgent:
    """Queries the ChromaDB vector store for existing competitive intelligence."""

    def __init__(self):
        from vectorstore.store import VectorStore
        from vectorstore.embedder import Embedder

        self.store = VectorStore()
        openai_key = os.getenv("OPENAI_API_KEY", "")
        self.embedder = Embedder(api_key=openai_key or None)

    def gather(
        self,
        competitor: str,
        use_case: str = "",
        topics: Optional[list[str]] = None,
    ) -> AgentResult:
        """Query vector store for competitive intelligence."""
        try:
            queries = self._build_queries(competitor, use_case)
            all_chunks = []
            seen_ids = set()

            for query_text in queries:
                where = {"competitor": competitor} if competitor else None
                results = self.store.query_by_text(
                    query_text=query_text,
                    embedder=self.embedder,
                    n_results=8,
                    where=where,
                )

                for i, (doc_id, doc, meta) in enumerate(
                    zip(
                        results.get("ids", [[]])[0],
                        results.get("documents", [[]])[0],
                        results.get("metadatas", [[]])[0],
                    )
                ):
                    if doc_id not in seen_ids:
                        seen_ids.add(doc_id)
                        all_chunks.append(
                            {
                                "text": doc[:1500],
                                "source_title": meta.get("source_title", ""),
                                "source_type": meta.get("source_type", ""),
                                "source_url": meta.get("source_url", ""),
                                "competitor": meta.get("competitor", ""),
                                "primary_topic": meta.get("primary_topic", ""),
                                "credibility": meta.get("credibility", ""),
                            }
                        )

            # Also query for KX strengths
            kx_queries = [
                f"kdb+ advantages over {competitor}",
                f"KX performance benchmarks vs {competitor}",
                "kdb+ time-series analytics capabilities strengths",
            ]
            for query_text in kx_queries:
                results = self.store.query_by_text(
                    query_text=query_text,
                    embedder=self.embedder,
                    n_results=5,
                    where={"competitor": "kx"},
                )
                for i, (doc_id, doc, meta) in enumerate(
                    zip(
                        results.get("ids", [[]])[0],
                        results.get("documents", [[]])[0],
                        results.get("metadatas", [[]])[0],
                    )
                ):
                    if doc_id not in seen_ids:
                        seen_ids.add(doc_id)
                        all_chunks.append(
                            {
                                "text": doc[:1500],
                                "source_title": meta.get("source_title", ""),
                                "source_type": meta.get("source_type", ""),
                                "source_url": meta.get("source_url", ""),
                                "competitor": meta.get("competitor", ""),
                                "primary_topic": meta.get("primary_topic", ""),
                                "credibility": meta.get("credibility", ""),
                            }
                        )

            return AgentResult(
                agent_name="Internal Knowledge Base",
                data={
                    "chunks": all_chunks[:40],
                    "total_found": len(all_chunks),
                },
                sources_count=len(all_chunks),
            )

        except Exception as e:
            logger.error("InternalKBAgent failed: %s", e)
            return AgentResult(
                agent_name="Internal Knowledge Base",
                data={"chunks": []},
                error=str(e),
            )

    def _build_queries(self, competitor: str, use_case: str) -> list[str]:
        queries = [
            f"{competitor} limitations weaknesses",
            f"{competitor} performance benchmarks latency",
            f"{competitor} high availability replication",
            f"{competitor} architecture storage engine",
            f"{competitor} vs kdb+ comparison",
            f"{competitor} security compliance enterprise",
        ]
        use_case_map = {
            "alpha_generation": f"{competitor} alpha generation quantitative trading",
            "order_book_analytics": f"{competitor} order book level 2 market data",
            "tick_to_trade": f"{competitor} tick-to-trade latency throughput",
            "risk_management": f"{competitor} risk management real-time analytics",
            "agentic_ai": f"{competitor} AI ML vector integration agentic",
        }
        if use_case in use_case_map:
            queries.insert(0, use_case_map[use_case])
        return queries


class BenchmarkAgent:
    """Gathers benchmark and performance data via web search."""

    def gather(self, competitor: str, use_case: str = "") -> AgentResult:
        """Search for benchmark data comparing KX vs competitor."""
        try:
            import anthropic

            client = anthropic.Anthropic()

            prompt = f"""Search for the latest independent benchmark comparisons between kdb+/KX and {competitor} for time-series database workloads.

Focus on:
1. STAC-M3 benchmark results (if available)
2. TSBS (Time Series Benchmark Suite) results
3. ClickBench results
4. Any independent third-party performance comparisons
5. Ingestion throughput benchmarks (rows/second)
6. Query latency benchmarks (especially for time-series analytics)
{"7. Specific benchmarks for " + use_case.replace("_", " ") if use_case else ""}

Return the data as a JSON object with this structure:
{{
  "benchmarks": [
    {{"metric": "Query Latency (100-user volume curve)", "kx_value": "0.3ms", "competitor_value": "12ms", "source": "STAC-M3 2024"}},
    ...
  ],
  "summary": "Brief summary of benchmark landscape",
  "sources": ["url1", "url2"]
}}

Only include data you can find evidence for. Do not make up numbers."""

            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=2048,
                tools=[
                    {
                        "type": "web_search_20250305",
                        "name": "web_search",
                        "max_uses": 5,
                    }
                ],
                messages=[{"role": "user", "content": prompt}],
            )

            # Extract text from response
            text = ""
            for block in response.content:
                if getattr(block, "type", None) == "text":
                    text += block.text

            data = self._parse_json(text)
            return AgentResult(
                agent_name="Financial Benchmark",
                data=data,
                sources_count=len(data.get("benchmarks", [])),
            )

        except Exception as e:
            logger.error("BenchmarkAgent failed: %s", e)
            return AgentResult(
                agent_name="Financial Benchmark",
                data={"benchmarks": [], "summary": "", "sources": []},
                error=str(e),
            )

    def _parse_json(self, text: str) -> dict:
        import re

        match = re.search(r"```(?:json)?\s*\n([\s\S]*?)\n```", text)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return {"benchmarks": [], "summary": text[:500], "sources": []}


class DeveloperSentimentAgent:
    """Scrapes developer sentiment from GitHub issues, Reddit, HN, forums."""

    def gather(self, competitor: str) -> AgentResult:
        """Search for developer complaints and sentiment about the competitor."""
        try:
            import anthropic

            client = anthropic.Anthropic()

            prompt = f"""Search for recent developer complaints, issues, and sentiment about {competitor} database.

Look for:
1. Open GitHub issues reporting bugs or architecture limitations
2. Reddit posts (r/algotrading, r/quant, r/databases) complaining about {competitor}
3. HackerNews discussions about {competitor} problems
4. StackOverflow questions about {competitor} limitations
5. Wilmott forum discussions about {competitor}

Return as JSON:
{{
  "complaints": [
    {{"issue": "Memory leaks under high concurrency", "source": "GitHub Issue #1234", "severity": "high", "url": "..."}},
    ...
  ],
  "positive_sentiment": [
    {{"point": "Easy SQL interface", "source": "Reddit", "url": "..."}}
  ],
  "developer_concerns": ["concern1", "concern2"],
  "summary": "Overall sentiment summary"
}}

Only include real findings with sources. Do not fabricate."""

            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=2048,
                tools=[
                    {
                        "type": "web_search_20250305",
                        "name": "web_search",
                        "max_uses": 5,
                    }
                ],
                messages=[{"role": "user", "content": prompt}],
            )

            text = ""
            for block in response.content:
                if getattr(block, "type", None) == "text":
                    text += block.text

            data = self._parse_json(text)
            return AgentResult(
                agent_name="Developer Sentiment",
                data=data,
                sources_count=len(data.get("complaints", []))
                + len(data.get("positive_sentiment", [])),
            )

        except Exception as e:
            logger.error("DeveloperSentimentAgent failed: %s", e)
            return AgentResult(
                agent_name="Developer Sentiment",
                data={
                    "complaints": [],
                    "positive_sentiment": [],
                    "developer_concerns": [],
                    "summary": "",
                },
                error=str(e),
            )

    def _parse_json(self, text: str) -> dict:
        import re

        match = re.search(r"```(?:json)?\s*\n([\s\S]*?)\n```", text)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return {
            "complaints": [],
            "positive_sentiment": [],
            "developer_concerns": [],
            "summary": text[:500],
        }


class MarketNewsAgent:
    """Pulls recent press releases, funding news, and market activity."""

    def gather(self, competitor: str) -> AgentResult:
        """Search for recent market news about the competitor."""
        try:
            import anthropic

            client = anthropic.Anthropic()

            prompt = f"""Search for the most recent news and developments about {competitor} (database company) from the last 90 days.

Focus on:
1. Funding rounds or acquisitions
2. New product releases or major version updates
3. New partnerships or customer wins
4. Key executive hires or departures
5. Analyst reports or market positioning changes
6. Any controversy, outages, or security incidents

Return as JSON:
{{
  "news_items": [
    {{"headline": "...", "date": "2025-01-15", "source": "TechCrunch", "url": "...", "implication": "What this means for the competitive landscape"}},
    ...
  ],
  "funding_status": "Latest known funding round and valuation",
  "recent_releases": ["version X.Y with feature Z"],
  "key_hires": ["Name - Role"],
  "summary": "Brief competitive implications summary"
}}

Only include verifiable news with sources. Do not fabricate."""

            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=2048,
                tools=[
                    {
                        "type": "web_search_20250305",
                        "name": "web_search",
                        "max_uses": 5,
                    }
                ],
                messages=[{"role": "user", "content": prompt}],
            )

            text = ""
            for block in response.content:
                if getattr(block, "type", None) == "text":
                    text += block.text

            data = self._parse_json(text)
            return AgentResult(
                agent_name="Market News",
                data=data,
                sources_count=len(data.get("news_items", [])),
            )

        except Exception as e:
            logger.error("MarketNewsAgent failed: %s", e)
            return AgentResult(
                agent_name="Market News",
                data={
                    "news_items": [],
                    "funding_status": "",
                    "recent_releases": [],
                    "key_hires": [],
                    "summary": "",
                },
                error=str(e),
            )

    def _parse_json(self, text: str) -> dict:
        import re

        match = re.search(r"```(?:json)?\s*\n([\s\S]*?)\n```", text)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return {
            "news_items": [],
            "funding_status": "",
            "recent_releases": [],
            "key_hires": [],
            "summary": text[:500],
        }
