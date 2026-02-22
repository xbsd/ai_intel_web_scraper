"""Pydantic models for LLM-generated competitive intelligence entries."""

from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import date


class SourceCitation(BaseModel):
    url: str
    title: str
    source_type: str
    excerpt: str = Field(description="Relevant excerpt from the source")


class CompetitorAssessment(BaseModel):
    summary: str = Field(description="What the competitor actually offers for this topic")
    strengths: List[str] = Field(default_factory=list)
    details: str = Field(description="Detailed factual analysis with citations")
    citations: List[SourceCitation] = Field(default_factory=list)


class CompetitorLimitation(BaseModel):
    limitation: str = Field(description="Description of the limitation")
    evidence_type: str = Field(
        description="'confirmed' (docs/official), 'reported' (community), 'inferred' (analysis)"
    )
    details: str
    citations: List[SourceCitation] = Field(default_factory=list)


class KXDifferentiator(BaseModel):
    differentiator: str = Field(description="Where KDB+ wins")
    explanation: str
    evidence: str
    citations: List[SourceCitation] = Field(default_factory=list)


class ObjectionHandler(BaseModel):
    objection: str = Field(description="The prospect's objection or question")
    response: str = Field(description="Recommended response with evidence")
    supporting_evidence: List[str] = Field(default_factory=list)
    tone: str = Field(
        default="consultative",
        description="'consultative', 'technical', 'executive'"
    )
    citations: List[SourceCitation] = Field(default_factory=list)


class ElevatorPitch(BaseModel):
    pitch: str = Field(description="2-3 sentence summary for quick conversations")
    key_stat: Optional[str] = Field(
        None, description="One compelling statistic or proof point"
    )


class CompetitiveEntry(BaseModel):
    topic_id: str
    topic_name: str
    competitor: str
    generated_date: date
    model_used: str = Field(description="LLM model ID used for generation")

    competitor_assessment: CompetitorAssessment
    competitor_limitations: List[CompetitorLimitation] = Field(default_factory=list)
    kx_differentiators: List[KXDifferentiator] = Field(default_factory=list)
    objection_handlers: List[ObjectionHandler] = Field(default_factory=list)
    elevator_pitch: ElevatorPitch

    confidence: str = Field(
        description="'high' (well-sourced), 'medium' (some gaps), 'low' (needs manual research)"
    )
    gaps: List[str] = Field(
        default_factory=list,
        description="Topics where source data was insufficient"
    )
    source_count: int = Field(
        default=0, description="Number of source documents used"
    )


class ComparisonRow(BaseModel):
    capability: str
    kx_rating: str = Field(description="Brief assessment for KX")
    competitor_rating: str = Field(description="Brief assessment for competitor")
    verdict: str = Field(description="'kx_wins', 'competitor_wins', 'tie', 'depends'")
    notes: str = ""


class ComparisonTable(BaseModel):
    competitor: str
    generated_date: date
    rows: List[ComparisonRow] = Field(default_factory=list)


class DealStageTalkingPoints(BaseModel):
    discovery: List[str] = Field(
        default_factory=list,
        description="Questions/points for initial discovery calls"
    )
    technical_eval: List[str] = Field(
        default_factory=list,
        description="Points for technical evaluation phase"
    )
    procurement: List[str] = Field(
        default_factory=list,
        description="Points for procurement/final decision phase"
    )


class PositioningNarrative(BaseModel):
    competitor: str
    generated_date: date
    sixty_second_pitch: str = Field(
        description="The overall 60-second pitch against this competitor"
    )
    cross_cutting_objections: List[ObjectionHandler] = Field(default_factory=list)
    comparison_table: ComparisonTable
    deal_stage_talking_points: DealStageTalkingPoints
    model_used: str
