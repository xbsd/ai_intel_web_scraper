# Competitive Intelligence Platform

RAG-powered competitive intelligence system for KX sales teams. Scrapes, processes, and vectorizes competitor data (QuestDB, ClickHouse, and more), then serves it through an interactive web application with Claude-powered analysis, native citations, and conversation memory.

Built for capital markets database positioning — answers questions like *"How does QuestDB's ingestion throughput compare to kdb+?"* with grounded, cited responses drawn from documentation, GitHub activity, blog posts, community discussions, and benchmarks.

---

## Architecture

```
 SCRAPE          PROCESS         GENERATE        VECTORIZE         SERVE
┌──────────┐   ┌──────────┐   ┌──────────┐   ┌───────────┐   ┌──────────────┐
│ docs     │   │ tag      │   │ compare  │   │ chunk     │   │ FastAPI      │
│ github   │──>│ filter   │──>│ objection│──>│ embed     │──>│ RAG + Claude │
│ blog     │   │ dedup    │   │ summary  │   │ store     │   │ web UI       │
│ community│   │          │   │          │   │           │   │              │
└──────────┘   └──────────┘   └──────────┘   └───────────┘   └──────────────┘
  data/raw/     data/processed/  data/generated/  data/vectordb/    :8501
```

Each stage is independent and replayable. Data persists to disk between stages.

---

## Features

### Data Pipeline

- **Multi-source scraping** — documentation sites, GitHub (issues, discussions, releases), blogs, Reddit, Hacker News, benchmark pages
- **Taxonomy-driven tagging** — 20-topic hierarchy across 3 tiers, tailored for capital markets databases
- **Quality filtering** — removes short content, code-heavy pages, untagged records
- **Deduplication** — exact URL, GitHub ID, and MinHash near-duplicate detection (70% threshold)
- **LLM-powered generation** — per-topic competitive analysis, objection handlers, positioning narratives via Claude
- **Content-aware chunking** — blog section splitting, doc hierarchy, GitHub comment boundaries, release-as-single-chunk
- **Vector embeddings** — OpenAI `text-embedding-3-small` (1536 dimensions) with batch processing

### Web Application

- **RAG Q&A interface** — ask competitive questions, get grounded answers with source citations
- **HyDE retrieval** — generates hypothetical documents to improve recall
- **Multi-query expansion** — decomposes questions into sub-queries for broader coverage
- **Reciprocal rank fusion** — merges results from multiple search strategies
- **Executive summary** — auto-extracted hero card with semantic highlighting (KX strengths in green, competitor gaps in amber)
- **Comparison widgets** — auto-detected differentiators rendered in the right rail
- **Real-time filters** — competitor, topic, and source type filtering
- **6 persona modes** — Sales Executive, Sales Engineer, Product Manager, Technical Architect, C-Level, Research Analyst
- **Database explorer** — visual breakdown of vector store contents with drill-down navigation
- **Content ingestion** — drag-and-drop file upload (PDF, images, text, CSV, Office documents)
- **Follow-up suggestions** — auto-generated continuation questions after each answer
- **SSE streaming** — server-sent events for real-time answer generation
- **Example queries** — pre-loaded starter questions for new users

### Claude API Integration

- **Adaptive thinking** — `thinking: {"type": "adaptive"}` with thinking text displayed in progress popup
- **Thinking toggle** — UI switch to enable/disable extended thinking per query (auto-enabled by default, auto-disabled when fast mode is on)
- **Native citations** — `search_result` content blocks with `citations: {"enabled": true}`, replacing manual `[N]` prompt engineering
- **Web search tool** — native Claude API `web_search_20250305` server-side tool for current information
- **Memory tool** — `memory_20250818` with local file storage per user, path traversal protection, 10KB/100 file limits
- **Fast mode** — switches to `claude-haiku-4-5` with thinking disabled and reduced max tokens for fastest responses
- **Prompt caching** — `cache_control: {"type": "ephemeral"}` on system prompt and conversation history
- **Token tracking** — per-query usage breakdown (input, output, cache created, cache read) and cumulative session totals
- **Per-query inline metrics** — model badge, response time, token counts, cache hits, and estimated cost displayed on each message
- **Cost estimation** — approximate per-query cost based on model pricing (Sonnet 4.6: $3/$15 per M tokens, Haiku 4.5: $0.80/$4 per M tokens)
- **Session management** — SQLite-backed conversation history with login screen and auto-resume
- **New session / reset history** — start fresh sessions without losing history; bulk-delete all sessions from the History panel
- **Auto-compaction** — token-aware conversation history trimming (~4000 token budget) to manage API costs on long conversations
- **Collapsible Q&A** — expand/collapse individual Q&A pairs or all at once to manage long conversations
- **Progress popup** — centered overlay showing backend processing steps (retrieval, thinking, web search) that auto-dismisses when text streaming begins

