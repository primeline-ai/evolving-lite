#!/usr/bin/env python3
"""
Delegation Enforcer - UserPromptSubmit hook.
Adapted from Evolving (1137 lines -> ~250 lines).

Calculates delegation score from user prompt keywords.
If score >= 3, suggests delegation to appropriate agent + model.

Tier 2: Only active from session 3+.

Removed from Evolving version:
- Team detection (lines 583-726)
- Gap tracking (lines 131-237)
- Ambiguous prompt detection (lines 389-435)
- Worktree isolation hints
- Trait fitness lookup
- Stop event handler
- Context persistence across prompts
"""

import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))
from common import (
    PLUGIN_ROOT, GRAPH_CACHE_DIR, write_sentinel,
    is_tier_active, read_hook_input, safe_read_json, load_redactor
)

MIN_PROMPT_LENGTH = 10
DELEGATION_THRESHOLD = 3

# Inline hints: [hint] or #hint -> task_type
INLINE_HINTS = {
    "explore": "exploration", "exp": "exploration", "find": "exploration",
    "debug": "debugging", "dbg": "debugging",
    "plan": "planning",
    "review": "code_review", "rev": "code_review",
    "sec": "security", "security": "security",
    "fix": "bug_fix", "bugfix": "bug_fix",
    "arch": "architecture", "design": "architecture",
    "research": "research", "learn": "research",
    "doc": "documentation", "docs": "documentation",
    "creative": "creative", "brainstorm": "creative",
}

# Score factors
POSITIVE_KEYWORDS = {
    "exploration": ["find", "search", "where", "grep", "locate", "list all", "show all",
                    "finde", "suche", "wo ist", "zeig alle"],
    "bulk": ["all files", "every", "batch", "bulk", "alle dateien", "jede"],
    "research": ["research", "investigate", "learn about", "deep dive",
                 "recherchiere", "untersuche"],
    "code_review": ["review", "check quality", "audit", "prüfe", "überprüfe"],
    "multi_file": ["across", "multiple files", "codebase", "repo", "projekt"],
    "independent": ["separately", "parallel", "independent", "unabhängig"],
}

CRITICAL_KEYWORDS = [
    "production", "deploy", "payment", "password", "secret",
    "credential", "api key", "delete all", "drop database", "rm -rf",
    "produktion", "passwort", "geheimnis"
]

USER_WANTS_TO_SEE = [
    "show me", "explain", "walk me through", "tell me",
    "zeig mir", "erkläre", "erklär mir"
]

# Destructive patterns - NEVER delegate
DESTRUCTIVE_PATTERNS = [
    r"delete\s+(all|every|.*\*)",
    r"rm\s+-rf",
    r"drop\s+(table|database)",
    r"overwrite\s+(all|every)",
    r"reset\s+--hard",
    r"force\s+push",
    r"truncate",
    r"destroy"
]


def extract_keywords(text: str) -> list:
    """Extract keywords from user prompt."""
    return re.findall(r'\b[\w\u00C0-\u024F]+\b', text.lower())


def extract_inline_hint(text: str) -> str | None:
    """Extract [hint] or #hint from prompt."""
    for pattern in [r'\[(\w+)\]', r'#(\w+)']:
        for match in re.findall(pattern, text.lower()):
            if match in INLINE_HINTS:
                return INLINE_HINTS[match]
    return None


def is_destructive(text: str) -> bool:
    """Check if prompt contains destructive patterns."""
    text_lower = text.lower()
    for pattern in DESTRUCTIVE_PATTERNS:
        if re.search(pattern, text_lower):
            return True
    return False


def calculate_score(text: str, keywords: list) -> tuple:
    """Calculate delegation score. Returns (score, matched_factors)."""
    text_lower = text.lower()
    score = 0
    factors = []

    # Positive factors
    for factor, kw_list in POSITIVE_KEYWORDS.items():
        if any(kw in text_lower for kw in kw_list):
            points = 3 if factor == "exploration" else 2
            score += points
            factors.append(f"+{points} {factor}")

    # Negative factors
    if any(kw in text_lower for kw in CRITICAL_KEYWORDS):
        score -= 10
        factors.append("-10 critical_keyword")

    if any(kw in text_lower for kw in USER_WANTS_TO_SEE):
        score -= 5
        factors.append("-5 user_wants_to_see")

    return score, factors


