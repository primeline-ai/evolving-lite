#!/usr/bin/env python3
"""
Shared correction detection heuristics used by learning hooks and importers.
"""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class DetectedCorrection:
    type: str
    matched: str
    weight: float


PATTERNS = {
    "repeated_mistake": {
        "keywords": [
            "again you didn't", "you still didn't", "you keep",
            "still not doing", "again no", "again not", "same mistake",
            "schon wieder", "immer wieder", "immer noch nicht",
            "wieder nicht", "zum wiederholten"
        ],
        "weight": 0.95,
    },
    "explicit_negation": {
        "keywords": [
            "nein", "falsch", "incorrect", "that's not right",
            "das ist falsch", "that's wrong", "you're wrong",
            "not correct", "not right", "das stimmt nicht"
        ],
        "weight": 0.9,
    },
    "alternative": {
        "patterns": [
            r"(do|use|mach|nimm)\s+(instead|stattdessen)",
            r"nicht.*sondern", r"rather than", r"instead of"
        ],
        "weight": 0.85,
    },
    "wrong_assumption": {
        "patterns": [
            r"i (never|didn't) (say|ask)",
            r"ich (hab|habe) (nie|nicht) gesagt",
            r"where did you get", r"woher (hast|nimmst) du"
        ],
        "weight": 0.85,
    },
    "override": {
        "patterns": [
            r"ignore", r"forget", r"undo",
            r"stop that", r"lass das", r"vergiss"
        ],
        "weight": 0.8,
    },
    "too_much": {
        "keywords": [
            "too much", "too complex", "over-engineered", "overkill",
            "zu viel", "zu komplex", "i don't need that",
            "das brauche ich nicht"
        ],
        "weight": 0.75,
    },
    "clarification": {
        "keywords": [
            "i meant", "actually", "what i wanted",
            "ich meinte", "eigentlich", "was ich wollte"
        ],
        "weight": 0.7,
    },
    "preference_correction": {
        "keywords": [
            "i prefer", "rather", "please don't",
            "ich bevorzuge", "lieber", "bitte nicht"
        ],
        "weight": 0.65,
    },
}

CATEGORY_MAP = {
    "repeated_mistake": "repeated_behavior",
    "explicit_negation": "misunderstanding",
    "alternative": "scope",
    "wrong_assumption": "assumption",
    "override": "automation",
    "too_much": "over_engineering",
    "clarification": "misunderstanding",
    "preference_correction": "preference",
}


def detect_patterns(text: str) -> list[DetectedCorrection]:
    """Detect correction patterns in free-form user text."""
    detected: list[DetectedCorrection] = []
    text_lower = text.lower()

    for pattern_type, config in PATTERNS.items():
        for keyword in config.get("keywords", []):
            if keyword.lower() in text_lower:
                detected.append(
                    DetectedCorrection(
                        type=pattern_type,
                        matched=keyword,
                        weight=config["weight"],
                    )
                )
                break

        for pattern in config.get("patterns", []):
            if re.search(pattern, text_lower, re.IGNORECASE):
                detected.append(
                    DetectedCorrection(
                        type=pattern_type,
                        matched=pattern,
                        weight=config["weight"],
                    )
                )
                break

    return detected


def calculate_confidence(text: str, patterns: list[DetectedCorrection]) -> tuple[int, str | None]:
    """Score a possible correction and map it to a category."""
    if not patterns:
        return 0, None

    max_weight = max(pattern.weight for pattern in patterns)
    score = max_weight * 70

    if len(patterns) > 1:
        score += 10 * (len(patterns) - 1)
    if any(pattern.type == "repeated_mistake" for pattern in patterns):
        score += 15

    word_count = len(text.split())
    if word_count < 2:
        score -= 15
    elif word_count > 10:
        score += 10

    category = CATEGORY_MAP.get(patterns[0].type, "misunderstanding")
    return min(100, max(0, int(score))), category


def should_create_experience(text: str, patterns: list[DetectedCorrection]) -> bool:
    """Only keep corrections that are likely to matter later."""
    if not patterns:
        return False
    if len(patterns) >= 2:
        return True
    if any(pattern.type == "repeated_mistake" for pattern in patterns):
        return True
    return len(text.split()) > 20 and patterns[0].weight >= 0.85
