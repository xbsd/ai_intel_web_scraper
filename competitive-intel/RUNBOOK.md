# Competitive Intelligence Pipeline — Runbook

Step-by-step instructions for running the pipeline end-to-end, adding new competitors, and querying the resulting vector database.

## Prerequisites

```bash
cd competitive-intel
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Create `.env` from the example and fill in your keys:

```bash
cp .env.example .env
```

Required keys:

| Variable | Purpose | Where to get it |
|----------|---------|-----------------|
| `GITHUB_TOKEN` | GitHub API scraping (5000 req/hr) | github.com → Settings → Developer settings → Personal access tokens |
| `ANTHROPIC_API_KEY` | LLM generation (Claude) | console.anthropic.com |
| `OPENAI_API_KEY` | Embedding generation (text-embedding-3-small) | platform.openai.com |

---

## Pipeline Overview

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌───────────┐    ┌─────────┐    ┌─────────┐
│  SCRAPE  │ →  │ PROCESS  │ →  │ GENERATE │ →  │ VECTORIZE │ →  │  QUERY  │ →  │  SERVE  │
│          │    │          │    │          │    │           │    │         │    │         │
│ docs     │    │ tag      │    │ compare  │    │ chunk     │    │ search  │    │ FastAPI │
│ github   │    │ filter   │    │ objection│    │ embed     │    │ filter  │    │ RAG Q&A │
│ blog     │    │ dedup    │    │ summary  │    │ store     │    │ retrieve│    │ web UI  │
│ community│    │          │    │          │    │           │    │         │    │         │
└──────────┘    └──────────┘    └──────────┘    └───────────┘    └─────────┘    └─────────┘
  raw/ dir       processed/      generated/      data/vectordb/                  :8501
```

Each stage is independent. You can re-run any stage without re-running earlier ones (the data is persisted to disk between stages).

> **Data is not checked into git.** After cloning, run the scrape and vectorize steps
> below to populate the pipeline. The directory structure (`data/raw/`, `data/vectordb/`, etc.)
> is preserved via `.gitkeep` files.

---

## Step 1: Scrape Data

After cloning, scrape the targets you need. **Always scrape KX first** — it is the baseline knowledge base shared across all competitor comparisons.

```bash
# Re-scrape KX to get latest data
python pipeline.py scrape --target kx

# Re-scrape a competitor
python pipeline.py scrape --target questdb

# Scrape a new competitor (after creating its config)
python pipeline.py scrape --target clickhouse

# Or scrape everything in one go
python pipeline.py scrape --target all

# Verify data was collected
python pipeline.py status
```

If scraping returns 0 records, check:
- Is your `.env` populated? (`GITHUB_TOKEN` is needed for GitHub sources)
- Are you in the `competitive-intel/` directory?
- Check `pipeline.log` for errors: `tail -50 pipeline.log`

What gets scraped (per competitor config in `config/competitors/*.json`):
- **Documentation** — website crawl with configurable depth and CSS selectors
- **GitHub** — issues, discussions, releases via the GitHub API
- **Blog** — post discovery and full-text extraction
- **Community** — Reddit and Hacker News search results
- **Benchmarks** — performance benchmark pages

Output: JSON files in `data/raw/{target}/`

### Monitor scraping

```bash
# In another terminal, watch progress:
tail -f pipeline.log
```

---

## Step 2: Process Data

Tags each record with taxonomy topics, filters low-quality content, and deduplicates.

```bash
python pipeline.py process --target questdb
python pipeline.py process --target all
```

Output: JSON files in `data/processed/{target}/`

---

## Step 3: Generate Competitive Intelligence (LLM)

Uses Claude to generate per-topic competitive analysis, objection handlers, and positioning narratives.

```bash
# Generate all topics for a competitor
python pipeline.py generate --competitor questdb

# Generate a specific topic
python pipeline.py generate --competitor questdb --topic high_availability
```

Available topic IDs (use these with `--topic`):

