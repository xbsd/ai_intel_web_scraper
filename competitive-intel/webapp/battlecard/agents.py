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


# ---------------------------------------------------------------------------
# Client Name Disambiguation
# ---------------------------------------------------------------------------


def lookup_client(query: str) -> list[dict]:
    """Look up a client name and return potential company matches.

    Uses Claude with web search to disambiguate company names and return
    structured information about matching companies.
    """
    import anthropic

    client = anthropic.Anthropic()

    prompt = f"""I need to identify which company the user means by "{query}".

This is for a competitive intelligence platform focused on capital markets,
investment banking, quantitative finance, and database technology.

Search for companies matching "{query}" and return the top 3-5 most likely matches.

Return as JSON:
{{
  "matches": [
    {{
      "name": "Full Official Company Name",
      "description": "One-line company description",
      "industry": "Industry classification (e.g. Tier 1 Investment Bank, Hedge Fund, Exchange)",
      "headquarters": "City, Country",
      "ticker": "Stock ticker if publicly traded, empty string otherwise",
      "employees": "Approximate employee count (e.g. '50,000+', '500-1000')",
      "relevance": "Why this company would be relevant in a capital markets / database technology context",
      "logo_url": ""
    }}
  ]
}}

Prioritize financial services companies, technology companies, and database vendors.
If the name clearly identifies a single well-known company, still return it as the
only match so the user can confirm. Return ONLY valid JSON."""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            tools=[
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 3,
                }
            ],
            messages=[{"role": "user", "content": prompt}],
        )

        text = ""
        for block in response.content:
            if getattr(block, "type", None) == "text":
                text += block.text

        data = _parse_json_safe(text)
        return data.get("matches", [])

    except Exception as e:
        logger.error("Client lookup failed: %s", e)
        return [{"name": query, "description": "Could not look up — using as entered", "industry": "", "headquarters": "", "ticker": "", "employees": "", "relevance": "", "logo_url": ""}]


def _parse_json_safe(text: str) -> dict:
    """Extract JSON from LLM response text."""
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
    return {}


# ---------------------------------------------------------------------------
# Client Intelligence Agent
# ---------------------------------------------------------------------------


class ClientIntelligenceAgent:
    """Gathers current intelligence about the client company.

    Searches for recent news, AI/database initiatives, technology stack,
    and strategic priorities to make the battle card more targeted.
    """

    def gather(self, client_name: str, client_industry: str = "") -> AgentResult:
        """Search for current intelligence about the client company."""
        try:
            import anthropic

            client = anthropic.Anthropic()

            prompt = f"""Research the company "{client_name}" {f'(industry: {client_industry})' if client_industry else ''} and gather current competitive intelligence.

Focus on:
1. Company overview and current strategic direction
2. Recent news (last 6 months) — especially AI initiatives, database/technology migrations, digital transformation
3. Their current technology stack for data/analytics (do they use kdb+, Oracle, Hadoop, Snowflake, etc.?)
4. Key business priorities and challenges
5. Any known database or time-series analytics pain points
6. Leadership changes relevant to technology decisions
7. Recent earnings calls mentions of technology investments

Return as JSON:
{{
  "company_overview": "2-3 sentence overview of the company and their business",
  "recent_news": [
    {{"headline": "...", "date": "YYYY-MM-DD", "source": "Publication name", "category": "AI Initiative|Database Migration|Leadership|Partnership|Financial|Technology", "summary": "Brief summary of the news item"}}
  ],
  "ai_db_initiatives": "Summary of their AI and database technology initiatives. What are they investing in? Any known migrations or evaluations?",
  "technology_stack": "Known technology stack details, especially for data analytics, time-series, and trading systems",
  "key_priorities": ["priority1", "priority2", "priority3"],
  "potential_pain_points": ["pain point that KX could address", "another pain point"]
}}

Only include verifiable information. Do not fabricate. Return ONLY valid JSON."""

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

            data = _parse_json_safe(text)
            news_count = len(data.get("recent_news", []))

            return AgentResult(
                agent_name="Client Intelligence",
                data=data,
                sources_count=news_count + (1 if data.get("company_overview") else 0),
            )

        except Exception as e:
            logger.error("ClientIntelligenceAgent failed: %s", e)
            return AgentResult(
                agent_name="Client Intelligence",
                data={
                    "company_overview": "",
                    "recent_news": [],
                    "ai_db_initiatives": "",
                    "technology_stack": "",
                    "key_priorities": [],
                    "potential_pain_points": [],
                },
                error=str(e),
            )


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
