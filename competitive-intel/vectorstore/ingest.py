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
import time
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

    t0 = time.perf_counter()
    target_dir = RAW_DIR / target
    if not target_dir.exists():
        logger.warning("No raw data directory for '%s'", target)
        return []

    records: list[SourceRecord] = []
    json_files = sorted(target_dir.rglob("*.json"))
    skipped = 0

    for jf in json_files:
        file_count_before = len(records)
        try:
            data = orjson.loads(jf.read_bytes())
            items = data if isinstance(data, list) else [data]
            for item in items:
                try:
                    record = SourceRecord(**item)
                    records.append(record)
                except Exception as e:
                    skipped += 1
                    logger.debug("Skipping invalid record in %s: %s", jf.name, e)
        except Exception as e:
            logger.error("Failed to load %s: %s", jf, e)
        file_count = len(records) - file_count_before
        logger.info("  [load] %s → %d records", jf.relative_to(RAW_DIR), file_count)

    elapsed = time.perf_counter() - t0
    logger.info(
        "Loaded %d records for '%s' from %d files (skipped %d invalid) in %.1fs",
        len(records), target, len(json_files), skipped, elapsed,
    )
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
    pipeline_start = time.perf_counter()

    # 1. Load raw records
    logger.info("[%s] STEP 1/4: Loading raw records...", target)
    t0 = time.perf_counter()
    records = load_all_records(target)
    load_elapsed = time.perf_counter() - t0
    if not records:
        logger.warning("No records to ingest for '%s'", target)
        return {"records_loaded": 0, "chunks_created": 0, "chunks_stored": 0}
    logger.info("[%s] STEP 1/4 done: %d records loaded in %.1fs", target, len(records), load_elapsed)

    # 2. Chunk
    logger.info("[%s] STEP 2/4: Chunking %d records...", target, len(records))
    t0 = time.perf_counter()
    chunks = chunker.chunk_records(records)
    chunk_elapsed = time.perf_counter() - t0

    if not chunks:
        logger.warning("No chunks produced for '%s'", target)
        return {"records_loaded": len(records), "chunks_created": 0, "chunks_stored": 0}
    logger.info("[%s] STEP 2/4 done: %d chunks in %.1fs (%.0f chunks/sec)", target, len(chunks), chunk_elapsed, len(chunks) / max(chunk_elapsed, 0.001))

    # 3. Embed
    logger.info("[%s] STEP 3/4: Generating embeddings for %d chunks...", target, len(chunks))
    t0 = time.perf_counter()
    texts = [chunk.text for chunk in chunks]
    embeddings = embedder.embed(texts)
    embed_elapsed = time.perf_counter() - t0
    logger.info("[%s] STEP 3/4 done: %d embeddings in %.1fs (%.1f chunks/sec)", target, len(embeddings), embed_elapsed, len(embeddings) / max(embed_elapsed, 0.001))

    # 4. Store
    logger.info("[%s] STEP 4/4: Storing %d chunks in ChromaDB...", target, len(chunks))
    t0 = time.perf_counter()
    stored = store.upsert_source_chunks(chunks, embeddings)
    store_elapsed = time.perf_counter() - t0
    logger.info("[%s] STEP 4/4 done: %d chunks stored in %.1fs", target, stored, store_elapsed)

    total_elapsed = time.perf_counter() - pipeline_start
    stats = {
        "records_loaded": len(records),
        "chunks_created": len(chunks),
        "chunks_stored": stored,
        "timings": {
            "load_s": round(load_elapsed, 1),
            "chunk_s": round(chunk_elapsed, 1),
            "embed_s": round(embed_elapsed, 1),
            "store_s": round(store_elapsed, 1),
            "total_s": round(total_elapsed, 1),
        },
    }
    logger.info("[%s] Ingestion complete in %.1fs: %s", target, total_elapsed, stats)
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
        logger.warning("Resetting vector store (wiping all collections). Use without --reset to add incrementally.")
        store.reset()
    else:
        logger.info("Running in incremental mode (existing vectors preserved). Use --reset to wipe first.")

    all_stats: dict[str, dict] = {}
    overall_start = time.perf_counter()

    for i, target in enumerate(targets, 1):
        logger.info("=" * 60)
        logger.info("INGESTING: %s (%d/%d targets)", target, i, len(targets))
        logger.info("=" * 60)

        stats = ingest_target(target, chunker, embedder, store)
        all_stats[target] = stats

    overall_elapsed = time.perf_counter() - overall_start
    logger.info("All targets completed in %.1fs", overall_elapsed)

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
        timings = stats.get("timings", {})
        if timings:
            print(f"    Timings:  load={timings.get('load_s', '?')}s  chunk={timings.get('chunk_s', '?')}s  embed={timings.get('embed_s', '?')}s  store={timings.get('store_s', '?')}s  total={timings.get('total_s', '?')}s")
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
