"""Tests for scripts/recalc-fitness.py (cognitive-fitness recalculator).

Math + config-injection tests run in-process (module loaded via importlib
because the file name is hyphenated). Pipeline tests run the script as a
subprocess against a throwaway plugin tree (CLAUDE_PLUGIN_ROOT override),
matching the substrate test convention.
"""

import importlib.util
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from conftest import pending_marker

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "recalc-fitness.py"
ENFORCER = REPO / "hooks" / "scripts" / "delegation-enforcer.py"

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _load_module():
    spec = importlib.util.spec_from_file_location("recalc_fitness", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rf = _load_module()


def _cfg(**over):
    cfg = dict(rf.DEFAULT_CONFIG)
    cfg.update(over)
    return cfg


def _delegation_row(outcome="positive", was_delegated=True, task="exploration", **over):
    row = {
        "ts": "2026-06-10T12:00:00Z",
        "system": "delegation",
        "entity": task,
        "domain": task,
        "outcome": outcome,
        "details": {
            "was_delegated": was_delegated,
            "score": 5.0,
            "threshold": 3.0,
            "session": "test",
        },
    }
    row.update(over)
    return row


# =========================================================================
# EMA math
# =========================================================================

class TestComputeEma:
    def test_empty_values_neutral(self):
        assert rf.compute_ema([], _cfg()) == 0.5

    def test_asymmetric_down_faster_than_legacy(self, monkeypatch):
        monkeypatch.delenv("FITNESS_EMA_HONESTY", raising=False)
        cfg = _cfg()
        asym = rf.compute_ema([1.0, 0.0], cfg)
        legacy = rf.compute_ema([1.0, 0.0], cfg, alpha=cfg["ema_alpha"])
        # alpha_down=0.45 folds the negative in faster than symmetric 0.3
        assert asym == 0.55
        assert legacy == 0.7
        assert asym < legacy

    def test_up_direction_matches_legacy_alpha(self, monkeypatch):
        monkeypatch.delenv("FITNESS_EMA_HONESTY", raising=False)
        # Upward move uses alpha_up == legacy alpha, so results coincide.
        assert rf.compute_ema([0.0, 1.0], _cfg()) == 0.3

    def test_flag_off_restores_symmetric(self, monkeypatch):
        monkeypatch.setenv("FITNESS_EMA_HONESTY", "0")
        assert rf.compute_ema([1.0, 0.0], _cfg()) == 0.7

    def test_explicit_alpha_forces_symmetric(self, monkeypatch):
        monkeypatch.delenv("FITNESS_EMA_HONESTY", raising=False)
        assert rf.compute_ema([1.0, 0.0], _cfg(), alpha=0.3) == 0.7

    def test_output_clamped_to_unit_interval(self):
        out = rf.compute_ema([5.0, -3.0, float("nan")], _cfg())
        assert 0.0 <= out <= 1.0


class TestRatchetFloor:
    def test_sparse_regime_floors(self, monkeypatch):
        monkeypatch.delenv("FITNESS_EMA_HONESTY", raising=False)
        cfg = _cfg()
        assert rf.apply_ratchet_floor(0.0, 3, cfg) == cfg["recovery_floor"]

    def test_viable_population_passes_honest_zero(self, monkeypatch):
        monkeypatch.delenv("FITNESS_EMA_HONESTY", raising=False)
        cfg = _cfg()
        assert rf.apply_ratchet_floor(0.0, cfg["minimum_viable_population"], cfg) == 0.0

    def test_flag_off_noop(self, monkeypatch):
        monkeypatch.setenv("FITNESS_EMA_HONESTY", "0")
        assert rf.apply_ratchet_floor(0.0, 3, _cfg()) == 0.0

    def test_negative_count_does_not_engage_floor(self, monkeypatch):
        monkeypatch.delenv("FITNESS_EMA_HONESTY", raising=False)
        assert rf.apply_ratchet_floor(0.0, -1, _cfg()) == 0.0

    def test_mvp_threshold_injectable(self, monkeypatch):
        monkeypatch.delenv("FITNESS_EMA_HONESTY", raising=False)
        cfg = _cfg(minimum_viable_population=2)
        assert rf.apply_ratchet_floor(0.0, 2, cfg) == 0.0  # at threshold: honest
        assert rf.apply_ratchet_floor(0.0, 1, cfg) == cfg["recovery_floor"]


class TestColdStartBlend:
    def test_zero_events_returns_default(self):
        assert rf.cold_start_blend(1.0, 0.5, 0, _cfg()) == 0.6  # 0.2*1.0 + 0.8*0.5

    def test_at_threshold_returns_observed(self):
        cfg = _cfg()
        assert rf.cold_start_blend(1.0, 0.5, cfg["cold_start_threshold"], cfg) == 1.0

    def test_partial_blend(self):
        # 1 of 5 events: observed_weight = 0.2 + 0.8*(1/5) = 0.36
        assert rf.cold_start_blend(1.0, 0.5, 1, _cfg()) == 0.68

    def test_threshold_injectable(self):
        cfg = _cfg(cold_start_threshold=1)
        assert rf.cold_start_blend(1.0, 0.5, 1, cfg) == 1.0


# =========================================================================
# Config loading (injectable taxonomy + tunables, fail-open)
# =========================================================================

class TestLoadConfig:
    def test_missing_file_yields_defaults(self, tmp_path):
        cfg = rf.load_config(tmp_path)
        assert cfg == rf.DEFAULT_CONFIG

    def test_file_overrides_merge_over_defaults(self, tmp_path):
        cache = tmp_path / "_graph" / "cache"
        cache.mkdir(parents=True)
        (cache / "fitness-config.json").write_text(json.dumps({
            "window_size": 10,
            "minimum_viable_population": 4,
            "lens_defaults": {"first-principles": 0.5},
            "stale_trait_task_types": ["legacy_type"],
        }))
        cfg = rf.load_config(tmp_path)
        assert cfg["window_size"] == 10
        assert cfg["minimum_viable_population"] == 4
        assert cfg["lens_defaults"] == {"first-principles": 0.5}
        assert cfg["stale_trait_task_types"] == ["legacy_type"]
        # untouched keys keep code defaults
        assert cfg["ema_alpha_down"] == rf.DEFAULT_CONFIG["ema_alpha_down"]

    def test_malformed_file_fails_open_to_defaults(self, tmp_path):
        cache = tmp_path / "_graph" / "cache"
        cache.mkdir(parents=True)
        (cache / "fitness-config.json").write_text("{NOT JSON")
        assert rf.load_config(tmp_path) == rf.DEFAULT_CONFIG

    def test_out_of_range_numerics_rejected(self, tmp_path):
        # RC finding: a zero/negative cold_start_threshold silently disables
        # blending, an out-of-unit alpha breaks the EMA contract - both must
        # be rejected (code default kept), not clamped silently.
        cache = tmp_path / "_graph" / "cache"
        cache.mkdir(parents=True)
        (cache / "fitness-config.json").write_text(json.dumps({
            "cold_start_threshold": -1,
            "ema_alpha_down": 1.7,
            "window_size": 0,
            "recovery_floor": -0.2,
            "minimum_viable_population": 4,  # valid, must survive
        }))
        cfg = rf.load_config(tmp_path)
        assert cfg["cold_start_threshold"] == rf.DEFAULT_CONFIG["cold_start_threshold"]
        assert cfg["ema_alpha_down"] == rf.DEFAULT_CONFIG["ema_alpha_down"]
        assert cfg["window_size"] == rf.DEFAULT_CONFIG["window_size"]
        assert cfg["recovery_floor"] == rf.DEFAULT_CONFIG["recovery_floor"]
        assert cfg["minimum_viable_population"] == 4

    def test_wrong_typed_key_ignored(self, tmp_path):
        cache = tmp_path / "_graph" / "cache"
        cache.mkdir(parents=True)
        (cache / "fitness-config.json").write_text(json.dumps({
            "window_size": "thirty",        # wrong type -> ignored
            "lens_defaults": ["not", "a", "map"],  # wrong type -> ignored
            "recovery_floor": 0.2,          # valid override
        }))
        cfg = rf.load_config(tmp_path)
        assert cfg["window_size"] == rf.DEFAULT_CONFIG["window_size"]
        assert cfg["lens_defaults"] == rf.DEFAULT_CONFIG["lens_defaults"]
        assert cfg["recovery_floor"] == 0.2


# =========================================================================
# Per-system recalc from cold + synthetic events
# =========================================================================

class TestRecalcCold:
    def test_lens_cold_produces_valid_cache(self):
        out = rf.recalc_lens([], _cfg())
        assert out["scores"] == {}
        assert out["quick_picks"]["_default"] == []
        assert out["window"] == rf.DEFAULT_CONFIG["window_size"]
        assert "updated" in out and "ema_honesty" in out

    def test_trait_cold_produces_valid_cache(self):
        out = rf.recalc_trait([], _cfg())
        assert out["scores"] == {}
        assert "updated" in out

    def test_delegation_cold_produces_valid_cache(self):
        out = rf.recalc_delegation([], _cfg())
        assert out["scores"] == {}
        assert out["prediction_delta"] == {}
        assert out["schema_version"] == "1.0"

    def test_lens_config_defaults_seed_cold_scores(self):
        cfg = _cfg(lens_defaults={"first-principles": 0.5, "inversion": 0.5},
                   static_quick_picks=["first-principles"])
        out = rf.recalc_lens([], cfg)
        assert out["scores"]["first-principles"]["_default"] == 0.5
        # With configured lenses present, quick_picks are computed from them
        # (static_quick_picks is only the no-lenses-at-all fallback).
        assert out["quick_picks"]["_default"] == ["first-principles", "inversion"]


class TestRecalcDelegation:
    def test_synthetic_rows_produce_bounded_score(self):
        events = [_delegation_row() for _ in range(3)]
        out = rf.recalc_delegation(events, _cfg())
        score = out["scores"]["exploration"]["exploration"]
        assert 0.0 <= score <= 1.0
        assert out["prediction_delta"]["exploration"] == 1.0

    def test_quarantined_and_drifted_rows_excluded(self):
        events = [
            _delegation_row(),
            _delegation_row(quarantined=True),
            _delegation_row(details={"was_delegated": "true"}),
            {"system": "lens", "entity": "x", "outcome": "positive"},
        ]
        out = rf.recalc_delegation(events, _cfg())
        # only the single clean row counts: 1 event, blended toward neutral
        assert out["scores"]["exploration"]["exploration"] == 0.68

    def test_missed_delegation_scores_negative(self):
        events = [_delegation_row(outcome="negative", was_delegated=False)
                  for _ in range(3)]
        out = rf.recalc_delegation(events, _cfg())
        score = out["scores"]["exploration"]["exploration"]
        assert 0.0 <= score < 0.5
        # not delegating + non-positive outcome = expectation matched
        assert out["prediction_delta"]["exploration"] == 1.0


class TestRecalcTrait:
    def test_stale_task_types_skipped(self):
        cfg = _cfg(stale_trait_task_types=["legacy_type"])
        events = [
            {"system": "trait", "domain": "legacy_type", "entity": "a+b+c",
             "outcome": "positive"},
            {"system": "trait", "domain": "research", "entity": "A+B+C",
             "outcome": "positive"},
        ]
        out = rf.recalc_trait(events, cfg)
        assert "legacy_type" not in out["scores"]
        # entity case-normalized to lowercase
        assert "a+b+c" in out["scores"]["research"]


# =========================================================================
# Pipeline (subprocess against throwaway plugin tree)
# =========================================================================

def _plugin_tree(tmp_path: Path) -> Path:
    (tmp_path / "_memory" / "analytics").mkdir(parents=True)
    (tmp_path / "_graph" / "cache").mkdir(parents=True)
    return tmp_path


def _run_script(root: Path, *args) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["CLAUDE_PLUGIN_ROOT"] = str(root)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, env=env, timeout=30,
    )


