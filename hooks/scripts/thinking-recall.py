#!/usr/bin/env python3
"""
Thinking Recall - PreToolUse hook for mid-stream memory injection.
Adapted from Evolving (1058 lines -> ~150 lines).

Reads Claude's reasoning context, extracts keywords, matches against
stored experiences, and injects relevant memories.

Tier 3: Only active from session 10+.
Fail-open: any exception = silent exit 0, never blocks.

Removed from Evolving version:
- Context-router integration (uses simple keyword matching)
- Analytics logging to JSONL
- Cache directory management
- Domain boost words (Evolving-specific)
- Tool call counter (simplified)
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))
from common import (
    PLUGIN_ROOT, EXPERIENCES_DIR, PREWARMED_DIR,
    write_sentinel, is_tier_active, read_hook_input, safe_read_json
)
from codex_bridge import sync_codex_memory

STOP_WORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "can", "this", "that", "these",
    "those", "you", "he", "she", "it", "we", "they", "what", "which",
    "who", "when", "where", "why", "how", "all", "each", "every", "some",
    "not", "only", "so", "than", "very", "just", "also", "now", "if",
    "use", "using", "used", "file", "files", "code", "let", "me",
    "need", "want", "like", "make", "get", "set", "new", "about",
    "der", "die", "das", "ein", "eine", "und", "oder", "aber",
    "ist", "sind", "war", "nicht", "nur", "auch", "noch", "schon",
    "ich", "du", "er", "sie", "es", "wir", "was", "wie", "wo",
}

MIN_KEYWORD_LENGTH = 3
MIN_EXPERIENCES = 5  # Don't scan if too few experiences
MAX_INJECTIONS = 2   # Max experiences to inject per tool call
CONFIDENCE_THRESHOLD = 0.5


def extract_keywords(text: str) -> set:
    """Extract meaningful keywords from text."""
    words = re.findall(r'\b[\w\u00C0-\u024F]+\b', text.lower())
    return {w for w in words if len(w) >= MIN_KEYWORD_LENGTH and w not in STOP_WORDS}


def load_experiences() -> list:
    """Load all experience files (user + prewarmed)."""
    experiences = []

    for directory in [EXPERIENCES_DIR, PREWARMED_DIR]:
        if not directory.exists():
            continue
        for exp_file in directory.glob("exp-*.json"):
            data = safe_read_json(exp_file)
            if data and data.get("confidence", 0) >= CONFIDENCE_THRESHOLD:
                data["_file"] = str(exp_file)
                experiences.append(data)

    return experiences


def match_experiences(keywords: set, experiences: list) -> list:
    """Find experiences matching the current keywords."""
    matches = []

    for exp in experiences:
        # Build searchable text from experience
        search_text = " ".join([
            exp.get("summary", ""),
            exp.get("problem", ""),
            exp.get("solution", ""),
            " ".join(exp.get("tags", []))
        ]).lower()

        exp_words = set(re.findall(r'\b[\w\u00C0-\u024F]+\b', search_text))
        overlap = keywords & exp_words

        if len(overlap) >= 2:  # At least 2 keyword matches
            score = len(overlap) * exp.get("confidence", 0.5)
            matches.append({
                "experience": exp,
                "score": score,
                "matched_keywords": list(overlap)[:5]
            })

    # Sort by score, return top N
    matches.sort(key=lambda x: x["score"], reverse=True)
    return matches[:MAX_INJECTIONS]


def format_injection(matches: list) -> str:
    """Format matched experiences for context injection."""
    parts = ["MEMORY RECALL (from past experiences):"]

    for m in matches:
        exp = m["experience"]
        summary = exp.get("summary", "")
        solution = exp.get("solution", "")
        keywords = ", ".join(m["matched_keywords"])

        parts.append(f"- {summary}")
        if solution:
            parts.append(f"  Solution: {solution[:150]}")
        parts.append(f"  (matched: {keywords})")

    return "\n".join(parts)


def main():
    try:
        # Tier gate
        if not is_tier_active(3):
            write_sentinel("thinking-recall", "skip-tier")
            sys.exit(0)

        hook_input = read_hook_input()

        # Extract keywords from tool input context
        tool_input = hook_input.get("tool_input", {})
        context_text = json.dumps(tool_input) if tool_input else ""

        if len(context_text) < 20:
            write_sentinel("thinking-recall", "skip-short")
            sys.exit(0)

        # Pull in fresh Codex learnings before we search the shared memory pool.
        sync_codex_memory(force=False)

        # Load experiences
        experiences = load_experiences()
        if len(experiences) < MIN_EXPERIENCES:
            write_sentinel("thinking-recall", "skip-few-exp")
            sys.exit(0)

        # Extract and match
        keywords = extract_keywords(context_text)
        if len(keywords) < 2:
            write_sentinel("thinking-recall", "skip-few-kw")
            sys.exit(0)

        matches = match_experiences(keywords, experiences)
        if not matches:
            write_sentinel("thinking-recall", "no-match")
            sys.exit(0)

        # Inject
        injection = format_injection(matches)
        print(json.dumps({
            "hookSpecificOutput": {
                "additionalContext": injection
            }
        }))

        # Update access count on matched experiences
        for m in matches:
            exp = m["experience"]
            exp_file = Path(exp.get("_file", ""))
            if exp_file.exists():
                data = safe_read_json(exp_file)
                if data:
                    data["access_count"] = data.get("access_count", 0) + 1
                    data["last_accessed"] = datetime.now().isoformat()
                    from common import safe_write_json
                    safe_write_json(exp_file, data)

        write_sentinel("thinking-recall", "injected")

    except Exception:
        # Fail-open: never block tool execution
        write_sentinel("thinking-recall", "error")

    sys.exit(0)


if __name__ == "__main__":
    main()
