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
    ClientIntelligenceAgent,
    DeveloperSentimentAgent,
    InternalKBAgent,
    MarketNewsAgent,
)
from webapp.battlecard.models import (
    AgentType,
    BattleCardReport,
    BattleCardRequest,
    BenchmarkDataPoint,
    ClientIntelItem,
    ClientIntelligence,
    CompetitivePositioning,
    CompetitorNewsItem,
    DealStrategyItem,
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
    client_intel: Optional[dict] = None,
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
        # Add confirmed client details if available
        if request.confirmed_client:
            cc = request.confirmed_client
            if cc.description:
                ctx += f"**Company Description**: {cc.description}\n"
            if cc.headquarters:
                ctx += f"**Headquarters**: {cc.headquarters}\n"
            if cc.employees:
                ctx += f"**Employees**: {cc.employees}\n"
            if cc.ticker:
                ctx += f"**Ticker**: {cc.ticker}\n"
        if request.call_notes:
            ctx += f"\n**Call Notes / Transcripts**:\n{request.call_notes[:3000]}\n"
        if request.client_emails:
            ctx += f"\n**Recent Client Emails**:\n{request.client_emails[:2000]}\n"
        sections.append(ctx)

    # Client intelligence (current news, AI/DB activity)
    if client_intel:
        intel_section = "## CLIENT INTELLIGENCE (Current Company Research)\n"
        if client_intel.get("company_overview"):
            intel_section += f"**Overview**: {client_intel['company_overview']}\n"
        if client_intel.get("ai_db_initiatives"):
            intel_section += f"\n**AI & Database Initiatives**: {client_intel['ai_db_initiatives']}\n"
        if client_intel.get("technology_stack"):
            intel_section += f"\n**Known Technology Stack**: {client_intel['technology_stack']}\n"
        if client_intel.get("key_priorities"):
            intel_section += f"\n**Key Priorities**: {', '.join(client_intel['key_priorities'])}\n"
        if client_intel.get("potential_pain_points"):
            intel_section += f"\n**Potential Pain Points**: {', '.join(client_intel['potential_pain_points'])}\n"
        if client_intel.get("recent_news"):
            intel_section += "\n**Recent News**:\n"
            for item in client_intel["recent_news"][:10]:
                intel_section += f"- [{item.get('date', 'N/A')}] {item.get('headline', '')} ({item.get('category', '')})\n"
        sections.append(intel_section)

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
    {{"headline": "Recent news item", "date": "YYYY-MM-DD", "implication": "What it means competitively"}}
  ],

  "competitive_positioning": {{
    "positioning_statement": "A 2-3 sentence statement on how to position KX against {competitor_name} specifically for this client. Reference their industry, use case, and known pain points.",
    "key_differentiators": ["differentiator 1", "differentiator 2", "differentiator 3"],
    "landmines_to_set": ["A technical requirement to plant early in the evaluation that {competitor_name} cannot meet", "another landmine"],
    "proof_points": ["Customer reference or case study that resonates with this client's profile"]
  }},

  "deal_strategy": [
    {{"stage": "Discovery", "action": "Key action for this deal stage", "talking_point": "What to emphasize"}},
    {{"stage": "Technical Evaluation", "action": "Key action", "talking_point": "What to emphasize"}},
    {{"stage": "POC / Benchmark", "action": "Key action", "talking_point": "What to emphasize"}},
    {{"stage": "Procurement / Close", "action": "Key action", "talking_point": "What to emphasize"}}
  ],

  "pricing_guidance": "Strategic pricing guidance: how to position KX's pricing model vs {competitor_name}. Include total cost of ownership arguments, licensing model advantages, and ROI talking points. Reference the client's scale and use case."
}}

