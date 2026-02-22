"""Summary and positioning narrative generator.

Generates the overall positioning narrative, quick-reference comparison table,
and deal-stage talking points for a competitor. This runs after per-topic
competitive entries have been generated.
"""

import json
import logging
from datetime import date
from pathlib import Path
from typing import Optional

import anthropic

from schemas.competitive_entry import (
    ComparisonTable,
    CompetitiveEntry,
    DealStageTalkingPoints,
    PositioningNarrative,
)
from schemas.source_record import SourceRecord

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"


class SummaryGenerator:
    """Generates positioning narratives and comparison tables using Claude."""

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        max_source_tokens: int = 80000,
    ):
        self.client = anthropic.Anthropic()
        self.model = model
        self.max_source_tokens = max_source_tokens

        self.system_prompt = (PROMPTS_DIR / "system_prompt.txt").read_text()
        self.elevator_template = (PROMPTS_DIR / "elevator_pitch.txt").read_text()

    def generate_narrative(
        self,
        competitor_name: str,
        kx_sources: list[SourceRecord],
        competitor_sources: list[SourceRecord],
        topic_entries: list[CompetitiveEntry],
    ) -> PositioningNarrative:
        """Generate the overall positioning narrative.

        Args:
            competitor_name: Name of the competitor.
            kx_sources: All KX source records.
            competitor_sources: All competitor source records.
            topic_entries: Already-generated per-topic competitive entries.

        Returns:
            PositioningNarrative with pitch, comparison table, and talking points.
        """
        # Summarize existing topic entries for context
        entries_summary = self._summarize_entries(topic_entries)

        kx_text = self._format_sources(kx_sources)
        competitor_text = self._format_sources(competitor_sources)

        prompt = self.elevator_template.format(
            competitor_name=competitor_name,
            kx_sources=kx_text,
            competitor_sources=competitor_text,
            topic_entries_summary=entries_summary,
        )

        logger.info("Generating positioning narrative for %s", competitor_name)

        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=self.system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )

        response_text = response.content[0].text
        json_text = self._extract_json(response_text)

        try:
            data = json.loads(json_text)
            return PositioningNarrative(
                competitor=competitor_name,
                generated_date=date.today(),
                sixty_second_pitch=data.get("sixty_second_pitch", ""),
                cross_cutting_objections=data.get("cross_cutting_objections", []),
                comparison_table=ComparisonTable(
                    competitor=competitor_name,
                    generated_date=date.today(),
                    rows=data.get("comparison_table", {}).get("rows", []),
                ),
                deal_stage_talking_points=DealStageTalkingPoints(
                    **data.get("deal_stage_talking_points", {})
                ),
                model_used=self.model,
            )
        except (json.JSONDecodeError, Exception) as e:
            logger.error("Failed to parse narrative response: %s", e)
            return PositioningNarrative(
                competitor=competitor_name,
                generated_date=date.today(),
                sixty_second_pitch=f"Generation failed: {e}",
                comparison_table=ComparisonTable(
                    competitor=competitor_name,
                    generated_date=date.today(),
                ),
                deal_stage_talking_points=DealStageTalkingPoints(),
                model_used=self.model,
            )

    def _summarize_entries(self, entries: list[CompetitiveEntry]) -> str:
        """Create a compact summary of existing competitive entries for prompt context."""
        parts = []
        for entry in entries:
            summary = (
                f"### {entry.topic_name} (confidence: {entry.confidence})\n"
                f"**Elevator Pitch**: {entry.elevator_pitch.pitch}\n"
                f"**Differentiators**: {len(entry.kx_differentiators)} identified\n"
                f"**Limitations**: {len(entry.competitor_limitations)} identified\n"
                f"**Gaps**: {', '.join(entry.gaps) if entry.gaps else 'None'}\n"
            )
            parts.append(summary)
        return "\n".join(parts) if parts else "[No topic entries generated yet]"

    def _format_sources(self, records: list[SourceRecord]) -> str:
        """Format sources for prompt, truncating to fit."""
        max_chars = self.max_source_tokens * 3
        credibility_order = {"official": 0, "third_party": 1, "community": 2}
        sorted_records = sorted(
            records,
            key=lambda r: credibility_order.get(r.credibility.value, 3),
        )

        parts = []
        total = 0
        for record in sorted_records:
            entry = (
                f"### [{record.source_type.value}] {record.title}\n"
                f"**URL**: {record.url}\n\n"
                f"{record.text[:2000]}\n\n---\n\n"
            )
            if total + len(entry) > max_chars:
                break
            parts.append(entry)
            total += len(entry)

        return "".join(parts) if parts else "[No sources available]"

    def _extract_json(self, text: str) -> str:
        """Extract JSON from response text."""
        import re
        match = re.search(r"```(?:json)?\s*\n([\s\S]*?)\n```", text)
        if match:
            return match.group(1)
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            return match.group(0)
        return text
