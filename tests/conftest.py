"""Shared test helpers.

One definition of the pending-marker path, so the tests cannot drift away from
the hook again. Six hand-written copies of `/tmp/...` are what broke the suite:
the hooks moved to `tempfile.gettempdir()` in 732e0c3 ("Make evolving-lite
cross-platform") and the tests kept looking in `/tmp`. That passes on Linux,
where gettempdir() happens to BE /tmp, and fails on macOS and Windows.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def pending_marker(session_id: str) -> Path:
    """Where delegation-enforcer.py writes its per-session pending marker.

    Must stay byte-identical in behaviour to `write_pending_marker()` in
    hooks/scripts/delegation-enforcer.py. If that resolution changes, change it
    here in the same commit.
    """
    tmp = os.environ.get("EVOLVING_TMP") or tempfile.gettempdir()
    return Path(tmp) / f"delegation-pending-{session_id}.json"