class TestPipeline:
    def test_cold_run_writes_three_valid_caches(self, tmp_path):
        root = _plugin_tree(tmp_path)
        res = _run_script(root)
        assert res.returncode == 0, res.stderr
        for name in ("lens-fitness.json", "trait-fitness.json",
                     "delegation-fitness.json"):
            data = json.loads((root / "_graph" / "cache" / name).read_text())
            assert "updated" in data and "scores" in data
        ledger = (root / "_ledgers" / "recalc-fitness-invocations.jsonl")
        last = json.loads(ledger.read_text().splitlines()[-1])
        assert last["success"] is True and last["events_read"] == 0

    def test_ledger_rows_produce_bounded_delegation_score(self, tmp_path):
        root = _plugin_tree(tmp_path)
        ledger = root / "_memory" / "analytics" / "cognitive-fitness.jsonl"
        rows = [_delegation_row() for _ in range(2)]
        ledger.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        res = _run_script(root)
        assert res.returncode == 0, res.stderr
        data = json.loads(
            (root / "_graph" / "cache" / "delegation-fitness.json").read_text())
        score = data["scores"]["exploration"]["exploration"]
        assert 0.0 <= score <= 1.0

    def test_system_filter_writes_single_cache(self, tmp_path):
        root = _plugin_tree(tmp_path)
        res = _run_script(root, "--system", "delegation")
        assert res.returncode == 0, res.stderr
        assert (root / "_graph" / "cache" / "delegation-fitness.json").exists()
        assert not (root / "_graph" / "cache" / "lens-fitness.json").exists()

    def test_unknown_system_exits_nonzero(self, tmp_path):
        root = _plugin_tree(tmp_path)
        res = _run_script(root, "--system", "bogus")
        assert res.returncode == 1

    def test_trigger_flag_recorded_in_invocation_ledger(self, tmp_path):
        root = _plugin_tree(tmp_path)
        res = _run_script(root, "--trigger", "session-start")
        assert res.returncode == 0, res.stderr
        ledger = root / "_ledgers" / "recalc-fitness-invocations.jsonl"
        last = json.loads(ledger.read_text().splitlines()[-1])
        assert last["trigger"] == "session-start"


