#!/usr/bin/env python3
"""
Correction Detector - UserPromptSubmit hook.
Adapted from Evolving (483 lines -> ~180 lines).

Detects user corrections in prompts and auto-creates experiences.
Tier 2: Only active from session 3+.

Removed from Evolving version:
- Kairn sync staging
- fcntl file locking (overkill for single-user)
- Experience index management (simplified)
- Active project lookup
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))
from common import (
    write_sentinel, is_tier_active,
    create_experience, read_hook_input, load_redactor
)

# Detection patterns with weights (all 8 from Evolving)
PATTERNS = {
    "repeated_mistake": {
        "keywords": [
            "again you didn't", "you still didn't", "you keep",
            "still not doing", "again no", "again not", "same mistake",
            "schon wieder", "immer wieder", "immer noch nicht",
            "wieder nicht", "zum wiederholten"
        ],
        "weight": 0.95
    },
    "explicit_negation": {
        "keywords": [
            "nein", "falsch", "incorrect", "that's not right",
            "das ist falsch", "that's wrong", "you're wrong",
            "not correct", "not right", "das stimmt nicht"
        ],
        "weight": 0.9
    },
    "alternative": {
        "patterns": [
            r"(do|use|mach|nimm)\s+(instead|stattdessen)",
            r"nicht.*sondern", r"rather than", r"instead of"
        ],
        "weight": 0.85
    },
    "wrong_assumption": {
        "patterns": [
            r"I (never|didn't) (say|ask)",
            r"ich (hab|habe) (nie|nicht) gesagt",
            r"where did you get", r"woher (hast|nimmst) du"
        ],
        "weight": 0.85
    },
    "override": {
        "patterns": [
            r"ignore", r"forget", r"undo",
            r"stop that", r"lass das", r"vergiss"
        ],
        "weight": 0.8
    },
    "too_much": {
        "keywords": [
            "too much", "too complex", "over-engineered", "overkill",
            "zu viel", "zu komplex", "I don't need that",
            "das brauche ich nicht"
        ],
        "weight": 0.75
    },
    "clarification": {
        "keywords": [
            "I meant", "actually", "what I wanted",
            "ich meinte", "eigentlich", "was ich wollte"
        ],
        "weight": 0.7
    },
    "preference_correction": {
        "keywords": [
            "I prefer", "rather", "please don't",
            "ich bevorzuge", "lieber", "bitte nicht"
        ],
        "weight": 0.65
    }
}

CATEGORY_MAP = {
    "repeated_mistake": "repeated_behavior",
    "explicit_negation": "misunderstanding",
    "alternative": "scope",
    "wrong_assumption": "assumption",
    "override": "automation",
    "too_much": "over_engineering",
    "clarification": "misunderstanding",
    "preference_correction": "preference"
}


def detect_patterns(text: str) -> list:
    """Detect correction patterns in user input."""
    detected = []
    text_lower = text.lower()

    for ptype, config in PATTERNS.items():
        if "keywords" in config:
            for kw in config["keywords"]:
                if kw.lower() in text_lower:
                    detected.append({"type": ptype, "matched": kw, "weight": config["weight"]})
                    break

        if "patterns" in config:
            for pat in config["patterns"]:
                if re.search(pat, text_lower, re.IGNORECASE):
                    detected.append({"type": ptype, "matched": pat, "weight": config["weight"]})
                    break

    return detected


def calculate_confidence(text: str, patterns: list) -> tuple:
    """Calculate confidence score. Returns (score, category)."""
    if not patterns:
        return 0, None

    max_weight = max(p["weight"] for p in patterns)
    score = max_weight * 70

    if len(patterns) > 1:
        score += 10 * (len(patterns) - 1)
    if any(p["type"] == "repeated_mistake" for p in patterns):
        score += 15

    word_count = len(text.split())
    if word_count < 2:
        score -= 15
    elif word_count > 10:
        score += 10

    category = CATEGORY_MAP.get(patterns[0]["type"], "misunderstanding")
    return min(100, max(0, int(score))), category


def should_create_experience(patterns: list, text: str) -> bool:
    """Only create experience for meaningful corrections."""
    if not patterns:
        return False
    if len(patterns) >= 2:
        return True
    if any(p["type"] == "repeated_mistake" for p in patterns):
        return True
    return len(text.split()) > 20 and patterns[0]["weight"] >= 0.85


def main():
    try:
        # Tier gate
        if not is_tier_active(2):
            write_sentinel("correction-detector", "skip-tier")
            sys.exit(0)

        hook_input = read_hook_input()
        # CC's UserPromptSubmit payload carries the text in "prompt";
        # "content"/"message" kept as fallbacks for older payload shapes.
        # Reading only content/message made this hook a no-op in production.
        user_input = (hook_input.get("prompt")
                      or hook_input.get("content")
                      or hook_input.get("message", ""))

        # A truthy non-string (True, 42, a list) reaches len() and raises, which
        # the blanket except below would swallow into a silent "error" sentinel -
        # a second way for this hook to be invisibly dead.
        if not isinstance(user_input, str):
            write_sentinel("correction-detector", "skip-nonstring")
            sys.exit(0)

        if not user_input or len(user_input) < 3:
            write_sentinel("correction-detector", "skip-short")
            sys.exit(0)

        patterns = detect_patterns(user_input)
        if not patterns:
            write_sentinel("correction-detector", "no-match")
            sys.exit(0)

        confidence, category = calculate_confidence(user_input, patterns)

        if confidence < 50:
            write_sentinel("correction-detector", "low-confidence")
            sys.exit(0)

        # Create experience if meaningful
        if should_create_experience(patterns, user_input):
            pattern_names = [p["type"].replace("_", " ") for p in patterns]

            # Redact BEFORE truncating. The stored text is the user's own prompt
            # verbatim, this file is the only thing between a pasted credential
            # and a permanent file on disk, and truncating first would leave the
            # tail of a key sitting in plain text inside the 200-char window.
            redact_secrets = load_redactor()
            if redact_secrets is None:
                write_sentinel("correction-detector", "skip-no-redactor")
                sys.exit(0)

            safe_input, redacted_ids = redact_secrets(user_input)
            tags = ["correction", "auto-logged", category] + [
                p["type"].replace("_", "-") for p in patterns[:2]
            ]
            if redacted_ids:
                tags.append("redacted")

            create_experience(
                summary=f"User correction [{category}]: {safe_input[:80].strip()}",
                exp_type="gotcha",
                tags=tags,
                problem="Claude made an error that the user corrected",
                solution=safe_input[:200],
                root_cause=f"Detected patterns: {', '.join(pattern_names)}",
                confidence=confidence / 100,
                source="correction-detector"
            )

        # Inform Claude about the correction
        msg = (
            f"CORRECTION DETECTED (confidence: {confidence}%, category: {category}). "
            f"Acknowledge the correction, adjust your approach, and do NOT repeat this mistake."
        )
        print(json.dumps({"systemMessage": msg, "continue": True}))

        write_sentinel("correction-detector", "detected")

    except Exception:
        write_sentinel("correction-detector", "error")

    sys.exit(0)


if __name__ == "__main__":
    main()