def determine_routing(text: str, keywords: list, config: dict) -> dict:
    """Determine agent type and model for delegation."""
    text_lower = text.lower()
    task_routing = config.get("task_type_routing", {})

    # Check inline hint first
    hint = extract_inline_hint(text)
    if hint and hint in task_routing:
        routing = task_routing[hint]
        return {"task_type": hint, "model": routing.get("model", "sonnet"),
                "effort": routing.get("effort", "medium"), "source": "inline_hint"}

    # Keyword-based detection
    if any(kw in text_lower for kw in ["find", "search", "grep", "where", "locate", "finde", "suche"]):
        return {"task_type": "exploration", "model": "haiku", "effort": "low", "source": "keyword"}
    if any(kw in text_lower for kw in ["debug", "error", "bug", "crash", "fehler"]):
        return {"task_type": "debugging", "model": "sonnet", "effort": "medium", "source": "keyword"}
    if any(kw in text_lower for kw in ["review", "audit", "quality", "prüfe"]):
        return {"task_type": "code_review", "model": "sonnet", "effort": "medium", "source": "keyword"}
    if any(kw in text_lower for kw in ["research", "investigate", "learn", "recherchiere"]):
        return {"task_type": "research", "model": "sonnet", "effort": "medium", "source": "keyword"}
    if any(kw in text_lower for kw in ["plan", "design", "architect", "struktur"]):
        return {"task_type": "planning", "model": "sonnet", "effort": "medium", "source": "keyword"}

    # Default
    return {"task_type": "general", "model": "sonnet", "effort": "medium", "source": "default"}


def lookup_fitness(task_type: str) -> float | None:
    """Read the per-task-type delegation fitness score from the recalc cache.

    delegation-fitness.json is produced by scripts/recalc-fitness.py from the
    cognitive-fitness ledger (which this hook's own pending markers feed via
    the delegation-outcome-tracker - the closed fitness loop). Fail-open:
    absent cache / absent task_type / malformed value -> None (no hint)."""
    try:
        cache = safe_read_json(GRAPH_CACHE_DIR / "delegation-fitness.json")
        entry = (cache.get("scores") or {}).get(task_type) or {}
        # This plugin's producer (delegation-outcome-tracker) always writes
        # entity == task_type, so only the exact cell is surfaced. Foreign
        # entity keys (other producers) get no hint rather than a guessed one.
        value = entry.get(task_type)
        if isinstance(value, (int, float)) and 0.0 <= float(value) <= 1.0:
            return float(value)
        return None
    except Exception:
        return None


def format_delegation_message(score: int, factors: list, routing: dict,
                              fitness: float | None = None) -> str:
    """Format the delegation suggestion message."""
    task_type = routing["task_type"]
    model = routing["model"]
    effort = routing["effort"]
    source = routing["source"]

    # Agent type mapping
    agent_map = {
        "exploration": "Explore agent (subagent_type='Explore')",
        "debugging": "debugger agent (subagent_type='debugger')",
        "planning": "Plan agent (subagent_type='Plan')",
    }
    agent_hint = agent_map.get(task_type, f"general-purpose agent (model='{model}')")

    msg = (
        f"DELEGATION SUGGESTED (score: {score}, factors: {', '.join(factors)}). "
        f"Task type: {task_type} ({source}). "
        f"Use: {agent_hint}. "
        f"Effort: {effort}."
    )

    if model == "haiku":
        msg += " Haiku is sufficient and 10x cheaper for this task."

    if fitness is not None:
        msg += (f" Historical delegation fitness for {task_type}: {fitness}"
                f" (1.0 = delegations consistently worked out).")

    return msg


