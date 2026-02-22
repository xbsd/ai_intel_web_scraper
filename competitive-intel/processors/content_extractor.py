"""Content extractor for cleaning and normalizing scraped text.

Provides additional cleaning on top of the basic HTML extraction in utils.py.
Removes remaining boilerplate, normalizes whitespace, and extracts structured
metadata from the text content.
"""

import logging
import re
from typing import Optional

from schemas.source_record import SourceRecord

logger = logging.getLogger(__name__)


class ContentExtractor:
    """Cleans and normalizes scraped text content."""

    def __init__(self):
        # Patterns for content that should be stripped
        self._strip_patterns = [
            # Cookie consent / GDPR banners
            re.compile(
                r"(we use cookies|cookie policy|accept all cookies|manage preferences).*?\.",
                re.IGNORECASE | re.DOTALL,
            ),
            # Newsletter signup CTAs
            re.compile(
                r"(subscribe to|sign up for|join our|get the latest).*?(newsletter|updates|news).*?\.",
                re.IGNORECASE | re.DOTALL,
            ),
            # Social media share buttons text
            re.compile(
                r"(share on|follow us on|tweet this|share this).*?(twitter|linkedin|facebook|x\.com).*?\n",
                re.IGNORECASE,
            ),
            # Copyright notices
            re.compile(
                r"©\s*\d{4}.*?(all rights reserved|inc\.|ltd\.|corp\.).*?\n",
                re.IGNORECASE,
            ),
        ]

    def clean(self, record: SourceRecord) -> SourceRecord:
        """Clean a single SourceRecord's text content.

        Modifies the record in place and returns it.
        """
        text = record.text

        # Apply strip patterns
        for pattern in self._strip_patterns:
            text = pattern.sub("", text)

        # Normalize whitespace
        text = self._normalize_whitespace(text)

        # Remove excessive blank lines (more than 2 consecutive)
        text = re.sub(r"\n{3,}", "\n\n", text)

        # Trim leading/trailing whitespace
        text = text.strip()

        record.text = text
        record.word_count = len(text.split())
        return record

    def clean_batch(self, records: list[SourceRecord]) -> list[SourceRecord]:
        """Clean a batch of SourceRecords."""
        cleaned = [self.clean(r) for r in records]
        logger.info("Cleaned %d records", len(cleaned))
        return cleaned

    def _normalize_whitespace(self, text: str) -> str:
        """Normalize whitespace while preserving code blocks and tables."""
        # Split by code blocks, normalize non-code parts
        parts = re.split(r"(```[\s\S]*?```)", text)
        normalized = []

        for i, part in enumerate(parts):
            if part.startswith("```"):
                # Preserve code blocks as-is
                normalized.append(part)
            else:
                # Normalize whitespace in regular text
                lines = part.split("\n")
                cleaned_lines = []
                for line in lines:
                    # Preserve markdown heading formatting
                    if line.strip().startswith("#"):
                        cleaned_lines.append(line)
                    # Preserve table formatting
                    elif line.strip().startswith("|"):
                        cleaned_lines.append(line)
                    # Preserve list items
                    elif line.strip().startswith(("-", "*", "1.", "2.", "3.")):
                        cleaned_lines.append(line)
                    else:
                        # Collapse multiple spaces
                        cleaned = re.sub(r"  +", " ", line)
                        cleaned_lines.append(cleaned)
                normalized.append("\n".join(cleaned_lines))

        return "".join(normalized)
