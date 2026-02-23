# Competitive Intelligence Data Repository

A comprehensive, parameterized scraping framework to collect competitive data from public sources for KX Systems' sales team. Produces structured competitive intelligence that an AI-powered RAG system can query to answer prospect questions during sales cycles.

## Architecture

KX is the constant. Competitors are pluggable. The KX knowledge base is scraped once and shared across all competitor comparisons. Each competitor is an independent, config-driven pipeline.

## Quick Start

> **Note:** Scraped data for KX and QuestDB (~20 MB) is included in the repo, so
> `pipeline.py status` works immediately after cloning. See [RUNBOOK.md](competitive-intel/RUNBOOK.md) for the full walkthrough.

```bash
cd competitive-intel

# 1. Install dependencies
pip install -r requirements.txt

# 2. Set up environment variables
cp .env.example .env
# Edit .env with your API keys (GITHUB_TOKEN, ANTHROPIC_API_KEY, OPENAI_API_KEY)

# 3. Scrape data (KX + QuestDB data already included, re-scrape to refresh)
python pipeline.py scrape --target kx       # Always scrape KX first
python pipeline.py scrape --target questdb

# 4. Process (tag, filter, deduplicate)
python pipeline.py process --target all

# 5. Generate competitive intelligence
python pipeline.py generate --competitor questdb

# 6. Export for SE review
python pipeline.py export --competitor questdb

# Check status at any time
python pipeline.py status
```

## Adding a New Competitor

1. Create `config/competitors/{name}.json` following the existing schema
2. Run:
   ```bash
   python pipeline.py scrape --target {name}
   python pipeline.py process --target {name}
   python pipeline.py generate --competitor {name}
   ```

## Project Structure

```
competitive-intel/
├── config/                    # Configuration files
│   ├── taxonomy.json          # 20-topic competitive taxonomy
│   ├── keywords.json          # Topic keyword mappings
│   └── competitors/           # Per-competitor scrape configs
├── scrapers/                  # Data collection modules
│   ├── docs_scraper.py        # Documentation site crawler
│   ├── github_scraper.py      # GitHub issues/discussions/releases
│   ├── blog_scraper.py        # Blog post scraper
│   ├── community_scraper.py   # Reddit + Hacker News
│   └── benchmark_scraper.py   # Performance benchmark sources
├── processors/                # Data processing pipeline
│   ├── topic_tagger.py        # Auto-tag with taxonomy topics
│   ├── quality_filter.py      # Remove low-value content
│   ├── deduplicator.py        # Near-duplicate detection (MinHash)
│   └── content_extractor.py   # HTML cleaning and normalization
├── generators/                # LLM-powered content generation
│   ├── comparison_generator.py # Per-topic competitive analysis
│   ├── objection_generator.py  # Objection handlers
│   ├── summary_generator.py    # Positioning narratives
│   └── prompts/               # Prompt templates
├── schemas/                   # Pydantic data models
├── data/                      # Pipeline data (~20 MB, checked into git)
│   ├── raw/                   # Scraped data (KX + QuestDB included)
│   ├── processed/             # Tagged, filtered, deduplicated
│   ├── generated/             # LLM-generated content
│   └── reviewed/              # Human-approved final content
├── pipeline.py                # Main orchestrator
├── dry_run.py                 # Test vectorization on a small sample
├── RUNBOOK.md                 # Full end-to-end operating guide
└── requirements.txt           # Python dependencies
```

## Configured Competitors

| Competitor | Config | Status |
|-----------|--------|--------|
| KX (self) | `config/competitors/kx.json` | Ready |
| QuestDB | `config/competitors/questdb.json` | Ready |
| ClickHouse | `config/competitors/clickhouse.json` | Ready (config only) |

## Documentation

| Document | Description |
|----------|-------------|
| [RUNBOOK.md](competitive-intel/RUNBOOK.md) | **Step-by-step instructions** for running the full pipeline end-to-end — scraping, processing, vectorizing, querying, adding new competitors, metadata filtering, and using the vector store in your application. Start here if you want to operate the pipeline. |
| This README | Architecture overview, project structure, and quick-start reference. |

## API Keys Required

| Variable | Required | Purpose |
|----------|----------|---------|
| `GITHUB_TOKEN` | Yes | GitHub API (5000 req/hr) |
| `ANTHROPIC_API_KEY` | Yes | LLM generation via Claude |
| `OPENAI_API_KEY` | Yes | Embeddings via text-embedding-3-small |

## License

This project is licensed under the **Commons Clause + Apache 2.0** license — free for development, research, and internal use, but **not for commercial sale or hosted services**. See [LICENSE](LICENSE) for details.
