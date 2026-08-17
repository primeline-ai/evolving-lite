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


def _run_detector(root: Path, payload: dict) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(root / "hooks" / "scripts" / "correction-detector.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "CLAUDE_PLUGIN_ROOT": str(root),
            "EVOLVING_TMP": str(root / "_runtime"),
        },
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


def test_redactor_is_the_scanner_s_own_pattern_list(tmp_path):
    """One source of truth. If someone hand-copies the patterns into the
    detector, this stops pointing at the shared list and the two drift."""
    detector = (REPO / "hooks" / "scripts" / "correction-detector.py").read_text()
    assert "content-scanner.py" in detector
    assert "_SECRET_PATTERNS" not in detector, (
        "credential patterns must live in content-scanner.py only"
    )


# --------------------------------------------------------------------------
# Health banner - the README quotes this verbatim
# --------------------------------------------------------------------------

def _run_sentinel(root: Path) -> dict:
    proc = subprocess.run(
        ["bash", str(root / "hooks" / "scripts" / "health-sentinel.sh")],
        input="", capture_output=True, text=True,
    )
    return json.loads(proc.stdout.strip().splitlines()[0])


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
