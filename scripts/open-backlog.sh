#!/usr/bin/env bash
# Open the read-only Fable human-review backlog in the local browser.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "$root/scripts/startup.sh" console --open-browser "$@"
