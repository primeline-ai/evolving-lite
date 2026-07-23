#!/usr/bin/env python3
"""Delegation Outcome Tracker.

Bridges UserPromptSubmit -> Stop for delegation outcome tracking (the
"backward signal": did the recommended delegation actually happen?).

Architecture:
  - delegation-enforcer.py (UserPromptSubmit) writes the pending marker at
    /tmp/delegation-pending-{session_id}.json when score >= threshold.
  - This hook on PreToolUse (tool Task/Agent) marks the marker resolved=True.
  - This hook on Stop reads the marker and writes the outcome to
    _memory/analytics/delegation-gaps.jsonl, then unlinks the marker.
  - When the AutoEvolve flag (mutation_rules.v2_tuning_enabled) is on, it
    ALSO appends a cognitive-fitness.jsonl row (the fitness loop's input).

Safety:
  - Fail-open: any exception exits 0 (never blocks tools).
  - Hard timeout via SIGALRM (4s) on top of the CC-level timeout.
  - Idempotent: re-running on the same marker is safe.
"""

from __future__ import annotations

try:
    import fcntl  # POSIX file locking
except ImportError:  # Windows: degrade to best-effort lock-free (no fcntl)
    class _NoFcntl:
        LOCK_EX = LOCK_UN = LOCK_NB = LOCK_SH = 0
        @staticmethod
        def flock(*_a, **_k):
            return None
    fcntl = _NoFcntl()
import json
import os
import signal
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

SCRIPT_DIR = Path(__file__).parent.resolve()


def _plugin_root() -> Path:
    env = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env:
        return Path(env)
    return SCRIPT_DIR.parent.parent  # hooks/scripts/ -> hooks/ -> plugin root


PLUGIN_ROOT = _plugin_root()
GAPS_FILE = PLUGIN_ROOT / "_memory" / "analytics" / "delegation-gaps.jsonl"
FITNESS_FILE = PLUGIN_ROOT / "_memory" / "analytics" / "cognitive-fitness.jsonl"
DELEGATION_CONFIG = PLUGIN_ROOT / "_graph" / "cache" / "delegation-config.json"
INVOCATION_LEDGER = (
    PLUGIN_ROOT / "_ledgers" / "delegation-outcome-tracker-invocations.jsonl"
)


def _evolving_tmp() -> str:
    # EVOLVING_TMP unifies the temp namespace across bash+Python hooks (see
    # common.py evolving_tmp_dir); default stays the OS tempdir. The writer
    # (delegation-enforcer) resolves this identically.
    return os.environ.get("EVOLVING_TMP") or tempfile.gettempdir()


def _pending_marker_path(session_id: str) -> Path:
    return Path(_evolving_tmp()) / f"delegation-pending-{session_id}.json"


def _resolve_session_id(input_data: Optional[Dict]) -> str:
    sid = None
    if input_data:
        sid = input_data.get("session_id") or input_data.get("session")
    if not sid:
        sid = os.environ.get("CLAUDE_SESSION_ID")
    if not sid:
        sid = f"pid-{os.getppid()}"
    return sid or "unknown"


# =========================================================================
# Marker IO
# =========================================================================

def _read_marker(session_id: str) -> Optional[Dict]:
    path = _pending_marker_path(session_id)
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_marker(session_id: str, marker: Dict) -> None:
    path = _pending_marker_path(session_id)
    tmp = path.with_suffix(".json.tmp")
    try:
        # Self-heal a planted symlink at the tmp staging path; O_NOFOLLOW
        # refuses symlinks at the write target (predictable-/tmp-path TOCTOU
        # hardening - a planted symlink degrades to a single miss, fail-open).
        if tmp.is_symlink():
            tmp.unlink()
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            f = os.fdopen(fd, "w", encoding="utf-8")
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        with f:
            json.dump(marker, f)
        os.replace(tmp, path)
    except Exception:
        pass


def _unlink_marker(session_id: str) -> None:
    try:
        _pending_marker_path(session_id).unlink(missing_ok=True)
    except Exception:
        pass


# =========================================================================
# Quality-signal bridge (two-signal costimulation)
#
# A delegated task auto-scores "positive" merely because the delegation
# happened - regardless of output quality. The subagent-router (SubagentStop)
# can bridge a quality verdict via /tmp/quality-signal-{session_id}.json;
# this hook consumes it at Stop so a low-quality delegation no longer
# rubber-stamps positive. Flag DELEGATION_QUALITY_SIGNAL in {off, observe,
# on}, default "observe" (shadow-log the would-be downgrade, change nothing
# live). Fail-open everywhere: absent/ok/garbage verdict -> legacy outcome.
# =========================================================================

def _quality_signal_path(session_id: str) -> Path:
    return Path(_evolving_tmp()) / f"quality-signal-{session_id}.json"


def _quality_signal_mode() -> str:
    val = (os.environ.get("DELEGATION_QUALITY_SIGNAL") or "observe").strip().lower()
    return val if val in ("off", "observe", "on") else "observe"


