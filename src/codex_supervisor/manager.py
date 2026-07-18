"""Foreground lifecycle controller for interactive Codex and Claude sessions."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable


SESSION_SCHEMA_VERSION = 2
ACTIVE_PHASES = {"starting", "running", "stopping"}


@dataclass(frozen=True)
class Provider:
    name: str
    command: str
    status_args: tuple[str, ...]
    login_args: tuple[str, ...]
    launch_args: tuple[str, ...] = ()


PROVIDERS = {
    "codex": Provider(
        "codex",
        os.environ.get("SUPERVISOR_CODEX_COMMAND", "codex"),
        ("login", "status"),
        ("login",),
        ("--model", "gpt-5.6-sol", "--config", 'model_reasoning_effort="ultra"'),
    ),
    "claude": Provider(
        "claude",
        os.environ.get("SUPERVISOR_CLAUDE_COMMAND", "claude"),
        ("auth", "status"),
        ("auth", "login"),
    ),
}


def now() -> str:
    return datetime.now(UTC).isoformat()


def default_manager_state_path() -> Path:
    root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return root / "codex-supervisor-manager"


def process_start_ticks(pid: int) -> str | None:
    """Return Linux /proc start ticks, protecting against PID reuse."""
    try:
        payload = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        return payload.rsplit(")", 1)[1].split()[19]
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


def process_matches(
    pid: int | None, start_ticks: str | None, command: str | None = None
) -> bool:
    if pid is None or not process_is_live(pid):
        return False
    if start_ticks and process_start_ticks(pid) != start_ticks:
        return False
    if not command:
        return True
    try:
        command_line = Path(f"/proc/{pid}/cmdline").read_text(encoding="utf-8")
    except OSError:
        return True
    return not command_line or command in command_line


@dataclass
class Session:
    schema_version: int
    id: str
    provider: str
    command: str
    workspace: str
    args: list[str]
    controller_pid: int | None
    controller_start_ticks: str | None
    provider_pid: int | None
    provider_start_ticks: str | None
    phase: str
    created_at: str
    started_at: str | None = None
    ended_at: str | None = None
    heartbeat_at: str | None = None
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
        temporary.write_text(
            json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        temporary.chmod(0o600)
        temporary.replace(path)

    def save(self, session: Session) -> None:
        self._write_json(self.session_path(session.id), asdict(session))

    def load(self, session_id: str) -> Session | None:
        try:
            value = json.loads(
                self.session_path(session_id).read_text(encoding="utf-8")
            )
        except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError):
            return None
        if not isinstance(value, dict):
            return None
        # Read the launcher-only schema so existing state fails visibly rather
        # than disappearing after an upgrade.
        if "provider_pid" not in value and "pid" in value:
            value["provider_pid"] = value.pop("pid")
            value["provider_start_ticks"] = value.pop("process_start_ticks", None)
            value["controller_pid"] = None
            value["controller_start_ticks"] = None
            value["heartbeat_at"] = None
            value["schema_version"] = 1
        allowed = {item.name for item in fields(Session)}
        try:
            return Session(
                **{key: item for key, item in value.items() if key in allowed}
            )
        except (TypeError, ValueError):
            return None

    def record_event(self, session: Session, event: str) -> None:
        path = self.log_path(session.id)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.open("a", encoding="utf-8") as log:
            log.write(f"{now()} {event}\n")
        path.chmod(0o600)

    def active_for_workspace(self, workspace: Path) -> Session | None:
        try:
            session_id = json.loads(
                self.workspace_path(workspace).read_text(encoding="utf-8")
            )["session_id"]
        except (FileNotFoundError, OSError, KeyError, TypeError, json.JSONDecodeError):
            return None
        return self.load(str(session_id))

    def set_active(self, workspace: Path, session: Session) -> None:
        self._write_json(
            self.workspace_path(workspace),
            {"session_id": session.id, "workspace": str(workspace)},
        )

    def clear_active(self, workspace: Path, session_id: str) -> None:
        index = self.workspace_path(workspace)
        try:
            if (
                json.loads(index.read_text(encoding="utf-8")).get("session_id")
                == session_id
            ):
                index.unlink(missing_ok=True)
        except (FileNotFoundError, OSError, TypeError, json.JSONDecodeError):
            pass

    def all_active(self) -> Iterable[Session]:
        seen: set[str] = set()
        for index in self.workspaces.glob("*.json"):
            try:
                session_id = str(
                    json.loads(index.read_text(encoding="utf-8"))["session_id"]
                )
            except (OSError, KeyError, TypeError, json.JSONDecodeError):
                continue
            if session_id in seen:
                continue
            seen.add(session_id)
            if session := self.load(session_id):
                yield session


def session_matches_controller(session: Session) -> bool:
    return process_matches(session.controller_pid, session.controller_start_ticks)


def session_matches_provider(session: Session) -> bool:
    return process_matches(
        session.provider_pid, session.provider_start_ticks, session.command
    )


# Compatibility name used by existing callers and tests.
def session_matches_process(session: Session) -> bool:
    return session_matches_provider(session)


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
        status = subprocess.run(
            [provider.command, *provider.status_args],
            check=False,
            capture_output=True,
            text=True,
        )
        if status.returncode == 0:
            return
        print(
            f"{provider.name} is not logged in; starting its official login flow…",
            flush=True,
        )
        login = subprocess.run([provider.command, *provider.login_args], check=False)
        if login.returncode != 0:
            raise RuntimeError(f"{provider.name} login was cancelled or failed")
        verified = subprocess.run(
            [provider.command, *provider.status_args],
            check=False,
            capture_output=True,
            text=True,
        )
        if verified.returncode != 0:
            raise RuntimeError(f"{provider.name} login did not complete successfully")

    def refresh(self, session: Session) -> Session:
        if session.phase not in ACTIVE_PHASES:
            return session
        reason: str | None = None
        if not session_matches_controller(session):
            reason = "controller process is absent or its identity changed"
        elif session.phase == "running" and not session_matches_provider(session):
            reason = "provider process is absent or its identity changed"
        if reason:
            session.phase = "attention"
            session.reason = reason
            self.store.save(session)
            self.store.record_event(session, reason)
        return session

    def start(
        self, provider_name: str, workspace: Path, task: str | None = None
    ) -> Session:
        workspace = workspace.resolve()
        if not workspace.is_dir():
            raise ValueError(f"workspace does not exist: {workspace}")
        if existing := self.store.active_for_workspace(workspace):
            existing = self.refresh(existing)
            if session_matches_controller(existing) or session_matches_provider(
                existing
            ):
                raise RuntimeError(
                    f"workspace is already managed by session {existing.id}"
                )
            self.store.clear_active(workspace, existing.id)
        provider = self.provider(provider_name)
        self.ensure_authenticated(provider)
        controller_pid = os.getpid()
        session = Session(
            schema_version=SESSION_SCHEMA_VERSION,
            id=f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}",
            provider=provider.name,
            command=provider.command,
            workspace=str(workspace),
            args=[*provider.launch_args, *([task] if task else [])],
            controller_pid=controller_pid,
            controller_start_ticks=process_start_ticks(controller_pid),
            provider_pid=None,
            provider_start_ticks=None,
            phase="starting",
            created_at=now(),
            heartbeat_at=now(),
        )
        self.store.save(session)
        self.store.set_active(workspace, session)
        self.store.record_event(
            session, f"created {provider.name} session in {workspace}"
        )
        try:
            process = subprocess.Popen(
                [provider.command, *session.args],
                cwd=workspace,
                stdin=None,
                stdout=None,
                stderr=None,
                start_new_session=True,
            )
        except OSError as error:
            session.phase, session.ended_at, session.reason = (
                "failed",
                now(),
                str(error),
            )
            self.store.save(session)
            self.store.clear_active(workspace, session.id)
            self.store.record_event(session, f"failed to launch: {error}")
            raise RuntimeError(f"could not launch {provider.name}: {error}") from error
        session.provider_pid = process.pid
        session.provider_start_ticks = process_start_ticks(process.pid)
        session.phase, session.started_at, session.heartbeat_at = (
            "running",
            now(),
            now(),
        )
        self.store.save(session)
        self.store.record_event(
            session,
            f"started provider pid={process.pid}; controller pid={controller_pid}",
        )
        return session

    def heartbeat(self, session: Session) -> None:
        session.heartbeat_at = now()
        self.store.save(session)

    def complete(self, session: Session, return_code: int) -> None:
        session.phase = "completed" if return_code == 0 else "failed"
        session.ended_at, session.heartbeat_at, session.reason = (
            now(),
            now(),
            f"provider exit code {return_code}",
        )
        self.store.save(session)
        self.store.clear_active(Path(session.workspace), session.id)
        self.store.record_event(session, session.reason)

    def complete_stop(
        self, session: Session, return_code: int | None, forced: bool
    ) -> None:
        session.phase = "force-stopped" if forced else "stopped"
        session.ended_at = session.heartbeat_at = now()
        session.reason = "forced stop" if forced else "operator stop"
        if return_code is not None:
            session.reason += f"; provider exit code {return_code}"
        self.store.save(session)
        self.store.clear_active(Path(session.workspace), session.id)
        self.store.record_event(session, session.reason)

    def terminate_provider(self, session: Session, force: bool = False) -> bool:
        if not session_matches_provider(session):
            return False
        provider_pid = session.provider_pid
        assert provider_pid is not None
        try:
            os.killpg(provider_pid, signal.SIGKILL if force else signal.SIGTERM)
        except ProcessLookupError:
            return False
        except OSError:
            os.kill(provider_pid, signal.SIGKILL if force else signal.SIGTERM)
        self.store.record_event(
            session,
            "provider force stop requested"
            if force
            else "provider graceful stop requested",
        )
        return True

    def stop(self, session: Session, force: bool = False) -> str:
        controller_live = session_matches_controller(session)
        provider_live = session_matches_provider(session)
        if not controller_live and not provider_live:
            return f"session {session.id} is already stopped"
        session.stop_requested_at = now()
        if force:
            self.terminate_provider(session, force=True)
            if controller_live and session.controller_pid != os.getpid():
                assert session.controller_pid is not None
                os.kill(session.controller_pid, signal.SIGKILL)
            self.complete_stop(session, None, forced=True)
            return f"force stopped session {session.id}"
        session.phase = "stopping"
        self.store.save(session)
        self.store.record_event(session, "graceful stop requested")
        if controller_live and session.controller_pid != os.getpid():
            assert session.controller_pid is not None
            os.kill(session.controller_pid, signal.SIGTERM)
        else:
            self.terminate_provider(session)
        return f"stop requested for session {session.id}"


class ManagedRuntime:
    """The small foreground loop that owns heartbeat and provider shutdown."""

    def __init__(
        self,
        manager: Manager,
        *,
        heartbeat_interval: float = 1.0,
        stop_grace: float = 5.0,
    ):
        if heartbeat_interval <= 0 or stop_grace < 0:
            raise ValueError(
                "heartbeat interval must be positive and stop grace cannot be negative"
            )
        self.manager = manager
        self.heartbeat_interval = heartbeat_interval
        self.stop_grace = stop_grace
        self._stop_requested = threading.Event()

    def request_stop(self) -> None:
        self._stop_requested.set()

    @staticmethod
    def _poll_child(session: Session) -> int | None:
        if session.provider_pid is None:
            return 0
        try:
            pid, status = os.waitpid(session.provider_pid, os.WNOHANG)
        except ChildProcessError:
            return None if session_matches_provider(session) else 0
        if pid == 0:
            return None
        return os.waitstatus_to_exitcode(status)

    def _stop_provider(self, session: Session) -> int | None:
        self.manager.terminate_provider(session)
        deadline = time.monotonic() + self.stop_grace
        while (
            return_code := self._poll_child(session)
        ) is None and time.monotonic() < deadline:
            self._stop_requested.wait(0.05)
        if return_code is not None:
            return return_code
        self.manager.terminate_provider(session, force=True)
        deadline = time.monotonic() + 1.0
        while (
            return_code := self._poll_child(session)
        ) is None and time.monotonic() < deadline:
            time.sleep(0.05)
        return return_code

    def run(self, session: Session) -> int:
        next_heartbeat = 0.0
        while not self._stop_requested.is_set():
            if (return_code := self._poll_child(session)) is not None:
                self.manager.complete(session, return_code)
                return return_code
            current = time.monotonic()
            if current >= next_heartbeat:
                self.manager.heartbeat(session)
                next_heartbeat = current + self.heartbeat_interval
            self._stop_requested.wait(min(0.1, max(0.0, next_heartbeat - current)))
        return_code = self._stop_provider(session)
        self.manager.complete_stop(session, return_code, forced=False)
        return 130
