"""Quality filter for removing low-value scraped content.

Filters out:
- Documents shorter than a minimum word count
- Documents with no matched topics (with exceptions for benchmarks/releases)
- Content that is primarily code with minimal explanatory text
"""

import logging
import re

from schemas.source_record import SourceRecord, SourceType

logger = logging.getLogger(__name__)

# Source types exempt from topic-match requirement
TOPIC_EXEMPT_TYPES = {
    SourceType.BENCHMARK,
    SourceType.GITHUB_RELEASE,
}


class QualityFilter:
    """Filters out low-quality or irrelevant scraped content."""

    def __init__(
        self,
        min_word_count: int = 100,
        max_code_ratio: float = 0.85,
        require_topics: bool = True,
    ):
        """Initialize the filter.

        Args:
            min_word_count: Minimum word count to keep a document.
            max_code_ratio: Maximum ratio of code to total content (0-1).
            require_topics: Whether to filter out untagged content.
        """
        self.min_word_count = min_word_count
        self.max_code_ratio = max_code_ratio
        self.require_topics = require_topics

    def filter(self, records: list[SourceRecord]) -> list[SourceRecord]:
        """Filter a list of SourceRecords, returning only those passing quality checks.

        Returns:
            Filtered list of SourceRecords.
        """
        kept = []
        removed_reasons: dict[str, int] = {}

        for record in records:
            reason = self._should_remove(record)
            if reason:
                removed_reasons[reason] = removed_reasons.get(reason, 0) + 1
                continue
            kept.append(record)

        logger.info(
            "Quality filter: kept %d / %d records. Removed: %s",
            len(kept),
            len(records),
            removed_reasons,
        )
        return kept

    def _should_remove(self, record: SourceRecord) -> str:
        """Check if a record should be removed.

        Returns:
            Reason string if should remove, empty string if should keep.
        """
        # Check minimum word count
        if record.word_count < self.min_word_count:
            return "too_short"

        # Check topic requirement (with exemptions)
        if self.require_topics:
            if (
                record.source_type not in TOPIC_EXEMPT_TYPES
                and (not record.topics or record.topics == ["unclassified"])
            ):
                return "no_topics"

        # Check code-to-text ratio for docs pages
        if record.source_type == SourceType.OFFICIAL_DOCS:
            code_ratio = self._code_ratio(record.text)
            if code_ratio > self.max_code_ratio:
                return "mostly_code"

        # Check for boilerplate/navigation-only content
        if self._is_boilerplate(record.text):
            return "boilerplate"

        return ""

    def _code_ratio(self, text: str) -> float:
        """Calculate the ratio of code block content to total content."""
        code_blocks = re.findall(r"```[\s\S]*?```", text)
        if not code_blocks:
            return 0.0

        code_chars = sum(len(block) for block in code_blocks)
        total_chars = len(text)
        if total_chars == 0:
            return 0.0

        return code_chars / total_chars

    def _is_boilerplate(self, text: str) -> bool:
        """Detect if text is mostly navigation/boilerplate."""
        # Common boilerplate indicators
        boilerplate_phrases = [
            "skip to content",
            "table of contents",
            "cookie policy",
            "privacy policy",
            "terms of service",
            "subscribe to newsletter",
        ]

        text_lower = text.lower()
        boilerplate_count = sum(
            1 for phrase in boilerplate_phrases if phrase in text_lower
        )

        # If more than half the text matches boilerplate patterns
        if boilerplate_count >= 3:
            return True

        # Very short text that's mostly links/navigation
        words = text.split()
        if len(words) < 50:
            link_words = sum(1 for w in words if w.startswith("http") or w.startswith("/"))
            if link_words > len(words) * 0.3:
                return True

        return False
