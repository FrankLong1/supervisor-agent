#!/usr/bin/env python3
"""Small provider CLI used by manual and subprocess integration checks."""

from __future__ import annotations

import sys
import time


if sys.argv[1:] in (
    ["login", "status"],
    ["login"],
    ["auth", "status"],
    ["auth", "login"],
):
    raise SystemExit(0)

while True:
    time.sleep(1)
