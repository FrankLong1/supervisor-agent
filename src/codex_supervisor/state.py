from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def default_state_path() -> Path:
    root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return root / "demo-agent-supervisor" / "state.sqlite3"


def read_human_review_queue(path: str | Path) -> list[dict[str, str | int | None]]:
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
        has_turn_audit = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='supervisor_fable_turns'"
        ).fetchone() is not None
        turn_count = (
            "(SELECT count(*) FROM supervisor_fable_turns turns "
            "WHERE turns.host_id=human_review_tasks.host_id AND turns.thread_id=human_review_tasks.thread_id)"
            if has_turn_audit else "NULL"
        )
        rows = db.execute(
            f"SELECT host_id,thread_id,{title},reason,marked_at,{turn_count} AS fable_turn_count "
            "FROM human_review_tasks ORDER BY marked_at DESC"
        ).fetchall()
        return [
            {"host_id": str(host_id), "thread_id": str(thread_id), "title": str(row_title), "reason": str(reason), "marked_at": str(marked_at), "fable_turn_count": None if fable_turn_count is None else int(fable_turn_count)}
            for host_id, thread_id, row_title, reason, marked_at, fable_turn_count in rows
        ]
    finally:
        db.close()


class SupervisorState:
    """Durable terminal markers, session ID, and dry-run audit records."""

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
        CREATE TABLE IF NOT EXISTS supervisor_fable_turns (
          id INTEGER PRIMARY KEY AUTOINCREMENT, host_id TEXT NOT NULL, thread_id TEXT NOT NULL,
          recorded_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS supervisor_canary_evidence (
          evidence_id TEXT PRIMARY KEY, inventory_identity TEXT NOT NULL,
          delivery_identity TEXT NOT NULL, recorded_at TEXT NOT NULL, revoked_at TEXT);
        CREATE TABLE IF NOT EXISTS supervisor_delivery_claims (
          host_id TEXT NOT NULL, thread_id TEXT NOT NULL, unread_at INTEGER NOT NULL,
          status TEXT NOT NULL CHECK (status IN ('CLAIMED','AWAITING_CLEARANCE','CONFIRMED')),
          claimed_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          PRIMARY KEY (host_id, thread_id, unread_at));
        CREATE TABLE IF NOT EXISTS supervisor_inbox_observations (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          delivery_id TEXT NOT NULL, message_id TEXT NOT NULL, thread_id TEXT NOT NULL,
          kind TEXT NOT NULL, proposed_route TEXT NOT NULL,
          proposed_disposition TEXT NOT NULL, reason TEXT NOT NULL,
          observed_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS supervisor_inbox_observations_delivery
          ON supervisor_inbox_observations(delivery_id, observed_at);
        CREATE TABLE IF NOT EXISTS supervisor_inbox_processing (
          delivery_id TEXT PRIMARY KEY, message_id TEXT NOT NULL, thread_id TEXT NOT NULL,
          claimant_instance_id TEXT NOT NULL, shared_claim_until TEXT NOT NULL,
          local_status TEXT NOT NULL CHECK (local_status IN
            ('CLAIMED','RECEIVED','HANDLED','REPLIED','NEEDS_HUMAN','AMBIGUOUS')),
          handler_kind TEXT NOT NULL, proposed_outcome TEXT,
          reply_idempotency_key TEXT, reply_message_id TEXT, last_error TEXT,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS supervisor_inbox_canary_evidence (
          evidence_id TEXT PRIMARY KEY, contract_version TEXT NOT NULL,
          adapter_identity TEXT NOT NULL, principal_identity TEXT NOT NULL,
          instance_id TEXT NOT NULL, handler_identity TEXT NOT NULL,
          delivery_id TEXT NOT NULL, reply_message_id TEXT NOT NULL,
          recorded_at TEXT NOT NULL, revoked_at TEXT);
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

    def record_dry_run(self, host_id: str, thread_id: str, decision: str, reason: str, reply: str | None) -> None:
        # Keep the original table name so existing SQLite state needs no risky
        # migration; the current product terminology is "dry run."
        self.db.execute("INSERT INTO supervisor_shadow_decisions(host_id,thread_id,decision,reason,reply,recorded_at) VALUES (?,?,?,?,?,?)", (host_id, thread_id, decision, reason, reply, datetime.now(UTC).isoformat()))
        self.db.commit()

    def record_fable_turn(self, host_id: str, thread_id: str) -> None:
        self.db.execute(
            "INSERT INTO supervisor_fable_turns(host_id,thread_id,recorded_at) VALUES (?,?,?)",
            (host_id, thread_id, datetime.now(UTC).isoformat()),
        )
        self.db.commit()

    def record_inbox_observation(self, *, delivery_id: str, message_id: str, thread_id: str,
                                 kind: str, route: str, disposition: str, reason: str) -> None:
        self.db.execute(
            "INSERT INTO supervisor_inbox_observations(delivery_id,message_id,thread_id,kind,proposed_route,proposed_disposition,reason,observed_at) VALUES (?,?,?,?,?,?,?,?)",
            (delivery_id, message_id, thread_id, kind[:64], route[:64], disposition[:64], reason[:512], datetime.now(UTC).isoformat()),
        )
        self.db.commit()

    def record_inbox_poll(self) -> None:
        self.db.execute(
            "INSERT INTO supervisor_settings(key,value) VALUES ('inbox_last_successful_poll',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (datetime.now(UTC).isoformat(),),
        )
        self.db.commit()

    def begin_inbox_processing(self, claim, handler_kind: str) -> bool:
        return self.begin_inbox_processing_reference(
            delivery_id=claim.envelope.delivery_id,
            message_id=claim.envelope.message_id,
            thread_id=claim.envelope.thread_id,
            claimant_instance_id=claim.claimant_instance_id,
            shared_claim_until=claim.claim_until.isoformat(),
            handler_kind=handler_kind,
        )

    def begin_inbox_processing_reference(self, *, delivery_id: str, message_id: str,
                                         thread_id: str, claimant_instance_id: str,
                                         shared_claim_until: str, handler_kind: str) -> bool:
        now = datetime.now(UTC).isoformat()
        cursor = self.db.execute(
            "INSERT OR IGNORE INTO supervisor_inbox_processing(delivery_id,message_id,thread_id,claimant_instance_id,shared_claim_until,local_status,handler_kind,created_at,updated_at) VALUES (?,?,?,?,?,'CLAIMED',?,?,?)",
            (delivery_id, message_id, thread_id, claimant_instance_id,
             shared_claim_until, handler_kind[:64], now, now),
        )
        self.db.commit()
        return cursor.rowcount == 1

    def inbox_processing(self, delivery_id: str) -> dict[str, str | None] | None:
        row = self.db.execute(
            "SELECT delivery_id,message_id,thread_id,claimant_instance_id,shared_claim_until,local_status,handler_kind,proposed_outcome,reply_idempotency_key,reply_message_id,last_error,created_at,updated_at FROM supervisor_inbox_processing WHERE delivery_id=?",
            (delivery_id,),
        ).fetchone()
        if row is None:
            return None
        keys = ("delivery_id","message_id","thread_id","claimant_instance_id","shared_claim_until","local_status","handler_kind","proposed_outcome","reply_idempotency_key","reply_message_id","last_error","created_at","updated_at")
        return {key: None if value is None else str(value) for key, value in zip(keys, row, strict=True)}

    def update_inbox_processing(self, delivery_id: str, status: str, *,
                                proposed_outcome: str | None = None,
                                reply_idempotency_key: str | None = None,
                                reply_message_id: str | None = None,
                                last_error: str | None = None) -> None:
        allowed = {"CLAIMED", "RECEIVED", "HANDLED", "REPLIED", "NEEDS_HUMAN", "AMBIGUOUS"}
        if status not in allowed:
            raise ValueError("invalid inbox processing status")
        self.db.execute(
            "UPDATE supervisor_inbox_processing SET local_status=?, proposed_outcome=COALESCE(?,proposed_outcome), reply_idempotency_key=COALESCE(?,reply_idempotency_key), reply_message_id=COALESCE(?,reply_message_id), last_error=COALESCE(?,last_error), updated_at=? WHERE delivery_id=?",
            (status, proposed_outcome, reply_idempotency_key, reply_message_id,
             None if last_error is None else last_error[:512], datetime.now(UTC).isoformat(), delivery_id),
        )
        self.db.commit()

    def mark_inbox_human_review(self, instance_id: str, delivery_id: str, thread_id: str,
                                reason: str, title: str = "Shared inbox delivery") -> None:
        self.update_inbox_processing(delivery_id, "AMBIGUOUS" if "ambiguous" in reason.lower() else "NEEDS_HUMAN", last_error=reason)
        self.mark_human_review(f"inbox:{instance_id}", thread_id, reason[:512], title[:256])

    def record_inbox_canary(self, *, evidence_id: str, contract_version: str,
                            adapter_identity: str, principal_identity: str,
                            instance_id: str, handler_identity: str,
                            delivery_id: str, reply_message_id: str) -> None:
        values = (evidence_id, contract_version, adapter_identity, principal_identity,
                  instance_id, handler_identity, delivery_id, reply_message_id)
        if not all(value and value.strip() for value in values):
            raise ValueError("inbox canary evidence fields must be non-empty")
        self.db.execute(
            "INSERT INTO supervisor_inbox_canary_evidence(evidence_id,contract_version,adapter_identity,principal_identity,instance_id,handler_identity,delivery_id,reply_message_id,recorded_at,revoked_at) VALUES (?,?,?,?,?,?,?,?,?,NULL) ON CONFLICT(evidence_id) DO UPDATE SET contract_version=excluded.contract_version,adapter_identity=excluded.adapter_identity,principal_identity=excluded.principal_identity,instance_id=excluded.instance_id,handler_identity=excluded.handler_identity,delivery_id=excluded.delivery_id,reply_message_id=excluded.reply_message_id,recorded_at=excluded.recorded_at,revoked_at=NULL",
            (*values, datetime.now(UTC).isoformat()),
        )
        self.db.commit()

    def inbox_canary_matches(self, *, evidence_id: str, contract_version: str,
                             adapter_identity: str, principal_identity: str,
                             instance_id: str, handler_identity: str) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM supervisor_inbox_canary_evidence WHERE evidence_id=? AND contract_version=? AND adapter_identity=? AND principal_identity=? AND instance_id=? AND handler_identity=? AND revoked_at IS NULL",
            (evidence_id, contract_version, adapter_identity, principal_identity, instance_id, handler_identity),
        ).fetchone()
        return row is not None

    def inbox_status(self) -> dict[str, int | str | None]:
        last = self.db.execute("SELECT value FROM supervisor_settings WHERE key='inbox_last_successful_poll'").fetchone()
        return {
            "last_successful_poll": None if last is None else str(last[0]),
            "observation_count": int(self.db.execute("SELECT count(*) FROM supervisor_inbox_observations").fetchone()[0]),
            "active_processing_count": int(self.db.execute("SELECT count(*) FROM supervisor_inbox_processing WHERE local_status IN ('CLAIMED','RECEIVED')").fetchone()[0]),
            "ambiguous_count": int(self.db.execute("SELECT count(*) FROM supervisor_inbox_processing WHERE local_status='AMBIGUOUS'").fetchone()[0]),
            "inbox_human_review_count": int(self.db.execute("SELECT count(*) FROM human_review_tasks WHERE host_id LIKE 'inbox:%'").fetchone()[0]),
            "active_canary_evidence_count": int(self.db.execute("SELECT count(*) FROM supervisor_inbox_canary_evidence WHERE revoked_at IS NULL").fetchone()[0]),
        }

    def fable_turn_count(self, host_id: str, thread_id: str) -> int:
        return int(self.db.execute(
            "SELECT count(*) FROM supervisor_fable_turns WHERE host_id=? AND thread_id=?",
            (host_id, thread_id),
        ).fetchone()[0])

    def status(self) -> dict[str, int | str | None]:
        return {"state_path": str(self.path), "session_id": self.session_id(), "human_review_count": self.db.execute("SELECT count(*) FROM human_review_tasks").fetchone()[0], "dry_run_decision_count": self.db.execute("SELECT count(*) FROM supervisor_shadow_decisions").fetchone()[0], "pending_delivery_count": self.db.execute("SELECT count(*) FROM supervisor_delivery_claims WHERE status != 'CONFIRMED'").fetchone()[0]}
