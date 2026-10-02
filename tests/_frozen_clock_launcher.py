"""Run a hook script in THIS process with the experience clock frozen.

Usage: python _frozen_clock_launcher.py <script.py> <iso-timestamp>

The collision tests need several writer PROCESSES to read the same second.
Launched independently, they straddle a second boundary on slow runners, and a
patch in the test process cannot reach them. So each child patches its own
copy of `common.datetime` before the hook runs. The claim logic, the stdin
payload and the filesystem writes stay real; only the clock is pinned.
"""
from __future__ import annotations

import runpy
import sys
from datetime import datetime
from pathlib import Path

script = Path(sys.argv[1])
fixed = datetime.fromisoformat(sys.argv[2])

sys.path.insert(0, str(script.parent / "lib"))
import common  # noqa: E402  (the hook's own lib, resolved like the hook does)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return fixed if tz is None else fixed.replace(tzinfo=tz)


common.datetime = _FrozenDatetime

sys.argv = [str(script)]
runpy.run_path(str(script), run_name="__main__")
