"""Auto-tag scraped content with taxonomy topics based on keyword matching.

For each SourceRecord, checks the text against topic keywords and assigns
the top matching topics. Uses both global keywords (from keywords.json)
and competitor-specific keywords (from the competitor config).
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

from schemas.source_record import SourceRecord

logger = logging.getLogger(__name__)


class TopicTagger:
    """Tags SourceRecords with taxonomy topic IDs based on keyword matching."""

    def __init__(
        self,
        global_keywords_path: str = "config/keywords.json",
        competitor_keywords: Optional[dict[str, list[str]]] = None,
        max_topics: int = 3,
        min_score_threshold: float = 0.01,
    ):
        """Initialize the tagger.

        Args:
            global_keywords_path: Path to the global keywords.json file.
            competitor_keywords: Optional competitor-specific keyword overrides.
            max_topics: Maximum number of topics to assign per record.
            min_score_threshold: Minimum score to qualify as a match.
        """
        self.max_topics = max_topics
        self.min_score_threshold = min_score_threshold

        # Load global keywords
        self.topic_keywords: dict[str, list[str]] = {}
        kw_path = Path(global_keywords_path)
        if kw_path.exists():
            with open(kw_path) as f:
                data = json.load(f)
                self.topic_keywords = data.get("topic_keywords", {})

        # Merge competitor-specific keywords (they supplement, not replace)
        if competitor_keywords:
            for topic_id, keywords in competitor_keywords.items():
                if topic_id in self.topic_keywords:
                    # Add competitor keywords, deduplicate
                    existing = set(kw.lower() for kw in self.topic_keywords[topic_id])
                    for kw in keywords:
                        if kw.lower() not in existing:
                            self.topic_keywords[topic_id].append(kw)
                            existing.add(kw.lower())
                else:
                    self.topic_keywords[topic_id] = keywords

        # Precompile patterns for efficiency
        self._compiled_patterns: dict[str, list[tuple[re.Pattern, float]]] = {}
        for topic_id, keywords in self.topic_keywords.items():
            patterns = []
            for kw in keywords:
                # Give multi-word keywords higher weight (more specific)
                weight = 1.0 + (kw.count(" ") * 0.5)
                try:
                    pattern = re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)
                    patterns.append((pattern, weight))
                except re.error:
                    logger.warning("Invalid keyword pattern: %s", kw)
            self._compiled_patterns[topic_id] = patterns

    def tag(self, record: SourceRecord) -> SourceRecord:
        """Tag a single SourceRecord with matching topics.

        Modifies record.topics in place and returns the record.
        """
        text = f"{record.title} {record.text}"
        scores = self._score_topics(text)

        # Sort by score descending, take top N above threshold
        sorted_topics = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top_topics = [
            topic_id
            for topic_id, score in sorted_topics[:self.max_topics]
            if score >= self.min_score_threshold
        ]

        if not top_topics:
            record.topics = ["unclassified"]
        else:
            record.topics = top_topics

        return record

    def tag_batch(self, records: list[SourceRecord]) -> list[SourceRecord]:
        """Tag a batch of SourceRecords."""
        tagged = []
        for record in records:
            tagged.append(self.tag(record))

        # Log statistics
        topic_counts: dict[str, int] = {}
        unclassified = 0
        for r in tagged:
            if r.topics == ["unclassified"]:
                unclassified += 1
            for t in r.topics:
                topic_counts[t] = topic_counts.get(t, 0) + 1

        logger.info(
            "Tagged %d records: %d unclassified, topic distribution: %s",
            len(tagged),
            unclassified,
            dict(sorted(topic_counts.items(), key=lambda x: x[1], reverse=True)[:10]),
        )
        return tagged

    def _score_topics(self, text: str) -> dict[str, float]:
        """Score all topics for a given text.

        Score = sum of (keyword_match_count * keyword_weight) / total_keywords_for_topic.
        """
        scores = {}
        text_len = len(text.split())

        for topic_id, patterns in self._compiled_patterns.items():
            if not patterns:
                continue

            total_score = 0.0
            for pattern, weight in patterns:
                matches = len(pattern.findall(text))
                total_score += matches * weight

            # Normalize by number of keywords and text length
            if total_score > 0:
                scores[topic_id] = total_score / len(patterns)

        return scores
