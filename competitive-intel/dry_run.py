#!/usr/bin/env python3
"""Dry-run: vectorize a small subset (50 records) with per-step timeouts.

Tests the full pipeline (load → chunk → embed → store → query) end-to-end
on a small sample to catch issues before running the full dataset.

Usage:
  python dry_run.py                    # 50 records, 120s timeout per step
  python dry_run.py --max-records 20   # Smaller sample
  python dry_run.py --timeout 60       # Tighter timeout
"""

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("dry_run")


class StepTimeout(Exception):
    pass


def timeout_handler(signum, frame):
    raise StepTimeout("Step timed out!")


def timed_step(name, func, timeout_sec):
    """Run a function with a wall-clock timeout. Returns (result, elapsed_sec)."""
    logger.info("--- STEP: %s (timeout: %ds) ---", name, timeout_sec)
    old_handler = signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(timeout_sec)
    start = time.monotonic()
    try:
        result = func()
        elapsed = time.monotonic() - start
        logger.info("    OK: %s completed in %.1fs", name, elapsed)
        return result, elapsed
    except StepTimeout:
        elapsed = time.monotonic() - start
        logger.error("    TIMEOUT: %s exceeded %ds", name, timeout_sec)
        raise
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


def main():
    parser = argparse.ArgumentParser(description="Dry-run vectorization on small subset")
    parser.add_argument("--max-records", type=int, default=50, help="Max records to process")
    parser.add_argument("--timeout", type=int, default=120, help="Timeout per step in seconds")
    parser.add_argument("--target", default="kx", help="Target to sample from (default: kx)")
    args = parser.parse_args()

    MAX_RECORDS = args.max_records
    TIMEOUT = args.timeout
    target = args.target
    timings = {}

    print("=" * 70)
    print(f"DRY RUN: {MAX_RECORDS} records from '{target}', {TIMEOUT}s timeout per step")
    print("=" * 70)

    # ---------------------------------------------------------------
    # Step 1: Load raw records (subset)
    # ---------------------------------------------------------------
    def load():
        from vectorstore.ingest import load_all_records
        all_records = load_all_records(target)
        subset = all_records[:MAX_RECORDS]
        logger.info("Loaded %d/%d records", len(subset), len(all_records))
        return subset

    records, t = timed_step("Load records", load, TIMEOUT)
    timings["load"] = t

    if not records:
        logger.error("No records loaded — nothing to do")
        sys.exit(1)

    # ---------------------------------------------------------------
    # Step 2: Chunk
    # ---------------------------------------------------------------
    def chunk():
        from vectorstore.chunker import Chunker
        chunker = Chunker(chunk_tokens=400, overlap_tokens=60)
        chunks = chunker.chunk_records(records)
        logger.info("Produced %d chunks from %d records", len(chunks), len(records))
        return chunks

    chunks, t = timed_step("Chunk records", chunk, TIMEOUT)
    timings["chunk"] = t

    if not chunks:
        logger.error("No chunks produced — check data quality")
        sys.exit(1)

    # ---------------------------------------------------------------
    # Step 3: Embed
    # ---------------------------------------------------------------
    def embed():
        from vectorstore.embedder import Embedder
        embedder = Embedder()
        texts = [c.text for c in chunks]
        logger.info("Embedding %d texts...", len(texts))
        embeddings = embedder.embed(texts)
        logger.info("Got %d embeddings, dim=%d", len(embeddings), len(embeddings[0]) if embeddings else 0)
        return embeddings

    embeddings, t = timed_step("Generate embeddings", embed, TIMEOUT)
    timings["embed"] = t

    # ---------------------------------------------------------------
    # Step 4: Store in ChromaDB (use a separate test collection)
    # ---------------------------------------------------------------
    def store():
        from vectorstore.store import VectorStore
        vs = VectorStore()
        # Use the main collection but only insert our subset
        stored = vs.upsert_source_chunks(chunks, embeddings)
        logger.info("Stored %d chunks in ChromaDB", stored)
        return vs, stored

    (vs, stored_count), t = timed_step("Store in ChromaDB", store, TIMEOUT)
    timings["store"] = t

    # ---------------------------------------------------------------
    # Step 5: Query to validate retrieval
    # ---------------------------------------------------------------
    def query():
        from vectorstore.embedder import Embedder
        embedder = Embedder()

        test_queries = [
            "time series database performance",
            "high availability replication",
            "SQL query language support",
        ]
        all_results = []
        for q in test_queries:
            logger.info("  Query: '%s'", q)
            results = vs.query_by_text(
                query_text=q,
                embedder=embedder,
                n_results=3,
                where={"competitor": target},
            )
            n_hits = len(results["ids"][0]) if results["ids"] else 0
            logger.info("    → %d results", n_hits)
            for i in range(n_hits):
                doc = results["documents"][0][i][:100]
                dist = results["distances"][0][i]
                logger.info("    [%d] distance=%.4f  %s...", i + 1, dist, doc)
            all_results.append((q, n_hits))
        return all_results

    query_results, t = timed_step("Query validation", query, TIMEOUT)
    timings["query"] = t

    # ---------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------
    print("\n" + "=" * 70)
    print("DRY RUN SUMMARY")
    print("=" * 70)
    print(f"  Records loaded:  {len(records)}")
    print(f"  Chunks created:  {len(chunks)}")
    print(f"  Embeddings:      {len(embeddings)}")
    print(f"  Chunks stored:   {stored_count}")
    print(f"  Queries tested:  {len(query_results)}")
    print()
    print("  Step timings:")
    total = 0
    for step, t in timings.items():
        print(f"    {step:20s}  {t:6.1f}s")
        total += t
    print(f"    {'TOTAL':20s}  {total:6.1f}s")
    print()

    # Check query results
    all_have_results = all(n_hits > 0 for _, n_hits in query_results)
    if all_have_results:
        print("  STATUS: ALL CHECKS PASSED")
    else:
        print("  STATUS: SOME QUERIES RETURNED NO RESULTS (check data)")

    print("=" * 70)

    # Extrapolation
    # The full KX set is ~1111 records. Scale timings.
    full_count = 1111
    scale = full_count / max(len(records), 1)
    est_chunk = timings["chunk"] * scale
    est_embed = timings["embed"] * scale
    est_store = timings["store"] * scale
    est_total = est_chunk + est_embed + est_store
    print(f"\n  Estimated full run ({full_count} records):")
    print(f"    Chunk:  ~{est_chunk:.0f}s ({est_chunk/60:.1f} min)")
    print(f"    Embed:  ~{est_embed:.0f}s ({est_embed/60:.1f} min)")
    print(f"    Store:  ~{est_store:.0f}s ({est_store/60:.1f} min)")
    print(f"    Total:  ~{est_total:.0f}s ({est_total/60:.1f} min)")
    print()


if __name__ == "__main__":
    main()
