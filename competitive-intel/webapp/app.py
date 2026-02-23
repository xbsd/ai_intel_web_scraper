"""FastAPI web application for competitive intelligence Q&A.

Launch:
    cd competitive-intel
    python -m uvicorn webapp.app:app --reload --port 8501

Or via pipeline:
    python pipeline.py serve --port 8501
"""

import json
import logging
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Ensure the project root is on sys.path so we can import vectorstore, etc.
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from vectorstore.store import VectorStore
from vectorstore.embedder import Embedder
from webapp.rag.retriever import Retriever
from webapp.rag.query_engine import QueryEngine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Competitive Intelligence Q&A",
    description="RAG-powered competitive intelligence for KX sales teams",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files
WEBAPP_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=str(WEBAPP_DIR / "static")), name="static")

# ---------------------------------------------------------------------------
# Global state — lazy-initialized on first request
# ---------------------------------------------------------------------------

_store: Optional[VectorStore] = None
_embedder: Optional[Embedder] = None
_retriever: Optional[Retriever] = None

# Session-level settings (in-memory; reset on restart)
_settings = {
    "llm_provider": "anthropic",
    "llm_model": None,  # use default per provider
    "openai_api_key": os.getenv("OPENAI_API_KEY", ""),
    "anthropic_api_key": os.getenv("ANTHROPIC_API_KEY", ""),
}


def _get_retriever() -> Retriever:
    global _store, _embedder, _retriever
    if _retriever is None:
        _store = VectorStore()
        _embedder = Embedder(api_key=_settings["openai_api_key"] or None)
        _retriever = Retriever(_store, _embedder)
    return _retriever


def _get_query_engine() -> QueryEngine:
    retriever = _get_retriever()
    provider = _settings["llm_provider"]
    if provider == "anthropic":
        api_key = _settings["anthropic_api_key"] or None
    else:
        api_key = _settings["openai_api_key"] or None
    return QueryEngine(
        retriever=retriever,
        llm_provider=provider,
        llm_model=_settings["llm_model"],
        llm_api_key=api_key,
    )


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    query: str
    competitor_filter: Optional[list[str]] = None
    topic_filter: Optional[list[str]] = None
    source_type_filter: Optional[list[str]] = None
    n_results: int = 12


class SettingsUpdate(BaseModel):
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the main Q&A interface."""
    html_path = WEBAPP_DIR / "templates" / "index.html"
    return HTMLResponse(content=html_path.read_text())


@app.post("/api/query")
async def api_query(req: QueryRequest):
    """Execute a RAG query and return grounded answer with citations."""
    try:
        engine = _get_query_engine()
        result = engine.query(
            query=req.query,
            competitor_filter=req.competitor_filter,
            topic_filter=req.topic_filter,
            source_type_filter=req.source_type_filter,
            n_results=req.n_results,
        )

        return {
            "query": result.query,
            "answer": result.answer,
            "citations": [asdict(c) for c in result.citations],
            "follow_up_questions": result.follow_up_questions,
            "metadata": result.metadata,
        }
    except Exception as e:
        logger.exception("Query failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/settings")
async def get_settings():
    """Return current settings (keys masked)."""
    return {
        "llm_provider": _settings["llm_provider"],
        "llm_model": _settings["llm_model"],
        "openai_api_key_set": bool(_settings["openai_api_key"]),
        "anthropic_api_key_set": bool(_settings["anthropic_api_key"]),
        "available_providers": [
            {
                "id": "anthropic",
                "name": "Anthropic (Claude)",
                "models": [
                    {"id": "claude-sonnet-4-20250514", "name": "Claude Sonnet 4"},
                    {"id": "claude-haiku-4-5-20251001", "name": "Claude Haiku 4.5"},
                    {"id": "claude-opus-4-20250514", "name": "Claude Opus 4"},
                ],
            },
            {
                "id": "openai",
                "name": "OpenAI",
                "models": [
                    {"id": "gpt-4o", "name": "GPT-4o"},
                    {"id": "gpt-4o-mini", "name": "GPT-4o Mini"},
                    {"id": "gpt-4.1", "name": "GPT-4.1"},
                ],
            },
        ],
    }


@app.post("/api/settings")
async def update_settings(req: SettingsUpdate):
    """Update LLM settings."""
    global _retriever, _embedder

    if req.llm_provider is not None:
        _settings["llm_provider"] = req.llm_provider
    if req.llm_model is not None:
        _settings["llm_model"] = req.llm_model or None
    if req.openai_api_key is not None:
        _settings["openai_api_key"] = req.openai_api_key
        # Reset embedder to use new key
        _embedder = None
        _retriever = None
    if req.anthropic_api_key is not None:
        _settings["anthropic_api_key"] = req.anthropic_api_key

    return {"status": "ok", "settings": await get_settings()}


@app.get("/api/status")
async def api_status():
    """Return vector store status and competitor/topic metadata."""
    try:
        store = VectorStore()
        stats = store.get_stats()
    except Exception:
        stats = {}

    # Load taxonomy for topic labels
    taxonomy_path = PROJECT_ROOT / "config" / "taxonomy.json"
    topics = []
    if taxonomy_path.exists():
        taxonomy = json.loads(taxonomy_path.read_bytes())
        for tier in taxonomy.get("tiers", {}).values():
            for tid, tinfo in tier.get("topics", {}).items():
                topics.append({"id": tid, "name": tinfo["name"]})

    # Load competitor names
    competitors = []
    comp_dir = PROJECT_ROOT / "config" / "competitors"
    if comp_dir.exists():
        for f in comp_dir.glob("*.json"):
            try:
                data = json.loads(f.read_bytes())
                competitors.append({
                    "id": data.get("short_name", f.stem),
                    "name": data.get("name", f.stem),
                    "is_self": data.get("is_self", False),
                })
            except Exception:
                pass

    source_types = [
        "official_docs", "blog", "github_issue", "github_discussion",
        "github_release", "community_reddit", "community_hn", "benchmark",
        "product_page", "case_study", "whitepaper", "comparison_page",
    ]

    return {
        "vector_store": stats,
        "competitors": competitors,
        "topics": topics,
        "source_types": source_types,
    }
