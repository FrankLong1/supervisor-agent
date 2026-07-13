#!/usr/bin/env bash
# Start the supervisor from this checkout. Safe by default: `serve` writes only
# a liveness heartbeat until a reviewed Fable adapter and unread canary exist.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python="$root/.venv/bin/python"

if [[ ! -x "$python" ]]; then
  python="${PYTHON_BIN:-python3}"
fi

export PYTHONPATH="$root/src${PYTHONPATH:+:$PYTHONPATH}"
export XDG_STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"

if [[ $# -eq 0 ]]; then
  set -- serve --interval "${SUPERVISOR_INTERVAL_SECONDS:-30}"
fi

exec "$python" -m codex_supervisor.cli "$@"