| Tier | Topic ID | Name |
|------|----------|------|
| 1 | `performance_query_latency` | Query Latency & Response Time |
| 1 | `performance_ingestion` | Ingestion Throughput |
| 1 | `time_series_analytics` | Time-Series Analytics Capabilities |
| 1 | `sql_query_language` | Query Language & SQL Support |
| 1 | `high_availability` | High Availability & Disaster Recovery |
| 1 | `streaming_realtime` | Real-Time Streaming & CEP |
| 1 | `scalability_data_volume` | Scalability & Data Volume Handling |
| 2 | `security_compliance` | Security, Authentication & Compliance |
| 2 | `architecture_storage` | Architecture & Storage Engine |
| 2 | `concurrency_multiuser` | Concurrent Users & Workload Isolation |
| 2 | `backtesting_historical` | Backtesting & Historical Analysis |
| 2 | `ai_ml_integration` | AI/ML & Vector Data Integration |
| 2 | `cloud_deployment` | Cloud & On-Premises Deployment |
| 3 | `licensing_pricing` | Licensing, Pricing & TCO |
| 3 | `ecosystem_integration` | Ecosystem & Third-Party Integration |
| 3 | `enterprise_support` | Enterprise Support & SLAs |
| 3 | `developer_experience` | Developer Experience & Hiring |
| 3 | `operational_complexity` | Operational Complexity |
| 3 | `vendor_maturity` | Vendor Maturity & Track Record |
| 3 | `benchmark_results` | Independent Benchmark Results |

Output: JSON files in `data/generated/{competitor}/`

---

## Step 4: Vectorize (Chunk + Embed + Store)

This is the step that loads raw data into ChromaDB for semantic search.

```bash
# Vectorize a single target (incremental — won't wipe existing data)
python pipeline.py vectorize --target kx
python pipeline.py vectorize --target questdb

# Vectorize everything
python pipeline.py vectorize --target all

# Wipe and rebuild from scratch
python pipeline.py vectorize --target all --reset
```

What happens under the hood:
1. **Load** — reads all JSON files from `data/raw/{target}/`
2. **Chunk** — splits records into ~400-token chunks using content-type-aware strategies (blog sections, GitHub comments, doc hierarchy, etc.)
3. **Embed** — calls OpenAI `text-embedding-3-small` (1536 dimensions) in batches of 512
4. **Store** — upserts chunks + embeddings + metadata into ChromaDB (`data/vectordb/`)

### Dry-run first

Before running the full dataset, test with a small sample:

```bash
# Test with 50 records (takes ~20 seconds)
python dry_run.py --max-records 50 --timeout 120

# Even smaller test
python dry_run.py --max-records 10 --timeout 60
```

The dry-run will show timing per step and extrapolate how long the full run will take.

### Check vector store status

```bash
python pipeline.py vector-status
```

---

## Step 5: Query the Vector Store

### From the CLI

```bash
# Basic semantic search
python pipeline.py vector-query "kdb time series performance"

# Filter by competitor
python pipeline.py vector-query "high availability replication" --competitor questdb

# Filter by topic
python pipeline.py vector-query "SQL support" --topic sql_query_language

# Both filters + more results
python pipeline.py vector-query "ingestion throughput" --competitor kx --topic performance_ingestion --top-k 10
```

### From Python code (for your application)

```python
from dotenv import load_dotenv
load_dotenv()

from vectorstore.store import VectorStore
from vectorstore.embedder import Embedder

store = VectorStore()          # connects to data/vectordb/
embedder = Embedder()          # connects to OpenAI API

# --- Simple semantic search ---
results = store.query_by_text(
    query_text="How does QuestDB handle high availability?",
    embedder=embedder,
    n_results=5,
)

for doc, meta, dist in zip(
    results["documents"][0],
    results["metadatas"][0],
    results["distances"][0],
):
    print(f"Score: {1 - dist:.4f}")
    print(f"  Competitor: {meta['competitor']}")
    print(f"  Source:     {meta['source_type']}")
    print(f"  Topic:     {meta['primary_topic']}")
    print(f"  URL:       {meta['source_url']}")
    print(f"  Text:      {doc[:200]}...")
    print()
```