### Models

| Provider | Models | Purpose |
|----------|--------|---------|
| Anthropic | Claude Sonnet 4.6 (default), Haiku 4.5 (fast mode), Sonnet 4, Opus 4 | Answer generation, query analysis |
| OpenAI | GPT-4o, GPT-4o Mini, GPT-4.1 | Alternative answer generation |
| OpenAI | text-embedding-3-small | Vector embeddings (always required) |

---

## Quick Start

### Prerequisites

- Python 3.11+
- API keys: Anthropic, OpenAI, GitHub

### Setup

```bash
cd competitive-intel
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env with your API keys
```

### Required API Keys

| Variable | Purpose | Where to get it |
|----------|---------|-----------------|
| `GITHUB_TOKEN` | GitHub API scraping (5000 req/hr) | github.com > Settings > Developer settings > Personal access tokens |
| `ANTHROPIC_API_KEY` | LLM generation (Claude) | console.anthropic.com |
| `OPENAI_API_KEY` | Embedding generation | platform.openai.com |

### Build the Dataset

```bash
# Scrape all competitors (KX first as baseline)
python pipeline.py scrape --target all

# Process (tag, filter, deduplicate)
python pipeline.py process --target all

# Vectorize (chunk, embed, store in ChromaDB)
python pipeline.py vectorize --target all

# Verify
python pipeline.py vector-status
```

### Launch the Web App

```bash
python pipeline.py serve --port 8501
```

Open `http://localhost:8501`. Log in with any username to start asking competitive questions.

---

## Pipeline Commands

```bash
# SCRAPE
python pipeline.py scrape --target kx
python pipeline.py scrape --target questdb
python pipeline.py scrape --target all

# PROCESS
python pipeline.py process --target questdb
python pipeline.py process --target all

# GENERATE (LLM-powered competitive analysis)
python pipeline.py generate --competitor questdb
python pipeline.py generate --competitor questdb --topic high_availability
python pipeline.py generate --competitor questdb --step topics

# VECTORIZE
python pipeline.py vectorize --target all
python pipeline.py vectorize --target all --reset    # wipe and rebuild

# STATUS
python pipeline.py status
python pipeline.py vector-status

# QUERY (CLI)
python pipeline.py vector-query "QuestDB HA" --competitor questdb --top-k 5

# EXPORT
python pipeline.py export --competitor questdb

# SERVE
python pipeline.py serve --port 8501
python pipeline.py serve --port 8501 --reload
```

---

## Topic Taxonomy

20 topics across 3 tiers for capital markets database analysis:

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

---

## Project Structure

```
competitive-intel/
├── config/
│   ├── competitors/              # Competitor profiles (KX, QuestDB, ClickHouse)
│   │   ├── kx.json              # Self profile (baseline)
│   │   ├── questdb.json         # Competitor with source specs
│   │   └── clickhouse.json
│   ├── keywords.json            # Global topic-keyword mapping
│   └── taxonomy.json            # 20-topic taxonomy (3 tiers)
│
├── scrapers/                     # Data collection layer
│   ├── docs_scraper.py          # Documentation site crawler
│   ├── github_scraper.py        # GitHub API (issues, discussions, releases)
│   ├── blog_scraper.py          # Blog discovery & extraction
│   ├── community_scraper.py     # Reddit + Hacker News
│   ├── benchmark_scraper.py     # Performance benchmark pages
│   └── utils.py                 # HTTP, parsing, rate-limiting
│
├── processors/                   # Data processing layer
│   ├── topic_tagger.py          # Taxonomy topic assignment
│   ├── quality_filter.py        # Content quality filtering
│   ├── deduplicator.py          # URL, ID, MinHash dedup
│   └── content_extractor.py     # Boilerplate removal
│
├── schemas/                      # Pydantic data models
│   ├── source_record.py         # Scraped source documents
│   ├── competitive_entry.py     # LLM-generated comparisons
│   └── chunk.py                 # Vector chunks
│
├── generators/                   # LLM-powered generation
│   ├── comparison_generator.py  # Per-topic competitive analysis
│   ├── objection_generator.py   # Cross-cutting objection handlers
│   ├── summary_generator.py     # Positioning narrative & tables
│   └── prompts/                 # System prompts and templates
│
├── vectorstore/                  # Embeddings & vector DB
│   ├── chunker.py               # Content-aware chunking
│   ├── embedder.py              # OpenAI text-embedding-3-small
│   ├── ingest.py                # Pipeline orchestrator
│   └── store.py                 # ChromaDB persistent store
│
├── webapp/                       # FastAPI web application
│   ├── app.py                   # Server, REST/SSE endpoints
│   ├── sessions.py              # SQLite session management
│   ├── rag/
│   │   ├── retriever.py         # HyDE, multi-query, RRF
│   │   ├── query_engine.py      # Claude API integration, streaming
│   │   └── prompts.py           # RAG system prompts, personas
│   ├── templates/
│   │   └── index.html           # Web UI template
│   └── static/
│       ├── css/main.css         # Design system
│       └── js/app.js            # Client-side application
│
├── data/                         # Pipeline data (gitignored)
│   ├── raw/                     # Scraped JSON by competitor
│   ├── processed/               # Tagged, filtered, deduped
│   ├── generated/               # LLM competitive analysis
│   ├── reviewed/                # Human-reviewed exports
│   ├── vectordb/                # ChromaDB storage
│   ├── memories/                # Per-user memory tool storage
│   ├── uploads/                 # Uploaded content files
│   └── sessions.db             # Session database
│
├── pipeline.py                   # Main CLI orchestrator
├── dry_run.py                    # Vectorization test utility
├── requirements.txt
├── .env.example
├── RUNBOOK.md                    # Operational guide
└── README.md
```

