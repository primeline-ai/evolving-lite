#!/usr/bin/env python3
"""
Runner for importing Codex memories into Evolving Lite's experience store.
Prints only the imported count so shell hooks can consume it cheaply.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))
from codex_bridge import sync_codex_memory


def main() -> int:
    stats = sync_codex_memory(force=False, cooldown_seconds=0)
    print(stats.imported_count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