def _read_quality_verdict(session_id: str) -> Optional[Dict]:
    path = _quality_signal_path(session_id)
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _unlink_quality_verdict(session_id: str) -> None:
    try:
        _quality_signal_path(session_id).unlink(missing_ok=True)
    except Exception:
        pass


def _apply_quality_signal(base_outcome: str, was_delegated: bool, verdict):
    """Returns (final_outcome, shadow). Only ever touches DELEGATED rows
    (whose base outcome is neutral since the honest-derivation fix - it was
    the auto-positive branch before). Fail-open: off-mode or absent/ok/
    non-dict verdict -> unchanged."""
    try:
        mode = _quality_signal_mode()
        if mode == "off":
            return base_outcome, None
        if not was_delegated:
            return base_outcome, None
        if not isinstance(verdict, dict) or verdict.get("quality") != "low":
            return base_outcome, None
        if mode == "on":
            return "negative", None
        return base_outcome, {
            "would_outcome": "negative",
            "reason": verdict.get("reason"),
            "task_type": verdict.get("task_type"),
        }
    except Exception:
        return base_outcome, None


# =========================================================================
# Ledgers
# =========================================================================

def _log_invocation(session_id: str, event: str, action: str, reason: str,
                    was_delegated: Optional[bool] = None,
                    duration_ms: Optional[float] = None,
                    error: Optional[str] = None) -> None:
    """One row per handler entry; disambiguates gate-skips from writes.
    Fail-open; never alters the primary write path."""
    try:
        INVOCATION_LEDGER.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session": session_id,
            "event": event,
            "action": action,
            "reason": reason,
            "was_delegated": was_delegated,
            "duration_ms": duration_ms,
            "error": error,
        }
        with open(INVOCATION_LEDGER, "a", encoding="utf-8") as f:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            except OSError:
                pass
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def _append_gap_entry(session_id: str, marker: Dict, was_delegated: bool) -> bool:
    """Append outcome to delegation-gaps.jsonl. Returns True on success."""
    try:
        GAPS_FILE.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task_hint": marker.get("task_type", "unknown"),
            "was_delegated": bool(was_delegated),
            "task_description": (marker.get("task_description") or "")[:100],
            "session": session_id,
            "score": marker.get("score"),
            "threshold": marker.get("effective_threshold"),
            "emit_ts": marker.get("emit_ts"),
            "source": "delegation-outcome-tracker.py",
        }
        with open(GAPS_FILE, "a", encoding="utf-8") as f:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            except OSError:
                pass
            f.write(json.dumps(entry) + "\n")
        return True
    except Exception:
        return False


# =========================================================================
# Fitness bridge (input to the cognitive-fitness loop)
# =========================================================================

def _v2_tuning_enabled() -> bool:
    """Read the AutoEvolve kill-switch. False on any read error."""
    try:
        with open(DELEGATION_CONFIG, encoding="utf-8") as f:
            cfg = json.load(f)
        return bool(cfg.get("mutation_rules", {}).get("v2_tuning_enabled", False))
    except Exception:
        return False


def _parse_ts_utc(ts_raw: str) -> Optional[datetime]:
    if not isinstance(ts_raw, str):
        return None
    try:
        dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _derive_outcome(was_delegated: bool, score: float, threshold: float) -> str:
    """was_delegated=True -> neutral (dispatching a subagent is an ACTION, not
    a success - scoring it positive builds a fitness signal out of its own
    routing decisions) | missed above-threshold -> negative | legitimate
    self-handling below threshold -> neutral."""
    if was_delegated:
        return "neutral"
    if score >= threshold:
        return "negative"
    return "neutral"


def _write_fitness_from_gap(session_id: str, marker: Dict, was_delegated: bool) -> None:
    """Append a cognitive-fitness.jsonl row for this Stop event.

    Consumes the quality verdict REGARDLESS of the flag state so a stale
    "low" bridge file never lingers to mis-score a later delegation.
    Silent skip when the AutoEvolve flag is off or marker fields are
    missing/unparseable.
    """
    verdict = _read_quality_verdict(session_id)
    _unlink_quality_verdict(session_id)

    if not _v2_tuning_enabled():
        return

    emit_utc = _parse_ts_utc(marker.get("emit_ts")) or datetime.now(timezone.utc)

    task_hint = marker.get("task_type")
    if not isinstance(task_hint, str) or not task_hint:
        return
    try:
        score = float(marker.get("score") or 0.0)
        threshold = float(marker.get("effective_threshold") or 0.0)
    except (TypeError, ValueError):
        return

    base_outcome = _derive_outcome(bool(was_delegated), score, threshold)
    final_outcome, shadow = _apply_quality_signal(
        base_outcome, bool(was_delegated), verdict
    )
    if shadow is not None:
        _log_invocation(
            session_id, "Stop", "quality_signal_shadow",
            f"observe: would downgrade positive->negative (reason={shadow.get('reason')})",
            was_delegated=bool(was_delegated),
        )

    row = {
        "ts": emit_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "system": "delegation",
        "entity": task_hint,
        "domain": task_hint,
        "outcome": final_outcome,
        "details": {
            "was_delegated": bool(was_delegated),
            "score": score,
            "threshold": threshold,
            "session": session_id,
        },
    }

    # File-I/O exceptions intentionally propagate to handle_stop's try/except
    # so the "fitness bridge write failed" stderr line is reachable.
    FITNESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(FITNESS_FILE, "a", encoding="utf-8") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        except OSError:
            pass
        f.write(json.dumps(row) + "\n")