---

## Web Application Details

### SSE Event Flow

When a query is submitted, the backend streams events in this order:

| Event | Data | Description |
|-------|------|-------------|
| `status` | `{step, message}` | Processing step updates |
| `thinking` | `{text}` | Adaptive thinking content (shown in popup) |
| `citations_sources` | `[{index, source_title, ...}]` | Source metadata for right rail |
| `citation_delta` | `{type, source, cited_text, ...}` | Native citation references |
| `token` | `{text}` | Answer text chunks (popup dismisses on first) |
| `web_search_result` | `{results}` | Web search results (when enabled) |
| `usage` | `{input_tokens, output_tokens, cache_*}` | Token usage |
| `followups` | `["q1", "q2", "q3"]` | Follow-up suggestions |
| `metadata` | `{timings, model, provider, ...}` | Query metadata |
| `done` | `{}` | Stream complete |

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Web UI |
| `POST` | `/api/query` | Non-streaming RAG query |
| `POST` | `/api/query-stream` | SSE streaming RAG query |
| `GET` | `/api/status` | Vector store status, competitors, topics |
| `GET` | `/api/settings` | Current LLM settings |
| `POST` | `/api/settings` | Update LLM settings |
| `GET` | `/api/db-stats` | Database explorer statistics |
| `POST` | `/api/login` | Create or retrieve user |
| `POST` | `/api/sessions` | Create new session |
| `GET` | `/api/sessions` | List user sessions |
| `DELETE` | `/api/sessions` | Delete all sessions for a user |
| `GET` | `/api/sessions/{id}/messages` | Session message history |
| `GET` | `/api/sessions/{id}/tokens` | Session token totals |
| `GET` | `/api/content/list` | List uploaded files |
| `POST` | `/api/content/upload` | Upload file for ingestion |

### Query Request Options

```json
{
  "query": "How does QuestDB handle HA?",
  "competitor_filter": ["questdb"],
  "topic_filter": ["high_availability"],
  "source_type_filter": ["official_docs"],
  "n_results": 12,
  "persona": "sales_engineer",
  "use_llm_knowledge": false,
  "use_web_search": true,
  "use_thinking": true,
  "fast_mode": false,
  "session_id": "abc123",
  "username": "jdoe"
}
```

---

## Adding a New Competitor

1. Create `config/competitors/{name}.json` with source specifications (docs, blog, GitHub, community, benchmarks)
2. Run the pipeline:

```bash
python pipeline.py scrape --target {name}
python pipeline.py process --target {name}
python pipeline.py generate --competitor {name}
python pipeline.py vectorize --target {name}
```

See [RUNBOOK.md](RUNBOOK.md) for a detailed worked example with TimescaleDB.

See [TECHNICAL.md](TECHNICAL.md) for full architecture and implementation details.

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Scraping | requests, BeautifulSoup, lxml |
| Data Models | Pydantic 2.x |
| Deduplication | MinHash (datasketch) |
| LLM | Anthropic Claude (Sonnet 4.6 / Opus 4.6) |
| Embeddings | OpenAI text-embedding-3-small |
| Vector DB | ChromaDB (persistent, embedded) |
| Web Server | FastAPI + Uvicorn |
| Sessions | SQLite (WAL mode) |
| Frontend | Vanilla JS, custom CSS design system |

---

## Data Volumes

After a full pipeline run:

| Target | Raw Records | Vector Chunks | Notes |
|--------|------------|---------------|-------|
| KX | ~1,100 | ~10,000+ | Baseline for all comparisons |
| QuestDB | ~530 | ~5,000+ | Primary competitor |
| ClickHouse | ~500+ | ~5,000+ | Config ready |

---

## License

Internal use. Not for public distribution.
