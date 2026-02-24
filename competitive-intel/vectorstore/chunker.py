"""Content-type-aware chunking engine for competitive intelligence data.

Implements different chunking strategies depending on the source type:
- Blog posts: Section-header-aware recursive splitting
- Documentation: Section-level chunking with hierarchy preservation
- GitHub issues/discussions: Comment-boundary chunking
- GitHub releases: Single-chunk per release (typically short)
- Community (HN/Reddit): Comment-level chunking
- Benchmarks/comparisons: Single-chunk (preserve context)

Each chunk is enriched with a context prefix for better embedding quality:
  [Competitor | SourceType | TopicName] actual content...
"""

import hashlib
import logging
import re
from datetime import date
from typing import Optional

import tiktoken

from schemas.source_record import SourceRecord, SourceType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Token counter (shared encoder instance)
# ---------------------------------------------------------------------------
_ENCODER: Optional[tiktoken.Encoding] = None


def _get_encoder() -> tiktoken.Encoding:
    global _ENCODER
    if _ENCODER is None:
        _ENCODER = tiktoken.encoding_for_model("text-embedding-3-small")
    return _ENCODER


def count_tokens(text: str) -> int:
    return len(_get_encoder().encode(text))


# ---------------------------------------------------------------------------
# Chunk dataclass (lightweight, not the full Pydantic model — converted later)
# ---------------------------------------------------------------------------

class RawChunk:
    """Intermediate chunk representation before embedding."""

    __slots__ = (
        "id", "text", "competitor", "source_type", "source_url", "source_title",
        "topic_ids", "credibility", "content_date", "scraped_date",
        "chunk_index", "parent_doc_id", "token_count", "metadata",
    )

    def __init__(
        self,
        text: str,
        competitor: str,
        source_type: str,
        source_url: str,
        source_title: str,
        topic_ids: list[str],
        credibility: str,
        content_date: Optional[date],
        scraped_date: date,
        chunk_index: int,
        parent_doc_id: str,
        metadata: Optional[dict] = None,
    ):
        self.text = text
        self.competitor = competitor
        self.source_type = source_type
        self.source_url = source_url
        self.source_title = source_title
        self.topic_ids = topic_ids
        self.credibility = credibility
        self.content_date = content_date
        self.scraped_date = scraped_date
        self.chunk_index = chunk_index
        self.parent_doc_id = parent_doc_id
        self.token_count = count_tokens(text)
        self.metadata = metadata or {}
        # Deterministic ID based on parent + chunk index
        self.id = self._make_id()

    def _make_id(self) -> str:
        hash_input = f"{self.parent_doc_id}:{self.chunk_index}:{self.text[:100]}"
        short_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:12]
        return f"{self.competitor}-chunk-{short_hash}"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_CHUNK_TOKENS = 400
DEFAULT_OVERLAP_TOKENS = 60
MIN_CHUNK_TOKENS = 50
MAX_CHUNK_TOKENS = 800

# Section header patterns (markdown)
SECTION_HEADERS = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)

# Separators in priority order for recursive splitting
SEPARATORS = ["\n## ", "\n### ", "\n#### ", "\n\n", "\n", ". ", " "]


# ---------------------------------------------------------------------------
# Topic name lookup (loaded once)
# ---------------------------------------------------------------------------

_TOPIC_NAMES: dict[str, str] = {}


def _load_topic_names() -> dict[str, str]:
    """Load topic ID → name mapping from taxonomy.json."""
    global _TOPIC_NAMES
    if _TOPIC_NAMES:
        return _TOPIC_NAMES
    import json
    from pathlib import Path
    taxonomy_path = Path(__file__).parent.parent / "config" / "taxonomy.json"
    if taxonomy_path.exists():
        data = json.load(open(taxonomy_path))
        for tier in data.get("tiers", {}).values():
            for tid, info in tier.get("topics", {}).items():
                _TOPIC_NAMES[tid] = info.get("name", tid)
    return _TOPIC_NAMES


def _get_primary_topic_name(topic_ids: list[str]) -> str:
    names = _load_topic_names()
    for tid in topic_ids:
        if tid in names:
            return names[tid]
    return "General"


# ---------------------------------------------------------------------------
# Context prefix builder
# ---------------------------------------------------------------------------