# =========================================================================
# Consumer: delegation-enforcer reads delegation-fitness.json
# =========================================================================

class TestEnforcerConsumesFitness:
    def _enforcer_tree(self, tmp_path: Path) -> Path:
        (tmp_path / "_memory" / "analytics").mkdir(parents=True)
        (tmp_path / "_graph" / "cache").mkdir(parents=True)
        (tmp_path / "_memory" / ".session-count").write_text("5")
        (tmp_path / "_graph" / "cache" / "delegation-config.json").write_text(
            json.dumps({"task_type_routing": {}}))
        (tmp_path / "scripts").symlink_to(REPO / "scripts")
        return tmp_path

    def _fire(self, root: Path, sid: str) -> dict:
        env = dict(os.environ)
        env.update({"CLAUDE_PLUGIN_ROOT": str(root), "CLAUDE_SESSION_ID": sid})
        prompt = "find all usages of the config loader across the codebase"
        res = subprocess.run(
            [sys.executable, str(ENFORCER)],
            input=json.dumps({"session_id": sid,
                              "hook_event_name": "UserPromptSubmit",
                              "prompt": prompt}),
            capture_output=True, text=True, env=env, timeout=30,
        )
        assert res.returncode == 0, res.stderr
        return json.loads(res.stdout)

    def test_fitness_hint_present_when_cache_has_score(self, tmp_path):
        root = self._enforcer_tree(tmp_path)
        (root / "_graph" / "cache" / "delegation-fitness.json").write_text(
            json.dumps({"scores": {"exploration": {"exploration": 0.8136}}}))
        sid = f"test-{uuid.uuid4().hex[:12]}"
        try:
            out = self._fire(root, sid)
            ctx = out["hookSpecificOutput"]["additionalContext"]
            assert "fitness" in ctx.lower()
            assert "0.8136" in ctx
        finally:
            pending_marker(sid).unlink(missing_ok=True)

    def test_no_hint_for_foreign_entity_keys(self, tmp_path):
        # Only the exact (task_type, task_type) cell is surfaced; a foreign
        # entity key must produce no hint rather than a guessed score.
        root = self._enforcer_tree(tmp_path)
        (root / "_graph" / "cache" / "delegation-fitness.json").write_text(
            json.dumps({"scores": {"exploration": {"some-agent": 0.9}}}))
        sid = f"test-{uuid.uuid4().hex[:12]}"
        try:
            out = self._fire(root, sid)
            ctx = out["hookSpecificOutput"]["additionalContext"]
            assert "fitness" not in ctx.lower()
        finally:
            pending_marker(sid).unlink(missing_ok=True)

    def test_no_hint_when_cache_absent(self, tmp_path):
        root = self._enforcer_tree(tmp_path)
        sid = f"test-{uuid.uuid4().hex[:12]}"
        try:
            out = self._fire(root, sid)
            ctx = out["hookSpecificOutput"]["additionalContext"]
            assert "DELEGATION SUGGESTED" in ctx
            assert "fitness" not in ctx.lower()
        finally:
            pending_marker(sid).unlink(missing_ok=True)
