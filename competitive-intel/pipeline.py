#!/usr/bin/env python3
"""Main pipeline orchestrator for competitive intelligence scraping and generation.

Usage:
  python pipeline.py scrape --target kx                 # Scrape KX sources
  python pipeline.py scrape --target questdb             # Scrape QuestDB sources
  python pipeline.py scrape --target all                 # Scrape everything

  python pipeline.py process --target questdb            # Tag, filter, dedup
  python pipeline.py process --target all                # Process all

  python pipeline.py generate --competitor questdb       # Generate all topics
  python pipeline.py generate --competitor questdb --topic high_availability

  python pipeline.py vectorize --target all              # Chunk + embed + store
  python pipeline.py vectorize --target questdb --reset  # Wipe & re-ingest
  python pipeline.py vector-status                       # ChromaDB stats
  python pipeline.py vector-query "QuestDB HA"           # Test query

  python pipeline.py status                              # Show pipeline status
  python pipeline.py export --competitor questdb          # Export for review

  python pipeline.py serve --port 8501                   # Launch Q&A web interface
"""

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Project paths
PROJECT_ROOT = Path(__file__).parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
GENERATED_DIR = DATA_DIR / "generated"
REVIEWED_DIR = DATA_DIR / "reviewed"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(PROJECT_ROOT / "pipeline.log"),
    ],
)
logger = logging.getLogger(__name__)


def load_competitor_config(name: str) -> dict:
    """Load a competitor configuration file."""
    config_path = CONFIG_DIR / "competitors" / f"{name}.json"
    if not config_path.exists():
        raise FileNotFoundError(f"Competitor config not found: {config_path}")
    with open(config_path) as f:
        return json.load(f)


def load_taxonomy() -> dict:
    """Load the taxonomy configuration."""
    with open(CONFIG_DIR / "taxonomy.json") as f:
        return json.load(f)


def get_all_competitors() -> list[str]:
    """Get all configured competitor short names."""
    config_dir = CONFIG_DIR / "competitors"
    return [f.stem for f in config_dir.glob("*.json")]


# ---------------------------------------------------------------------------
# SCRAPE
# ---------------------------------------------------------------------------

def cmd_scrape(args):
    """Run the scraping pipeline for specified targets."""
    from scrapers.docs_scraper import scrape_docs
    from scrapers.github_scraper import scrape_github
    from scrapers.blog_scraper import scrape_blog
    from scrapers.community_scraper import scrape_community
    from scrapers.benchmark_scraper import scrape_benchmarks

    targets = get_all_competitors() if args.target == "all" else [args.target]

    for target in targets:
        logger.info("=" * 60)
        logger.info("SCRAPING: %s", target)
        logger.info("=" * 60)

        config = load_competitor_config(target)
        raw_dir = str(RAW_DIR)

        # Ensure output directories exist
        target_raw = RAW_DIR / target
        for subdir in ["docs", "blog", "github_issues", "github_discussions",
                        "github_releases", "community", "benchmarks"]:
            (target_raw / subdir).mkdir(parents=True, exist_ok=True)

        total_records = 0

        # 1. Scrape documentation
        try:
            docs_records = scrape_docs(config, raw_dir)
            total_records += len(docs_records)
            logger.info("  Docs: %d records", len(docs_records))
        except Exception as e:
            logger.error("  Docs scraping failed: %s", e)

        # 2. Scrape GitHub
        try:
            github_records = scrape_github(config, raw_dir)
            total_records += len(github_records)
            logger.info("  GitHub: %d records", len(github_records))
        except Exception as e:
            logger.error("  GitHub scraping failed: %s", e)

        # 3. Scrape blog
        try:
            blog_records = scrape_blog(config, raw_dir)
            total_records += len(blog_records)
            logger.info("  Blog: %d records", len(blog_records))
        except Exception as e:
            logger.error("  Blog scraping failed: %s", e)

        # 4. Scrape community (Reddit + HN)
        try:
            community_records = scrape_community(config, raw_dir)
            total_records += len(community_records)
            logger.info("  Community: %d records", len(community_records))
        except Exception as e:
            logger.error("  Community scraping failed: %s", e)

        # 5. Scrape benchmarks
        try:
            benchmark_records = scrape_benchmarks(config, raw_dir)
            total_records += len(benchmark_records)
            logger.info("  Benchmarks: %d records", len(benchmark_records))
        except Exception as e:
            logger.error("  Benchmark scraping failed: %s", e)

        logger.info("TOTAL for %s: %d records", target, total_records)


