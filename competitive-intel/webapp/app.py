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
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
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
from webapp.sessions import SessionManager

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
_session_mgr: Optional[SessionManager] = None

# Session-level settings (in-memory; reset on restart)
_settings = {
    "llm_provider": "anthropic",
    "llm_model": None,  # use default per provider
    "openai_api_key": os.getenv("OPENAI_API_KEY", ""),
    "anthropic_api_key": os.getenv("ANTHROPIC_API_KEY", ""),
}


def _get_session_mgr() -> SessionManager:
    global _session_mgr
    if _session_mgr is None:
        _session_mgr = SessionManager()
    return _session_mgr


def _get_retriever() -> Retriever:
    global _store, _embedder, _retriever
    if _retriever is None:
        _store = VectorStore()
        _embedder = Embedder(api_key=_settings["openai_api_key"] or None)
        _retriever = Retriever(_store, _embedder)
    return _retriever


def _get_query_engine(fast_mode: bool = False) -> QueryEngine:
    retriever = _get_retriever()
    provider = _settings["llm_provider"]
    model = _settings["llm_model"]

    if fast_mode:
        provider = "anthropic"
        model = "claude-haiku-4-5-20251001"

    if provider == "anthropic":
        api_key = _settings["anthropic_api_key"] or None
    else:
        api_key = _settings["openai_api_key"] or None
    return QueryEngine(
        retriever=retriever,
        llm_provider=provider,
        llm_model=model,
        llm_api_key=api_key,
        fast_mode=fast_mode,
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
    persona: Optional[str] = None
    use_llm_knowledge: bool = False
    use_web_search: bool = False
    fast_mode: bool = False
    use_thinking: bool = True
    session_id: Optional[str] = None
    username: Optional[str] = None


class LoginRequest(BaseModel):
    username: str


class SessionCreateRequest(BaseModel):
    username: str


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
        engine = _get_query_engine(fast_mode=req.fast_mode)
        result = engine.query(
            query=req.query,
            competitor_filter=req.competitor_filter,
            topic_filter=req.topic_filter,
            source_type_filter=req.source_type_filter,
            n_results=req.n_results,
            persona=req.persona,
            use_llm_knowledge=req.use_llm_knowledge,
            use_web_search=req.use_web_search,
            session_id=req.session_id,
            username=req.username,
            use_thinking=req.use_thinking,
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


@app.post("/api/query-stream")
async def api_query_stream(req: QueryRequest):
    """Execute a RAG query with Server-Sent Events streaming."""
    try:
        engine = _get_query_engine(fast_mode=req.fast_mode)
        session_mgr = _get_session_mgr()

        def event_generator():
            try:
                full_answer_parts = []
                usage_data = {}

                for event_str in engine.query_stream(
                    query=req.query,
                    competitor_filter=req.competitor_filter,
                    topic_filter=req.topic_filter,
                    source_type_filter=req.source_type_filter,
                    n_results=req.n_results,
                    persona=req.persona,
                    use_llm_knowledge=req.use_llm_knowledge,
                    use_web_search=req.use_web_search,
                    session_id=req.session_id,
                    username=req.username,
                    use_thinking=req.use_thinking,
                ):
                    # Capture answer tokens and usage for session persistence
                    if event_str.startswith("event: token\n"):
                        try:
                            data_line = event_str.split("data: ", 1)[1].split("\n")[0]
                            token_data = json.loads(data_line)
                            full_answer_parts.append(token_data.get("text", ""))
                        except Exception:
                            pass
                    elif event_str.startswith("event: usage\n"):
                        try:
                            data_line = event_str.split("data: ", 1)[1].split("\n")[0]
                            usage_data = json.loads(data_line)
                        except Exception:
                            pass
                    yield event_str

                # Persist conversation to session after stream completes
                if req.session_id:
                    try:
                        session_mgr.add_message(
                            session_id=req.session_id,
                            role="user",
                            content=req.query,
                        )
                        full_answer = "".join(full_answer_parts)
                        if full_answer:
                            session_mgr.add_message(
                                session_id=req.session_id,
                                role="assistant",
                                content=full_answer,
                                model=engine.llm.model if hasattr(engine, 'llm') else None,
                                tokens_input=usage_data.get("input_tokens", 0),
                                tokens_output=usage_data.get("output_tokens", 0),
                                cache_creation_tokens=usage_data.get("cache_creation_input_tokens", 0),
                                cache_read_tokens=usage_data.get("cache_read_input_tokens", 0),
                            )
                        # Auto-title the session from first query
                        session = session_mgr.get_session(req.session_id)
                        if session and not session.get("title"):
                            title = req.query[:80] + ("..." if len(req.query) > 80 else "")
                            session_mgr.update_session_title(req.session_id, title)
                    except Exception as e:
                        logger.warning("Failed to persist session message: %s", e)

            except Exception as e:
                logger.exception("Streaming query failed: %s", e)
                yield f"event: error\ndata: {json.dumps({'detail': str(e)})}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except Exception as e:
        logger.exception("Query stream setup failed: %s", e)
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
                    {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6"},
                    {"id": "claude-opus-4-6", "name": "Claude Opus 4.6"},
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


@app.get("/api/db-stats")
async def api_db_stats(
    filter_field: Optional[str] = None,
    filter_value: Optional[str] = None,
):
    """Return detailed vector database statistics for the explorer panel.

    Optional filters for drill-down: filter_field=competitor&filter_value=clickhouse
    """
    try:
        store = VectorStore()
        detailed = store.get_detailed_stats(
            filter_field=filter_field,
            filter_value=filter_value,
        )

        # Load taxonomy for human-readable topic labels
        taxonomy_path = PROJECT_ROOT / "config" / "taxonomy.json"
        topic_labels = {}
        if taxonomy_path.exists():
            taxonomy = json.loads(taxonomy_path.read_bytes())
            for tier in taxonomy.get("tiers", {}).values():
                for tid, tinfo in tier.get("topics", {}).items():
                    topic_labels[tid] = tinfo["name"]

        # Load competitor labels
        comp_labels = {}
        comp_dir = PROJECT_ROOT / "config" / "competitors"
        if comp_dir.exists():
            for f in comp_dir.glob("*.json"):
                try:
                    data = json.loads(f.read_bytes())
                    comp_labels[data.get("short_name", f.stem)] = data.get("name", f.stem)
                except Exception:
                    pass

        detailed["topic_labels"] = topic_labels
        detailed["competitor_labels"] = comp_labels
        detailed["db_path"] = str(store.db_path)
        if filter_field and filter_value:
            detailed["active_filter"] = {"field": filter_field, "value": filter_value}

        return detailed
    except Exception as e:
        logger.exception("DB stats failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Session / Login
# ---------------------------------------------------------------------------

@app.post("/api/login")
async def api_login(req: LoginRequest):
    """Create or retrieve a user, return user info."""
    mgr = _get_session_mgr()
    user = mgr.get_or_create_user(req.username)
    sessions = mgr.list_sessions(req.username, limit=1)
    return {"user": user, "recent_session": sessions[0] if sessions else None}


@app.post("/api/sessions")
async def api_create_session(req: SessionCreateRequest):
    """Create a new conversation session."""
    mgr = _get_session_mgr()
    mgr.get_or_create_user(req.username)
    session_id = mgr.create_session(req.username)
    return {"session_id": session_id}


@app.get("/api/sessions")
async def api_list_sessions(username: str):
    """List sessions for a user."""
    mgr = _get_session_mgr()
    sessions = mgr.list_sessions(username)
    return {"sessions": sessions}


@app.delete("/api/sessions")
async def api_delete_all_sessions(username: str):
    """Delete all sessions and messages for a user."""
    mgr = _get_session_mgr()
    count = mgr.delete_all_sessions(username)
    return {"status": "ok", "deleted_count": count}


@app.get("/api/sessions/search")
async def api_search_sessions(q: str, username: str):
    """Search sessions by title or message content."""
    mgr = _get_session_mgr()
    sessions = mgr.search_sessions(username, q)
    return {"sessions": sessions}


@app.delete("/api/sessions/{session_id}")
async def api_delete_session(session_id: str):
    """Delete a session and its messages."""
    mgr = _get_session_mgr()
    deleted = mgr.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "ok"}


@app.get("/api/sessions/{session_id}/export")
async def api_export_session(session_id: str):
    """Export a session as JSON."""
    mgr = _get_session_mgr()
    data = mgr.export_session(session_id)
    if not data:
        raise HTTPException(status_code=404, detail="Session not found")
    return data


@app.get("/api/sessions/{session_id}/messages")
async def api_session_messages(session_id: str):
    """Get all messages for a session."""
    mgr = _get_session_mgr()
    messages = mgr.get_all_messages(session_id)
    return {"messages": messages}


@app.get("/api/sessions/{session_id}/tokens")
async def api_session_tokens(session_id: str):
    """Get cumulative token stats for a session."""
    mgr = _get_session_mgr()
    totals = mgr.get_session_token_totals(session_id)
    return totals


# ---------------------------------------------------------------------------
# Content Upload / Ingestion
# ---------------------------------------------------------------------------

UPLOAD_DIR = PROJECT_ROOT / "data" / "uploads"
UPLOAD_INDEX = UPLOAD_DIR / "_index.json"

ALLOWED_EXTENSIONS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".txt", ".md", ".csv", ".json", ".html", ".xml",
    ".doc", ".docx", ".xls", ".xlsx", ".pptx",
}


def _load_upload_index() -> list[dict]:
    if UPLOAD_INDEX.exists():
        return json.loads(UPLOAD_INDEX.read_bytes())
    return []


def _save_upload_index(entries: list[dict]):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_INDEX.write_text(json.dumps(entries, indent=2))


@app.get("/api/content/list")
async def list_content():
    """List all uploaded content files."""
    return {"files": _load_upload_index()}


@app.post("/api/content/upload")
async def upload_content(file: UploadFile = File(...)):
    """Upload a file for ingestion into the vector database."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{ext}' not supported. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    file_id = uuid.uuid4().hex[:12]
    safe_name = f"{file_id}_{file.filename}"
    file_path = UPLOAD_DIR / safe_name

    content = await file.read()
    file_path.write_bytes(content)

    entry = {
        "id": file_id,
        "filename": file.filename,
        "stored_as": safe_name,
        "size_bytes": len(content),
        "content_type": file.content_type or "application/octet-stream",
        "extension": ext,
        "uploaded_at": datetime.utcnow().isoformat() + "Z",
        "status": "pending",
    }

    index = _load_upload_index()
    index.append(entry)
    _save_upload_index(index)

    return {"status": "ok", "file": entry}


# ---------------------------------------------------------------------------
# Battle Card Generator
# ---------------------------------------------------------------------------

@app.post("/api/battlecard/generate")
async def api_battlecard_generate(req: Request):
    """Generate a battle card via SSE streaming."""
    from webapp.battlecard.models import BattleCardRequest
    from webapp.battlecard.generator import BattleCardGenerator

    body = await req.json()
    bc_request = BattleCardRequest(**body)

    generator = BattleCardGenerator()

    def event_stream():
        try:
            for event_type, data in generator.generate(bc_request):
                if event_type == "status":
                    yield f"event: status\ndata: {json.dumps(data)}\n\n"
                elif event_type == "report":
                    yield f"event: report\ndata: {json.dumps(data.model_dump(mode='json'), default=str)}\n\n"
                elif event_type == "error":
                    yield f"event: error\ndata: {json.dumps(data)}\n\n"
            yield "event: done\ndata: {}\n\n"
        except Exception as e:
            logger.exception("Battle card generation failed: %s", e)
            yield f"event: error\ndata: {json.dumps({'detail': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/battlecard/render")
async def api_battlecard_render(req: Request):
    """Render a battle card report as HTML."""
    from webapp.battlecard.models import BattleCardReport
    from webapp.battlecard.report_renderer import render_html

    body = await req.json()
    report = BattleCardReport(**body)
    html_content = render_html(report)
    return HTMLResponse(content=html_content)


@app.get("/api/battlecard/competitors")
async def api_battlecard_competitors():
    """Return available competitors for battle card generation."""
    competitors = []
    comp_dir = PROJECT_ROOT / "config" / "competitors"
    if comp_dir.exists():
        for f in comp_dir.glob("*.json"):
            try:
                data = json.loads(f.read_bytes())
                if not data.get("is_self", False):
                    competitors.append({
                        "id": data.get("short_name", f.stem),
                        "name": data.get("name", f.stem),
                    })
            except Exception:
                pass
    return {"competitors": competitors}
