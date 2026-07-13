from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def default_state_path() -> Path:
    root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return root / "demo-agent-supervisor" / "state.sqlite3"


def read_human_review_queue(path: str | Path) -> list[dict[str, str]]:
    """Read human-review rows without creating or migrating supervisor state."""
    state_path = Path(path)
    if not state_path.exists():
        return []
    db = sqlite3.connect(f"file:{state_path}?mode=ro", uri=True)
    try:
        columns = {str(row[1]) for row in db.execute("PRAGMA table_info(human_review_tasks)")}
        if "title" in columns:
            title = "title"
        else:
            title = "'' AS title"
        rows = db.execute(
            f"SELECT host_id,thread_id,{title},reason,marked_at FROM human_review_tasks ORDER BY marked_at DESC"
        ).fetchall()
        return [
            {"host_id": str(host_id), "thread_id": str(thread_id), "title": str(row_title), "reason": str(reason), "marked_at": str(marked_at)}
            for host_id, thread_id, row_title, reason, marked_at in rows
        ]
    finally:
        db.close()


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
          reason TEXT NOT NULL, marked_at TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
          PRIMARY KEY (host_id, thread_id));
        CREATE TABLE IF NOT EXISTS supervisor_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS supervisor_shadow_decisions (
          id INTEGER PRIMARY KEY AUTOINCREMENT, host_id TEXT NOT NULL, thread_id TEXT NOT NULL,
          decision TEXT NOT NULL, reason TEXT NOT NULL, reply TEXT, recorded_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS supervisor_canary_evidence (
          evidence_id TEXT PRIMARY KEY, inventory_identity TEXT NOT NULL,
          delivery_identity TEXT NOT NULL, recorded_at TEXT NOT NULL, revoked_at TEXT);
        CREATE TABLE IF NOT EXISTS supervisor_delivery_claims (
          host_id TEXT NOT NULL, thread_id TEXT NOT NULL, unread_at INTEGER NOT NULL,
          status TEXT NOT NULL CHECK (status IN ('CLAIMED','AWAITING_CLEARANCE','CONFIRMED')),
          claimed_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          PRIMARY KEY (host_id, thread_id, unread_at));
        """)
        columns = {str(row[1]) for row in self.db.execute("PRAGMA table_info(human_review_tasks)")}
        if "title" not in columns:
            self.db.execute("ALTER TABLE human_review_tasks ADD COLUMN title TEXT NOT NULL DEFAULT ''")
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def is_human_review(self, host_id: str, thread_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM human_review_tasks WHERE host_id=? AND thread_id=?", (host_id, thread_id)).fetchone() is not None

    def mark_human_review(self, host_id: str, thread_id: str, reason: str, title: str = "") -> None:
        self.db.execute(
            "INSERT INTO human_review_tasks (host_id,thread_id,disposition,reason,marked_at,title) VALUES (?,?,'HUMAN_REVIEW_NEEDED',?,?,?) "
            "ON CONFLICT(host_id,thread_id) DO UPDATE SET title=excluded.title "
            "WHERE human_review_tasks.title='' AND excluded.title != ''",
            (host_id, thread_id, reason, datetime.now(UTC).isoformat(), title.strip()),
        )
        self.db.commit()

    def human_review_queue(self) -> list[dict[str, str]]:
        return read_human_review_queue(self.path)

    def reset_human_review(self, host_id: str, thread_id: str) -> bool:
        cursor = self.db.execute("DELETE FROM human_review_tasks WHERE host_id=? AND thread_id=?", (host_id, thread_id))
        self.db.commit()
        return cursor.rowcount == 1

    @staticmethod
    def _session_key(host_id: str, thread_id: str) -> str:
        return f"claude_session_id:{host_id}:{thread_id}"

    def session_id(self, host_id: str | None = None, thread_id: str | None = None) -> str | None:
        key = "claude_session_id" if host_id is None or thread_id is None else self._session_key(host_id, thread_id)
        row = self.db.execute("SELECT value FROM supervisor_settings WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def set_session_id(self, session_id: str, host_id: str | None = None, thread_id: str | None = None) -> None:
        if not session_id.strip():
            raise ValueError("supervisor session ID must be non-empty")
        key = "claude_session_id" if host_id is None or thread_id is None else self._session_key(host_id, thread_id)
        self.db.execute("INSERT INTO supervisor_settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, session_id))
        self.db.commit()

    def record_canary_evidence(self, evidence_id: str, inventory_identity: str, delivery_identity: str) -> None:
        if not all(value.strip() for value in (evidence_id, inventory_identity, delivery_identity)):
            raise ValueError("canary evidence and adapter identities must be non-empty")
        self.db.execute(
            "INSERT INTO supervisor_canary_evidence(evidence_id,inventory_identity,delivery_identity,recorded_at,revoked_at) VALUES (?,?,?,?,NULL) "
            "ON CONFLICT(evidence_id) DO UPDATE SET inventory_identity=excluded.inventory_identity, delivery_identity=excluded.delivery_identity, recorded_at=excluded.recorded_at, revoked_at=NULL",
            (evidence_id, inventory_identity, delivery_identity, datetime.now(UTC).isoformat()),
        )
        self.db.commit()

    def canary_matches(self, evidence_id: str | None, inventory_identity: str | None, delivery_identity: str | None) -> bool:
        if not all((evidence_id, inventory_identity, delivery_identity)):
            return False
        row = self.db.execute(
            "SELECT 1 FROM supervisor_canary_evidence WHERE evidence_id=? AND inventory_identity=? AND delivery_identity=? AND revoked_at IS NULL",
            (evidence_id, inventory_identity, delivery_identity),
        ).fetchone()
        return row is not None

    def claim_delivery(self, host_id: str, thread_id: str, unread_at: int) -> bool:
        now = datetime.now(UTC).isoformat()
        cursor = self.db.execute(
            "INSERT OR IGNORE INTO supervisor_delivery_claims(host_id,thread_id,unread_at,status,claimed_at,updated_at) VALUES (?,?,?,'CLAIMED',?,?)",
            (host_id, thread_id, unread_at, now, now),
        )
        self.db.commit()
        return cursor.rowcount == 1

    def await_clearance(self, host_id: str, thread_id: str, unread_at: int) -> None:
        self.db.execute(
            "UPDATE supervisor_delivery_claims SET status='AWAITING_CLEARANCE', updated_at=? WHERE host_id=? AND thread_id=? AND unread_at=? AND status='CLAIMED'",
            (datetime.now(UTC).isoformat(), host_id, thread_id, unread_at),
        )
        self.db.commit()

    def delivery_claim(self, host_id: str, thread_id: str, unread_at: int) -> tuple[str, datetime] | None:
        row = self.db.execute(
            "SELECT status, updated_at FROM supervisor_delivery_claims WHERE host_id=? AND thread_id=? AND unread_at=?",
            (host_id, thread_id, unread_at),
        ).fetchone()
        return None if row is None else (str(row[0]), datetime.fromisoformat(str(row[1])))

    def reconcile_delivery_claims(self, observed_unread: dict[tuple[str, str, int], str], confirmation_timeout_seconds: int) -> None:
        now = datetime.now(UTC)
        rows = self.db.execute("SELECT host_id,thread_id,unread_at,status,updated_at FROM supervisor_delivery_claims WHERE status != 'CONFIRMED'").fetchall()
        for host_id, thread_id, unread_at, status, updated_at in rows:
            key = (str(host_id), str(thread_id), int(unread_at))
            if key not in observed_unread:
                self.db.execute("UPDATE supervisor_delivery_claims SET status='CONFIRMED', updated_at=? WHERE host_id=? AND thread_id=? AND unread_at=?", (now.isoformat(), *key))
            elif status == "CLAIMED":
                self.mark_human_review(key[0], key[1], "delivery claim interrupted before transport acknowledgement", observed_unread[key])
            elif (now - datetime.fromisoformat(str(updated_at))).total_seconds() > confirmation_timeout_seconds:
                self.mark_human_review(key[0], key[1], "reply transport acknowledged but unread result did not clear before confirmation timeout", observed_unread[key])
        self.db.commit()

    def record_shadow(self, host_id: str, thread_id: str, decision: str, reason: str, reply: str | None) -> None:
        self.db.execute("INSERT INTO supervisor_shadow_decisions(host_id,thread_id,decision,reason,reply,recorded_at) VALUES (?,?,?,?,?,?)", (host_id, thread_id, decision, reason, reply, datetime.now(UTC).isoformat()))
        self.db.commit()

    def status(self) -> dict[str, int | str | None]:
        return {"state_path": str(self.path), "session_id": self.session_id(), "human_review_count": self.db.execute("SELECT count(*) FROM human_review_tasks").fetchone()[0], "shadow_decision_count": self.db.execute("SELECT count(*) FROM supervisor_shadow_decisions").fetchone()[0], "pending_delivery_count": self.db.execute("SELECT count(*) FROM supervisor_delivery_claims WHERE status != 'CONFIRMED'").fetchone()[0]}
