# Technical Documentation

Detailed technical reference for the Competitive Intelligence Platform — architecture, features, key files, and implementation notes for developers and maintainers.

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Data Pipeline](#data-pipeline)
3. [Web Application](#web-application)
4. [RAG Engine](#rag-engine)
5. [Claude API Integration](#claude-api-integration)
6. [Session & History Management](#session--history-management)
7. [Frontend Architecture](#frontend-architecture)
8. [Key Files Reference](#key-files-reference)

---

## System Overview

The platform is a five-stage pipeline that scrapes competitor data, processes and tags it against a 20-topic taxonomy, generates LLM-powered competitive analysis, vectorizes everything into ChromaDB, and serves it through a FastAPI web application with Claude-powered RAG.

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

### Tech Stack

| Layer | Technology |
|-------|------------|
| Scraping | requests, BeautifulSoup, lxml |
| Data Models | Pydantic 2.x |
| Deduplication | MinHash (datasketch) |
| LLM | Anthropic Claude (Sonnet 4.6 / Haiku 4.5) |
| Embeddings | OpenAI text-embedding-3-small (1536 dimensions) |
| Vector DB | ChromaDB (persistent, embedded) |
| Web Server | FastAPI + Uvicorn |
| Sessions | SQLite (WAL mode) |
| Frontend | Vanilla JS (IIFE), custom CSS design system |

---

## Data Pipeline

Orchestrated by `pipeline.py`, each stage writes to disk and can be replayed independently.

### Scraping (`scrapers/`)

| File | Purpose |
|------|---------|
| `docs_scraper.py` | Crawls documentation sites, follows internal links, extracts structured content |
| `github_scraper.py` | GitHub API — issues, discussions, releases for configured repos |
| `blog_scraper.py` | Blog discovery via sitemap/RSS, full article extraction |
| `community_scraper.py` | Reddit and Hacker News threads |
| `benchmark_scraper.py` | Performance benchmark pages |
| `utils.py` | HTTP helpers, rate limiting, retry logic |

Competitor configurations live in `config/competitors/{name}.json` — each specifies documentation URLs, GitHub repos, blog feeds, and community search terms.

### Processing (`processors/`)

| File | Purpose |
|------|---------|
| `topic_tagger.py` | Assigns taxonomy topics using keyword matching against `config/keywords.json` |
| `quality_filter.py` | Removes short content, code-heavy pages, untagged records |
| `deduplicator.py` | Three-tier dedup: exact URL, GitHub ID, MinHash near-duplicate (70% threshold) |
| `content_extractor.py` | Boilerplate removal, content normalization |

### Generation (`generators/`)

| File | Purpose |
|------|---------|
| `comparison_generator.py` | Per-topic competitive analysis (KX vs. competitor) via Claude |
| `objection_generator.py` | Cross-cutting objection handlers |
| `summary_generator.py` | Positioning narratives and comparison tables |
| `prompts/` | System prompts and LLM templates |

### Vectorization (`vectorstore/`)

| File | Purpose |
|------|---------|
| `chunker.py` | Content-aware chunking — blog sections, doc hierarchy, GitHub comment boundaries, releases as single chunks |
| `embedder.py` | OpenAI `text-embedding-3-small` batch embedding with retry |
| `ingest.py` | Pipeline orchestrator: chunk → embed → store |
| `store.py` | ChromaDB persistent store with collection management |

---

## Web Application

### Server (`webapp/app.py`)

FastAPI application with REST and SSE endpoints. Key responsibilities:

- **Query endpoints**: `POST /api/query` (non-streaming) and `POST /api/query-stream` (SSE)
- **Session management**: CRUD for sessions and messages
- **Settings**: LLM model selection, provider switching
- **Content ingestion**: File upload with multi-format support (PDF, images, Office, CSV)
- **Database explorer**: Vector store statistics and drill-down

**QueryRequest model fields**:
```python
query: str
competitor_filter: list[str] = []
topic_filter: list[str] = []
source_type_filter: list[str] = []
n_results: int = 12
persona: str = "sales_executive"
use_llm_knowledge: bool = False
use_web_search: bool = False
use_thinking: bool = True          # Extended thinking toggle
fast_mode: bool = False
session_id: Optional[str] = None
username: Optional[str] = None
```

### Route Order

FastAPI routes are order-sensitive. Static routes (`DELETE /api/sessions`) must be defined before parameterized routes (`GET /api/sessions/{session_id}/...`) to avoid the path parameter capturing literal segments.

---

## RAG Engine

### Retriever (`webapp/rag/retriever.py`)

Multi-strategy retrieval with result fusion:

1. **HyDE** (Hypothetical Document Embeddings) — generates a hypothetical answer, embeds it, and searches for similar real chunks
2. **Multi-query expansion** — decomposes the user question into 2-3 sub-queries for broader coverage
3. **Reciprocal Rank Fusion (RRF)** — merges results from multiple search strategies into a single ranked list
4. **Filter application** — competitor, topic, and source type filters applied at the ChromaDB query level

### Query Engine (`webapp/rag/query_engine.py`)

Core orchestration file. Handles both Anthropic and OpenAI providers.

**Key methods**:

| Method | Purpose |
|--------|---------|
| `query()` | Non-streaming query, returns complete response |
| `query_stream()` | SSE streaming generator, yields typed events |
| `_build_messages()` | Constructs the message array with search results, conversation history, and cache control |
| `_analyze_query()` | Extracts competitor mentions and topic signals from the user query |

**Message construction** (`_build_messages`):

Returns a tuple `(messages: list[dict], history_count: int)`.

1. Loads up to 10 recent messages from the session
2. Applies token-aware compaction (4000 token budget, ~4 chars/token estimate)
3. Iterates newest-first, includes messages until budget exhausted
4. Applies `cache_control: {"type": "ephemeral"}` on the last history message for prompt caching
5. Appends the current query with `search_result` content blocks for native citations

**Thinking control**:
```python
# Fast mode always overrides thinking off
# The toggle can independently disable thinking
use_thinking = use_thinking and not self.llm.fast_mode
```

**Native citations**: Search results are passed as `search_result` content blocks with `citations: {"enabled": true}`. The API returns `citation` deltas inline with the text, which the frontend renders as numbered references.

### Prompts (`webapp/rag/prompts.py`)

System prompts for each persona mode (Sales Executive, Sales Engineer, Product Manager, Technical Architect, C-Level, Research Analyst). Each prompt instructs Claude on tone, detail level, and KX positioning strategy.

---

## Claude API Integration

### Models

| Model | Usage | Config |
|-------|-------|--------|
| Claude Sonnet 4.6 | Default answer generation | `max_tokens=16000`, thinking adaptive |
| Claude Haiku 4.5 | Fast mode | `max_tokens=8000`, thinking disabled |
| GPT-4o / GPT-4.1 | Alternative provider | OpenAI-compatible path |

### Features

**Adaptive Thinking**: Enabled via `thinking: {"type": "adaptive"}`. Thinking text is streamed to the frontend and displayed in a progress popup. The `temperature` parameter must be omitted when thinking is enabled (API requirement). The UI provides a toggle to disable thinking per-query.

**Native Citations**: Documents are passed as `search_result` content blocks. The API returns `citation` events with `cited_text` and source references. These replace the older `[N]` prompt-engineering approach.

**Web Search Tool**: `web_search_20250305` — a server-side tool that Claude invokes during generation to search the web for current information. Results are streamed as `web_search_result` SSE events.

**Memory Tool**: `memory_20250818` — per-user file storage at `data/memories/{username}/`. Supports save, search, delete operations. Protected against path traversal. Limits: 10KB per file, 100 files per user.

**Prompt Caching**: `cache_control: {"type": "ephemeral"}` is applied to:
1. The system prompt (first message)
2. The last conversation history message (enables caching of the full history prefix)

**Token Tracking**: Every query returns usage data:
- `input_tokens`, `output_tokens`
- `cache_creation_input_tokens`, `cache_read_input_tokens`
- Cumulative session totals stored in SQLite

### Auto-Compaction

Conversation history sent to the API is limited to ~4000 tokens to manage costs:

1. Fetch last 10 messages from the session
2. Iterate newest-first, estimating tokens at ~4 chars/token
3. Include messages until the budget is exhausted
4. The `metadata.history_messages_included` field reports how many messages were included

This is transparent to the user — the most recent messages always take priority.

### Cost Estimation

Per-query cost is estimated in the frontend using:

| Model | Input | Output |
|-------|-------|--------|
| Sonnet 4.6 | $3.00 / M tokens | $15.00 / M tokens |
| Haiku 4.5 | $0.80 / M tokens | $4.00 / M tokens |

Displayed as a badge on each message and in the metadata panel.

---

## Session & History Management

### Backend (`webapp/sessions.py`)

SQLite database (`data/sessions.db`) in WAL mode for concurrent reads during SSE streaming.

**Tables**:

| Table | Columns | Purpose |
|-------|---------|---------|
| `sessions` | `session_id`, `username`, `title`, `created_at`, `updated_at` | Session metadata |
| `messages` | `id`, `session_id`, `role`, `content`, `tokens_input`, `tokens_output`, `model`, `created_at` | Message history |

**Key methods**:

| Method | Purpose |
|--------|---------|
| `create_session(username)` | Creates a new session, returns session_id |
| `get_sessions(username)` | Lists all sessions for a user, newest first |
| `add_message(session_id, role, content, ...)` | Stores a message with token counts |
| `get_recent_messages(session_id, limit)` | Retrieves recent messages for context |
| `delete_session(session_id)` | Deletes a single session and its messages |
| `delete_all_sessions(username)` | Bulk-deletes all sessions for a user |
| `get_session_tokens(session_id)` | Aggregates total input/output tokens for a session |

### Frontend Features

- **History panel**: Modal listing all sessions with search, delete, and export (HTML/PDF)
- **New Session button**: Creates a fresh session without losing existing history
- **Clear All History**: Bulk-deletes all sessions from the History modal (with confirmation dialog)
- **Session token display**: Running total of tokens used in the current session

---

## Frontend Architecture

### Design Philosophy

Vanilla JS in an IIFE pattern — no framework, no build step. CSS uses BEM naming with custom properties (design tokens) for theming.

### HTML (`webapp/templates/index.html`)

Single-page template with these regions:

| Region | Purpose |
|--------|---------|
| `.top-nav` | Logo, New Session button, History, DB Explorer, Settings, user menu |
| `.main-content` | Sidebar filters + conversation area + right rail metadata |
| `.query-controls-row` | Toggles: Fast Mode, Web Search, Thinking, LLM Knowledge |
| `.conversation__toolbar` | Collapse All / Expand All buttons (visible when > 1 Q&A pair) |
| `.conversation-area` | Q&A pairs with collapsible containers |
| `.right-rail` | Metadata panel, token usage, source details |

Cache-busted with `?v=N` query params on CSS and JS includes.

### JavaScript (`webapp/static/js/app.js`)

Key modules/functions:

| Function | Purpose |
|----------|---------|
| `submitQuery()` | Reads all form inputs, sends POST to `/api/query-stream`, consumes SSE |
| `consumeSSEStream()` | Parses SSE events, routes to handlers (token, citation, thinking, etc.) |
| `createStreamingMessage()` | Creates the assistant message DOM with markdown renderer |
| `finalizeStreamingMessage()` | Cleans up after streaming: renders executive summary, follow-ups, metrics |
| `renderMessageMetrics()` | Inline metric badges (model, time, tokens, cache, cost) per message |
| `addCollapseToggle()` | Adds expand/collapse button to each Q&A pair |
| `renderUsage()` | Right rail token usage panel with cost estimation |
| `renderMetadata()` | Right rail metadata panel (timings, model, provider, history context) |
| `loadSessionMessages()` | Restores conversation from session history, auto-collapses older pairs |
| `startNewSession()` | Creates a new session and clears the conversation area |
| `clearAllHistory()` | Bulk-deletes all sessions after confirmation |

**SSE Event Handling**:

| Event | Handler |
|-------|---------|
| `status` | Updates progress popup step indicator |
| `thinking` | Appends to thinking text in popup |
| `token` | Appends text to streaming message, dismisses popup on first token |
| `citation_delta` | Renders native citation inline |
| `citations_sources` | Populates right rail source list |
| `web_search_result` | Renders web search results in message |
| `usage` | Captures token counts for metrics |
| `followups` | Renders follow-up suggestion buttons |
| `metadata` | Renders right rail metadata, triggers inline metrics |
| `done` | Finalizes message, adds collapse toggle, shows toolbar |

### CSS (`webapp/static/css/main.css`)

Custom design system with:

- **CSS custom properties**: Colors, spacing, typography, shadows defined as tokens
- **BEM naming**: `.block__element--modifier` convention throughout
- **Component styles**: Toggle switches, buttons, modals, toast notifications, metric badges
- **Print stylesheet**: Hides interactive elements, optimizes for PDF export
- **Responsive**: Sidebar collapses on narrow viewports

Notable style groups:

| Class Pattern | Purpose |
|---------------|---------|
| `.toggle__track--*` | Colored track variants (blue=fast, green=web, purple=thinking) |
| `.qa-pair--collapsed` | Collapsed state hides message body, summary, follow-ups |
| `.qa-collapse-btn` | Positioned collapse/expand toggle per Q&A pair |
| `.message__metrics` | Inline metric badge bar below each message |
| `.metric-badge--*` | Variants: cache (blue), cost (green), history (amber) |
| `.conversation__toolbar` | Collapse All / Expand All buttons |
| `.executive-summary` | Hero card with semantic highlighting |
| `.comparison-widget` | Side-by-side feature comparison in right rail |

---

## Key Files Reference

| File | Lines | Description |
|------|-------|-------------|
| `pipeline.py` | CLI orchestrator — scrape, process, generate, vectorize, serve |
| `webapp/app.py` | FastAPI server, REST/SSE endpoints, file upload |
| `webapp/sessions.py` | SQLite session/message CRUD, bulk delete, token aggregation |
| `webapp/rag/query_engine.py` | Core RAG: message construction, Claude API streaming, thinking, citations |
| `webapp/rag/retriever.py` | HyDE, multi-query, RRF retrieval strategies |
| `webapp/rag/prompts.py` | System prompts for 6 persona modes |
| `webapp/templates/index.html` | Single-page HTML template |
| `webapp/static/js/app.js` | Frontend application (SSE consumer, DOM rendering, session management) |
| `webapp/static/css/main.css` | Design system (tokens, components, print styles) |
| `config/taxonomy.json` | 20-topic taxonomy (3 tiers) for capital markets databases |
| `config/keywords.json` | Topic-to-keyword mapping for tagging |
| `config/competitors/*.json` | Per-competitor source specifications |
| `vectorstore/chunker.py` | Content-aware chunking strategies |
| `vectorstore/store.py` | ChromaDB collection management |

---

## Configuration

### Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `ANTHROPIC_API_KEY` | Yes | Claude API access |
| `OPENAI_API_KEY` | Yes | Embedding generation (always required) |
| `GITHUB_TOKEN` | Yes | GitHub API scraping (5000 req/hr) |
| `LLM_MODEL` | No | Override default model (default: `claude-sonnet-4-6`) |
| `LLM_PROVIDER` | No | `anthropic` or `openai` (default: `anthropic`) |

### Runtime Settings

Configurable via `POST /api/settings`:

```json
{
  "provider": "anthropic",
  "model": "claude-sonnet-4-6",
  "temperature": 0.3,
  "max_tokens": 16000,
  "fast_mode": false
}
```

---

## Development

### Running Locally

```bash
cd competitive-intel
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # Add your API keys
python pipeline.py serve --port 8501 --reload
```

### Validation

```bash
# JS syntax check
node --check webapp/static/js/app.js

# Python import check
python -c "from webapp.app import app; print('OK')"
python -c "from webapp.sessions import SessionManager; print('OK')"
python -c "from webapp.rag.query_engine import CompetitiveQueryEngine; print('OK')"
```

### Adding a New Competitor

1. Create `config/competitors/{name}.json` with source specs
2. Run the full pipeline:
```bash
python pipeline.py scrape --target {name}
python pipeline.py process --target {name}
python pipeline.py generate --competitor {name}
python pipeline.py vectorize --target {name}
```

See [RUNBOOK.md](RUNBOOK.md) for a worked example with TimescaleDB.
