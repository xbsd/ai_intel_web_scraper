"""Pydantic models for battle card generation requests and responses."""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class UseCase(str, Enum):
    ALPHA_GENERATION = "alpha_generation"
    ORDER_BOOK_ANALYTICS = "order_book_analytics"
    TICK_TO_TRADE = "tick_to_trade"
    RISK_MANAGEMENT = "risk_management"
    AGENTIC_AI = "agentic_ai"
    GENERAL = "general"


class TonePersona(str, Enum):
    HIGHLY_TECHNICAL = "highly_technical"
    EXECUTIVE_BUSINESS = "executive_business"


class AgentType(str, Enum):
    INTERNAL_KB = "internal_kb"
    BENCHMARK = "benchmark"
    DEVELOPER_SENTIMENT = "developer_sentiment"
    MARKET_NEWS = "market_news"


# ── Request Models ──


class BattleCardRequest(BaseModel):
    """Request to generate a battle card."""

    # Context
    client_name: str = Field(default="", description="Client/prospect name")
    client_industry: str = Field(
        default="", description="e.g. Tier 1 Bank, Hedge Fund"
    )
    use_case: UseCase = Field(default=UseCase.GENERAL)
    competitors: list[str] = Field(
        ..., min_length=1, description="Competitor short names"
    )

    # Unstructured data
    include_chat_context: bool = Field(
        default=False,
        description="Include active chat session context",
    )
    session_id: Optional[str] = Field(
        default=None, description="Chat session ID for context"
    )
    call_notes: str = Field(
        default="", description="Call transcripts or meeting notes"
    )
    client_emails: str = Field(
        default="", description="Recent client email content"
    )

    # Agent selection
    agents: list[AgentType] = Field(
        default_factory=lambda: [AgentType.INTERNAL_KB],
        description="Which intelligence agents to deploy",
    )

    # Generation controls
    tone: TonePersona = Field(default=TonePersona.HIGHLY_TECHNICAL)

    # Auth
    username: Optional[str] = None


# ── Response / Report Models ──


class PainPoint(BaseModel):
    client_pain: str
    kx_solution: str


class BenchmarkDataPoint(BaseModel):
    metric: str
    kx_value: str
    competitor_value: str
    source: str = ""


class FeatureComparison(BaseModel):
    feature: str
    kx_rating: str  # "green", "yellow", "red"
    competitor_rating: str
    kx_detail: str = ""
    competitor_detail: str = ""


class TrapQuestion(BaseModel):
    question: str
    why_it_works: str
    source: str = ""


class ObjectionHandler(BaseModel):
    objection: str
    response: str


class CompetitorNewsItem(BaseModel):
    headline: str
    date: str = ""
    implication: str = ""


class BattleCardReport(BaseModel):
    """The complete battle card report data."""

    # Meta
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    client_name: str = ""
    client_industry: str = ""
    use_case: str = ""
    competitor_name: str = ""
    tone: str = "highly_technical"

    # Page 1: Executive Overview
    why_kx_wins: str = ""
    pain_points: list[PainPoint] = Field(default_factory=list)

    # Page 2: Technical Evidence
    architecture_comparison: str = ""
    benchmarks: list[BenchmarkDataPoint] = Field(default_factory=list)
    feature_matrix: list[FeatureComparison] = Field(default_factory=list)

    # Page 3: Tactical Execution
    trap_questions: list[TrapQuestion] = Field(default_factory=list)
    objection_handlers: list[ObjectionHandler] = Field(default_factory=list)
    competitor_news: list[CompetitorNewsItem] = Field(default_factory=list)

    # Agent metadata
    agents_used: list[str] = Field(default_factory=list)
    sources_count: int = 0
    generation_time_ms: int = 0


class BattleCardGenerationStatus(BaseModel):
    """SSE status update during generation."""

    step: str
    message: str
    progress: float = 0.0  # 0-1