# ---------------------------------------------------------------------------
# PROCESS
# ---------------------------------------------------------------------------

def cmd_process(args):
    """Run the processing pipeline (tag, filter, dedup)."""
    from processors.topic_tagger import TopicTagger
    from processors.quality_filter import QualityFilter
    from processors.deduplicator import Deduplicator
    from processors.content_extractor import ContentExtractor
    from schemas.source_record import SourceRecord
    from scrapers.utils import load_records, save_records

    targets = get_all_competitors() if args.target == "all" else [args.target]

    for target in targets:
        logger.info("=" * 60)
        logger.info("PROCESSING: %s", target)
        logger.info("=" * 60)

        config = load_competitor_config(target)

        # Load all raw records for this target
        raw_target_dir = RAW_DIR / target
        all_records = []

        for json_file in raw_target_dir.rglob("*.json"):
            try:
                data = load_records(str(json_file))
                for item in data:
                    record = SourceRecord(**item)
                    all_records.append(record)
            except Exception as e:
                logger.error("Failed to load %s: %s", json_file, e)

        logger.info("Loaded %d raw records for %s", len(all_records), target)

        if not all_records:
            logger.warning("No raw records found for %s, skipping", target)
            continue

        # Step 1: Clean content
        extractor = ContentExtractor()
        all_records = extractor.clean_batch(all_records)

        # Step 2: Tag topics
        competitor_keywords = config.get("topic_keywords", {})
        tagger = TopicTagger(
            global_keywords_path=str(CONFIG_DIR / "keywords.json"),
            competitor_keywords=competitor_keywords,
        )
        all_records = tagger.tag_batch(all_records)

        # Step 3: Quality filter
        quality_filter = QualityFilter()
        all_records = quality_filter.filter(all_records)

        # Step 4: Deduplicate
        deduplicator = Deduplicator()
        all_records = deduplicator.deduplicate(all_records)

        # Save processed records
        output_dir = str(PROCESSED_DIR / target)
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        save_records(all_records, output_dir, f"{target}_processed.json")

        logger.info("PROCESSED %s: %d records saved", target, len(all_records))


# ---------------------------------------------------------------------------
# GENERATE
# ---------------------------------------------------------------------------

def cmd_generate(args):
    """Run the LLM generation pipeline."""
    from generators.comparison_generator import ComparisonGenerator
    from generators.objection_generator import ObjectionGenerator
    from generators.summary_generator import SummaryGenerator
    from schemas.source_record import SourceRecord
    from scrapers.utils import load_records, save_records

    competitor = args.competitor
    topic_filter = args.topic

    logger.info("=" * 60)
    logger.info("GENERATING: KX vs %s", competitor)
    logger.info("=" * 60)

    taxonomy = load_taxonomy()

    # Load processed KX records
    kx_path = PROCESSED_DIR / "kx" / "kx_processed.json"
    kx_records = []
    if kx_path.exists():
        for item in load_records(str(kx_path)):
            kx_records.append(SourceRecord(**item))
    logger.info("Loaded %d processed KX records", len(kx_records))

    # Load processed competitor records
    comp_path = PROCESSED_DIR / competitor / f"{competitor}_processed.json"
    comp_records = []
    if comp_path.exists():
        for item in load_records(str(comp_path)):
            comp_records.append(SourceRecord(**item))
    logger.info("Loaded %d processed %s records", len(comp_records), competitor)

    if not kx_records and not comp_records:
        logger.error("No processed records found. Run 'process' first.")
        return

    config = load_competitor_config(competitor)
    competitor_name = config["name"]

    output_dir = GENERATED_DIR / competitor
    output_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Generate per-topic competitive entries
    topics = [topic_filter] if topic_filter else None
    resume = not getattr(args, "no_resume", False)
    comp_gen = ComparisonGenerator()
    entries = comp_gen.generate_all_topics(
        competitor_name=competitor_name,
        kx_records=kx_records,
        competitor_records=comp_records,
        taxonomy_config=taxonomy,
        topics=topics,
        output_dir=output_dir,
        resume=resume,
    )

    if entries:
        save_records(entries, str(output_dir), f"{competitor}_topic_entries.json")
        logger.info("Generated %d topic entries", len(entries))

    # Step 2: Generate cross-cutting objection handlers (only if doing full generation)
    if not topic_filter:
        obj_gen = ObjectionGenerator()

        objections = obj_gen.generate_objections(
            competitor_name=competitor_name,
            kx_sources=kx_records,
            competitor_sources=comp_records,
        )
        if objections:
            save_records(
                objections, str(output_dir), f"{competitor}_objection_handlers.json"
            )
            logger.info("Generated %d objection handlers", len(objections))

        cross_cutting = obj_gen.generate_cross_cutting(
            competitor_name=competitor_name,
            kx_sources=kx_records,
            competitor_sources=comp_records,
        )
        if cross_cutting:
            save_records(
                cross_cutting, str(output_dir), f"{competitor}_cross_cutting.json"
            )
            logger.info("Generated %d cross-cutting handlers", len(cross_cutting))

        # Step 3: Generate positioning narrative
        sum_gen = SummaryGenerator()
        narrative = sum_gen.generate_narrative(
            competitor_name=competitor_name,
            kx_sources=kx_records,
            competitor_sources=comp_records,
            topic_entries=entries,
        )
        import orjson
        narrative_path = output_dir / f"{competitor}_narrative.json"
        narrative_path.write_bytes(
            orjson.dumps(narrative.model_dump(mode="json"), option=orjson.OPT_INDENT_2)
        )
        logger.info("Generated positioning narrative")

    logger.info("GENERATION COMPLETE for %s", competitor)


