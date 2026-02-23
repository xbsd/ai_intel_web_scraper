"""Full ingestion pipeline: load raw data → chunk → embed → store in ChromaDB.

Usage (standalone):
  python -m vectorstore.ingest --target kx
  python -m vectorstore.ingest --target questdb
  python -m vectorstore.ingest --target all
  python -m vectorstore.ingest --target all --reset  # wipe and re-ingest

Or via the main pipeline:
  python pipeline.py vectorize --target all
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional

from schemas.source_record import SourceRecord
from vectorstore.chunker import Chunker, RawChunk
from vectorstore.embedder import Embedder
from vectorstore.store import VectorStore

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
RAW_DIR = PROJECT_ROOT / "data" / "raw"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_all_records(target: str) -> list[SourceRecord]:
    """Load all raw JSON records for a given target (competitor short name)."""
    import orjson

    target_dir = RAW_DIR / target
    if not target_dir.exists():
        logger.warning("No raw data directory for '%s'", target)
        return []

    records: list[SourceRecord] = []
    json_files = sorted(target_dir.rglob("*.json"))

    for jf in json_files:
        try:
            data = orjson.loads(jf.read_bytes())
            items = data if isinstance(data, list) else [data]
            for item in items:
                try:
                    record = SourceRecord(**item)
                    records.append(record)
                except Exception as e:
                    logger.debug("Skipping invalid record in %s: %s", jf.name, e)
        except Exception as e:
            logger.error("Failed to load %s: %s", jf, e)

    logger.info("Loaded %d records for '%s' from %d files", len(records), target, len(json_files))
    return records


def get_all_targets() -> list[str]:
    """Get all configured competitor short names."""
    config_dir = CONFIG_DIR / "competitors"
    return [f.stem for f in config_dir.glob("*.json")]


# ---------------------------------------------------------------------------
# Main ingestion logic
# ---------------------------------------------------------------------------

def ingest_target(
    target: str,
    chunker: Chunker,
    embedder: Embedder,
    store: VectorStore,
) -> dict:
    """Ingest all raw data for a single target into the vector store.

    Returns a stats dict: {records_loaded, chunks_created, chunks_stored}.
    """
    # 1. Load raw records
    records = load_all_records(target)
    if not records:
        logger.warning("No records to ingest for '%s'", target)
        return {"records_loaded": 0, "chunks_created": 0, "chunks_stored": 0}

    # 2. Chunk
    logger.info("Chunking %d records for '%s'...", len(records), target)
    chunks = chunker.chunk_records(records)

    if not chunks:
        logger.warning("No chunks produced for '%s'", target)
        return {"records_loaded": len(records), "chunks_created": 0, "chunks_stored": 0}

    # 3. Embed
    logger.info("Generating embeddings for %d chunks...", len(chunks))
    texts = [chunk.text for chunk in chunks]
    embeddings = embedder.embed(texts)

    # 4. Store
    logger.info("Storing %d chunks in ChromaDB...", len(chunks))
    stored = store.upsert_source_chunks(chunks, embeddings)

    stats = {
        "records_loaded": len(records),
        "chunks_created": len(chunks),
        "chunks_stored": stored,
    }
    logger.info("Ingestion complete for '%s': %s", target, stats)
    return stats


def ingest_all(
    targets: Optional[list[str]] = None,
    reset: bool = False,
    chunk_tokens: int = 400,
    overlap_tokens: int = 60,
) -> dict[str, dict]:
    """Run the full ingestion pipeline for one or more targets.

    Args:
        targets: List of target names, or None for all.
        reset: Whether to wipe existing collections first.
        chunk_tokens: Target chunk size in tokens.
        overlap_tokens: Overlap between adjacent chunks.

    Returns:
        Dict mapping target name → stats dict.
    """
    if targets is None:
        targets = get_all_targets()

    # Initialize components
    chunker = Chunker(chunk_tokens=chunk_tokens, overlap_tokens=overlap_tokens)
    embedder = Embedder()
    store = VectorStore()

    if reset:
        logger.info("Resetting vector store (wiping all collections)...")
        store.reset()

    all_stats: dict[str, dict] = {}

    for target in targets:
        logger.info("=" * 60)
        logger.info("INGESTING: %s", target)
        logger.info("=" * 60)

        stats = ingest_target(target, chunker, embedder, store)
        all_stats[target] = stats

    # Print summary
    _print_summary(all_stats, store)
    return all_stats


def _print_summary(all_stats: dict[str, dict], store: VectorStore):
    """Print a summary of the ingestion results."""
    print("\n" + "=" * 70)
    print("VECTORIZATION SUMMARY")
    print("=" * 70)

    total_records = 0
    total_chunks = 0
    total_stored = 0

    for target, stats in all_stats.items():
        print(f"\n  {target}:")
        print(f"    Records loaded:  {stats['records_loaded']}")
        print(f"    Chunks created:  {stats['chunks_created']}")
        print(f"    Chunks stored:   {stats['chunks_stored']}")
        total_records += stats["records_loaded"]
        total_chunks += stats["chunks_created"]
        total_stored += stats["chunks_stored"]

    print(f"\n  TOTAL:")
    print(f"    Records:  {total_records}")
    print(f"    Chunks:   {total_chunks}")
    print(f"    Stored:   {total_stored}")

    # Collection stats
    db_stats = store.get_stats()
    print(f"\n  ChromaDB collections:")
    for name, info in db_stats.items():
        print(f"    {name}: {info.get('count', 0)} vectors")

    print("=" * 70)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    parser = argparse.ArgumentParser(description="Ingest raw data into the vector store")
    parser.add_argument("--target", required=True, help="Competitor short name or 'all'")
    parser.add_argument("--reset", action="store_true", help="Wipe existing data first")
    parser.add_argument("--chunk-tokens", type=int, default=400, help="Target chunk size")
    parser.add_argument("--overlap-tokens", type=int, default=60, help="Chunk overlap")
    args = parser.parse_args()

    targets = None if args.target == "all" else [args.target]
    ingest_all(
        targets=targets,
        reset=args.reset,
        chunk_tokens=args.chunk_tokens,
        overlap_tokens=args.overlap_tokens,
    )


if __name__ == "__main__":
    main()