# =========================================================================
# Event handlers
# =========================================================================

def handle_pre_tool_use(input_data: Dict, session_id: str) -> None:
    """Mark pending marker as resolved when the Task/Agent tool fires.

    The runtime field is "Agent" for the Agent tool; "Task" is accepted for
    older payload shapes. Checking both avoids the silent-no-op class where
    a renamed tool field disables the tracker without any error.
    """
    tool_name = input_data.get("tool_name") or input_data.get("tool")
    if tool_name not in ("Task", "Agent"):
        return
    marker = _read_marker(session_id)
    if not marker or marker.get("resolved"):
        return
    marker["resolved"] = True
    marker["resolved_ts"] = datetime.now(timezone.utc).isoformat()
    tool_input = input_data.get("tool_input") or {}
    if isinstance(tool_input, dict):
        sub = tool_input.get("subagent_type")
        if sub:
            marker["resolved_subagent"] = str(sub)[:60]
    _write_marker(session_id, marker)


_RACE_GRACE_SECONDS = 2.0


def _marker_age_seconds(marker: Dict) -> Optional[float]:
    raw = marker.get("emit_ts")
    if not raw:
        return None
    try:
        emitted = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - emitted).total_seconds()
    except Exception:
        return None


def handle_stop(session_id: str) -> None:
    """Drain pending marker into delegation-gaps.jsonl, then unlink.

    Race-safety: an unresolved marker younger than _RACE_GRACE_SECONDS may
    indicate a still-in-flight PreToolUse whose resolved=True write has not
    completed; re-read once after a short sleep before concluding
    was_delegated=False.
    """
    started = time.monotonic()
    marker = _read_marker(session_id)
    if not marker:
        _log_invocation(session_id, "Stop", "skip", "no_marker",
                        duration_ms=(time.monotonic() - started) * 1000.0)
        return
    if not marker.get("resolved"):
        age = _marker_age_seconds(marker)
        if age is not None and age < _RACE_GRACE_SECONDS:
            time.sleep(0.1)
            refreshed = _read_marker(session_id)
            if refreshed:
                marker = refreshed
    was_delegated = bool(marker.get("resolved"))
    gap_appended = _append_gap_entry(session_id, marker, was_delegated)
    fitness_error: Optional[str] = None
    try:
        _write_fitness_from_gap(session_id, marker, was_delegated)
    except Exception as exc:
        fitness_error = f"{type(exc).__name__}: {str(exc)[:200]}"
        try:
            sys.stderr.write(
                "delegation-outcome-tracker: fitness bridge write failed\n"
            )
        except Exception:
            pass
    _unlink_marker(session_id)
    _log_invocation(
        session_id, "Stop",
        action="write" if gap_appended else "write_failed",
        reason="ok_delegated" if was_delegated else "ok_no_delegation",
        was_delegated=was_delegated,
        duration_ms=(time.monotonic() - started) * 1000.0,
        error=fitness_error,
    )


# =========================================================================
# Main
# =========================================================================

def _install_timeout(seconds: int = 4) -> None:
    def _bail(_signum, _frame):
        sys.exit(0)
    try:
        signal.signal(signal.SIGALRM, _bail)
        signal.alarm(seconds)
    except Exception:
        pass


def main(input_data: Dict) -> None:
    _install_timeout(4)
    session_id = _resolve_session_id(input_data)
    event_type = input_data.get("hook_event_name") or input_data.get("event")
    try:
        if event_type == "PreToolUse":
            handle_pre_tool_use(input_data, session_id)
        elif event_type == "Stop":
            handle_stop(session_id)
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    raw_stdin = ""
    try:
        raw_stdin = sys.stdin.read()
    except Exception:
        pass
    try:
        input_data = json.loads(raw_stdin) if raw_stdin.strip() else {}
        if not isinstance(input_data, dict):
            input_data = {}
    except json.JSONDecodeError:
        input_data = {}

    try:
        scripts_dir = str(PLUGIN_ROOT / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from lib.hook_telemetry import track_hook
        try:
            # event=None lets track_hook derive PreToolUse vs Stop per call.
            with track_hook("delegation-outcome-tracker", event=None,
                            session_id=input_data.get("session_id"),
                            input_data=input_data or None):
                main(input_data)
        except SystemExit:
            raise
        except Exception:
            sys.exit(0)
    except ImportError:
        try:
            main(input_data)
        except Exception:
            sys.exit(0)