def _build_context_prefix(record: SourceRecord) -> str:
    """Build a context prefix to prepend to each chunk for better embeddings.

    Format: [CompetitorName | SourceType | PrimaryTopic]
    """
    competitor_display = record.origin.upper() if record.origin == "kx" else record.origin.capitalize()
    source_display = record.source_type.value.replace("_", " ").title()
    topic_display = _get_primary_topic_name(record.topics)
    return f"[{competitor_display} | {source_display} | {topic_display}]"


# ---------------------------------------------------------------------------
# Main chunking dispatcher
# ---------------------------------------------------------------------------

class Chunker:
    """Content-type-aware chunking engine."""

    def __init__(
        self,
        chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
        overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
    ):
        self.chunk_tokens = chunk_tokens
        self.overlap_tokens = overlap_tokens

    def chunk_record(self, record: SourceRecord) -> list[RawChunk]:
        """Chunk a single SourceRecord using the appropriate strategy."""
        if not record.text or not record.text.strip():
            return []

        source_type = record.source_type
        context_prefix = _build_context_prefix(record)

        if source_type == SourceType.BLOG:
            text_chunks = self._chunk_blog(record.text, record.title)
        elif source_type == SourceType.OFFICIAL_DOCS:
            text_chunks = self._chunk_docs(record.text, record.title)
        elif source_type == SourceType.GITHUB_ISSUE:
            text_chunks = self._chunk_github_issue(record)
        elif source_type == SourceType.GITHUB_DISCUSSION:
            text_chunks = self._chunk_github_discussion(record)
        elif source_type in (SourceType.GITHUB_RELEASE,):
            text_chunks = self._chunk_single(record.text, record.title)
        elif source_type in (SourceType.COMMUNITY_HN, SourceType.COMMUNITY_REDDIT):
            text_chunks = self._chunk_community(record)
        elif source_type in (SourceType.BENCHMARK, SourceType.COMPARISON_PAGE):
            text_chunks = self._chunk_single(record.text, record.title)
        else:
            text_chunks = self._chunk_generic(record.text, record.title)

        # Convert text chunks → RawChunk objects with context prefix
        raw_chunks = []
        for i, text in enumerate(text_chunks):
            prefixed_text = f"{context_prefix} {text}"
            chunk = RawChunk(
                text=prefixed_text,
                competitor=record.origin,
                source_type=record.source_type.value,
                source_url=record.url,
                source_title=record.title,
                topic_ids=record.topics if record.topics else ["unclassified"],
                credibility=record.credibility.value if hasattr(record.credibility, 'value') else str(record.credibility),
                content_date=record.content_date,
                scraped_date=record.scraped_date,
                chunk_index=i,
                parent_doc_id=record.id,
                metadata=self._extract_source_metadata(record),
            )
            raw_chunks.append(chunk)

        return raw_chunks

    def chunk_records(self, records: list[SourceRecord]) -> list[RawChunk]:
        """Chunk a batch of SourceRecords."""
        import time as _time
        all_chunks = []
        total = len(records)
        log_interval = max(1, total // 20)  # Log every ~5%
        slow_threshold = 2.0  # seconds — flag records slower than this
        start_all = _time.perf_counter()

        for i, record in enumerate(records):
            text_len = len(record.text) if record.text else 0
            if text_len > 50000 or i == 282:  # Log big records and the known stall point
                logger.info(
                    "  → START record %d/%d: %s type=%s text_len=%d title='%.80s'",
                    i + 1, total, record.origin, record.source_type.value, text_len, record.title or "",
                )
            t0 = _time.perf_counter()
            chunks = self.chunk_record(record)
            elapsed = _time.perf_counter() - t0
            all_chunks.extend(chunks)

            if elapsed > slow_threshold:
                text_len = len(record.text) if record.text else 0
                logger.warning(
                    "SLOW RECORD %d/%d: %.1fs — %s type=%s text_len=%d chunks=%d title='%.80s'",
                    i + 1, total, elapsed, record.origin, record.source_type.value,
                    text_len, len(chunks), record.title or "",
                )

            if (i + 1) % log_interval == 0 or (i + 1) == total:
                wall = _time.perf_counter() - start_all
                rate = (i + 1) / max(wall, 0.001)
                eta = (total - i - 1) / max(rate, 0.001)
                logger.info(
                    "Chunking progress: %d/%d records (%3.0f%%), %d chunks, %.1fs elapsed, ETA %.0fs",
                    i + 1, total, (i + 1) / total * 100, len(all_chunks), wall, eta,
                )

        total_time = _time.perf_counter() - start_all
        logger.info(
            "Chunked %d records into %d chunks (avg %.1f chunks/record) in %.1fs",
            len(records), len(all_chunks),
            len(all_chunks) / max(len(records), 1), total_time,
        )
        return all_chunks

    # -------------------------------------------------------------------
    # Strategy: Blog posts — section-header-aware recursive splitting
    # -------------------------------------------------------------------

    def _chunk_blog(self, text: str, title: str) -> list[str]:
        """Split blog posts by section headers, then recursively within sections."""
        sections = self._split_by_headers(text)
        chunks = []

        for section_header, section_text in sections:
            full_text = f"{title}\n{section_header}\n{section_text}" if section_header else f"{title}\n{section_text}"

            if count_tokens(full_text) <= self.chunk_tokens:
                if count_tokens(full_text) >= MIN_CHUNK_TOKENS:
                    chunks.append(full_text.strip())
            else:
                sub_chunks = self._recursive_split(full_text)
                # Prepend section header to subsequent sub-chunks for context
                for j, sc in enumerate(sub_chunks):
                    if j > 0 and section_header and not sc.startswith(section_header):
                        sc = f"{section_header}\n{sc}"
                    chunks.append(sc.strip())

        if not chunks:
            chunks = self._recursive_split(f"{title}\n{text}")

        return chunks

    # -------------------------------------------------------------------
    # Strategy: Documentation — section-level with hierarchy
    # -------------------------------------------------------------------

    def _chunk_docs(self, text: str, title: str) -> list[str]:
        """Split documentation by section headers, preserving hierarchy."""
        sections = self._split_by_headers(text)
        chunks = []
        # Build a hierarchy path
        hierarchy: list[str] = [title]

        for section_header, section_text in sections:
            # Update hierarchy based on header level
            if section_header:
                level = section_header.count("#")
                # Trim hierarchy to current level
                hierarchy = hierarchy[:level]
                clean_header = section_header.lstrip("#").strip()
                hierarchy.append(clean_header)

            hierarchy_path = " > ".join(hierarchy)
            full_text = f"{hierarchy_path}\n{section_text}"

            if count_tokens(full_text) <= self.chunk_tokens:
                if count_tokens(full_text) >= MIN_CHUNK_TOKENS:
                    chunks.append(full_text.strip())
            else:
                sub_chunks = self._recursive_split(full_text)
                for j, sc in enumerate(sub_chunks):
                    if j > 0:
                        sc = f"{hierarchy_path}\n{sc}"
                    chunks.append(sc.strip())

        if not chunks:
            chunks = self._recursive_split(f"{title}\n{text}")

        return chunks

    # -------------------------------------------------------------------
    # Strategy: GitHub Issues — comment-boundary chunking
    # -------------------------------------------------------------------

    def _chunk_github_issue(self, record: SourceRecord) -> list[str]:
        """Chunk GitHub issues: issue body + individual comments."""
        chunks = []
        meta = record.metadata

        # Issue body as first chunk (with title + labels)
        labels = meta.get("labels", [])
        state = meta.get("state", "unknown")
        label_str = f" [{', '.join(labels)}]" if labels else ""
        header = f"{record.title}{label_str} (state: {state})"

        body_text = f"{header}\n{record.text}"
        if count_tokens(body_text) <= self.chunk_tokens:
            chunks.append(body_text.strip())
        else:
            # Split long issue body
            sub_chunks = self._recursive_split(body_text)
            chunks.extend(sc.strip() for sc in sub_chunks)

        # Top comments as separate chunks
        top_comments = meta.get("top_comments", [])
        comment_buffer = []
        buffer_tokens = 0

        for comment in top_comments:
            comment_text = f"Comment on '{record.title}': {comment}"
            ctokens = count_tokens(comment_text)

            if ctokens >= self.chunk_tokens:
                # Flush buffer first
                if comment_buffer:
                    chunks.append("\n\n".join(comment_buffer).strip())
                    comment_buffer = []
                    buffer_tokens = 0
                # Split the long comment
                sub_chunks = self._recursive_split(comment_text)
                chunks.extend(sc.strip() for sc in sub_chunks)
            elif buffer_tokens + ctokens > self.chunk_tokens:
                # Flush buffer and start new
                if comment_buffer:
                    chunks.append("\n\n".join(comment_buffer).strip())
                comment_buffer = [comment_text]
                buffer_tokens = ctokens
            else:
                comment_buffer.append(comment_text)
                buffer_tokens += ctokens

        if comment_buffer:
            merged = "\n\n".join(comment_buffer).strip()
            if count_tokens(merged) >= MIN_CHUNK_TOKENS:
                chunks.append(merged)

        return chunks

    # -------------------------------------------------------------------
    # Strategy: GitHub Discussions — similar to issues, highlight answers
    # -------------------------------------------------------------------

    def _chunk_github_discussion(self, record: SourceRecord) -> list[str]:
        """Chunk GitHub discussions, giving special treatment to answers."""
        chunks = []
        meta = record.metadata

        category = meta.get("category", "")
        is_answered = meta.get("is_answered", False)
        header = f"{record.title} (discussion, category: {category})"

        body_text = f"{header}\n{record.text}"
        if count_tokens(body_text) <= self.chunk_tokens:
            chunks.append(body_text.strip())
        else:
            sub_chunks = self._recursive_split(body_text)
            chunks.extend(sc.strip() for sc in sub_chunks)

        # If there's an accepted answer, add it as a high-priority chunk
        answer_body = meta.get("answer_body")
        if answer_body and is_answered:
            answer_text = f"Accepted answer for '{record.title}': {answer_body}"
            if count_tokens(answer_text) <= self.chunk_tokens:
                chunks.append(answer_text.strip())
            else:
                sub_chunks = self._recursive_split(answer_text)
                chunks.extend(sc.strip() for sc in sub_chunks)

        return chunks

    # -------------------------------------------------------------------
    # Strategy: Community (HN/Reddit) — comment-level chunking
    # -------------------------------------------------------------------

    def _chunk_community(self, record: SourceRecord) -> list[str]:
        """Chunk community discussions at the comment level."""
        chunks = []
        meta = record.metadata

        # Main post as first chunk
        post_text = f"{record.title}\n{record.text}"
        if count_tokens(post_text) <= self.chunk_tokens:
            if count_tokens(post_text) >= MIN_CHUNK_TOKENS:
                chunks.append(post_text.strip())
        else:
            sub_chunks = self._recursive_split(post_text)
            chunks.extend(sc.strip() for sc in sub_chunks)

        # Comments
        top_comments = meta.get("top_comments", [])
        for comment in top_comments:
            comment_text = f"Community comment on '{record.title}': {comment}"
            ctokens = count_tokens(comment_text)

            if ctokens < MIN_CHUNK_TOKENS:
                continue  # Skip very short/low-value comments

            if ctokens <= self.chunk_tokens:
                chunks.append(comment_text.strip())
            else:
                sub_chunks = self._recursive_split(comment_text)
                chunks.extend(sc.strip() for sc in sub_chunks)

        return chunks

    # -------------------------------------------------------------------
    # Strategy: Single chunk (releases, benchmarks, comparisons)
    # -------------------------------------------------------------------

    def _chunk_single(self, text: str, title: str) -> list[str]:
        """Treat the entire document as a single chunk (or split if too long)."""
        full_text = f"{title}\n{text}"
        tokens = count_tokens(full_text)

        if tokens <= MAX_CHUNK_TOKENS:
            if tokens >= MIN_CHUNK_TOKENS:
                return [full_text.strip()]
            return []

        return [sc.strip() for sc in self._recursive_split(full_text)]

    # -------------------------------------------------------------------
    # Strategy: Generic fallback
    # -------------------------------------------------------------------

    def _chunk_generic(self, text: str, title: str) -> list[str]:
        """Fallback: recursive splitting with title prepended."""
        full_text = f"{title}\n{text}"
        return [sc.strip() for sc in self._recursive_split(full_text)]

    # -------------------------------------------------------------------
    # Core splitting utilities
    # -------------------------------------------------------------------

    def _split_by_headers(self, text: str) -> list[tuple[str, str]]:
        """Split text by markdown headers, returning (header, content) pairs."""
        parts = SECTION_HEADERS.split(text)
        sections: list[tuple[str, str]] = []

        if not parts:
            return [("", text)]

        # First part is content before any header
        if parts[0].strip():
            sections.append(("", parts[0].strip()))

        # Remaining parts come in groups of 3: (hashes, title, content)
        i = 1
        while i < len(parts) - 2:
            hashes = parts[i]
            title = parts[i + 1]
            content = parts[i + 2] if i + 2 < len(parts) else ""
            header = f"{hashes} {title}"
            sections.append((header, content.strip()))
            i += 3

        if not sections:
            sections = [("", text)]

        return sections

    def _recursive_split(self, text: str) -> list[str]:
        """Recursively split text into chunks of ~chunk_tokens with overlap."""
        tokens = count_tokens(text)
        if tokens <= self.chunk_tokens:
            return [text] if tokens >= MIN_CHUNK_TOKENS else ([text] if text.strip() else [])

        # Try each separator in priority order
        for sep in SEPARATORS:
            parts = text.split(sep)
            if len(parts) <= 1:
                continue

            chunks = self._merge_splits(parts, sep)
            if len(chunks) > 1:
                return chunks

        # Last resort: hard split by tokens
        return self._hard_split(text)

    def _merge_splits(self, parts: list[str], separator: str) -> list[str]:
        """Merge small splits into chunks respecting the token limit with overlap."""
        encoder = _get_encoder()
        chunks: list[str] = []
        current_parts: list[str] = []
        current_tokens = 0

        for part in parts:
            part_tokens = len(encoder.encode(part))

            if current_tokens + part_tokens > self.chunk_tokens and current_parts:
                # Flush current chunk
                chunk_text = separator.join(current_parts)
                chunks.append(chunk_text)

                # Overlap: keep last parts that fit within overlap budget
                overlap_parts: list[str] = []
                overlap_tokens = 0
                for p in reversed(current_parts):
                    pt = len(encoder.encode(p))
                    if overlap_tokens + pt > self.overlap_tokens:
                        break
                    overlap_parts.insert(0, p)
                    overlap_tokens += pt

                current_parts = overlap_parts + [part]
                current_tokens = overlap_tokens + part_tokens
            else:
                current_parts.append(part)
                current_tokens += part_tokens

        if current_parts:
            chunk_text = separator.join(current_parts)
            if count_tokens(chunk_text) >= MIN_CHUNK_TOKENS or not chunks:
                chunks.append(chunk_text)
            elif chunks:
                # Merge tiny trailing chunk with previous
                chunks[-1] = chunks[-1] + separator + chunk_text

        return chunks

    def _hard_split(self, text: str) -> list[str]:
        """Hard split by token count when no separator works."""
        encoder = _get_encoder()
        tokens = encoder.encode(text)
        chunks = []
        start = 0

        while start < len(tokens):
            end = min(start + self.chunk_tokens, len(tokens))
            chunk_tokens = tokens[start:end]
            chunk_text = encoder.decode(chunk_tokens)
            chunks.append(chunk_text)
            # If we've reached the end, stop
            if end >= len(tokens):
                break
            start = end - self.overlap_tokens  # overlap

        return chunks

    # -------------------------------------------------------------------
    # Source metadata extraction
    # -------------------------------------------------------------------

    def _extract_source_metadata(self, record: SourceRecord) -> dict:
        """Extract source-specific metadata for ChromaDB filtering."""
        meta = {}

        # GitHub-specific
        if record.source_type == SourceType.GITHUB_ISSUE:
            rm = record.metadata
            meta["github_state"] = rm.get("state", "")
            meta["is_bug"] = rm.get("is_bug", False)
            meta["is_feature_request"] = rm.get("is_feature_request", False)
            meta["comments_count"] = rm.get("comments_count", 0)
            if rm.get("labels"):
                meta["labels"] = ",".join(rm["labels"])

        elif record.source_type == SourceType.GITHUB_DISCUSSION:
            rm = record.metadata
            meta["is_answered"] = rm.get("is_answered", False)
            meta["category"] = rm.get("category", "")

        elif record.source_type == SourceType.GITHUB_RELEASE:
            rm = record.metadata
            meta["tag_name"] = rm.get("tag_name", "")
            meta["is_prerelease"] = rm.get("is_prerelease", False)

        elif record.source_type == SourceType.BLOG:
            rm = record.metadata
            meta["relevance_score"] = rm.get("relevance_score", 0.0)
            kw = rm.get("priority_keywords_matched", [])
            if kw:
                meta["priority_keywords"] = ",".join(kw[:10])

        elif record.source_type in (SourceType.COMMUNITY_HN, SourceType.COMMUNITY_REDDIT):
            rm = record.metadata
            meta["points"] = rm.get("points", rm.get("score", 0))
            meta["num_comments"] = rm.get("num_comments", 0)

        return meta
