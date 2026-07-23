"""E2E telemetry chain: enforcer marker -> tracker resolve -> Stop drain.

Runs the real hook scripts as subprocesses against a throwaway plugin tree
(CLAUDE_PLUGIN_ROOT override) with a unique session id per test.
"""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENFORCER = REPO / "hooks" / "scripts" / "delegation-enforcer.py"
TRACKER = REPO / "hooks" / "scripts" / "delegation-outcome-tracker.py"
USAGE = REPO / "hooks" / "scripts" / "usage-tracker.py"


def _run_hook(script: Path, payload: dict, env_extra: dict) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(payload), capture_output=True, text=True,
        env=env, timeout=30,
    )


def _plugin_tree(tmp_path: Path) -> Path:
    (tmp_path / "_memory" / "analytics").mkdir(parents=True)
    (tmp_path / "_graph" / "cache").mkdir(parents=True)
    (tmp_path / "_memory" / ".session-count").write_text("5")  # tier 2 active
    (tmp_path / "_graph" / "cache" / "delegation-config.json").write_text(
        json.dumps({"task_type_routing": {}}))
    # The hooks import scripts/lib (telemetry); link the real scripts dir in.
    (tmp_path / "scripts").symlink_to(REPO / "scripts")
    return tmp_path


def test_full_delegation_outcome_chain(tmp_path):
    root = _plugin_tree(tmp_path)
    sid = f"test-{uuid.uuid4().hex[:12]}"
    env = {"CLAUDE_PLUGIN_ROOT": str(root), "CLAUDE_SESSION_ID": sid}
    marker = Path(f"/tmp/delegation-pending-{sid}.json")

    try:
        # 1. UserPromptSubmit: delegation-worthy prompt -> suggestion + marker.
        prompt = "find all usages of the config loader across the codebase"
        res = _run_hook(ENFORCER, {
            "session_id": sid, "hook_event_name": "UserPromptSubmit",
            "prompt": prompt,
        }, env)
        assert res.returncode == 0
        out = json.loads(res.stdout)
        assert "decision" not in out  # invalid schema key must not reappear
        assert "DELEGATION SUGGESTED" in out["hookSpecificOutput"]["additionalContext"]
        assert marker.exists(), "enforcer must write the pending marker"
        assert json.loads(marker.read_text())["resolved"] is False

        # 2. PreToolUse with Agent tool -> marker resolved.
        res = _run_hook(TRACKER, {
            "session_id": sid, "hook_event_name": "PreToolUse",
            "tool_name": "Agent", "tool_input": {"subagent_type": "Explore"},
        }, env)
        assert res.returncode == 0
        m = json.loads(marker.read_text())
        assert m["resolved"] is True
        assert m["resolved_subagent"] == "Explore"

        # 3. Stop -> gaps row written, marker unlinked.
        res = _run_hook(TRACKER, {
            "session_id": sid, "hook_event_name": "Stop",
        }, env)
        assert res.returncode == 0
        gaps = root / "_memory" / "analytics" / "delegation-gaps.jsonl"
        rows = [json.loads(l) for l in gaps.read_text().splitlines()]
        assert rows[-1]["was_delegated"] is True
        assert rows[-1]["session"] == sid
        assert not marker.exists()

        # hook_telemetry rows landed for both tracker invocations.
        inv = root / "_ledgers" / "hook-invocations.jsonl"
        hooks_seen = [json.loads(l)["hook"] for l in inv.read_text().splitlines()]
        assert "delegation-outcome-tracker" in hooks_seen
    finally:
        marker.unlink(missing_ok=True)


