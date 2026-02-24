"""SQLite-backed session management for competitive intelligence Q&A.

Provides persistent conversation history, token tracking, and user sessions.
Uses WAL mode for concurrent reads during SSE streaming.
"""

import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "sessions.db"


class SessionManager:
    """Manages users, sessions, and message history via SQLite."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self):
        conn = self._get_conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    display_name TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL REFERENCES users(username),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_active_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    title TEXT
                );

                CREATE TABLE IF NOT EXISTS messages (
                    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id),
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    model TEXT,
                    tokens_input INTEGER DEFAULT 0,
                    tokens_output INTEGER DEFAULT 0,
                    cache_creation_tokens INTEGER DEFAULT 0,
                    cache_read_tokens INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_messages_session
                    ON messages(session_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_sessions_username
                    ON sessions(username, last_active_at DESC);
            """)
            conn.commit()
            logger.info("Session database initialized at %s", self.db_path)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    def get_or_create_user(self, username: str) -> dict:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
            if row:
                return dict(row)
            conn.execute(
                "INSERT INTO users (username, display_name) VALUES (?, ?)",
                (username, username),
            )
            conn.commit()
            return {"username": username, "display_name": username, "created_at": datetime.utcnow().isoformat()}
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def create_session(self, username: str, title: Optional[str] = None) -> str:
        session_id = uuid.uuid4().hex[:16]
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT INTO sessions (session_id, username, title) VALUES (?, ?, ?)",
                (session_id, username, title),
            )
            conn.commit()
            return session_id
        finally:
            conn.close()

    def list_sessions(self, username: str, limit: int = 20) -> list[dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT s.session_id, s.title, s.created_at, s.last_active_at,
                          COUNT(m.message_id) as message_count,
                          COALESCE(SUM(m.tokens_input), 0) as total_tokens_input,
                          COALESCE(SUM(m.tokens_output), 0) as total_tokens_output
                   FROM sessions s
                   LEFT JOIN messages m ON s.session_id = m.session_id
                   WHERE s.username = ?
                   GROUP BY s.session_id
                   ORDER BY s.last_active_at DESC
                   LIMIT ?""",
                (username, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_session(self, session_id: str) -> Optional[dict]:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def update_session_title(self, session_id: str, title: str):
        conn = self._get_conn()
        try:
            conn.execute(
                "UPDATE sessions SET title = ? WHERE session_id = ? AND title IS NULL",
                (title, session_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _touch_session(self, conn: sqlite3.Connection, session_id: str):
        conn.execute(
            "UPDATE sessions SET last_active_at = CURRENT_TIMESTAMP WHERE session_id = ?",
            (session_id,),
        )

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        model: Optional[str] = None,
        tokens_input: int = 0,
        tokens_output: int = 0,
        cache_creation_tokens: int = 0,
        cache_read_tokens: int = 0,
    ) -> int:
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """INSERT INTO messages
                   (session_id, role, content, model, tokens_input, tokens_output,
                    cache_creation_tokens, cache_read_tokens)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, role, content, model, tokens_input, tokens_output,
                 cache_creation_tokens, cache_read_tokens),
            )
            self._touch_session(conn, session_id)
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def get_recent_messages(self, session_id: str, limit: int = 5) -> list[dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT role, content, model, tokens_input, tokens_output, created_at
                   FROM messages
                   WHERE session_id = ?
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (session_id, limit),
            ).fetchall()
            # Return in chronological order (oldest first)
            return [dict(r) for r in reversed(rows)]
        finally:
            conn.close()

    def get_all_messages(self, session_id: str) -> list[dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT role, content, model, tokens_input, tokens_output,
                          cache_creation_tokens, cache_read_tokens, created_at
                   FROM messages
                   WHERE session_id = ?
                   ORDER BY created_at ASC""",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_session_token_totals(self, session_id: str) -> dict:
        conn = self._get_conn()
        try:
            row = conn.execute(
                """SELECT
                    COALESCE(SUM(tokens_input), 0) as total_input,
                    COALESCE(SUM(tokens_output), 0) as total_output,
                    COALESCE(SUM(cache_creation_tokens), 0) as total_cache_creation,
                    COALESCE(SUM(cache_read_tokens), 0) as total_cache_read,
                    COUNT(*) as message_count
                   FROM messages
                   WHERE session_id = ?""",
                (session_id,),
            ).fetchone()
            return dict(row) if row else {
                "total_input": 0, "total_output": 0,
                "total_cache_creation": 0, "total_cache_read": 0,
                "message_count": 0,
            }
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Session management (delete, search, export)
    # ------------------------------------------------------------------

    def delete_all_sessions(self, username: str) -> int:
        """Delete all sessions and messages for a user. Returns count deleted."""
        conn = self._get_conn()
        try:
            session_ids = [r["session_id"] for r in conn.execute(
                "SELECT session_id FROM sessions WHERE username = ?", (username,)
            ).fetchall()]
            if not session_ids:
                return 0
            placeholders = ",".join("?" * len(session_ids))
            conn.execute(f"DELETE FROM messages WHERE session_id IN ({placeholders})", session_ids)
            cursor = conn.execute("DELETE FROM sessions WHERE username = ?", (username,))
            conn.commit()
            return cursor.rowcount
        finally:
            conn.close()

    def delete_session(self, session_id: str) -> bool:
        """Delete a session and all its messages. Returns True if deleted."""
        conn = self._get_conn()
        try:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            cursor = conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def search_sessions(self, username: str, query: str, limit: int = 50) -> list[dict]:
        """Search sessions by title or message content."""
        conn = self._get_conn()
        try:
            search_term = f"%{query}%"
            rows = conn.execute(
                """SELECT DISTINCT s.session_id, s.title, s.created_at, s.last_active_at,
                          COUNT(m.message_id) as message_count,
                          COALESCE(SUM(m.tokens_input), 0) as total_tokens_input,
                          COALESCE(SUM(m.tokens_output), 0) as total_tokens_output
                   FROM sessions s
                   LEFT JOIN messages m ON s.session_id = m.session_id
                   WHERE s.username = ?
                     AND (s.title LIKE ? OR s.session_id IN (
                         SELECT DISTINCT session_id FROM messages
                         WHERE content LIKE ?
                     ))
                   GROUP BY s.session_id
                   ORDER BY s.last_active_at DESC
                   LIMIT ?""",
                (username, search_term, search_term, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def export_session(self, session_id: str) -> Optional[dict]:
        """Export a session with all messages and token totals."""
        session = self.get_session(session_id)
        if not session:
            return None
        messages = self.get_all_messages(session_id)
        totals = self.get_session_token_totals(session_id)
        return {
            "session": session,
            "messages": messages,
            "token_totals": totals,
            "exported_at": datetime.utcnow().isoformat() + "Z",
        }