### Filtering by metadata

ChromaDB supports metadata filters via the `where` parameter. Every chunk stores these metadata fields:

| Field | Type | Example values | Description |
|-------|------|---------------|-------------|
| `competitor` | str | `"kx"`, `"questdb"`, `"clickhouse"` | Which target this data belongs to |
| `source_type` | str | `"blog"`, `"official_docs"`, `"github_issue"`, `"github_discussion"`, `"github_release"`, `"community_hn"`, `"community_reddit"`, `"benchmark"` | Data source type |
| `primary_topic` | str | `"high_availability"`, `"sql_query_language"` | Auto-tagged primary topic ID |
| `topic_ids` | str | `"high_availability,architecture_storage"` | Comma-separated list of all matched topics |
| `credibility` | str | `"official"`, `"community"`, `"third_party"` | Source credibility level |
| `source_url` | str | Full URL | Original source URL |
| `source_title` | str | Article/page title | Human-readable title |
| `content_date` | str | `"2025-01-15"` or `""` | When the content was published |
| `scraped_date` | str | `"2026-02-22"` | When we scraped it |
| `chunk_index` | int | `0`, `1`, `2` | Position within the parent document |
| `parent_doc_id` | str | Hash-based ID | Groups chunks from the same document |
| `token_count` | int | `350` | Token count of this chunk |

### Example metadata filters (ChromaDB `where` syntax)

```python
# Only QuestDB sources
where = {"competitor": "questdb"}

# Only official documentation
where = {"source_type": "official_docs"}

# Only high-credibility sources
where = {"credibility": "official"}

# Combine filters (AND)
where = {
    "$and": [
        {"competitor": "questdb"},
        {"source_type": "blog"},
    ]
}

# OR filter — docs or blog posts
where = {
    "$or": [
        {"source_type": "official_docs"},
        {"source_type": "blog"},
    ]
}

# Compare KX vs a competitor side-by-side
kx_results = store.query_by_text(
    query_text="high availability replication",
    embedder=embedder,
    n_results=5,
    where={"competitor": "kx"},
)
competitor_results = store.query_by_text(
    query_text="high availability replication",
    embedder=embedder,
    n_results=5,
    where={"competitor": "questdb"},
)
```

### Document-level filters

ChromaDB also supports filtering on the document text itself:

```python
# Only chunks that mention "replication"
results = store.query(
    query_embedding=embedder.embed_single("HA setup"),
    where={"competitor": "questdb"},
    where_document={"$contains": "replication"},
)
```

---

## Adding a New Competitor (Worked Example: TimescaleDB)

### 1. Create the config file

Create `config/competitors/timescaledb.json`:

```json
{
  "name": "TimescaleDB",
  "short_name": "timescaledb",
  "is_self": false,
  "description": "PostgreSQL extension for time-series data",

  "sources": {
    "docs": [
      {
        "id": "timescaledb-docs",
        "base_url": "https://docs.timescale.com/",
        "scrape_method": "crawl",
        "content_selector": "article",
        "max_depth": 3,
        "max_pages": 100,
        "rate_limit_seconds": 0.5
      }
    ],

    "blog": {
      "base_url": "https://www.timescale.com/blog/",
      "scrape_method": "crawl",
      "content_selector": "article",
      "max_pages": 30,
      "rate_limit_seconds": 0.5,
      "priority_keywords": ["benchmark", "performance", "time-series", "financial", "release"]
    },

    "github": {
      "repos": [
        {
          "repo": "timescale/timescaledb",
          "scrape_issues": true,
          "scrape_discussions": true,
          "scrape_releases": true,
          "max_issues": 200,
          "max_discussions": 100,
          "issue_sort": "comments",
          "issue_direction": "desc",
          "fetch_comments_for_top_n": 20,
          "labels_of_interest": ["bug", "enhancement", "feature request"]
        }
      ]
    },

    "community": {
      "reddit": {
        "search_terms": ["TimescaleDB", "timescaledb vs", "timescaledb performance"],
        "subreddits": ["databases", "devops", "dataengineering"],
        "max_results_per_query": 50
      },
      "hackernews": {
        "search_terms": ["TimescaleDB"],
        "max_results_per_query": 50
      }
    },

    "benchmarks": [
      {
        "name": "tsbs_blog",
        "url": "https://www.timescale.com/blog/tags/benchmarks/",
        "scrape_method": "crawl",
        "max_depth": 2
      }
    ]
  },

  "topic_keywords": {
    "performance_query_latency": ["latency", "query time", "response time", "hypertable"],
    "performance_ingestion": ["ingestion", "throughput", "COPY", "insert", "batch"],
    "time_series_analytics": ["continuous aggregate", "time_bucket", "hyperfunctions", "downsampling"],
    "sql_query_language": ["SQL", "PostgreSQL", "PL/pgSQL", "JOIN", "window function", "CTE"],
    "high_availability": ["replication", "HA", "failover", "streaming replication", "patroni"]
  }
}
```

