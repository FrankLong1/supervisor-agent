#!/usr/bin/env bash
set -euo pipefail

# Install both project console scripts in the current user's tool directory.
# This is safe to run repeatedly in a remote-image provisioning step.
repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# --force replaces a prior supervisor executable from an older image build.
uv tool install --reinstall --force --from "$repo_dir" codex-unread-task-supervisor

zshrc="${HOME}/.zshrc"
start="# >>> codex-supervisor-manager >>>"
end="# <<< codex-supervisor-manager <<<"
block="$start
export PATH=\"\$HOME/.local/bin:\$PATH\"
alias supervisor=\"\$HOME/.local/bin/supervisor\"
$end"

if [[ -f "$zshrc" ]] && grep -Fq "$start" "$zshrc"; then
  python3 - "$zshrc" "$start" "$end" "$block" <<'PY'
from pathlib import Path
import sys
path, start, end, block = map(str, sys.argv[1:])
text = Path(path).read_text(encoding="utf-8")
before, remainder = text.split(start, 1)
_, after = remainder.split(end, 1)
Path(path).write_text(before + block + after, encoding="utf-8")
PY
else
  printf '\n%s\n' "$block" >> "$zshrc"
fi

printf 'Installed supervisor and codex-unread-supervisor. Open a new Zsh terminal or run: source %s\n' "$zshrc"