# ---------------------------------------------------------------------------
# STATUS
# ---------------------------------------------------------------------------

def cmd_status(args):
    """Show the current status of the pipeline for all competitors."""
    competitors = get_all_competitors()

    print("\n" + "=" * 70)
    print("COMPETITIVE INTELLIGENCE PIPELINE STATUS")
    print("=" * 70)

    for comp in competitors:
        config = load_competitor_config(comp)
        print(f"\n--- {config['name']} ({comp}) ---")
        print(f"  Type: {'Self (KX)' if config.get('is_self') else 'Competitor'}")

        # Check raw data
        raw_dir = RAW_DIR / comp
        raw_files = list(raw_dir.rglob("*.json")) if raw_dir.exists() else []
        raw_count = 0
        for f in raw_files:
            try:
                import orjson
                data = orjson.loads(f.read_bytes())
                raw_count += len(data) if isinstance(data, list) else 1
            except Exception:
                pass
        print(f"  Raw records: {raw_count} ({len(raw_files)} files)")

        # Check processed data
        proc_file = PROCESSED_DIR / comp / f"{comp}_processed.json"
        if proc_file.exists():
            try:
                import orjson
                data = orjson.loads(proc_file.read_bytes())
                print(f"  Processed records: {len(data)}")
            except Exception:
                print("  Processed records: [error reading]")
        else:
            print("  Processed records: 0 (not yet processed)")

        # Check generated data
        gen_dir = GENERATED_DIR / comp
        if gen_dir.exists():
            gen_files = list(gen_dir.glob("*.json"))
            print(f"  Generated files: {len(gen_files)}")
            for gf in gen_files:
                print(f"    - {gf.name}")
        else:
            print("  Generated files: 0 (not yet generated)")

        # Check reviewed data
        rev_dir = REVIEWED_DIR / comp
        if rev_dir.exists():
            rev_files = list(rev_dir.glob("*.json"))
            print(f"  Reviewed files: {len(rev_files)}")
        else:
            print("  Reviewed files: 0 (not yet reviewed)")

    print("\n" + "=" * 70)


# ---------------------------------------------------------------------------
# EXPORT
# ---------------------------------------------------------------------------

