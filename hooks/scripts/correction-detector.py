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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))
from common import (
    write_sentinel, is_tier_active,
    create_experience, read_hook_input
)
from correction_patterns import (
    calculate_confidence,
    detect_patterns,
    should_create_experience,
)


def main():
    try:
        # Tier gate
        if not is_tier_active(2):
            write_sentinel("correction-detector", "skip-tier")
            sys.exit(0)

        hook_input = read_hook_input()
        user_input = hook_input.get("content", hook_input.get("message", ""))

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
        if should_create_experience(user_input, patterns):
            pattern_names = [pattern.type.replace("_", " ") for pattern in patterns]
            create_experience(
                summary=f"User correction [{category}]: {user_input[:80].strip()}",
                exp_type="gotcha",
                tags=["correction", "auto-logged", category] + [pattern.type.replace("_", "-") for pattern in patterns[:2]],
                problem="Claude made an error that the user corrected",
                solution=user_input[:200],
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
