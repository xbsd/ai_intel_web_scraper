"""Competitive comparison entry generator.

For each taxonomy topic, builds a research bundle of KX + competitor sources,
sends them to Claude for analysis, and produces structured CompetitiveEntry objects.
"""

import json
import logging
from datetime import date
from pathlib import Path
from typing import Optional

import anthropic

from schemas.competitive_entry import CompetitiveEntry
from schemas.source_record import SourceRecord

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"


class ComparisonGenerator:
    """Generates per-topic competitive comparison entries using Claude."""

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        max_source_tokens: int = 80000,
    ):
        """Initialize the generator.

        Args:
            model: Anthropic model ID to use.
            max_source_tokens: Approximate max tokens for source context.
        """
        self.client = anthropic.Anthropic()
        self.model = model
        self.max_source_tokens = max_source_tokens

        # Load prompt templates
        self.system_prompt = (PROMPTS_DIR / "system_prompt.txt").read_text()
        self.topic_template = (PROMPTS_DIR / "topic_analysis.txt").read_text()

    def generate_topic(
        self,
        topic_id: str,
        topic_name: str,
        topic_description: str,
        competitor_name: str,
        kx_sources: list[SourceRecord],
        competitor_sources: list[SourceRecord],
        taxonomy_config: Optional[dict] = None,
    ) -> CompetitiveEntry:
        """Generate a competitive entry for a single topic.

        Args:
            topic_id: Topic ID from taxonomy.
            topic_name: Human-readable topic name.
            topic_description: What this topic covers.
            competitor_name: Name of the competitor.
            kx_sources: KX source records tagged with this topic.
            competitor_sources: Competitor source records tagged with this topic.
            taxonomy_config: Optional taxonomy config for additional context.

        Returns:
            CompetitiveEntry with structured competitive intelligence.
        """
        # Format sources for the prompt
        kx_text = self._format_sources(kx_sources, self.max_source_tokens // 2)
        competitor_text = self._format_sources(
            competitor_sources, self.max_source_tokens // 2
        )

        # Get the "why capital markets cares" from taxonomy if available
        topic_why = topic_description
        if taxonomy_config:
            for tier in taxonomy_config.get("tiers", {}).values():
                topics = tier.get("topics", {})
                if topic_id in topics:
                    topic_why = topics[topic_id].get("description", topic_description)
                    break

        # Build the prompt
        prompt = self.topic_template.format(
            topic_id=topic_id,
            topic_name=topic_name,
            topic_description=topic_description,
            topic_why=topic_why,
            competitor_name=competitor_name,
            kx_source_count=len(kx_sources),
            kx_sources=kx_text,
            competitor_source_count=len(competitor_sources),
            competitor_sources=competitor_text,
        )

        # Call Claude
        logger.info(
            "Generating competitive entry for topic '%s' vs %s (%d KX + %d competitor sources)",
            topic_name,
            competitor_name,
            len(kx_sources),
            len(competitor_sources),
        )

        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=self.system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )

        # Parse the response
        response_text = response.content[0].text

        # Extract JSON from the response (handle markdown code fences)
        json_text = self._extract_json(response_text)
        try:
            data = json.loads(json_text)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse JSON response for topic %s: %s", topic_id, e)
            logger.debug("Response text: %s", response_text[:500])
            # Return a minimal entry indicating failure
            return self._empty_entry(
                topic_id, topic_name, competitor_name, f"JSON parse error: {e}"
            )

        # Normalize LLM response to match our schema before validation
        data = self._normalize_response(data)

        # Build CompetitiveEntry from parsed data
        try:
            entry = CompetitiveEntry(
                topic_id=topic_id,
                topic_name=topic_name,
                competitor=competitor_name,
                generated_date=date.today(),
                model_used=self.model,
                competitor_assessment=data.get("competitor_assessment", {}),
                competitor_limitations=data.get("competitor_limitations", []),
                kx_differentiators=data.get("kx_differentiators", []),
                objection_handlers=data.get("objection_handlers", []),
                elevator_pitch=data.get("elevator_pitch", {}),
                confidence=data.get("confidence", "medium"),
                gaps=data.get("gaps", []),
                source_count=len(kx_sources) + len(competitor_sources),
            )
            return entry
        except Exception as e:
            logger.error("Failed to build CompetitiveEntry for topic %s: %s", topic_id, e)
            return self._empty_entry(
                topic_id, topic_name, competitor_name, f"Schema validation error: {e}"
            )

    def generate_all_topics(
        self,
        competitor_name: str,
        kx_records: list[SourceRecord],
        competitor_records: list[SourceRecord],
        taxonomy_config: dict,
        topics: Optional[list[str]] = None,
        output_dir: Optional[Path] = None,
        resume: bool = True,
    ) -> list[CompetitiveEntry]:
        """Generate competitive entries for all (or specified) topics.

        Args:
            competitor_name: Name of the competitor.
            kx_records: All KX source records.
            competitor_records: All competitor source records.
            taxonomy_config: Full taxonomy config.
            topics: Optional list of specific topic IDs to generate.
            output_dir: Optional directory to save each entry incrementally.
                        When set, each topic is saved to its own JSON file
                        immediately after generation.
            resume: If True and output_dir is set, skip topics that already
                    have a saved file from a previous run.

        Returns:
            List of CompetitiveEntry objects.
        """
        # Build topic index
        all_topic_ids = taxonomy_config.get("all_topic_ids", [])
        if topics:
            topic_ids = [t for t in topics if t in all_topic_ids]
        else:
            topic_ids = all_topic_ids

        # Index records by topic
        kx_by_topic = self._index_by_topic(kx_records)
        comp_by_topic = self._index_by_topic(competitor_records)

        entries = []
        skipped = 0
        for i, topic_id in enumerate(topic_ids, 1):
            # Resume: skip topics that already have a saved file
            topic_file = None
            if output_dir:
                topic_file = output_dir / f"topic_{topic_id}.json"
                if resume and topic_file.exists():
                    try:
                        existing = json.loads(topic_file.read_text())
                        entry = CompetitiveEntry(**existing)
                        entries.append(entry)
                        skipped += 1
                        logger.info(
                            "[%d/%d] Skipping topic '%s' (already generated)",
                            i, len(topic_ids), topic_id,
                        )
                        continue
                    except Exception:
                        logger.warning(
                            "Corrupt saved file for topic %s, regenerating", topic_id
                        )

            # Get topic metadata from taxonomy
            topic_name = topic_id
            topic_desc = ""
            for tier in taxonomy_config.get("tiers", {}).values():
                topics_dict = tier.get("topics", {})
                if topic_id in topics_dict:
                    topic_name = topics_dict[topic_id].get("name", topic_id)
                    topic_desc = topics_dict[topic_id].get("description", "")
                    break

            kx_srcs = kx_by_topic.get(topic_id, [])
            comp_srcs = comp_by_topic.get(topic_id, [])

            if not kx_srcs and not comp_srcs:
                logger.warning(
                    "[%d/%d] No sources for topic %s, skipping",
                    i, len(topic_ids), topic_id,
                )
                continue

            logger.info(
                "[%d/%d] Generating topic '%s'...",
                i, len(topic_ids), topic_id,
            )
            entry = self.generate_topic(
                topic_id=topic_id,
                topic_name=topic_name,
                topic_description=topic_desc,
                competitor_name=competitor_name,
                kx_sources=kx_srcs,
                competitor_sources=comp_srcs,
                taxonomy_config=taxonomy_config,
            )
            entries.append(entry)

            # Save incrementally so progress survives crashes
            if topic_file:
                topic_file.write_text(
                    json.dumps(entry.model_dump(mode="json"), indent=2)
                )
                logger.info("  Saved %s", topic_file.name)

        if skipped:
            logger.info(
                "Resumed: %d topics loaded from cache, %d newly generated",
                skipped, len(entries) - skipped,
            )

        return entries

    def _format_sources(self, records: list[SourceRecord], max_chars: int) -> str:
        """Format source records for inclusion in a prompt."""
        # Sort by credibility: official > third_party > community
        credibility_order = {"official": 0, "third_party": 1, "community": 2}
        sorted_records = sorted(
            records,
            key=lambda r: credibility_order.get(r.credibility.value, 3),
        )

        parts = []
        total_chars = 0

        for record in sorted_records:
            entry = (
                f"### [{record.source_type.value}] {record.title}\n"
                f"**URL**: {record.url}\n"
                f"**Credibility**: {record.credibility.value}\n\n"
                f"{record.text}\n\n---\n\n"
            )

            if total_chars + len(entry) > max_chars:
                # Truncate remaining text to fit
                remaining = max_chars - total_chars
                if remaining > 200:
                    parts.append(entry[:remaining] + "\n[TRUNCATED]")
                break

            parts.append(entry)
            total_chars += len(entry)

        if not parts:
            return "[No source documents available for this topic]"

        return "".join(parts)

    def _index_by_topic(
        self, records: list[SourceRecord]
    ) -> dict[str, list[SourceRecord]]:
        """Index records by their topic tags."""
        index: dict[str, list[SourceRecord]] = {}
        for record in records:
            for topic in record.topics:
                if topic not in index:
                    index[topic] = []
                index[topic].append(record)
        return index

    def _extract_json(self, text: str) -> str:
        """Extract JSON from a response that may contain markdown fences."""
        # Try to find JSON in code fences
        import re

        match = re.search(r"```(?:json)?\s*\n([\s\S]*?)\n```", text)
        if match:
            return match.group(1)

        # Try to find a JSON object directly
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            return match.group(0)

        return text

    def _normalize_response(self, data: dict) -> dict:
        """Normalize common LLM response deviations to match our Pydantic schema.

        Handles cases where the LLM returns slightly different structures, e.g.:
        - competitor_limitations as a dict grouped by category instead of a flat list
        - kx_differentiators as strings instead of objects
        - elevator_pitch as a string instead of an object
        - competitor_assessment as a string instead of an object
        - competitor_limitations items using 'category' instead of 'evidence_type'
        """
        # --- competitor_assessment ---
        ca = data.get("competitor_assessment")
        if isinstance(ca, str):
            data["competitor_assessment"] = {
                "summary": ca, "strengths": [], "details": ca, "citations": []
            }
        elif isinstance(ca, dict):
            if "details" not in ca:
                ca["details"] = ca.get("summary", "")
            if "summary" not in ca:
                ca["summary"] = ca.get("details", "")

        # --- competitor_limitations: flatten grouped dict to list ---
        cl = data.get("competitor_limitations")
        if isinstance(cl, dict):
            flat = []
            for category, items in cl.items():
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, str):
                            flat.append({
                                "limitation": item,
                                "evidence_type": category,
                                "details": item,
                            })
                        elif isinstance(item, dict):
                            item.setdefault("evidence_type", category)
                            flat.append(item)
            data["competitor_limitations"] = flat
        elif isinstance(cl, list):
            normalized = []
            for item in cl:
                if isinstance(item, str):
                    normalized.append({
                        "limitation": item,
                        "evidence_type": "inferred",
                        "details": item,
                    })
                elif isinstance(item, dict):
                    # Map 'category' -> 'evidence_type' if needed
                    if "evidence_type" not in item and "category" in item:
                        item["evidence_type"] = item.pop("category")
                    item.setdefault("evidence_type", "inferred")
                    item.setdefault("details", item.get("limitation", ""))
                    normalized.append(item)
            data["competitor_limitations"] = normalized

        # --- kx_differentiators: convert strings to objects ---
        kd = data.get("kx_differentiators")
        if isinstance(kd, str):
            kd = [kd]
            data["kx_differentiators"] = kd
        if isinstance(kd, list):
            normalized = []
            for item in kd:
                if isinstance(item, str):
                    normalized.append({
                        "differentiator": item,
                        "explanation": item,
                        "evidence": "",
                    })
                elif isinstance(item, dict):
                    item.setdefault("differentiator", item.get("explanation", ""))
                    item.setdefault("explanation", item.get("differentiator", ""))
                    item.setdefault("evidence", "")
                    normalized.append(item)
            data["kx_differentiators"] = normalized

        # --- elevator_pitch: convert string to object ---
        ep = data.get("elevator_pitch")
        if isinstance(ep, str):
            data["elevator_pitch"] = {"pitch": ep, "key_stat": None}
        elif isinstance(ep, dict):
            ep.setdefault("pitch", "")

        # --- objection_handlers: convert strings to objects ---
        oh = data.get("objection_handlers")
        if isinstance(oh, list):
            normalized = []
            for item in oh:
                if isinstance(item, str):
                    normalized.append({
                        "objection": item,
                        "response": "",
                        "supporting_evidence": [],
                    })
                elif isinstance(item, dict):
                    item.setdefault("objection", "")
                    item.setdefault("response", "")
                    normalized.append(item)
            data["objection_handlers"] = normalized

        return data

    def _empty_entry(
        self, topic_id: str, topic_name: str, competitor: str, error: str
    ) -> CompetitiveEntry:
        """Create an empty entry for error cases."""
        return CompetitiveEntry(
            topic_id=topic_id,
            topic_name=topic_name,
            competitor=competitor,
            generated_date=date.today(),
            model_used=self.model,
            competitor_assessment={
                "summary": f"Generation failed: {error}",
                "strengths": [],
                "details": "",
                "citations": [],
            },
            competitor_limitations=[],
            kx_differentiators=[],
            objection_handlers=[],
            elevator_pitch={"pitch": "Generation failed — requires manual creation."},
            confidence="low",
            gaps=[f"Generation error: {error}"],
            source_count=0,
        )
