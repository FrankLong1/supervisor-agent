from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def default_state_path() -> Path:
    root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return root / "demo-agent-supervisor" / "state.sqlite3"


class SupervisorState:
    """Durable terminal markers, session ID, and shadow-mode audit records."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS human_review_tasks (
          host_id TEXT NOT NULL, thread_id TEXT NOT NULL,
          disposition TEXT NOT NULL CHECK (disposition = 'HUMAN_REVIEW_NEEDED'),
          reason TEXT NOT NULL, marked_at TEXT NOT NULL, PRIMARY KEY (host_id, thread_id));
        CREATE TABLE IF NOT EXISTS supervisor_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS supervisor_shadow_decisions (
          id INTEGER PRIMARY KEY AUTOINCREMENT, host_id TEXT NOT NULL, thread_id TEXT NOT NULL,
          decision TEXT NOT NULL, reason TEXT NOT NULL, reply TEXT, recorded_at TEXT NOT NULL);
        """)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def is_human_review(self, host_id: str, thread_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM human_review_tasks WHERE host_id=? AND thread_id=?", (host_id, thread_id)).fetchone() is not None

    def mark_human_review(self, host_id: str, thread_id: str, reason: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO human_review_tasks (host_id,thread_id,disposition,reason,marked_at) VALUES (?,?,'HUMAN_REVIEW_NEEDED',?,?)", (host_id, thread_id, reason, datetime.now(UTC).isoformat()))
        self.db.commit()

    def reset_human_review(self, host_id: str, thread_id: str) -> bool:
        cursor = self.db.execute("DELETE FROM human_review_tasks WHERE host_id=? AND thread_id=?", (host_id, thread_id))
        self.db.commit()
        return cursor.rowcount == 1

    def session_id(self) -> str | None:
        row = self.db.execute("SELECT value FROM supervisor_settings WHERE key='claude_session_id'").fetchone()
        return None if row is None else str(row[0])

    def set_session_id(self, session_id: str) -> None:
        if not session_id.strip():
            raise ValueError("supervisor session ID must be non-empty")
        self.db.execute("INSERT INTO supervisor_settings(key,value) VALUES ('claude_session_id',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (session_id,))
        self.db.commit()

    def record_shadow(self, host_id: str, thread_id: str, decision: str, reason: str, reply: str | None) -> None:
        self.db.execute("INSERT INTO supervisor_shadow_decisions(host_id,thread_id,decision,reason,reply,recorded_at) VALUES (?,?,?,?,?,?)", (host_id, thread_id, decision, reason, reply, datetime.now(UTC).isoformat()))
        self.db.commit()

    def status(self) -> dict[str, int | str | None]:
        return {"state_path": str(self.path), "session_id": self.session_id(), "human_review_count": self.db.execute("SELECT count(*) FROM human_review_tasks").fetchone()[0], "shadow_decision_count": self.db.execute("SELECT count(*) FROM supervisor_shadow_decisions").fetchone()[0]}
