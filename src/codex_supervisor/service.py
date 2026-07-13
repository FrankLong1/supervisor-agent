from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path


SERVICE_NAME = "codex-unread-supervisor"


def default_unit_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def _systemctl(*arguments: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(["systemctl", "--user", *arguments], check=False, capture_output=True, text=True)
    except FileNotFoundError:
        return subprocess.CompletedProcess(["systemctl", "--user", *arguments], 127, "", "systemctl is not available")


def render_units(program: Path, state_path: Path, interval: float) -> dict[str, str]:
    command = " ".join(shlex.quote(part) for part in (str(program), "serve", "--state-path", str(state_path), "--interval", str(interval)))
    doctor = " ".join(shlex.quote(part) for part in (str(program), "watchdog", "--state-path", str(state_path), "--interval", str(interval), "--strict"))
    return {
        f"{SERVICE_NAME}.service": f"""[Unit]
Description=Fail-closed Codex unread-task supervisor
After=graphical-session.target
StartLimitIntervalSec=300
StartLimitBurst=3

[Service]
Type=simple
ExecStart={command}
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
""",
        f"{SERVICE_NAME}-watchdog.service": f"""[Unit]
Description=Health check for {SERVICE_NAME}

[Service]
Type=oneshot
ExecStart={doctor}
""",
        f"{SERVICE_NAME}-watchdog.timer": f"""[Unit]
Description=Run {SERVICE_NAME} health check

[Timer]
OnBootSec=2min
OnUnitActiveSec=2min
Unit={SERVICE_NAME}-watchdog.service

[Install]
WantedBy=timers.target
""",
    }


def install_units(unit_dir: Path, program: Path, state_path: Path, interval: float, dry_run: bool) -> dict[str, str]:
    rendered = render_units(program, state_path, interval)
    if not dry_run:
        unit_dir.mkdir(parents=True, exist_ok=True)
        for name, content in rendered.items():
            (unit_dir / name).write_text(content, encoding="utf-8")
        _systemctl("daemon-reload")
    return rendered


def service_status() -> tuple[int, str]:
    completed = _systemctl("status", SERVICE_NAME, f"{SERVICE_NAME}-watchdog.timer", "--no-pager")
    return completed.returncode, completed.stdout + completed.stderr


def uninstall_units(unit_dir: Path) -> list[Path]:
    _systemctl("disable", "--now", SERVICE_NAME, f"{SERVICE_NAME}-watchdog.timer")
    removed: list[Path] = []
    for name in render_units(Path(sys.argv[0]).resolve(), Path("/unused"), 60).keys():
        path = unit_dir / name
        if path.exists():
            path.unlink()
            removed.append(path)
    _systemctl("daemon-reload")
    return removed
