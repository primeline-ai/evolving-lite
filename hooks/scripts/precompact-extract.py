#!/usr/bin/env python3
"""
Precompact Extract - PreCompact hook for knowledge rescue.
Adapted from Evolving (~150 lines -> ~100 lines).

Before context compaction wipes the conversation, scan the recent
transcript for decisions, solutions, and patterns worth saving.
Creates experience files from extracted knowledge.

Tier 3: Only active from session 10+.
Fail-open: never blocks compaction.

Removed from Evolving version:
- Kairn sync staging (writes directly to experiences)
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
from knowledge_extract import extract_knowledge


def main():
    try:
        # Tier gate
        if not is_tier_active(3):
            write_sentinel("precompact-extract", "skip-tier")
            sys.exit(0)

        hook_input = read_hook_input()

        # The PreCompact hook receives the conversation transcript
        transcript = hook_input.get("transcript", hook_input.get("content", ""))

        if not transcript or len(transcript) < 100:
            write_sentinel("precompact-extract", "skip-short")
            sys.exit(0)

        findings = extract_knowledge(transcript)

        if not findings:
            write_sentinel("precompact-extract", "no-findings")
            sys.exit(0)

        # Save each finding as an experience
        saved = 0
        for finding in findings:
            result = create_experience(
                summary=f"Pre-compaction extract [{finding.type}]: {finding.content[:80]}",
                exp_type=finding.type,
                tags=["precompact", "auto-extracted", finding.type],
                solution=finding.content,
                confidence=0.6,  # Lower confidence for auto-extracted
                source="precompact-extract"
            )
            if result:
                saved += 1

        write_sentinel("precompact-extract", f"saved-{saved}")

    except Exception:
        # Fail-open: NEVER block compaction
        write_sentinel("precompact-extract", "error")

    sys.exit(0)


if __name__ == "__main__":
    main()
