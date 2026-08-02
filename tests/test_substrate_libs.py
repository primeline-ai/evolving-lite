"""Substrate shared-lib tests: locking, atomic writes, attribution, telemetry."""

import json
import multiprocessing
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from lib.cache_writer import (  # noqa: E402
    atomic_consume_json,
    atomic_write_json,
    safe_read_json,
)
from lib.locked_json_rmw import LockTimeout, locked_rmw_json, locked_write_remerge  # noqa: E402
from lib.session_attribution import (  # noqa: E402
    attribute_row,
    normalize_session_key,
    resolve_session_id,
)


# ---------------------------------------------------------------- locked RMW

def test_locked_rmw_roundtrip(tmp_path):
    f = tmp_path / "doc.json"
    f.write_text(json.dumps({"items": [1]}))

    def add(doc):
        doc["items"].append(2)
        return doc, True

    assert locked_rmw_json(f, add) is True
    assert json.loads(f.read_text())["items"] == [1, 2]


def test_locked_rmw_unchanged_leaves_bytes(tmp_path):
    f = tmp_path / "doc.json"
    raw = '{"a": 1}'
    f.write_text(raw)
    assert locked_rmw_json(f, lambda d: (d, False)) is False
    assert f.read_text() == raw


def test_locked_rmw_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        locked_rmw_json(tmp_path / "nope.json", lambda d: (d, True))


def _append_worker(path: str, label: str, n: int) -> None:
    sys.path.insert(0, str(Path(path).resolve().parents[2] / "scripts"))
    from lib.locked_json_rmw import locked_write_remerge as lwr
    for i in range(n):
        def add(doc, item=f"{label}-{i}"):
            doc["items"].append(item)
            return True
        assert lwr(path, add, acquire_timeout_s=10.0)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "locked_json_rmw has NO locking on Windows. fcntl is POSIX-only and the "
        "module falls back to a no-op stub (see its `except ImportError` branch), "
        "so concurrent appends CAN be lost there. This is a real limitation, not "
        "a test-environment quirk - skipping asserts nothing about Windows safety. "
        "Remove this skip only once the module implements msvcrt-based locking."
    ),
)
def test_remerge_concurrent_appenders_lose_nothing(tmp_path):
    """The lost-update race the lib exists to prevent: two processes append
    concurrently; every append must survive.

    POSIX only - see the skipif above.
    """
    f = tmp_path / "shared.json"
    f.write_text(json.dumps({"items": []}))
    n = 25
    procs = [
        multiprocessing.Process(target=_append_worker, args=(str(f), lab, n))
        for lab in ("a", "b")
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
        assert p.exitcode == 0
    items = json.loads(f.read_text())["items"]
    assert len(items) == 2 * n
    assert len(set(items)) == 2 * n


def test_remerge_fails_open_on_missing_file(tmp_path):
    assert locked_write_remerge(tmp_path / "nope.json", lambda d: True) is False


# ------------------------------------------------------------- cache_writer

def test_atomic_write_and_safe_read(tmp_path):
    f = tmp_path / "cache.json"
    atomic_write_json(f, {"k": "v"}, lock=True)
    assert safe_read_json(f) == {"k": "v"}
    assert safe_read_json(tmp_path / "absent.json", default={}) == {}


def test_atomic_consume_exactly_once(tmp_path):
    f = tmp_path / "handoff.json"
    atomic_write_json(f, {"x": 1})
    assert atomic_consume_json(f) == {"x": 1}
    assert atomic_consume_json(f) is None  # second consumer gets nothing
    assert not f.exists()


# ------------------------------------------------------- session attribution

def test_resolve_session_id_cascade(monkeypatch):
    assert resolve_session_id({"session_id": "abc"}) == "abc"
    assert resolve_session_id({"session": "def"}) == "def"
    monkeypatch.setenv("CLAUDE_SESSION_ID", "env-sid")
    assert resolve_session_id() == "env-sid"
    monkeypatch.delenv("CLAUDE_SESSION_ID")
    assert resolve_session_id(fallback="fb") == "fb"
    assert resolve_session_id().startswith("pid-")


def test_normalize_session_key_fills_both():
    row = {"session": "s1"}
    normalize_session_key(row)
    assert row["session_id"] == "s1"
    row2 = {"session_id": "s2"}
    normalize_session_key(row2)
    assert row2["session"] == "s2"
    row3 = {"session": "win", "session_id": "lose"}
    normalize_session_key(row3)
    assert row3["session_id"] == "win"


def test_attribute_row_idempotent():
    row = {"session": "keep"}
    attribute_row(row, session_id="other")
    assert row["session"] == "keep"


# ------------------------------------------------------------ hook telemetry

def test_track_hook_writes_row_and_clears_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path))
    monkeypatch.setenv("HOOK_ACTIVE_MARKER_DIR", str(tmp_path / "markers"))
    # Re-import with fresh env-dependent paths.
    import importlib
    import lib.hook_telemetry as ht
    importlib.reload(ht)

    with ht.track_hook("test-hook", event="PostToolUse", session_id="sid-1") as t:
        t.add_meta(action="noop")
        marker_files = list((tmp_path / "markers").glob("*.json"))
        assert len(marker_files) == 1  # marker live during execution

    assert not list((tmp_path / "markers").glob("*.json"))  # marker cleared
    ledger = tmp_path / "_ledgers" / "hook-invocations.jsonl"
    rows = [json.loads(l) for l in ledger.read_text().splitlines()]
    assert rows[-1]["hook"] == "test-hook"
    assert rows[-1]["session"] == "sid-1"
    assert rows[-1]["exit_code"] == 0
    assert rows[-1]["meta"]["action"] == "noop"


def test_lock_telemetry_threshold_gating(tmp_path, monkeypatch):
    ledger = tmp_path / "contention.jsonl"
    monkeypatch.setenv("LOCK_CONTENTION_LEDGER", str(ledger))
    monkeypatch.setenv("LOCK_CONTENTION_THRESHOLD_MS", "5")
    from lib.lock_telemetry import record_lock_event
    record_lock_event(tmp_path / "x.json", wait_ms=1.0, hold_ms=1.0)
    assert not ledger.exists()  # below threshold -> dropped
    record_lock_event(tmp_path / "x.json", wait_ms=50.0, hold_ms=2.0)
    rows = [json.loads(l) for l in ledger.read_text().splitlines()]
    assert rows[0]["wait_ms"] == 50.0
