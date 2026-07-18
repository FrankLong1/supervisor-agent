"""Local lifecycle management for interactive Codex and Claude sessions.

This module is intentionally separate from the unread-task supervisor.  That
service makes fail-closed decisions about existing Codex tasks; this manager
starts and observes an operator-requested interactive CLI process.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Provider:
    name: str
    command: str
    status_args: tuple[str, ...]
    login_args: tuple[str, ...]


PROVIDERS = {
    "codex": Provider("codex", os.environ.get("SUPERVISOR_CODEX_COMMAND", "codex"), ("login", "status"), ("login",)),
    "claude": Provider("claude", os.environ.get("SUPERVISOR_CLAUDE_COMMAND", "claude"), ("auth", "status"), ("auth", "login")),
}


def now() -> str:
    return datetime.now(UTC).isoformat()


def default_manager_state_path() -> Path:
    root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return root / "codex-supervisor-manager"


def process_start_ticks(pid: int) -> str | None:
    """Return Linux /proc start ticks, protecting against PID reuse."""
    try:
        # The command field can contain spaces.  Field 22 starts after the
        # final ')' and is index 19 in the remaining fields.
        payload = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        fields = payload.rsplit(")", 1)[1].split()
        return fields[19]
    except (FileNotFoundError, IndexError, OSError):
        return None


def process_is_live(pid: int) -> bool:
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


@dataclass
class Session:
    id: str
    provider: str
    command: str
    workspace: str
    args: list[str]
    pid: int | None
    process_start_ticks: str | None
    phase: str
    created_at: str
    started_at: str | None = None
    ended_at: str | None = None
    reason: str | None = None
    stop_requested_at: str | None = None


class ManagerStore:
    def __init__(self, root: Path | None = None):
        self.root = root or default_manager_state_path()
        self.sessions = self.root / "sessions"
        self.workspaces = self.root / "workspaces"
        self.sessions.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.workspaces.mkdir(parents=True, exist_ok=True, mode=0o700)

    def session_path(self, session_id: str) -> Path:
        if not session_id.replace("-", "").isalnum():
            raise ValueError("invalid session ID")
        return self.sessions / session_id / "state.json"

    def log_path(self, session_id: str) -> Path:
        return self.session_path(session_id).with_name("lifecycle.log")

    def workspace_path(self, workspace: Path) -> Path:
        digest = hashlib.sha256(str(workspace).encode()).hexdigest()[:20]
        return self.workspaces / f"{digest}.json"

    @staticmethod
    def _write_json(path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(path)

    def save(self, session: Session) -> None:
        self._write_json(self.session_path(session.id), asdict(session))

    def load(self, session_id: str) -> Session | None:
        try:
            value = json.loads(self.session_path(session_id).read_text(encoding="utf-8"))
            return Session(**value)
        except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def record_event(self, session: Session, event: str) -> None:
        with self.log_path(session.id).open("a", encoding="utf-8") as log:
            log.write(f"{now()} {event}\n")

    def active_for_workspace(self, workspace: Path) -> Session | None:
        index = self.workspace_path(workspace)
        try:
            session_id = json.loads(index.read_text(encoding="utf-8"))["session_id"]
        except (FileNotFoundError, KeyError, json.JSONDecodeError):
            return None
        session = self.load(session_id)
        if not session or not session_matches_process(session):
            index.unlink(missing_ok=True)
            if session and session.phase in {"starting", "running", "stopping"}:
                session.phase, session.ended_at, session.reason = "stopped", now(), "stale process recovered"
                self.save(session)
            return None
        return session

    def set_active(self, workspace: Path, session: Session) -> None:
        self._write_json(self.workspace_path(workspace), {"session_id": session.id, "workspace": str(workspace)})

    def clear_active(self, workspace: Path, session_id: str) -> None:
        index = self.workspace_path(workspace)
        try:
            if json.loads(index.read_text(encoding="utf-8")).get("session_id") == session_id:
                index.unlink(missing_ok=True)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def all_active(self) -> Iterable[Session]:
        for state_path in self.sessions.glob("*/state.json"):
            session = self.load(state_path.parent.name)
            if session and session_matches_process(session):
                yield session


def session_matches_process(session: Session) -> bool:
    if session.pid is None or not process_is_live(session.pid):
        return False
    if session.process_start_ticks and process_start_ticks(session.pid) != session.process_start_ticks:
        return False
    try:
        command_line = Path(f"/proc/{session.pid}/cmdline").read_text(encoding="utf-8")
    except OSError:
        return True
    return not command_line or session.command in command_line


class Manager:
    def __init__(self, store: ManagerStore):
        self.store = store

    @staticmethod
    def provider(name: str) -> Provider:
        try:
            return PROVIDERS[name]
        except KeyError as error:
            raise ValueError(f"unknown provider: {name}") from error

    @staticmethod
    def ensure_authenticated(provider: Provider) -> None:
        if shutil.which(provider.command) is None:
            raise RuntimeError(f"{provider.name} CLI is not installed or not on PATH")
        status = subprocess.run([provider.command, *provider.status_args], check=False, capture_output=True, text=True)
        if status.returncode == 0:
            return
        print(f"{provider.name} is not logged in; starting its official login flow…", flush=True)
        login = subprocess.run([provider.command, *provider.login_args], check=False)
        if login.returncode != 0:
            raise RuntimeError(f"{provider.name} login was cancelled or failed")
        verified = subprocess.run([provider.command, *provider.status_args], check=False, capture_output=True, text=True)
        if verified.returncode != 0:
            raise RuntimeError(f"{provider.name} login did not complete successfully")

    def start(self, provider_name: str, workspace: Path, task: str | None = None) -> Session:
        workspace = workspace.resolve()
        if not workspace.is_dir():
            raise ValueError(f"workspace does not exist: {workspace}")
        if existing := self.store.active_for_workspace(workspace):
            raise RuntimeError(f"workspace is already managed by session {existing.id}")
        provider = self.provider(provider_name)
        self.ensure_authenticated(provider)
        session = Session(
            id=f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}",
            provider=provider.name,
            command=provider.command,
            workspace=str(workspace),
            args=[task] if task else [],
            pid=None,
            process_start_ticks=None,
            phase="starting",
            created_at=now(),
        )
        self.store.save(session)
        self.store.record_event(session, f"created {provider.name} session in {workspace}")
        try:
            process = subprocess.Popen(
                [provider.command, *session.args], cwd=workspace, stdin=None, stdout=None, stderr=None, start_new_session=True
            )
        except OSError as error:
            session.phase, session.ended_at, session.reason = "failed", now(), str(error)
            self.store.save(session)
            self.store.record_event(session, f"failed to launch: {error}")
            raise RuntimeError(f"could not launch {provider.name}: {error}") from error
        session.pid = process.pid
        session.process_start_ticks = process_start_ticks(process.pid)
        session.phase, session.started_at = "running", now()
        self.store.save(session)
        self.store.set_active(workspace, session)
        self.store.record_event(session, f"started pid={process.pid}")
        return session

    def complete(self, session: Session, return_code: int) -> None:
        session.phase = "completed" if return_code == 0 else "failed"
        session.ended_at, session.reason = now(), f"exit code {return_code}"
        self.store.save(session)
        self.store.clear_active(Path(session.workspace), session.id)
        self.store.record_event(session, session.reason)

    def stop(self, session: Session, force: bool = False) -> str:
        if session.pid is None or not process_is_live(session.pid):
            return f"session {session.id} is already stopped"
        if not session_matches_process(session):
            raise RuntimeError(f"refusing to stop PID {session.pid}: it no longer matches the managed session")
        target = -session.pid
        try:
            os.killpg(session.pid, signal.SIGKILL if force else signal.SIGTERM)
        except ProcessLookupError:
            return f"session {session.id} is already stopped"
        except OSError:
            os.kill(session.pid, signal.SIGKILL if force else signal.SIGTERM)
        session.phase = "force-stopped" if force else "stopping"
        session.stop_requested_at = now()
        self.store.save(session)
        self.store.record_event(session, "force stop requested" if force else "graceful stop requested")
        return f"{'force stop' if force else 'stop'} requested for session {session.id}"

    def wait_for_exit(self, session: Session) -> int:
        while session_matches_process(session):
            time.sleep(0.2)
        # A foreground child is normally reaped by the CLI; this fallback is
        # for the small interval between exit and state refresh.
        return 0
