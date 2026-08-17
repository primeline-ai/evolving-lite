"""Correction detector + health banner: pin the LIVE hook path.

Why these tests exist, and why they subprocess the hook instead of importing it:

The detector read `hook_input.get("content", hook_input.get("message", ""))`.
Claude Code's UserPromptSubmit payload carries the text in `prompt`. So the
hook exited at `skip-short` on every real turn and never once created an
experience in production - while a unit test that imports `detect_patterns()`
and feeds it a bare string passes happily, because the string never travels
through the payload at all.

That is the whole lesson: a matcher test cannot see a wiring bug. Every test
below runs the real script with a real stdin payload in an isolated plugin
root, so the field name, the tier gate and the file write are all on the path
under test.

The sibling hook `delegation-enforcer.py` already read `prompt` first, with a
comment naming the correct shape. The repo knew; this hook did not.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DETECTOR = REPO / "hooks" / "scripts" / "correction-detector.py"
SENTINEL = REPO / "hooks" / "scripts" / "health-sentinel.sh"
PREWARMED_SRC = REPO / "_memory" / "experiences" / "_prewarmed"

# Measured on both detector forks, 2026-08-17: two patterns
# (repeated_mistake "you keep" + override /forget/), confidence 91.
CORRECTION = "You keep forgetting to check tsconfig first"

# Scores 0 patterns on both forks - this was the shipped demo string.
NON_CORRECTION = "No, check tsconfig first"


def _plugin_root(tmp_path: Path, session_count: int) -> Path:
    """An isolated plugin root: hooks, manifest, seeds, session counter."""
    root = tmp_path / "plugin"
    (root / "_memory" / "experiences").mkdir(parents=True)
    (root / "_runtime").mkdir()
    (root / ".claude-plugin").mkdir()

    # Hooks are copied so PLUGIN_ROOT resolution (script location, two levels
    # up) lands inside the sandbox rather than the real checkout.
    import shutil
    shutil.copytree(REPO / "hooks", root / "hooks")
    shutil.copy(REPO / ".claude-plugin" / "plugin.json", root / ".claude-plugin")
    if PREWARMED_SRC.exists():
        shutil.copytree(PREWARMED_SRC, root / "_memory" / "experiences" / "_prewarmed")

    (root / "_memory" / ".session-count").write_text(str(session_count))
    return root


def _sandbox_env(root: Path) -> dict:
    """Inherit the real environment, then point the hook at the sandbox.

    Replacing the environment wholesale with a POSIX `PATH` left the child with
    no `SystemRoot`/`COMSPEC`/`TEMP` on Windows, so the interpreter could not
    start at all - green on macOS and Linux, red on both Windows legs. Every
    other subprocess helper in this repo builds `dict(os.environ)` first.

    EVOLVING_TMP is not optional here either: `health-sentinel.sh` runs
    `find "$RUNTIME_DIR" -type f -mtime +7 -delete` on whatever that resolves
    to, so an unset value would let a test prune a developer's real runtime
    files.
    """
    env = dict(os.environ)
    env["CLAUDE_PLUGIN_ROOT"] = str(root)
    env["EVOLVING_TMP"] = str(root / "_runtime")
    env.pop("CLAUDE_SESSION_ID", None)  # sentinel filenames must not collide
    return env


def _run_detector(root: Path, payload: dict) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(root / "hooks" / "scripts" / "correction-detector.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
        env=_sandbox_env(root),
    )
    return proc.returncode, proc.stdout


def _sentinel_status(root: Path) -> str | None:
    """The status the hook recorded for its own exit path.

    Needed because several distinct outcomes are indistinguishable from the
    outside: a clean guarded skip and an unhandled exception BOTH exit 0, write
    no file and print nothing. Asserting only on those three would pass whether
    or not the guard exists - which is exactly what a mutation run caught this
    test doing.
    """
    files = list((root / "_runtime").glob("evolving-lite-sentinel-correction-detector-*.json"))
    if not files:
        return None
    newest = max(files, key=lambda p: p.stat().st_mtime)
    return json.loads(newest.read_text()).get("status")


def _saved(root: Path) -> list[Path]:
    """User-created experiences only - seeds live one level down in _prewarmed."""
    return sorted((root / "_memory" / "experiences").glob("exp-*.json"))


# --------------------------------------------------------------------------
# The regression this file exists for
# --------------------------------------------------------------------------

def test_real_cc_payload_creates_experience(tmp_path):
    """`{"prompt": ...}` is what Claude Code actually sends. It must save.

    Before the fix this asserted 0 files: the hook read `content`, found
    nothing, and exited at skip-short.
    """
    root = _plugin_root(tmp_path, session_count=5)
    code, stdout = _run_detector(root, {"prompt": CORRECTION})

    assert code == 0
    assert len(_saved(root)) == 1, (
        "UserPromptSubmit sends the text in 'prompt'. If this is 0, the hook "
        "is reading a field Claude Code does not send and is a no-op in "
        "production, exactly as it was before 2026-08-17."
    )
    assert "CORRECTION DETECTED" in stdout

    body = json.loads(_saved(root)[0].read_text())
    assert CORRECTION in body["solution"]
    assert body["confidence"] == pytest.approx(0.91)


def test_legacy_content_payload_still_creates_experience(tmp_path):
    """`content` stays a fallback - the fix adds `prompt`, it does not swap."""
    root = _plugin_root(tmp_path, session_count=5)
    _run_detector(root, {"content": CORRECTION})
    assert len(_saved(root)) == 1


def test_legacy_message_payload_still_creates_experience(tmp_path):
    root = _plugin_root(tmp_path, session_count=5)
    _run_detector(root, {"message": CORRECTION})
    assert len(_saved(root)) == 1


def test_prompt_wins_over_stale_content_key(tmp_path):
    """If both arrive, the live field decides - not the legacy one."""
    root = _plugin_root(tmp_path, session_count=5)
    _run_detector(root, {"prompt": CORRECTION, "content": NON_CORRECTION})
    saved = _saved(root)
    assert len(saved) == 1
    assert CORRECTION in json.loads(saved[0].read_text())["solution"]


# --------------------------------------------------------------------------
# Negative controls - a test that only ever asserts "saved" cannot fail
# --------------------------------------------------------------------------

def test_non_correction_saves_nothing(tmp_path):
    """The old README demo string. 0 patterns, so nothing is stored."""
    root = _plugin_root(tmp_path, session_count=5)
    code, stdout = _run_detector(root, {"prompt": NON_CORRECTION})
    assert code == 0
    assert _saved(root) == []
    assert stdout.strip() == ""


def test_tier_1_session_saves_nothing(tmp_path):
    """Capture is Tier 2, session 3+. Sessions 1-2 store nothing at all."""
    root = _plugin_root(tmp_path, session_count=1)
    _run_detector(root, {"prompt": CORRECTION})
    assert _saved(root) == [], "Tier gate must still hold after the field fix"


def test_tier_boundary_is_session_three(tmp_path):
    root_two = _plugin_root(tmp_path / "a", session_count=2)
    _run_detector(root_two, {"prompt": CORRECTION})
    assert _saved(root_two) == []

    root_three = _plugin_root(tmp_path / "b", session_count=3)
    _run_detector(root_three, {"prompt": CORRECTION})
    assert len(_saved(root_three)) == 1


def test_empty_payload_is_survivable(tmp_path):
    root = _plugin_root(tmp_path, session_count=5)
    code, _ = _run_detector(root, {})
    assert code == 0
    assert _saved(root) == []


@pytest.mark.parametrize("bad", [True, 42, ["a", "b"], {"k": "v"}, 3.14])
def test_non_string_prompt_is_survivable(tmp_path, bad):
    """A truthy non-string reaches len() and raises.

    The blanket `except Exception` would swallow that into a silent "error"
    sentinel - a second way for this hook to be invisibly dead, which is the
    exact class it was just fixed for. Guarded explicitly instead.
    """
    root = _plugin_root(tmp_path / str(abs(hash(str(bad)))), session_count=5)
    code, stdout = _run_detector(root, {"prompt": bad})
    assert code == 0
    assert _saved(root) == []
    assert stdout.strip() == ""
    # The discriminator. Without the guard the TypeError from len() is swallowed
    # by the blanket except and the status reads "error" - same exit code, same
    # empty stdout, same absent file. Only the sentinel tells the two apart.
    assert _sentinel_status(root) == "skip-nonstring"


# --------------------------------------------------------------------------
# Secret redaction
#
# Fixing the payload field turned a dormant no-op into a live persistence path
# for the user's own prompt text. README.md promises "No secrets stored", and
# content-scanner only ever ran on WebFetch/firecrawl results, so nothing stood
# between a pasted credential and _memory/experiences/. These pin that it does
# now - and, just as importantly, that the hook refuses to write at all if the
# redactor cannot be loaded.
# --------------------------------------------------------------------------

# Assembled from fragments so this source carries no verbatim credential token,
# matching the convention in content-scanner.py and test_security.py.
_j = lambda *p: "".join(p)
_FAKE_KEY = _j("sk", "-", "live", "51H8xQ2eZvKYlo2C0", "FAKEexample", "0000")
_FAKE_PW = _j("hunter2", "correct", "horse", "battery")
_FAKE_JWT = (_j("ey", "JhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9")
             + ".eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5NabcdEFGH")

# 30 words, so it clears the >20-word bar with a single 0.85 pattern. Note this
# is an ORDINARY instruction, not a correction - which is the point: the gate is
# loose enough that everyday prompts reach the writer.
SECRET_PROMPT = (
    f"Please put STRIPE_SECRET_KEY={_FAKE_KEY} and DB_PASSWORD={_FAKE_PW} into "
    "the deployment env file instead of hardcoding either of them inside the "
    "config module, and make sure the staging box gets the same values too"
)


def test_secret_never_reaches_disk(tmp_path):
    root = _plugin_root(tmp_path, session_count=5)
    _run_detector(root, {"prompt": SECRET_PROMPT})

    saved = _saved(root)
    assert len(saved) == 1, "precondition: this prompt must reach the writer"

    blob = saved[0].read_text()
    assert _FAKE_KEY not in blob, "API key persisted verbatim"
    assert _FAKE_PW not in blob, "password persisted verbatim"
    assert "[REDACTED:" in blob
    assert "redacted" in json.loads(blob)["tags"]


def test_secret_never_reaches_the_analytics_log(tmp_path):
    """create_experience() also appends summary[:80] to evolution-log.jsonl.

    Redacting only the experience file would leave the same credential in a
    second sink - the exact 'I fixed the unit I edited' shape.
    """
    root = _plugin_root(tmp_path, session_count=5)
    _run_detector(root, {"prompt": SECRET_PROMPT})

    log = root / "_memory" / "analytics" / "evolution-log.jsonl"
    if log.exists():
        text = log.read_text()
        assert _FAKE_KEY not in text
        assert _FAKE_PW not in text


def test_redaction_preserves_the_surrounding_correction(tmp_path):
    """Redaction must remove the credential, not gut the note."""
    root = _plugin_root(tmp_path, session_count=5)
    _run_detector(root, {"prompt": SECRET_PROMPT})

    body = json.loads(_saved(root)[0].read_text())
    assert "deployment env file" in body["solution"]


def test_ordinary_correction_is_stored_unmodified(tmp_path):
    """Negative control: no secret means no redaction, no tag, text intact.

    Without this, a redactor that blanked every prompt would pass the tests above.
    """
    root = _plugin_root(tmp_path, session_count=5)
    _run_detector(root, {"prompt": CORRECTION})

    body = json.loads(_saved(root)[0].read_text())
    assert body["solution"] == CORRECTION
    assert "[REDACTED:" not in body["solution"]
    assert "redacted" not in body["tags"]


def test_write_is_refused_when_the_redactor_is_missing(tmp_path):
    """Fail CLOSED. No redactor means no write - never an unredacted write.

    Everything else in this hook fails open. This one path must not, because
    failing open here means "the safety net is gone, so store the credential
    anyway". The user's turn is still never blocked: exit code stays 0.
    """
    root = _plugin_root(tmp_path, session_count=5)
    scanner = root / "hooks" / "scripts" / "content-scanner.py"
    scanner.rename(scanner.with_suffix(".py.disabled"))

    code, stdout = _run_detector(root, {"prompt": SECRET_PROMPT})

    assert code == 0, "must not block the user's turn"
    assert _saved(root) == [], "wrote an experience with no redactor available"
    assert stdout.strip() == ""
    assert _sentinel_status(root) == "skip-no-redactor"


def test_sentinel_reader_sees_a_healthy_run(tmp_path):
    """Positive control for _sentinel_status().

    The two guard tests above assert a specific sentinel value. If the reader
    silently returned None for everything they would both pass vacuously, so
    prove the reader reports a real, different status on the happy path.
    """
    root = _plugin_root(tmp_path, session_count=5)
    _run_detector(root, {"prompt": CORRECTION})
    assert _sentinel_status(root) == "detected"


def test_the_shared_loader_points_at_the_scanner():
    """One source of truth for what counts as a secret.

    load_redactor() lives in lib/common.py because two hooks need it. If it
    stops importing content-scanner.py, or a hook hand-copies the pattern list,
    the two definitions drift - and the one that drifts is the one nobody tests.
    """
    common = (REPO / "hooks" / "scripts" / "lib" / "common.py").read_text()
    assert "content-scanner.py" in common
    assert "_SECRET_PATTERNS" not in common


# --------------------------------------------------------------------------
# Health banner - the README quotes this verbatim
# --------------------------------------------------------------------------

def _bash() -> str:
    """A bash that can actually run a script.

    On the GitHub Windows runner, plain `bash` resolves to the WSL stub, which
    prints "Windows Subsystem for Linux has no installed distributions" in
    UTF-16 and exits 1 - so the hook never runs and the test sees garbage where
    it expected JSON. The repo's own README already says the hooks need Git
    Bash; this is the same requirement, in the test harness.

    No other test in this suite shells out to bash yet, so this is the pattern
    for the ones that come after.
    """
    if os.name == "nt":
        for candidate in (
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
            shutil.which("bash.exe"),
        ):
            if candidate and Path(candidate).exists() and "System32" not in str(candidate):
                return str(candidate)
        pytest.skip("no Git Bash on this Windows host (plain `bash` is the WSL stub)")
    found = shutil.which("bash")
    if not found:
        pytest.skip("no bash on PATH")
    return found


def _run_sentinel(root: Path) -> dict:
    proc = subprocess.run(
        [_bash(), str(root / "hooks" / "scripts" / "health-sentinel.sh")],
        input="", capture_output=True, text=True, timeout=30,
        env=_sandbox_env(root),
    )
    out = proc.stdout.strip()
    assert out, f"health-sentinel produced no output (rc={proc.returncode}): {proc.stderr[:300]}"
    # Take the first line that actually parses, not blindly line 0. Git Bash on
    # Windows can put a line ahead of the hook's own output, and assuming the
    # JSON is first turned that into an opaque JSONDecodeError on two CI legs.
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    raise AssertionError(
        f"no JSON line in health-sentinel output (rc={proc.returncode}):\n"
        f"stdout={out[:400]!r}\nstderr={proc.stderr[:300]!r}"
    )


@pytest.mark.skipif(not PREWARMED_SRC.exists(), reason="no prewarmed seeds in tree")
def test_banner_counts_each_seed_once(tmp_path):
    """`find -name exp-*.json` is recursive and already matches exp-pw-*.json.

    A second prewarmed count added every seed twice, so a stock install
    reported 40 experiences while holding 20.
    """
    root = _plugin_root(tmp_path, session_count=0)
    on_disk = len(list((root / "_memory" / "experiences").rglob("exp-*.json")))

    msg = _run_sentinel(root)["systemMessage"]

    assert f"| {on_disk} experiences" in msg, (
        f"banner {msg!r} disagrees with {on_disk} files on disk"
    )


def test_banner_version_comes_from_the_manifest(tmp_path):
    """The banner hardcoded v1.0 while the manifest said 1.1.0."""
    manifest = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
    root = _plugin_root(tmp_path, session_count=0)

    msg = _run_sentinel(root)["systemMessage"]

    assert f"Evolving Lite v{manifest['version']} " in msg, (
        f"banner {msg!r} does not carry manifest version {manifest['version']}"
    )


# --------------------------------------------------------------------------
# The SECOND writer of the raw prompt
#
# An internal review found that redacting correction-detector alone was not
# enough: delegation-enforcer runs on the same UserPromptSubmit event, reads the
# same `prompt` field, and wrote user_input[:100] verbatim into the pending
# marker - which the Stop hook drains into delegation-gaps.jsonl. Fixing the
# sink I happened to be editing would have left the credential in the other one.
# --------------------------------------------------------------------------

ENFORCER = REPO / "hooks" / "scripts" / "delegation-enforcer.py"


def test_delegation_marker_carries_no_credential(tmp_path):
    root = _plugin_root(tmp_path, session_count=5)
    runtime = root / "_runtime"

    # The prompt has to CLEAR the delegation threshold or no marker is written
    # and the test proves nothing. Measured: a prompt containing the words
    # "secret", "key" or "password" takes the -10 critical-keyword penalty and
    # scores below 3, so the obvious secret-bearing prompt never reaches this
    # writer. That penalty is a partial accident of a defence, not a design:
    # a credential with no such word in it - a bearer token, a JWT, a vendor
    # token - sails through. So this uses one, which is the case that leaks.
    prompt = (
        "Search the entire codebase and find every file where the header "
        f"Authorization: Bearer {_FAKE_JWT} appears so I can refactor them"
    )
    subprocess.run(
        [sys.executable, str(root / "hooks" / "scripts" / "delegation-enforcer.py")],
        input=json.dumps({"prompt": prompt, "session_id": "redaction-test"}),
        capture_output=True, text=True,
        timeout=30,
        env=_sandbox_env(root),
    )

    markers = list(runtime.glob("delegation-pending-*.json"))
    assert markers, (
        "no marker written - the prompt fell below the delegation threshold, so "
        "this test would have proven nothing. A skip here is not a pass."
    )

    blob = markers[0].read_text()
    assert _FAKE_JWT not in blob, "bearer token persisted verbatim in the delegation marker"
    assert "REDACTED" in blob, "marker was written but nothing was redacted"


def test_both_prompt_writers_use_the_shared_redactor():
    """Structural pin. If a third writer of the prompt appears, or one of these
    two stops redacting, this is the check that names it."""
    for path in (REPO / "hooks" / "scripts" / "correction-detector.py", ENFORCER):
        src = path.read_text()
        assert "load_redactor" in src, f"{path.name} persists the prompt without redacting"
        assert "_SECRET_PATTERNS" not in src, (
            f"{path.name} carries its own credential patterns - they belong in "
            "content-scanner.py only"
        )


# --------------------------------------------------------------------------
# Same-second id collision (#2591)
#
# The id was `exp-{%Y%m%d-%H%M%S}` with no collision handling, so two
# experiences created inside the same second landed on the same filename and the
# second silently replaced the first. Hit twice while running an EPT for the
# payload fix: two prompts back-to-back produced ONE file. Reachable in
# production now that the detector actually fires.
# --------------------------------------------------------------------------

SECOND_CORRECTION = "You keep forgetting to run the migrations before the tests"


def _assert_same_second(saved: list) -> None:
    """Prove the run actually exercised a collision.

    Every reviewer of this file made the same point independently: two
    subprocess launches can straddle a second boundary, and then they get
    DIFFERENT base ids and the assertion below holds for a naive
    check-then-write implementation too. A test that only probably tests the
    thing is not a test - so fail loudly when the window was missed, rather
    than passing for the wrong reason.
    """
    bases = {p.stem.split("-")[1] + p.stem.split("-")[2] for p in saved}
    assert len(bases) == 1, (
        f"the writes straddled a second boundary ({sorted(bases)}), so no "
        "collision was exercised - re-run; this is not a product failure"
    )


def test_two_corrections_in_the_same_second_both_survive(tmp_path):
    root = _plugin_root(tmp_path, session_count=5)

    _run_detector(root, {"prompt": CORRECTION})
    _run_detector(root, {"prompt": SECOND_CORRECTION})

    saved = _saved(root)
    _assert_same_second(saved)
    assert len(saved) == 2, (
        f"expected 2 experiences, found {len(saved)} - a same-second id "
        "collision overwrote one of them"
    )

    solutions = " ".join(json.loads(p.read_text())["solution"] for p in saved)
    assert CORRECTION in solutions, "the FIRST correction is the one that gets lost"
    assert SECOND_CORRECTION in solutions

    # The id inside the file must match its own filename, or every consumer that
    # joins the two disagrees.
    for path in saved:
        assert json.loads(path.read_text())["id"] == path.stem


def _load_common(root: Path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        f"common_{root.parent.name}", root / "hooks" / "scripts" / "lib" / "common.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_ten_in_the_same_second_all_survive(tmp_path, monkeypatch):
    """The suffix ladder has to keep counting, not just handle one duplicate.

    FROZEN CLOCK, not ten subprocess launches. The subprocess version of this
    test straddled a second boundary on its first run - which meant it had been
    asserting "10 files exist" while the writes were in DIFFERENT seconds, so it
    would have passed on an implementation with no collision handling at all.
    A reviewer predicted exactly this; the same-second assertion caught it.
    """
    root = _plugin_root(tmp_path, session_count=5)
    common = _load_common(root)

    class _Frozen:
        @staticmethod
        def now():
            import datetime as _dt
            return _dt.datetime(2026, 8, 17, 21, 30, 0)

    monkeypatch.setattr(common, "datetime", _Frozen)

    for i in range(10):
        assert common.create_experience(summary=f"note {i}", source="t") is not None

    saved = _saved(root)
    assert len(saved) == 10, f"suffix ladder stopped early: {[p.name for p in saved]}"
    names = {p.stem for p in saved}
    assert "exp-20260817-213000" in names, "the first of a second must be unsuffixed"
    assert "exp-20260817-213000-10" in names, "the ladder must reach 10"


def test_the_ladder_declines_past_the_cap(tmp_path, monkeypatch):
    """At the cap it must decline, not spin or overwrite."""
    root = _plugin_root(tmp_path, session_count=5)
    common = _load_common(root)

    class _Frozen:
        @staticmethod
        def now():
            import datetime as _dt
            return _dt.datetime(2026, 8, 17, 21, 31, 0)

    monkeypatch.setattr(common, "datetime", _Frozen)
    monkeypatch.setattr(common, "MAX_EXPERIENCES_PER_SECOND", 3)

    made = [common.create_experience(summary=f"n{i}", source="t") for i in range(5)]
    assert sum(x is not None for x in made) == 3
    assert made[3] is None and made[4] is None
    assert len(_saved(root)) == 3, "declining must not overwrite an earlier file"


def test_ids_are_unique_and_filenames_match(tmp_path):
    root = _plugin_root(tmp_path, session_count=5)
    for i in range(5):
        _run_detector(root, {"prompt": f"You keep forgetting to check thing {i} first"})
    ids = [json.loads(p.read_text())["id"] for p in _saved(root)]
    assert len(set(ids)) == len(ids), f"duplicate ids: {ids}"


def test_the_first_id_of_a_second_is_unsuffixed(tmp_path):
    """Backwards compatibility: a single write keeps the historical shape, so
    existing files and any human reading a directory listing still see
    `exp-YYYYmmdd-HHMMSS.json`."""
    root = _plugin_root(tmp_path, session_count=5)
    _run_detector(root, {"prompt": CORRECTION})
    name = _saved(root)[0].stem
    assert name.count("-") == 2, f"unexpected id shape for a lone write: {name}"


def test_claim_is_exclusive_across_processes(tmp_path):
    """The writers are separate PROCESSES, so a check-then-write cannot hold.

    Fires several detectors concurrently and asserts none of them lost a file to
    another's claim. A test-then-act implementation passes the sequential tests
    above and fails this one.
    """
    import concurrent.futures

    root = _plugin_root(tmp_path, session_count=5)
    prompts = [f"You keep forgetting to verify item {i} before shipping" for i in range(8)]

    # A start BARRIER, not just a pool: without it the 8 subprocesses each pay
    # interpreter startup and can drift across a second boundary, at which point
    # they take different base ids and a naive check-then-write implementation
    # passes too. Every reviewer raised this independently.
    import threading
    barrier = threading.Barrier(8)

    def _fire(prompt):
        barrier.wait(timeout=30)
        return _run_detector(root, {"prompt": prompt})

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_fire, prompts))

    saved = _saved(root)
    assert len(saved) == 8, f"concurrent writes lost {8 - len(saved)} experience(s)"
    _assert_same_second(saved)
    ids = [json.loads(p.read_text())["id"] for p in saved]
    assert len(set(ids)) == 8
    # No empty placeholders left behind by a claim whose write failed.
    assert all(p.stat().st_size > 0 for p in saved)


def test_a_failed_write_leaves_no_empty_experience_behind(tmp_path, monkeypatch):
    """The claim publishes a real, glob-visible exp-*.json before the content
    lands. All three reviewers converged on this: if anything between the claim
    and the write does not raise OSError - a kill inside the 10-15s hook
    timeout, or a UnicodeEncodeError that safe_write_json does not catch - the
    0-byte file stays forever. integrity-checker FAILs on it, health-sentinel
    counts it, and auto-archival can never reclaim it.

    Before the collision fix a failure left nothing at all, so this is a
    regression the fix itself introduced.
    """
    import importlib.util
    root = _plugin_root(tmp_path, session_count=5)

    spec = importlib.util.spec_from_file_location(
        "common_under_test", root / "hooks" / "scripts" / "lib" / "common.py"
    )
    common = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(common)

    def _boom(*_a, **_kw):
        raise UnicodeEncodeError("utf-8", "x", 0, 1, "surrogates not allowed")

    monkeypatch.setattr(common, "safe_write_json", _boom)

    with pytest.raises(UnicodeEncodeError):
        common.create_experience(summary="a correction", source="correction-detector")

    leftovers = list((root / "_memory" / "experiences").glob("exp-*.json"))
    assert leftovers == [], f"claim leaked a placeholder: {[p.name for p in leftovers]}"


def test_a_declined_claim_is_logged_not_silent(tmp_path):
    """Both decline paths return None and the caller discards it, so without a
    log line the experience vanishes while the user sees CORRECTION DETECTED
    and the sentinel reads healthy - the exact silent-loss class this fix is
    about, one level up."""
    import importlib.util
    root = _plugin_root(tmp_path, session_count=5)

    spec = importlib.util.spec_from_file_location(
        "common_decline", root / "hooks" / "scripts" / "lib" / "common.py"
    )
    common = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(common)

    monkeypatch_cap = common.MAX_EXPERIENCES_PER_SECOND
    common.MAX_EXPERIENCES_PER_SECOND = 1
    try:
        assert common.create_experience(summary="first", source="t") is not None
        assert common.create_experience(summary="second", source="t") is None
    finally:
        common.MAX_EXPERIENCES_PER_SECOND = monkeypatch_cap

    log = root / "_memory" / "analytics" / "evolution-log.jsonl"
    assert log.exists(), "no evolution log at all"
    assert "experience_dropped" in log.read_text(), "the drop was silent"