### 2. Run the full pipeline

```bash
# Scrape
python pipeline.py scrape --target timescaledb

# Monitor progress
tail -f pipeline.log

# Check what was collected
python pipeline.py status

# Process (tag + filter + dedup)
python pipeline.py process --target timescaledb

# Generate competitive analysis (requires ANTHROPIC_API_KEY)
python pipeline.py generate --competitor timescaledb

# Dry-run vectorization first
python dry_run.py --target timescaledb --max-records 20

# Vectorize (incremental — adds to existing DB alongside kx and questdb)
python pipeline.py vectorize --target timescaledb

# Verify
python pipeline.py vector-status
python pipeline.py vector-query "time series performance" --competitor timescaledb
```

### 3. Query across competitors

```bash
# Compare all competitors on a topic
python pipeline.py vector-query "high availability replication" --top-k 5
# (no --competitor filter = search all)

# KX vs TimescaleDB on SQL
python pipeline.py vector-query "SQL support" --competitor kx --top-k 3
python pipeline.py vector-query "SQL support" --competitor timescaledb --top-k 3
```

---

## Data Storage and Git

All data directories are **gitignored** — only `.gitkeep` placeholder files are tracked so the directory structure is preserved on clone. After cloning:

```
data/
├── raw/           # Empty — populated by `scrape`
├── processed/     # Empty — populated by `process`
├── generated/     # Empty — populated by `generate`
├── reviewed/      # Empty — populated manually after SE review
└── vectordb/      # Empty — populated by `vectorize`
```

After cloning, build the full dataset:

```bash
python pipeline.py scrape --target all       # collect raw data
python pipeline.py vectorize --target all    # build ChromaDB
python pipeline.py vector-query "kdb performance"  # verify it works
```

### Data volumes after a full run

| Target | Raw Records | Raw Files | Vector Chunks | Notes |
|--------|------------|-----------|---------------|-------|
| KX | ~1,100 | ~33 | ~10,000+ | Our product — baseline for all comparisons |
| QuestDB | ~530 | ~8 | ~5,000+ | Primary competitor |
| ClickHouse | ~500+ | varies | ~5,000+ | Config ready, scrape to populate |

To rebuild the full vector database (e.g. after re-scraping):

```bash
python pipeline.py scrape --target all        # ~5-15 min depending on rate limits
python pipeline.py process --target all       # ~1 min
python pipeline.py vectorize --target all     # ~3-4 min
```

---

## Monitoring and Troubleshooting

```bash
# Watch the log in real-time
tail -f pipeline.log

# Pipeline status (record counts per stage)
python pipeline.py status

# Vector store status (chunk counts, metadata fields)
python pipeline.py vector-status

# Check data sizes
du -sh data/*/

# Dry-run to test before full vectorization
python dry_run.py --max-records 50 --timeout 120
```