def test_missed_delegation_records_gap(tmp_path):
    root = _plugin_tree(tmp_path)
    sid = f"test-{uuid.uuid4().hex[:12]}"
    env = {"CLAUDE_PLUGIN_ROOT": str(root), "CLAUDE_SESSION_ID": sid}
    marker = Path(f"/tmp/delegation-pending-{sid}.json")

    try:
        _run_hook(ENFORCER, {
            "session_id": sid, "hook_event_name": "UserPromptSubmit",
            "prompt": "search the whole repo and list all error handling patterns",
        }, env)
        assert marker.exists()
        # No Task fired; marker is older than the race grace via emit_ts rewrite.
        m = json.loads(marker.read_text())
        m["emit_ts"] = "2026-01-01T00:00:00+00:00"
        marker.write_text(json.dumps(m))

        _run_hook(TRACKER, {"session_id": sid, "hook_event_name": "Stop"}, env)
        gaps = root / "_memory" / "analytics" / "delegation-gaps.jsonl"
        rows = [json.loads(l) for l in gaps.read_text().splitlines()]
        assert rows[-1]["was_delegated"] is False
    finally:
        marker.unlink(missing_ok=True)


def test_fitness_bridge_gated_then_active(tmp_path):
    """Flag off -> no fitness row. Flag on -> fitness row with derived outcome."""
    root = _plugin_tree(tmp_path)
    sid = f"test-{uuid.uuid4().hex[:12]}"
    env = {"CLAUDE_PLUGIN_ROOT": str(root), "CLAUDE_SESSION_ID": sid}
    marker = Path(f"/tmp/delegation-pending-{sid}.json")
    fitness = root / "_memory" / "analytics" / "cognitive-fitness.jsonl"
    cfg = root / "_graph" / "cache" / "delegation-config.json"

    try:
        # Flag OFF (default config): no fitness row.
        _run_hook(ENFORCER, {
            "session_id": sid, "hook_event_name": "UserPromptSubmit",
            "prompt": "find all usages of the cache writer in this project",
        }, env)
        _run_hook(TRACKER, {"session_id": sid, "hook_event_name": "PreToolUse",
                            "tool_name": "Agent"}, env)
        _run_hook(TRACKER, {"session_id": sid, "hook_event_name": "Stop"}, env)
        assert not fitness.exists()

        # Flag ON: same chain produces a fitness row.
        cfg.write_text(json.dumps({
            "task_type_routing": {},
            "mutation_rules": {"v2_tuning_enabled": True},
        }))
        _run_hook(ENFORCER, {
            "session_id": sid, "hook_event_name": "UserPromptSubmit",
            "prompt": "find all usages of the cache writer in this project",
        }, env)
        _run_hook(TRACKER, {"session_id": sid, "hook_event_name": "PreToolUse",
                            "tool_name": "Agent"}, env)
        _run_hook(TRACKER, {"session_id": sid, "hook_event_name": "Stop"}, env)
        rows = [json.loads(l) for l in fitness.read_text().splitlines()]
        assert rows[-1]["system"] == "delegation"
        # honest-derivation fix: dispatching a subagent is an ACTION, not a
        # success - a delegated Stop without a quality verdict scores neutral
        assert rows[-1]["outcome"] == "neutral"
        assert rows[-1]["details"]["was_delegated"] is True
    finally:
        marker.unlink(missing_ok=True)


def test_usage_tracker_history_and_counts(tmp_path):
    root = _plugin_tree(tmp_path)
    sid = f"test-{uuid.uuid4().hex[:12]}"
    env = {"CLAUDE_PLUGIN_ROOT": str(root), "CLAUDE_SESSION_ID": sid}

    for tool in ("Read", "Read", "Bash"):
        res = _run_hook(USAGE, {"session_id": sid, "tool_name": tool}, env)
        assert res.returncode == 0

    usage = json.loads((root / "_memory" / "analytics" / "usage.json").read_text())
    assert usage["total_calls"] == 3
    assert usage["tools"]["Read"] == 2

    history = (root / "_memory" / "analytics" / "usage-history.jsonl").read_text().splitlines()
    assert len(history) == 3
    assert json.loads(history[0])["session"] == sid