Generate at least:
- 3-5 pain points (or infer from use case if no client context)
- 4-8 benchmarks
- 8-12 feature comparisons covering: query latency, ingestion throughput, time-series analytics, ASOF joins, real-time streaming, high availability, security/RBAC, AI/ML integration, scalability, SQL support, operational complexity, enterprise support
- 3-4 trap questions
- 4-6 objection handlers
- Any recent competitor news found
- Full competitive positioning with landmines and proof points
- Deal strategy for each sales stage
- Pricing guidance

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

        yield ("status", {"step": "starting", "message": f"Generating battle card: KX vs {competitor_name}", "progress": 0.02})

        # Phase 0: Client Intelligence (if client name provided)
        client_intel = None
        if request.client_name:
            yield ("status", {"step": "client_intel", "message": f"Researching {request.client_name} — current news, AI & database initiatives...", "progress": 0.05})
            client_intel = self._gather_client_intel(request)
            if client_intel:
                yield ("status", {"step": "client_intel_done", "message": f"Client intelligence gathered: {len(client_intel.get('recent_news', []))} news items found", "progress": 0.15})

        # Phase 1: Deploy agents in parallel
        agent_names = [a.value.replace("_", " ").title() for a in request.agents]
        yield ("status", {"step": "agents", "message": f"Deploying agents: {', '.join(agent_names)}", "progress": 0.18})

        agent_results = self._run_agents_with_progress(request, competitor)

        total_sources = sum(r.sources_count for r in agent_results)
        yield ("status", {"step": "agents_done", "message": f"All {len(agent_results)} agents complete — {total_sources} sources gathered", "progress": 0.55})

        # Phase 2: Load chat context if requested
        chat_context = ""
        if request.include_chat_context and request.session_id:
            yield ("status", {"step": "chat_context", "message": "Loading active chat session context...", "progress": 0.58})
            chat_context = self._load_chat_context(request.session_id)
            if chat_context:
                yield ("status", {"step": "chat_context_done", "message": "Chat context loaded — incorporating conversation history", "progress": 0.60})

        # Phase 3: Synthesize with Claude
        yield ("status", {"step": "synthesizing", "message": "Claude is synthesizing battle card — analyzing competitive positioning...", "progress": 0.62})

        try:
            yield ("status", {"step": "synthesizing_detail", "message": "Building executive overview, benchmarks, and feature matrix...", "progress": 0.68})

            report = self._synthesize(request, competitor_name, agent_results, chat_context, client_intel)

            yield ("status", {"step": "synthesizing_sales", "message": "Generating tactical sales section — trap questions, objection handlers...", "progress": 0.82})

            report.generation_time_ms = int((time.time() - t_start) * 1000)
            report.agents_used = [r.agent_name for r in agent_results]
            if client_intel:
                report.agents_used.append("Client Intelligence")
            report.sources_count = total_sources + (
                len(client_intel.get("recent_news", [])) if client_intel else 0
            )
            report.client_name = request.client_name
            report.client_industry = request.client_industry
            report.use_case = request.use_case.value.replace("_", " ").title()
            report.competitor_name = competitor_name
            report.tone = request.tone.value
            if request.confirmed_client and request.confirmed_client.logo_url:
                report.client_logo_url = request.confirmed_client.logo_url

            # Attach client intelligence to report
            if client_intel:
                report.client_intelligence = ClientIntelligence(
                    company_overview=client_intel.get("company_overview", ""),
                    recent_news=[
                        ClientIntelItem(**n) for n in client_intel.get("recent_news", []) if isinstance(n, dict)
                    ],
                    ai_db_initiatives=client_intel.get("ai_db_initiatives", ""),
                    technology_stack=client_intel.get("technology_stack", ""),
                    key_priorities=client_intel.get("key_priorities", []),
                    potential_pain_points=client_intel.get("potential_pain_points", []),
                )

            yield ("status", {"step": "rendering", "message": "Formatting premium battle card document...", "progress": 0.92})
            yield ("status", {"step": "done", "message": "Battle card generated successfully", "progress": 1.0})
            yield ("report", report)

        except Exception as e:
            logger.exception("Battle card synthesis failed: %s", e)
            yield ("error", {"detail": str(e)})

    def _gather_client_intel(self, request: BattleCardRequest) -> Optional[dict]:
        """Gather intelligence about the client company."""
        try:
            agent = ClientIntelligenceAgent()
            result = agent.gather(
                client_name=request.client_name,
                client_industry=request.client_industry,
            )
            if result.error:
                logger.warning("Client intel agent error: %s", result.error)
            return result.data
        except Exception as e:
            logger.error("Client intelligence gathering failed: %s", e)
            return None

    def _run_agents_with_progress(
        self, request: BattleCardRequest, competitor: str
    ) -> list[AgentResult]:
        """Run agents and return results (progress is yielded by caller)."""
        return self._run_agents(request, competitor)

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
        client_intel: Optional[dict] = None,
    ) -> BattleCardReport:
        """Use Claude to synthesize agent results into a battle card."""
        prompt = _build_synthesis_prompt(
            request, competitor_name, agent_results, chat_context, client_intel
        )

        response = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=12000,
            system=SYNTHESIS_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        text = ""
        for block in response.content:
            if getattr(block, "type", None) == "text":
                text += block.text

        data = self._parse_json(text)

        # Parse competitive positioning
        cp_data = data.get("competitive_positioning", {})
        competitive_positioning = None
        if isinstance(cp_data, dict) and cp_data:
            competitive_positioning = CompetitivePositioning(
                positioning_statement=cp_data.get("positioning_statement", ""),
                key_differentiators=cp_data.get("key_differentiators", []),
                landmines_to_set=cp_data.get("landmines_to_set", []),
                proof_points=cp_data.get("proof_points", []),
            )

        # Parse deal strategy
        deal_strategy = [
            DealStrategyItem(**d) for d in data.get("deal_strategy", []) if isinstance(d, dict)
        ]

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
            competitive_positioning=competitive_positioning,
            deal_strategy=deal_strategy,
            pricing_guidance=data.get("pricing_guidance", ""),
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
