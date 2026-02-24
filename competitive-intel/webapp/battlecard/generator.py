"""Battle card generation orchestrator.

Coordinates multiple intelligence agents in parallel, then synthesizes
their findings into a structured BattleCardReport using Claude.
"""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import anthropic

from webapp.battlecard.agents import (
    AgentResult,
    BenchmarkAgent,
    DeveloperSentimentAgent,
    InternalKBAgent,
    MarketNewsAgent,
)
from webapp.battlecard.models import (
    AgentType,
    BattleCardReport,
    BattleCardRequest,
    BenchmarkDataPoint,
    CompetitorNewsItem,
    FeatureComparison,
    ObjectionHandler,
    PainPoint,
    TrapQuestion,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent

SYNTHESIS_SYSTEM_PROMPT = """You are an elite competitive intelligence analyst at KX, the company behind kdb+ — the world's fastest time-series database used by every major investment bank and quantitative hedge fund.

You produce McKinsey-quality sales battle cards that arm enterprise sales reps with devastating competitive advantages. Your output must be factual, evidence-based, and grounded in the source data provided.

CRITICAL RULES:
- Only make claims you can support with the provided intelligence data
- Clearly distinguish between verified facts and inferred assessments
- Use precise technical language for the "Highly Technical" tone
- Use business-value language for the "Executive/Business Value" tone
- Focus on capital markets / quantitative finance use cases
- When stating performance numbers, always cite the source"""


def _build_synthesis_prompt(
    request: BattleCardRequest,
    competitor_name: str,
    agent_results: list[AgentResult],
    chat_context: str = "",
) -> str:
    """Build the synthesis prompt from all agent results."""
    sections = []

    # Client context
    if request.client_name or request.call_notes or request.client_emails:
        ctx = "## CLIENT CONTEXT\n"
        if request.client_name:
            ctx += f"**Client**: {request.client_name}\n"
        if request.client_industry:
            ctx += f"**Industry**: {request.client_industry}\n"
        if request.use_case:
            ctx += f"**Target Use Case**: {request.use_case.replace('_', ' ').title()}\n"
        if request.call_notes:
            ctx += f"\n**Call Notes / Transcripts**:\n{request.call_notes[:3000]}\n"
        if request.client_emails:
            ctx += f"\n**Recent Client Emails**:\n{request.client_emails[:2000]}\n"
        sections.append(ctx)

    if chat_context:
        sections.append(
            f"## ACTIVE CHAT SESSION CONTEXT\n{chat_context[:3000]}\n"
        )

    # Agent intelligence
    for result in agent_results:
        sections.append(
            f"## INTELLIGENCE: {result.agent_name.upper()}\n"
            f"Sources found: {result.sources_count}\n"
            f"{'Error: ' + result.error if result.error else ''}\n\n"
            f"```json\n{json.dumps(result.data, indent=2, default=str)[:8000]}\n```\n"
        )

    tone_instruction = (
        "Use HIGHLY TECHNICAL language suitable for quants, engineers, and architects. "
        "Include specific technical details, architecture comparisons, and precise metrics."
        if request.tone.value == "highly_technical"
        else "Use EXECUTIVE/BUSINESS VALUE language suitable for CTOs, MDs, and C-level executives. "
        "Focus on business outcomes, ROI, risk reduction, and strategic advantages."
    )

    use_case_label = request.use_case.value.replace("_", " ").title()

    prompt = f"""Based on the intelligence gathered below, generate a comprehensive sales battle card for pitching KX/kdb+ against **{competitor_name}**.

**Target Use Case**: {use_case_label}
**Tone**: {tone_instruction}

{"".join(sections)}

Generate a battle card in this EXACT JSON structure:

{{
  "why_kx_wins": "A compelling 2-3 sentence executive summary of why KX wins against {competitor_name} for this use case. Synthesize the client's pain points (if provided) with KX's core value proposition.",

  "pain_points": [
    {{"client_pain": "Specific pain point extracted from client context or inferred from use case", "kx_solution": "How kdb+ specifically solves this"}}
  ],

  "architecture_comparison": "A detailed technical comparison of kdb+'s architecture vs {competitor_name}. Cover data layout (vector-based columnar vs standard columnar), query engine design, memory management, and processing model. Use markdown formatting.",

  "benchmarks": [
    {{"metric": "Metric name", "kx_value": "KX result", "competitor_value": "{competitor_name} result", "source": "Source citation"}}
  ],

  "feature_matrix": [
    {{"feature": "Feature name", "kx_rating": "green|yellow|red", "competitor_rating": "green|yellow|red", "kx_detail": "Detail", "competitor_detail": "Detail"}}
  ],

  "trap_questions": [
    {{"question": "A probing technical question that exposes {competitor_name}'s weakness", "why_it_works": "Why this question is effective", "source": "Where we found this weakness"}}
  ],

  "objection_handlers": [
    {{"objection": "If they say X...", "response": "You say Y..."}}
  ],

  "competitor_news": [
    {{"headline": "Recent news item", "date": "Date", "implication": "What it means competitively"}}
  ]
}}

Generate at least:
- 3-5 pain points (or infer from use case if no client context)
- 4-8 benchmarks
- 8-12 feature comparisons covering: query latency, ingestion throughput, time-series analytics, ASOF joins, real-time streaming, high availability, security/RBAC, AI/ML integration, scalability, SQL support, operational complexity, enterprise support
- 3-4 trap questions
- 4-6 objection handlers
- Any recent competitor news found

Return ONLY valid JSON. No markdown fences, no explanation outside the JSON."""

    return prompt


class BattleCardGenerator:
    """Orchestrates multi-agent intelligence gathering and report synthesis."""

    def __init__(self):
        self.client = anthropic.Anthropic()

    def generate(self, request: BattleCardRequest):
        """Generator that yields SSE-formatted status updates and final report.

        Yields tuples of (event_type, data) where event_type is one of:
        - "status": progress update
        - "report": final BattleCardReport
        - "error": error message
        """
        t_start = time.time()

        # Resolve competitor name
        competitor = request.competitors[0]
        competitor_name = self._resolve_competitor_name(competitor)

        yield ("status", {"step": "starting", "message": f"Generating battle card: KX vs {competitor_name}", "progress": 0.05})

        # Phase 1: Deploy agents in parallel
        yield ("status", {"step": "agents", "message": "Deploying intelligence agents...", "progress": 0.1})

        agent_results = self._run_agents(request, competitor)

        yield ("status", {"step": "agents_done", "message": f"Intelligence gathered from {len(agent_results)} agents", "progress": 0.5})

        # Phase 2: Load chat context if requested
        chat_context = ""
        if request.include_chat_context and request.session_id:
            chat_context = self._load_chat_context(request.session_id)
            if chat_context:
                yield ("status", {"step": "chat_context", "message": "Chat context loaded", "progress": 0.55})

        # Phase 3: Synthesize with Claude
        yield ("status", {"step": "synthesizing", "message": "Synthesizing battle card with Claude...", "progress": 0.6})

        try:
            report = self._synthesize(request, competitor_name, agent_results, chat_context)
            report.generation_time_ms = int((time.time() - t_start) * 1000)
            report.agents_used = [r.agent_name for r in agent_results]
            report.sources_count = sum(r.sources_count for r in agent_results)
            report.client_name = request.client_name
            report.client_industry = request.client_industry
            report.use_case = request.use_case.value.replace("_", " ").title()
            report.competitor_name = competitor_name
            report.tone = request.tone.value

            yield ("status", {"step": "done", "message": "Battle card generated", "progress": 1.0})
            yield ("report", report)

        except Exception as e:
            logger.exception("Battle card synthesis failed: %s", e)
            yield ("error", {"detail": str(e)})

    def _run_agents(
        self, request: BattleCardRequest, competitor: str
    ) -> list[AgentResult]:
        """Run selected agents in parallel using ThreadPoolExecutor."""
        results = []
        futures = {}

        with ThreadPoolExecutor(max_workers=4) as executor:
            for agent_type in request.agents:
                if agent_type == AgentType.INTERNAL_KB:
                    try:
                        agent = InternalKBAgent()
                        future = executor.submit(
                            agent.gather,
                            competitor=competitor,
                            use_case=request.use_case.value,
                        )
                        futures[future] = agent_type.value
                    except Exception as e:
                        logger.error("Failed to init InternalKBAgent: %s", e)

                elif agent_type == AgentType.BENCHMARK:
                    agent = BenchmarkAgent()
                    future = executor.submit(
                        agent.gather,
                        competitor=competitor,
                        use_case=request.use_case.value,
                    )
                    futures[future] = agent_type.value

                elif agent_type == AgentType.DEVELOPER_SENTIMENT:
                    agent = DeveloperSentimentAgent()
                    future = executor.submit(
                        agent.gather, competitor=competitor
                    )
                    futures[future] = agent_type.value

                elif agent_type == AgentType.MARKET_NEWS:
                    agent = MarketNewsAgent()
                    future = executor.submit(
                        agent.gather, competitor=competitor
                    )
                    futures[future] = agent_type.value

            for future in as_completed(futures):
                agent_name = futures[future]
                try:
                    result = future.result(timeout=60)
                    results.append(result)
                    logger.info(
                        "Agent %s completed: %d sources",
                        agent_name,
                        result.sources_count,
                    )
                except Exception as e:
                    logger.error("Agent %s failed: %s", agent_name, e)
                    results.append(
                        AgentResult(
                            agent_name=agent_name,
                            data={},
                            error=str(e),
                        )
                    )

        return results

    def _load_chat_context(self, session_id: str) -> str:
        """Load recent chat messages from a session for context."""
        try:
            from webapp.sessions import SessionManager

            mgr = SessionManager()
            messages = mgr.get_recent_messages(session_id, limit=10)
            if not messages:
                return ""

            lines = []
            for msg in messages:
                role = msg.get("role", "user").upper()
                content = msg.get("content", "")[:500]
                lines.append(f"**{role}**: {content}")
            return "\n\n".join(lines)

        except Exception as e:
            logger.warning("Failed to load chat context: %s", e)
            return ""

    def _synthesize(
        self,
        request: BattleCardRequest,
        competitor_name: str,
        agent_results: list[AgentResult],
        chat_context: str = "",
    ) -> BattleCardReport:
        """Use Claude to synthesize agent results into a battle card."""
        prompt = _build_synthesis_prompt(
            request, competitor_name, agent_results, chat_context
        )

        response = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            system=SYNTHESIS_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        text = ""
        for block in response.content:
            if getattr(block, "type", None) == "text":
                text += block.text

        data = self._parse_json(text)

        return BattleCardReport(
            why_kx_wins=data.get("why_kx_wins", ""),
            pain_points=[
                PainPoint(**p) for p in data.get("pain_points", []) if isinstance(p, dict)
            ],
            architecture_comparison=data.get("architecture_comparison", ""),
            benchmarks=[
                BenchmarkDataPoint(**b) for b in data.get("benchmarks", []) if isinstance(b, dict)
            ],
            feature_matrix=[
                FeatureComparison(**f) for f in data.get("feature_matrix", []) if isinstance(f, dict)
            ],
            trap_questions=[
                TrapQuestion(**t) for t in data.get("trap_questions", []) if isinstance(t, dict)
            ],
            objection_handlers=[
                ObjectionHandler(**o) for o in data.get("objection_handlers", []) if isinstance(o, dict)
            ],
            competitor_news=[
                CompetitorNewsItem(**n) for n in data.get("competitor_news", []) if isinstance(n, dict)
            ],
        )

    def _resolve_competitor_name(self, short_name: str) -> str:
        """Resolve short name to full competitor name from config."""
        config_path = PROJECT_ROOT / "config" / "competitors" / f"{short_name}.json"
        if config_path.exists():
            try:
                data = json.loads(config_path.read_bytes())
                return data.get("name", short_name)
            except Exception:
                pass
        return short_name.title()

    def _parse_json(self, text: str) -> dict:
        """Extract JSON from LLM response."""
        import re

        # Try code fence first
        match = re.search(r"```(?:json)?\s*\n([\s\S]*?)\n```", text)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # Try raw JSON object
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        logger.error("Failed to parse synthesis response as JSON")
        return {}
