"""Cross-cutting objection handler generator.

Generates objection handlers that span multiple topics — the common
pushbacks that come up regardless of which specific capability is
being discussed (e.g., "it's free," "it's SQL," "it's open source").
"""

import json
import logging
from datetime import date
from pathlib import Path
from typing import Optional

import anthropic

from schemas.competitive_entry import ObjectionHandler
from schemas.source_record import SourceRecord

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"


class ObjectionGenerator:
    """Generates cross-cutting objection handlers using Claude."""

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        max_source_tokens: int = 80000,
    ):
        self.client = anthropic.Anthropic()
        self.model = model
        self.max_source_tokens = max_source_tokens

        self.system_prompt = (PROMPTS_DIR / "system_prompt.txt").read_text()
        self.objection_template = (PROMPTS_DIR / "objection_handler.txt").read_text()
        self.cross_cutting_template = (PROMPTS_DIR / "cross_cutting.txt").read_text()

    def generate_objections(
        self,
        competitor_name: str,
        kx_sources: list[SourceRecord],
        competitor_sources: list[SourceRecord],
    ) -> list[ObjectionHandler]:
        """Generate cross-cutting objection handlers.

        Args:
            competitor_name: Name of the competitor.
            kx_sources: All KX source records.
            competitor_sources: All competitor source records.

        Returns:
            List of ObjectionHandler objects.
        """
        kx_text = self._format_sources(kx_sources)
        competitor_text = self._format_sources(competitor_sources)

        prompt = self.objection_template.format(
            competitor_name=competitor_name,
            kx_sources=kx_text,
            competitor_sources=competitor_text,
        )

        logger.info("Generating cross-cutting objection handlers for %s", competitor_name)

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
            if isinstance(data, list):
                return [ObjectionHandler(**item) for item in data]
            elif isinstance(data, dict) and "objection_handlers" in data:
                return [ObjectionHandler(**item) for item in data["objection_handlers"]]
            else:
                logger.error("Unexpected response structure for objection handlers")
                return []
        except (json.JSONDecodeError, Exception) as e:
            logger.error("Failed to parse objection handlers: %s", e)
            return []

    def generate_cross_cutting(
        self,
        competitor_name: str,
        kx_sources: list[SourceRecord],
        competitor_sources: list[SourceRecord],
    ) -> list[ObjectionHandler]:
        """Generate cross-cutting theme analysis.

        Args:
            competitor_name: Name of the competitor.
            kx_sources: All KX source records.
            competitor_sources: All competitor source records.

        Returns:
            List of ObjectionHandler objects covering cross-cutting themes.
        """
        kx_text = self._format_sources(kx_sources)
        competitor_text = self._format_sources(competitor_sources)

        prompt = self.cross_cutting_template.format(
            competitor_name=competitor_name,
            kx_sources=kx_text,
            competitor_sources=competitor_text,
        )

        logger.info("Generating cross-cutting themes for %s", competitor_name)

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
            if isinstance(data, list):
                return [ObjectionHandler(**item) for item in data]
            elif isinstance(data, dict):
                items = data.get("objection_handlers", data.get("handlers", []))
                return [ObjectionHandler(**item) for item in items]
            return []
        except (json.JSONDecodeError, Exception) as e:
            logger.error("Failed to parse cross-cutting themes: %s", e)
            return []

    def _format_sources(self, records: list[SourceRecord]) -> str:
        """Format sources for prompt inclusion, truncating to fit."""
        max_chars = self.max_source_tokens * 3  # Rough chars-to-tokens estimate

        # Prioritize official and third-party sources
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
                f"{record.text[:3000]}\n\n---\n\n"
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
        match = re.search(r"[\[\{][\s\S]*[\]\}]", text)
        if match:
            return match.group(0)
        return text
