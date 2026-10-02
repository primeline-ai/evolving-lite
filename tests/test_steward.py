"""Tests for the steward apparatus (Phase 4 port).

Covers: the three check modules (audit / followup / retirement), the consolidated
steward-checker SessionStart hook, and the steward_actuator (with its
spine-fail-closed + library/test/reference safety guards).

The headline gate is cold-baseline ZERO findings + a planted positive control.
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from steward_checks import audit, common, followup, retirement  # noqa: E402

TODAY = datetime.date(2026, 6, 12)


def _load_module(path: Path, root: Path, mod_name: str):
    """Load a standalone module file fresh with CLAUDE_PLUGIN_ROOT=root so its
    module-level REPO_ROOT + derived path constants bind to the tmp repo."""
    os.environ["CLAUDE_PLUGIN_ROOT"] = str(root)
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# common
# ---------------------------------------------------------------------------

def test_finding_to_dict_uses_maintainer_decision():
    f = common.StewardFinding(
        module="followup", severity=common.Severity.P1, title="t", detail="d", source="s"
    )
    d = f.to_dict()
    assert d["severity"] == "P1"
    assert d["maintainer_decision"] == "silent"
    assert "robin_decision" not in d  # renamed for the public port


def test_load_steward_config_defaults_and_override(tmp_path):
    cfg = common.load_steward_config(tmp_path)  # no file -> defaults
    assert cfg["audit_never_audited_is_ok"] is True
    cache = tmp_path / "_graph" / "cache"
    cache.mkdir(parents=True)
    (cache / "steward-config.json").write_text(json.dumps({"audit_stale_threshold_days": 99}))
    cfg2 = common.load_steward_config(tmp_path)
    assert cfg2["audit_stale_threshold_days"] == 99
    assert cfg2["audit_never_audited_is_ok"] is True  # untouched key keeps default


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------

def test_audit_never_audited_is_ok_cold(tmp_path):
    res = audit.run_check(repo_root=tmp_path, today=TODAY)
    assert res.findings_count == 0  # cold-baseline: no audit dir -> silent


def test_audit_never_audited_flag_off_emits_p2(tmp_path):
    cache = tmp_path / "_graph" / "cache"
    cache.mkdir(parents=True)
    (cache / "steward-config.json").write_text(json.dumps({"audit_never_audited_is_ok": False}))
    res = audit.run_check(repo_root=tmp_path, today=TODAY)
    assert res.findings_count == 1
    assert res.findings[0].severity == common.Severity.P2


def test_audit_stale_report_flagged(tmp_path):
    d = tmp_path / "audit-reports"
    d.mkdir()
    (d / "audit-2026-05-01.md").write_text("old report")
    res = audit.run_check(repo_root=tmp_path, today=TODAY)
    assert res.findings_count == 1
    assert "stale" in res.findings[0].title.lower()


def test_audit_fresh_report_silent(tmp_path):
    d = tmp_path / "audit-reports"
    d.mkdir()
    (d / "audit-2026-06-10.md").write_text("fresh report")  # 2d old vs TODAY
    res = audit.run_check(repo_root=tmp_path, today=TODAY)
    assert res.findings_count == 0  # within threshold -> no noise


# ---------------------------------------------------------------------------
# followup
# ---------------------------------------------------------------------------

def test_followup_cold_zero(tmp_path):
    assert followup.run_check(repo_root=tmp_path, today=TODAY).findings_count == 0


def test_followup_overdue_positive_control(tmp_path):
    h = tmp_path / "_handoffs"
    h.mkdir()
    (h / "h1.md").write_text("- Follow-up 2026-06-01: re-check the deploy\n")
    res = followup.run_check(repo_root=tmp_path, today=TODAY)
    assert res.findings_count == 1
    f = res.findings[0]
    assert f.severity == common.Severity.P1  # 11d overdue
    assert f.days_overdue == 11


def test_followup_resolution_marker_suppresses(tmp_path):
    h = tmp_path / "_handoffs"
    h.mkdir()
    (h / "h1.md").write_text("- [x] Follow-up 2026-06-01: done already\n")
    assert followup.run_check(repo_root=tmp_path, today=TODAY).findings_count == 0


def test_followup_upcoming_is_p3(tmp_path):
    h = tmp_path / "_handoffs"
    h.mkdir()
    (h / "h1.md").write_text("- Follow-up 2026-06-15: upcoming soon\n")
    res = followup.run_check(repo_root=tmp_path, today=TODAY)
    assert res.findings_count == 1
    assert res.findings[0].severity == common.Severity.P3


def test_followup_too_far_future_skipped(tmp_path):
    h = tmp_path / "_handoffs"
    h.mkdir()
    (h / "h1.md").write_text("- Follow-up 2026-08-01: way out\n")
    assert followup.run_check(repo_root=tmp_path, today=TODAY).findings_count == 0


# ---------------------------------------------------------------------------
# retirement
# ---------------------------------------------------------------------------

def _make_hooks_repo(tmp_path, register: list[str]) -> Path:
    """Build a tmp repo with hooks/hooks.json registering `register` basenames."""
    (tmp_path / "hooks" / "scripts").mkdir(parents=True)
    cmds = [{"type": "command", "command": f"python3 ${{CLAUDE_PLUGIN_ROOT}}/hooks/scripts/{n}"} for n in register]
    hooks = {"hooks": {"SessionStart": [{"hooks": cmds}]}}
    (tmp_path / "hooks" / "hooks.json").write_text(json.dumps(hooks))
    return tmp_path / "hooks" / "scripts"


def _age_file(path: Path, days: int):
    # Relative to the pinned TODAY that run_check is given, never to the real
    # clock: aging from now() made "200 days old" read as 88 days once the
    # real date passed 2026-09-28, and made a "5 days old" file lie in the
    # future of TODAY, so the grace test passed through the age < 0 branch.
    noon = datetime.datetime.combine(TODAY, datetime.time(12, 0))
    ts = (noon - datetime.timedelta(days=days)).timestamp()
    os.utime(path, (ts, ts))


def test_retirement_cold_all_registered(tmp_path):
    sd = _make_hooks_repo(tmp_path, ["live.py"])
    f = sd / "live.py"
    f.write_text("# handler\n")
    _age_file(f, 200)  # old but registered -> live -> no finding
    assert retirement.run_check(repo_root=tmp_path, today=TODAY).findings_count == 0


def test_retirement_unregistered_old_is_high_conf(tmp_path):
    sd = _make_hooks_repo(tmp_path, ["live.py"])
    dead = sd / "dead.py"
    dead.write_text("# an old unregistered event handler\n")
    _age_file(dead, 200)
    res = retirement.run_check(repo_root=tmp_path, today=TODAY)
    assert res.findings_count == 1
    assert common.HIGH_CONFIDENCE_MARKER in res.findings[0].detail


def test_retirement_age_grace_protects_fresh(tmp_path):
    sd = _make_hooks_repo(tmp_path, ["live.py"])
    fresh = sd / "fresh.py"
    fresh.write_text("# new unregistered file\n")
    _age_file(fresh, 5)  # within 90d grace
    assert retirement.run_check(repo_root=tmp_path, today=TODAY).findings_count == 0


def test_retirement_allowlist_excludes(tmp_path):
    sd = _make_hooks_repo(tmp_path, ["live.py"])
    dead = sd / "dead.py"
    dead.write_text("# old unregistered\n")
    _age_file(dead, 200)
    cache = tmp_path / "_graph" / "cache"
    cache.mkdir(parents=True)
    (cache / "steward-config.json").write_text(json.dumps({"retirement_critical_allowlist": ["dead.py"]}))
    assert retirement.run_check(repo_root=tmp_path, today=TODAY).findings_count == 0


def test_retirement_testfile_is_low_conf(tmp_path):
    sd = _make_hooks_repo(tmp_path, ["live.py"])
    t = sd / "test-thing.py"
    t.write_text("# a test harness\n")
    _age_file(t, 200)
    res = retirement.run_check(repo_root=tmp_path, today=TODAY)
    assert res.findings_count == 1
    assert common.HIGH_CONFIDENCE_MARKER not in res.findings[0].detail  # LOW conf


# ---------------------------------------------------------------------------
# steward-checker (consolidated hook)
# ---------------------------------------------------------------------------

def _build_min_repo(tmp_path):
    (tmp_path / "_inbox").mkdir()
    (tmp_path / "_graph" / "cache").mkdir(parents=True)
    (tmp_path / "hooks" / "scripts").mkdir(parents=True)
    (tmp_path / "hooks" / "hooks.json").write_text(json.dumps({"hooks": {}}))


def test_checker_cold_writes_no_findings(tmp_path, monkeypatch):
    _build_min_repo(tmp_path)
    monkeypatch.setattr("subprocess.run", _fake_no_claude_ps)
    checker = _load_module(
        REPO / "hooks" / "scripts" / "steward-checker.py", tmp_path, "steward_checker_t1"
    )
    checker.main(today=TODAY)
    assert not (tmp_path / "_inbox" / "steward-findings.jsonl").exists()


def test_checker_positive_control_followup(tmp_path, monkeypatch):
    _build_min_repo(tmp_path)
    monkeypatch.setattr("subprocess.run", _fake_no_claude_ps)
    (tmp_path / "_handoffs").mkdir()
    (tmp_path / "_handoffs" / "h.md").write_text("- Follow-up 2026-06-01: stale item\n")
    checker = _load_module(
        REPO / "hooks" / "scripts" / "steward-checker.py", tmp_path, "steward_checker_t2"
    )
    checker.main(today=TODAY)
    out = tmp_path / "_inbox" / "steward-findings.jsonl"
    assert out.exists()
    rows = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    modules = {r["module"] for r in rows}
    assert "followup" in modules


def _fake_no_claude_ps(*args, **kwargs):
    class _R:
        returncode = 0
        stdout = "  123 00:30 /usr/bin/python3 something\n"
    return _R()


# ---------------------------------------------------------------------------
# steward_actuator
# ---------------------------------------------------------------------------

def _load_actuator(tmp_path, mod_name):
    return _load_module(REPO / "scripts" / "steward_actuator.py", tmp_path, mod_name)


def test_actuator_spine_fail_closed_blocks_archive(tmp_path):
    act = _load_actuator(tmp_path, "act_failclosed")
    # Force the fail-closed (pre-spine) state explicitly so this contract test is
    # robust whether or not the spine module is present on the ambient sys.path
    # (Phase 5 ships scripts/lib/verifier/spine.py; this test still pins the
    # pre-spine behaviour the actuator must fall back to if the registry is gone).
    act._SPINE_AVAILABLE = False
    act._is_spine_path = lambda p: True  # fail-closed stub: cannot prove NOT-spine
    sd = tmp_path / "hooks" / "scripts"
    sd.mkdir(parents=True)
    (sd / "dead.py").write_text("# dead")
    # Pre-spine a dead candidate is classified AUTONOMOUS but the fail-closed
    # archive guard refuses it (cannot prove it is NOT a spine file).
    cls = act.classify_action(
        {"module": "retirement", "detail": act.HIGH_CONFIDENCE_MARKER, "source": "hooks/scripts/dead.py"}
    )
    assert cls == act.AutonomyClass.AUTONOMOUS
    safe, reason = act.is_safe_to_autonomously_archive(
        {"source": "hooks/scripts/dead.py"}, repo_root=tmp_path
    )
    assert safe is False
    assert "spine" in reason.lower()


def test_actuator_testfile_downgrades_to_supervised(tmp_path):
    act = _load_actuator(tmp_path, "act_testguard")
    # Force the spine guard off so we exercise the library/test guard in isolation.
    act._is_spine_path = lambda p: False
    (tmp_path / "hooks" / "scripts").mkdir(parents=True)
    f = tmp_path / "hooks" / "scripts" / "test-thing.py"
    f.write_text("# test harness")
    safe, reason = act.is_safe_to_autonomously_archive(
        {"source": "hooks/scripts/test-thing.py"}, repo_root=tmp_path
    )
    assert safe is False
    assert "test file" in reason


def test_actuator_referenced_file_not_archived(tmp_path):
    act = _load_actuator(tmp_path, "act_refguard")
    act._is_spine_path = lambda p: False
    (tmp_path / "hooks" / "scripts").mkdir(parents=True)
    dead = tmp_path / "hooks" / "scripts" / "shared.py"
    dead.write_text("x = 1")
    other = tmp_path / "hooks" / "scripts" / "user.py"
    other.write_text("# orchestrated via hooks/scripts/shared.py\n")  # references shared.py by token
    safe, reason = act.is_safe_to_autonomously_archive(
        {"source": "hooks/scripts/shared.py"}, repo_root=tmp_path
    )
    assert safe is False
    assert "referenced" in reason


def test_actuator_genuinely_dead_is_archivable_with_spine_off(tmp_path):
    act = _load_actuator(tmp_path, "act_deadok")
    act._is_spine_path = lambda p: False
    (tmp_path / "hooks" / "scripts").mkdir(parents=True)
    dead = tmp_path / "hooks" / "scripts" / "ghost.py"
    dead.write_text("# nobody references me")
    safe, _ = act.is_safe_to_autonomously_archive(
        {"source": "hooks/scripts/ghost.py"}, repo_root=tmp_path
    )
    assert safe is True


def test_actuator_followup_emits_pending(tmp_path):
    act = _load_actuator(tmp_path, "act_followup")
    findings = tmp_path / "_inbox" / "steward-findings.jsonl"
    findings.parent.mkdir(parents=True)
    findings.write_text(json.dumps({
        "module": "followup", "severity": "P1", "title": "overdue x",
        "detail": "d", "source": "_handoffs/h.md", "item_id": "h:2026-06-01",
        "days_overdue": 11, "maintainer_decision": "silent",
    }) + "\n")
    summary = act.run_actuator(findings_path=findings, dry_run=False)
    assert summary["supervised_emitted"] == 1
    assert (tmp_path / "_inbox" / "steward-actions-pending.jsonl").exists()


def test_actuator_dead_finding_dormant_without_spine(tmp_path):
    """End-to-end: a HIGH-confidence dead finding does NOT get archived while the
    spine is absent (Phase-4 fail-closed contract). Forced explicitly so the test
    pins the fail-closed fallback regardless of ambient spine availability (the
    spine module ships in Phase 5; spine-PRESENT archiving is covered in
    test_verifier_spine.py)."""
    act = _load_actuator(tmp_path, "act_dormant")
    act._SPINE_AVAILABLE = False
    act._is_spine_path = lambda p: True  # fail-closed stub
    sd = tmp_path / "hooks" / "scripts"
    sd.mkdir(parents=True)
    (sd / "ghost.py").write_text("# dead")
    findings = tmp_path / "_inbox" / "steward-findings.jsonl"
    findings.parent.mkdir(exist_ok=True)
    findings.write_text(json.dumps({
        "module": "retirement", "severity": "P2",
        "title": "Retirement candidate: ghost.py",
        "detail": act.HIGH_CONFIDENCE_MARKER, "source": "hooks/scripts/ghost.py",
        "item_id": "ghost.py", "maintainer_decision": "silent",
    }) + "\n")
    summary = act.run_actuator(findings_path=findings, dry_run=False)
    assert summary["autonomous_archived"] == 0  # spine absent -> never archives
    assert (sd / "ghost.py").exists()  # file untouched