def _redacted_description(user_input: str) -> str:
    """The prompt slice stored in the pending marker, credentials removed.

    Fails closed: if the shared redactor cannot be loaded we store a placeholder
    rather than the raw prompt. The marker itself is still written, because
    losing it loses the delegation outcome for this turn - only the free-text
    description is sacrificed.
    """
    redact = load_redactor()
    if redact is None:
        return "[description withheld: redactor unavailable]"
    try:
        return redact(user_input)[0][:100]
    except Exception:
        return "[description withheld: redaction failed]"


def write_pending_marker(hook_input: dict, score: int, routing: dict,
                         user_input: str) -> None:
    """Write the per-session pending marker the delegation-outcome-tracker
    consumes (PreToolUse marks it resolved; Stop drains it into
    delegation-gaps.jsonl). O_NOFOLLOW + 0600 against planted symlinks at
    the predictable /tmp path; fail-open."""
    try:
        session_id = (hook_input.get("session_id")
                      or os.environ.get("CLAUDE_SESSION_ID")
                      or f"pid-{os.getppid()}")
        marker = {
            "task_type": routing.get("task_type", "general"),
            "score": score,
            "effective_threshold": DELEGATION_THRESHOLD,
            # Redacted for the same reason correction-detector redacts: this is
            # the user's raw prompt, it lands in a file that survives the
            # session, and the Stop hook drains it into delegation-gaps.jsonl.
            # Redacting only the correction store would leave the credential
            # sitting in this one. No redactor -> no description, never a raw
            # one; the marker itself must still be written or the delegation
            # outcome for this turn is lost.
            "task_description": _redacted_description(user_input),
            "emit_ts": datetime.now(timezone.utc).isoformat(),
            "resolved": False,
        }
        # EVOLVING_TMP unifies the temp namespace across bash+Python hooks
        # (see common.py evolving_tmp_dir); default stays the OS tempdir. The
        # reader (delegation-outcome-tracker) resolves this identically.
        path = Path(os.environ.get("EVOLVING_TMP") or tempfile.gettempdir()) / f"delegation-pending-{session_id}.json"
        tmp = path.with_suffix(".json.tmp")
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


def main():
    try:
        # Tier gate
        if not is_tier_active(2):
            write_sentinel("delegation-enforcer", "skip-tier")
            sys.exit(0)

        hook_input = read_hook_input()
        # CC's UserPromptSubmit payload carries the text in "prompt";
        # "content"/"message" kept as fallbacks for older payload shapes.
        user_input = (hook_input.get("prompt")
                      or hook_input.get("content")
                      or hook_input.get("message", ""))

        if not user_input or len(user_input) < MIN_PROMPT_LENGTH:
            write_sentinel("delegation-enforcer", "skip-short")
            sys.exit(0)

        # Never delegate destructive operations
        if is_destructive(user_input):
            write_sentinel("delegation-enforcer", "skip-destructive")
            sys.exit(0)

        keywords = extract_keywords(user_input)
        score, factors = calculate_score(user_input, keywords)

        if score < DELEGATION_THRESHOLD:
            write_sentinel("delegation-enforcer", "below-threshold")
            sys.exit(0)

        # Load config for routing
        config = safe_read_json(GRAPH_CACHE_DIR / "delegation-config.json")
        routing = determine_routing(user_input, keywords, config)

        fitness = lookup_fitness(routing.get("task_type", "general"))
        msg = format_delegation_message(score, factors, routing, fitness)

        # Backward-signal producer: the delegation-outcome-tracker consumes
        # this marker at PreToolUse (resolved) + Stop (outcome row).
        write_pending_marker(hook_input, score, routing, user_input)

        # NOTE: no top-level "decision" key - that shape is rejected by the
        # hook schema; hookSpecificOutput alone is the valid form.
        output = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": msg
            }
        }
        print(json.dumps(output))

        write_sentinel("delegation-enforcer", "suggested")

    except Exception:
        write_sentinel("delegation-enforcer", "error")

    sys.exit(0)


if __name__ == "__main__":
    main()
