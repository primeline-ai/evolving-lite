#!/usr/bin/env python3
"""
Evolving Lite - Shared utilities for all hook scripts.

Provides:
- PLUGIN_ROOT: Authoritative path to plugin directory
- Sentinel output (every hook must call write_sentinel)
- Session counter (persistent across sessions)
- Tier activation check
- Safe JSON read/write with atomic operations
- Experience file creation
"""

import json
import os
import sys
import time
import tempfile
from pathlib import Path
from datetime import datetime


# ============================================================
# PATH RESOLUTION
# ============================================================

def _resolve_plugin_root() -> Path:
    """Resolve plugin root. CLAUDE_PLUGIN_ROOT from env is authoritative."""
    env_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env_root:
        return Path(env_root)
    # Fallback: common.py is at hooks/scripts/lib/common.py -> 3 levels up
    return Path(__file__).resolve().parents[3]


PLUGIN_ROOT = _resolve_plugin_root()
MEMORY_DIR = PLUGIN_ROOT / "_memory"
EXPERIENCES_DIR = MEMORY_DIR / "experiences"
PREWARMED_DIR = EXPERIENCES_DIR / "_prewarmed"
ANALYTICS_DIR = MEMORY_DIR / "analytics"
SESSIONS_DIR = MEMORY_DIR / "sessions"
PROJECTS_DIR = MEMORY_DIR / "projects"
PLANS_DIR = MEMORY_DIR / "plans"
GRAPH_CACHE_DIR = PLUGIN_ROOT / "_graph" / "cache"
HOOKS_DIR = PLUGIN_ROOT / "hooks"


# ============================================================
# SENTINEL SYSTEM
# ============================================================

def write_sentinel(hook_name: str, status: str = "ok") -> None:
    """Write sentinel marker proving hook executed successfully."""
    session_id = os.environ.get("CLAUDE_SESSION_ID", str(os.getppid()))
    sentinel_file = Path(f"/tmp/evolving-lite-sentinel-{hook_name}-{session_id}.json")
    try:
        sentinel_file.write_text(json.dumps({
            "hook": hook_name,
            "ts": time.time(),
            "status": status,
            "session": session_id
        }))
    except OSError:
        pass  # Sentinel failure is non-fatal


# ============================================================
# SESSION COUNTER
# ============================================================

def get_session_count() -> int:
    """Read persistent session counter."""
    counter_file = MEMORY_DIR / ".session-count"
    try:
        return int(counter_file.read_text().strip())
    except (FileNotFoundError, ValueError, OSError):
        return 0


def increment_session_count() -> int:
    """Increment session counter with per-session guard against double-increment."""
    session_id = os.environ.get("CLAUDE_SESSION_ID", str(os.getppid()))
    flag_file = Path(f"/tmp/evolving-lite-session-counted-{session_id}")

    # Guard: only increment once per session
    if flag_file.exists():
        return get_session_count()

    count = get_session_count() + 1
    counter_file = MEMORY_DIR / ".session-count"
    safe_write_text(counter_file, str(count))

    # Set flag so we don't double-increment
    try:
        flag_file.write_text(str(count))
    except OSError:
        pass

    return count


# ============================================================
# TIER ACTIVATION
# ============================================================

TIER_THRESHOLDS = {1: 0, 2: 3, 3: 10}


def is_tier_active(tier: int) -> bool:
    """Check if a tier is active based on session count."""
    threshold = TIER_THRESHOLDS.get(tier, 999)
    return get_session_count() >= threshold


def get_current_tier() -> int:
    """Get the highest active tier."""
    count = get_session_count()
    if count >= 10:
        return 3
    if count >= 3:
        return 2
    return 1


# ============================================================
# SAFE FILE OPERATIONS
# ============================================================

def safe_write_json(filepath: Path, data: dict) -> bool:
    """Atomic JSON write using temp+rename pattern."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=filepath.parent,
            suffix=".tmp",
            prefix=filepath.stem + "_"
        )
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, filepath)
        return True
    except OSError:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except (OSError, UnboundLocalError):
            pass
        return False


def safe_read_json(filepath: Path, default: dict = None) -> dict:
    """Safe JSON read with fallback to default."""
    if default is None:
        default = {}
    try:
        return json.loads(filepath.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def safe_write_text(filepath: Path, content: str) -> bool:
    """Atomic text write using temp+rename pattern."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=filepath.parent,
            suffix=".tmp",
            prefix=filepath.stem + "_"
        )
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp_path, filepath)
        return True
    except OSError:
        try:
            os.unlink(tmp_path)
        except (OSError, UnboundLocalError):
            pass
        return False


# ============================================================
# EXPERIENCE CREATION
# ============================================================

def create_experience(
    summary: str,
    exp_type: str = "solution",
    tags: list = None,
    problem: str = "",
    solution: str = "",
    root_cause: str = "",
    confidence: float = 0.7,
    source: str = "auto"
) -> Path | None:
    """Create a new experience file in _memory/experiences/."""
    if tags is None:
        tags = []

    # Microseconds avoid collisions when several experiences are created in one hook run.
    ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    exp_id = f"exp-{ts}"
    exp_file = EXPERIENCES_DIR / f"{exp_id}.json"

    data = {
        "id": exp_id,
        "type": exp_type,
        "summary": summary,
        "problem": problem,
        "solution": solution,
        "root_cause": root_cause,
        "tags": tags,
        "confidence": confidence,
        "effective_relevance": int(confidence * 100),
        "access_count": 0,
        "created": datetime.now().isoformat(),
        "source": source,
        "claude_code_version": os.environ.get("CLAUDE_CODE_VERSION", "unknown")
    }

    if safe_write_json(exp_file, data):
        # Update evolution log
        log_evolution_event("experience_created", f"New {exp_type}: {summary[:80]}", source=source)
        return exp_file
    return None


# ============================================================
# EVOLUTION LOG
# ============================================================

def log_evolution_event(event_type: str, summary: str, source: str = "system") -> None:
    """Append event to evolution log."""
    log_file = ANALYTICS_DIR / "evolution-log.jsonl"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now().isoformat(),
        "type": event_type,
        "summary": summary,
        "source": source,
        "session": get_session_count()
    }
    try:
        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


# ============================================================
# MEMORY INITIALIZATION
# ============================================================

def ensure_memory_initialized() -> bool:
    """Ensure _memory/ structure exists with index files."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    EXPERIENCES_DIR.mkdir(parents=True, exist_ok=True)
    ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    PLANS_DIR.mkdir(parents=True, exist_ok=True)

    # Create index.json if missing
    index_file = MEMORY_DIR / "index.json"
    if not index_file.exists():
        safe_write_json(index_file, {
            "active_project": None,
            "last_session": None,
            "created": datetime.now().isoformat(),
            "version": "1.0.0"
        })
        return True  # Fresh init
    return False  # Already existed


# ============================================================
# HOOK STDIN PARSING
# ============================================================

def read_hook_input() -> dict:
    """Read and parse JSON input from Claude Code hook system."""
    try:
        if not sys.stdin.isatty():
            raw = sys.stdin.read()
            if raw.strip():
                return json.loads(raw)
    except (json.JSONDecodeError, OSError):
        pass
    return {}