def cmd_export(args):
    """Export generated data as a human-readable review document."""
    from scrapers.utils import load_records

    competitor = args.competitor
    config = load_competitor_config(competitor)
    competitor_name = config["name"]

    gen_dir = GENERATED_DIR / competitor
    export_dir = REVIEWED_DIR / competitor
    export_dir.mkdir(parents=True, exist_ok=True)

    output_lines = []
    output_lines.append(f"# Competitive Intelligence Review: KX vs {competitor_name}")
    output_lines.append(f"Generated: {date.today()}")
    output_lines.append("=" * 70)
    output_lines.append("")

    # Load topic entries
    entries_file = gen_dir / f"{competitor}_topic_entries.json"
    if entries_file.exists():
        entries = load_records(str(entries_file))
        output_lines.append(f"## Per-Topic Analysis ({len(entries)} topics)")
        output_lines.append("")

        for entry in entries:
            output_lines.append(f"### {entry.get('topic_name', 'Unknown Topic')}")
            output_lines.append(f"**Topic ID**: {entry.get('topic_id', '')}")
            output_lines.append(f"**Confidence**: {entry.get('confidence', 'unknown')}")
            output_lines.append(f"**Sources Used**: {entry.get('source_count', 0)}")
            output_lines.append("")

            # Assessment
            assessment = entry.get("competitor_assessment", {})
            output_lines.append(f"**{competitor_name} Assessment**: {assessment.get('summary', 'N/A')}")
            strengths = assessment.get("strengths", [])
            if strengths:
                output_lines.append("**Strengths**:")
                for s in strengths:
                    output_lines.append(f"  - {s}")
            output_lines.append("")

            # Limitations
            limitations = entry.get("competitor_limitations", [])
            if limitations:
                output_lines.append(f"**{competitor_name} Limitations**:")
                for lim in limitations:
                    output_lines.append(
                        f"  - [{lim.get('evidence_type', '?')}] {lim.get('limitation', '')}"
                    )
                output_lines.append("")

            # Differentiators
            diffs = entry.get("kx_differentiators", [])
            if diffs:
                output_lines.append("**KX Differentiators**:")
                for d in diffs:
                    output_lines.append(f"  - {d.get('differentiator', '')}: {d.get('explanation', '')}")
                output_lines.append("")

            # Elevator pitch
            pitch = entry.get("elevator_pitch", {})
            output_lines.append(f"**Elevator Pitch**: {pitch.get('pitch', 'N/A')}")
            if pitch.get("key_stat"):
                output_lines.append(f"**Key Stat**: {pitch['key_stat']}")
            output_lines.append("")

            # Gaps
            gaps = entry.get("gaps", [])
            if gaps:
                output_lines.append("**GAPS (needs manual research)**:")
                for g in gaps:
                    output_lines.append(f"  - {g}")
                output_lines.append("")

            output_lines.append("-" * 50)
            output_lines.append("")

    # Load narrative
    narrative_file = gen_dir / f"{competitor}_narrative.json"
    if narrative_file.exists():
        try:
            import orjson
            narrative = orjson.loads(narrative_file.read_bytes())
            output_lines.append("## Overall Positioning Narrative")
            output_lines.append("")
            output_lines.append(
                f"**60-Second Pitch**: {narrative.get('sixty_second_pitch', 'N/A')}"
            )
            output_lines.append("")
        except Exception as e:
            output_lines.append(f"## Narrative: [Error loading: {e}]")
            output_lines.append("")

    # Load objection handlers
    objections_file = gen_dir / f"{competitor}_objection_handlers.json"
    if objections_file.exists():
        objections = load_records(str(objections_file))
        output_lines.append(f"## Objection Handlers ({len(objections)} handlers)")
        output_lines.append("")
        for obj in objections:
            output_lines.append(f"**Q**: {obj.get('objection', 'N/A')}")
            output_lines.append(f"**A**: {obj.get('response', 'N/A')}")
            output_lines.append("")

    # Write export file
    export_path = export_dir / f"{competitor}_review_{date.today()}.txt"
    export_path.write_text("\n".join(output_lines))
    logger.info("Exported review document to %s", export_path)
    print(f"\nExported to: {export_path}")


# ---------------------------------------------------------------------------
# VECTORIZE
# ---------------------------------------------------------------------------

def cmd_vectorize(args):
    """Run the vectorization pipeline (chunk → embed → store in ChromaDB)."""
    from vectorstore.ingest import ingest_all

    targets = None if args.target == "all" else [args.target]
    ingest_all(
        targets=targets,
        reset=args.reset,
        chunk_tokens=args.chunk_tokens,
        overlap_tokens=args.overlap_tokens,
    )


def cmd_vector_status(args):
    """Show vector store statistics."""
    from vectorstore.store import VectorStore

    store = VectorStore()
    stats = store.get_stats()

    print("\n" + "=" * 70)
    print("VECTOR STORE STATUS")
    print("=" * 70)

    for name, info in stats.items():
        count = info.get("count", 0)
        print(f"\n  Collection: {name}")
        print(f"    Vectors stored: {count}")
        keys = info.get("sample_metadata_keys", [])
        if keys:
            print(f"    Metadata fields: {', '.join(keys)}")

    print("\n" + "=" * 70)


