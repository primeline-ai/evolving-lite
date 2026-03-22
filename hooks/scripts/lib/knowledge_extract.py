#!/usr/bin/env python3
"""
Shared transcript extraction helpers for durable decisions and solutions.
"""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class KnowledgeFinding:
    type: str
    content: str


EXTRACTION_PATTERNS = {
    "decision": [
        r"(?:decided|decision|chose|choice|picked|selected)\s+(?:to\s+)?([^\n.!?]{20,120})",
        r"(?:entschieden|entscheidung|gewählt)\s+([^\n.!?]{20,120})",
        r"(?:we(?:'ll| will) (?:use|go with|implement))\s+([^\n.!?]{20,100})",
    ],
    "solution": [
        r"(?:fix(?:ed)?|solved|resolved|solution)\s*(?::|was|is)?\s*([^\n.!?]{20,120})",
        r"(?:the (?:issue|problem|bug) was)\s+([^\n.!?]{20,120})",
        r"(?:root cause)\s*(?::|was|is)?\s*([^\n.!?]{20,120})",
    ],
    "pattern": [
        r"(?:pattern|approach|strategy|technique)\s*(?::|is|was)?\s*([^\n.!?]{20,120})",
        r"(?:always|never|rule of thumb)\s*(?::)?\s*([^\n.!?]{20,120})",
        r"(?:best practice|lesson learned)\s*(?::)?\s*([^\n.!?]{20,120})",
    ],
}


def extract_knowledge(text: str, max_findings: int = 5) -> list[KnowledgeFinding]:
    """Extract reusable knowledge markers from a transcript."""
    findings: list[KnowledgeFinding] = []

    for knowledge_type, patterns in EXTRACTION_PATTERNS.items():
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                clean = match.strip().rstrip(".")
                if len(clean) >= 20:
                    findings.append(KnowledgeFinding(type=knowledge_type, content=clean))

    seen: set[str] = set()
    unique: list[KnowledgeFinding] = []
    for finding in findings:
        dedupe_key = finding.content[:50].lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        unique.append(finding)

    return unique[:max_findings]
