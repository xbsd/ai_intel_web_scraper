"""Deduplication of scraped content.

Handles three levels of deduplication:
1. Exact URL dedup — same URL from different search terms/scrapers
2. Near-duplicate text detection using MinHash (datasketch)
3. GitHub-specific dedup by issue/discussion number
"""

import logging
from typing import Optional

from datasketch import MinHash, MinHashLSH

from schemas.source_record import SourceRecord, SourceType

logger = logging.getLogger(__name__)


class Deduplicator:
    """Removes duplicate and near-duplicate content from scraped records."""

    def __init__(
        self,
        similarity_threshold: float = 0.7,
        num_perm: int = 128,
    ):
        """Initialize the deduplicator.

        Args:
            similarity_threshold: Jaccard similarity threshold for near-duplicates.
                0.7 means 70% similar content is considered a duplicate.
            num_perm: Number of permutation functions for MinHash.
        """
        self.similarity_threshold = similarity_threshold
        self.num_perm = num_perm

    def deduplicate(self, records: list[SourceRecord]) -> list[SourceRecord]:
        """Remove duplicates from a list of SourceRecords.

        Applies dedup in order:
        1. Exact URL dedup
        2. GitHub ID dedup
        3. Near-duplicate text dedup (MinHash LSH)

        Returns:
            Deduplicated list of SourceRecords.
        """
        initial_count = len(records)

        # Step 1: Exact URL dedup
        records = self._url_dedup(records)
        after_url = len(records)

        # Step 2: GitHub-specific dedup
        records = self._github_dedup(records)
        after_github = len(records)

        # Step 3: Near-duplicate text dedup
        records = self._minhash_dedup(records)
        after_minhash = len(records)

        logger.info(
            "Deduplication: %d → %d (URL: -%d, GitHub: -%d, MinHash: -%d)",
            initial_count,
            after_minhash,
            initial_count - after_url,
            after_url - after_github,
            after_github - after_minhash,
        )
        return records

    def _url_dedup(self, records: list[SourceRecord]) -> list[SourceRecord]:
        """Remove records with duplicate URLs, keeping the first occurrence."""
        seen_urls: set[str] = set()
        unique = []
        for record in records:
            url = record.url.rstrip("/").lower()
            if url not in seen_urls:
                seen_urls.add(url)
                unique.append(record)
        return unique

    def _github_dedup(self, records: list[SourceRecord]) -> list[SourceRecord]:
        """Remove duplicate GitHub issues/discussions by number."""
        github_types = {SourceType.GITHUB_ISSUE, SourceType.GITHUB_DISCUSSION}
        seen_github: set[str] = set()
        unique = []

        for record in records:
            if record.source_type in github_types:
                metadata = record.metadata
                if record.source_type == SourceType.GITHUB_ISSUE:
                    key = f"{record.origin}-issue-{metadata.get('issue_number', '')}"
                else:
                    key = f"{record.origin}-discussion-{metadata.get('discussion_number', '')}"

                if key in seen_github:
                    continue
                seen_github.add(key)

            unique.append(record)

        return unique

    def _minhash_dedup(self, records: list[SourceRecord]) -> list[SourceRecord]:
        """Remove near-duplicate content using MinHash LSH."""
        if len(records) <= 1:
            return records

        lsh = MinHashLSH(threshold=self.similarity_threshold, num_perm=self.num_perm)
        minhashes: dict[str, MinHash] = {}

        # Build MinHash for each record
        for record in records:
            mh = self._text_to_minhash(record.text)
            minhashes[record.id] = mh

        # Insert into LSH and find duplicates
        keep_ids: set[str] = set()
        duplicate_ids: set[str] = set()

        for record in records:
            if record.id in duplicate_ids:
                continue

            mh = minhashes[record.id]

            # Check if any existing entry is similar
            try:
                result = lsh.query(mh)
                if result:
                    # This is a near-duplicate of something already kept
                    duplicate_ids.add(record.id)
                    continue
            except ValueError:
                pass

            # Keep this record and add to LSH
            try:
                lsh.insert(record.id, mh)
                keep_ids.add(record.id)
            except ValueError:
                # Duplicate key — skip
                pass

        return [r for r in records if r.id in keep_ids]

    def _text_to_minhash(self, text: str) -> MinHash:
        """Convert text to a MinHash using word-level 3-shingles."""
        mh = MinHash(num_perm=self.num_perm)
        words = text.lower().split()

        # Create 3-word shingles
        for i in range(len(words) - 2):
            shingle = " ".join(words[i : i + 3])
            mh.update(shingle.encode("utf-8"))

        return mh