### Common issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| `status` shows 0 records after scraping | `.env` not configured or network issue | Check `GITHUB_TOKEN` in `.env`, check `pipeline.log` |
| `vector-status` shows 0 vectors | Haven't vectorized yet | Run `python pipeline.py vectorize --target all` |
| `vector-query` returns no results | No data in vector store | Run vectorize first |
| Embedding step fails silently | `OPENAI_API_KEY` not set | Add to `.env` |
| GitHub scraping returns 403 | `GITHUB_TOKEN` not set or expired | Regenerate token |
| Reddit scraping returns 403 | Reddit blocks unauthenticated API | Expected — HN data still works |
| Vectorize seems stuck | Chunking 1000+ records with no progress logging | Already fixed — progress logs every 10% |

---

## Using the Vector Store in Your Application

The vector store lives at `data/vectordb/` and is a standard ChromaDB persistent database. You can use it from any Python process:

```python
from dotenv import load_dotenv
load_dotenv()

from vectorstore.store import VectorStore
from vectorstore.embedder import Embedder

store = VectorStore()
embedder = Embedder()

def answer_prospect_question(question: str, competitor: str) -> list[dict]:
    """Retrieve relevant competitive intelligence for a prospect question."""
    # Get KX's position
    kx_context = store.query_by_text(
        query_text=question,
        embedder=embedder,
        n_results=5,
        where={"competitor": "kx"},
    )

    # Get competitor's position
    competitor_context = store.query_by_text(
        query_text=question,
        embedder=embedder,
        n_results=5,
        where={"competitor": competitor},
    )

    # Combine for RAG context
    context_chunks = []
    for docs, metas, label in [
        (kx_context, kx_context, "KX"),
        (competitor_context, competitor_context, competitor),
    ]:
        for doc, meta in zip(docs["documents"][0], docs["metadatas"][0]):
            context_chunks.append({
                "text": doc,
                "competitor": meta["competitor"],
                "source_type": meta["source_type"],
                "topic": meta["primary_topic"],
                "url": meta["source_url"],
                "credibility": meta["credibility"],
            })

    return context_chunks
```

Pass these context chunks to Claude (or any LLM) as RAG context to generate informed competitive responses.

---

## Step 7: Launch the Q&A Web Interface

The pipeline includes a full-featured web application for sales teams to query competitive intelligence interactively. It implements production-grade RAG with:

- **Query analysis** — LLM-powered decomposition, intent classification
- **HyDE** (Hypothetical Document Embeddings) — generates a hypothetical answer to improve retrieval
- **Multi-query retrieval** — searches original query + sub-queries + HyDE passage
- **Reciprocal Rank Fusion** — merges results from multiple search strategies
- **Chain-of-Thought synthesis** — grounded answer generation with inline `[N]` citations
- **Follow-up suggestions** — auto-generated continuation questions

### Launch

```bash
# Start the web server
python pipeline.py serve --port 8501

# With auto-reload during development
python pipeline.py serve --port 8501 --reload
```

Open `http://localhost:8501` in your browser.

### Features

- **Query input** with real-time filters (competitor, topic, source type)
- **Grounded answers** with inline `[N]` citation badges
- **Source citations panel** — full provenance for every claim (URL, source type, credibility)
- **Query metadata** — timing breakdown, model info, retrieval stats
- **Follow-up questions** — click to continue the investigation
- **Settings modal** — switch LLM provider/model, update API keys at runtime
- **Example queries** — pre-loaded starter questions

### Configuration

The web interface reads API keys from `.env` on startup. You can also update keys at runtime via the Settings modal (gear icon). These settings are session-scoped and reset on restart.

Supported LLM providers:

| Provider | Models | API Key |
|----------|--------|---------|
| Anthropic (default) | Claude Sonnet 4, Claude Opus 4, Claude Haiku 4.5 | `ANTHROPIC_API_KEY` |
| OpenAI | GPT-4o, GPT-4o Mini, GPT-4.1 | `OPENAI_API_KEY` |

OpenAI key is always required for embeddings regardless of which LLM provider you choose for answer generation.