def cmd_serve(args):
    """Launch the Q&A web application."""
    import uvicorn

    logger.info("=" * 60)
    logger.info("LAUNCHING Q&A WEB INTERFACE")
    logger.info("  http://localhost:%d", args.port)
    logger.info("=" * 60)

    uvicorn.run(
        "webapp.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


def cmd_vector_query(args):
    """Run a test query against the vector store."""
    from vectorstore.store import VectorStore
    from vectorstore.embedder import Embedder

    store = VectorStore()
    embedder = Embedder()

    where = {}
    if args.competitor:
        where["competitor"] = args.competitor
    if args.topic:
        where["primary_topic"] = args.topic

    results = store.query_by_text(
        query_text=args.query,
        embedder=embedder,
        n_results=args.top_k,
        where=where if where else None,
    )

    print(f"\nQuery: \"{args.query}\"")
    if where:
        print(f"Filters: {where}")
    print(f"Results: {len(results['ids'][0])}")
    print("-" * 50)

    for i, (doc_id, doc, meta, dist) in enumerate(
        zip(
            results["ids"][0],
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        )
    ):
        print(f"\n[{i+1}] Score: {1 - dist:.4f} | {meta.get('competitor', '?')} | {meta.get('source_type', '?')}")
        print(f"    Topic: {meta.get('primary_topic', '?')}")
        print(f"    Source: {meta.get('source_title', '?')}")
        print(f"    URL: {meta.get('source_url', '?')}")
        # Show first 200 chars of the document
        preview = doc[:200].replace("\n", " ")
        print(f"    Text: {preview}...")
        print()


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Competitive Intelligence Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="command", help="Pipeline command")

    # Scrape
    scrape_parser = subparsers.add_parser("scrape", help="Scrape data sources")
    scrape_parser.add_argument(
        "--target",
        required=True,
        help="Competitor short name or 'all'",
    )

    # Process
    process_parser = subparsers.add_parser("process", help="Process scraped data")
    process_parser.add_argument(
        "--target",
        required=True,
        help="Competitor short name or 'all'",
    )

    # Generate
    generate_parser = subparsers.add_parser("generate", help="Generate competitive entries")
    generate_parser.add_argument(
        "--competitor",
        required=True,
        help="Competitor short name",
    )
    generate_parser.add_argument(
        "--topic",
        default=None,
        help="Specific topic ID (optional)",
    )
    generate_parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Regenerate all topics from scratch (ignore cached results)",
    )

    # Status
    subparsers.add_parser("status", help="Show pipeline status")

    # Export
    export_parser = subparsers.add_parser("export", help="Export for review")
    export_parser.add_argument(
        "--competitor",
        required=True,
        help="Competitor short name",
    )

    # Vectorize
    vec_parser = subparsers.add_parser(
        "vectorize", help="Chunk, embed, and store data in ChromaDB"
    )
    vec_parser.add_argument(
        "--target",
        required=True,
        help="Competitor short name or 'all'",
    )
    vec_parser.add_argument(
        "--reset",
        action="store_true",
        help="Wipe existing vector store before ingesting",
    )
    vec_parser.add_argument(
        "--chunk-tokens",
        type=int,
        default=400,
        help="Target chunk size in tokens (default: 400)",
    )
    vec_parser.add_argument(
        "--overlap-tokens",
        type=int,
        default=60,
        help="Token overlap between chunks (default: 60)",
    )

    # Serve (web UI)
    serve_parser = subparsers.add_parser(
        "serve", help="Launch the Q&A web interface"
    )
    serve_parser.add_argument(
        "--port", type=int, default=8501, help="Port (default: 8501)"
    )
    serve_parser.add_argument(
        "--host", default="0.0.0.0", help="Host (default: 0.0.0.0)"
    )
    serve_parser.add_argument(
        "--reload", action="store_true", help="Auto-reload on code changes"
    )

    # Vector status
    subparsers.add_parser("vector-status", help="Show vector store statistics")

    # Vector query (test)
    vq_parser = subparsers.add_parser("vector-query", help="Test query against vector store")
    vq_parser.add_argument("query", help="Query text")
    vq_parser.add_argument("--competitor", default=None, help="Filter by competitor")
    vq_parser.add_argument("--topic", default=None, help="Filter by topic ID")
    vq_parser.add_argument("--top-k", type=int, default=5, help="Number of results")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "scrape": cmd_scrape,
        "process": cmd_process,
        "generate": cmd_generate,
        "status": cmd_status,
        "export": cmd_export,
        "vectorize": cmd_vectorize,
        "vector-status": cmd_vector_status,
        "vector-query": cmd_vector_query,
        "serve": cmd_serve,
    }

    try:
        commands[args.command](args)
    except Exception as e:
        logger.exception("Pipeline error: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
