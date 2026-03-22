#!/usr/bin/env python3
"""
Codex bridge that imports durable learnings from local Codex session rollouts.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path

from common import ANALYTICS_DIR, create_experience, safe_read_json, safe_write_json
from correction_patterns import (
    calculate_confidence,
    detect_patterns,
    should_create_experience,
)
from knowledge_extract import extract_knowledge


IMPORT_STATE_FILE = ANALYTICS_DIR / "codex-import-state.json"
MAX_RECENT_ROLLOUTS = 12
SCAN_COOLDOWN_SECONDS = 90
MAX_FINGERPRINTS = 4000
NOISY_USER_PREFIXES = (
    "# agents.md instructions",
    "<turn_aborted>",
)
NOISY_FINDING_MARKERS = (
    "<image>",
    "](/",
    "```",
    "http://",
    "https://",
)


@dataclass(frozen=True)
class CodexMessage:
    role: str
    text: str
    phase: str | None = None


@dataclass(frozen=True)
class CodexRollout:
    path: Path
    thread_id: str
    messages: list[CodexMessage]


@dataclass(frozen=True)
class SyncStats:
    imported_count: int
    scanned_files: int
    skipped_cooldown: bool = False


def codex_import_enabled() -> bool:
    """Allow local opt-out without changing the plugin code."""
    raw = os.environ.get("EVOLVING_LITE_CODEX_IMPORT", "1").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _codex_home() -> Path:
    env_home = os.environ.get("CODEX_HOME")
    if env_home:
        return Path(env_home).expanduser()
    return Path.home() / ".codex"


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _extract_text_blocks(content: list[dict]) -> str:
    blocks: list[str] = []
    for item in content:
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            blocks.append(text)
    return _normalize_whitespace(" ".join(blocks))


def _rollout_files() -> list[Path]:
    codex_home = _codex_home()
    candidates: list[Path] = []

    sessions_dir = codex_home / "sessions"
    if sessions_dir.exists():
        candidates.extend(sessions_dir.rglob("rollout-*.jsonl"))

    archived_dir = codex_home / "archived_sessions"
    if archived_dir.exists():
        candidates.extend(archived_dir.glob("rollout-*.jsonl"))

    unique = {path.resolve(): path.resolve() for path in candidates}
    return sorted(unique.values(), key=lambda path: path.stat().st_mtime, reverse=True)[:MAX_RECENT_ROLLOUTS]


def _load_state() -> dict:
    state = safe_read_json(IMPORT_STATE_FILE, default={})
    if not isinstance(state, dict):
        return {}
    return state


def _save_state(state: dict) -> None:
    safe_write_json(IMPORT_STATE_FILE, state)


def _cooldown_expired(state: dict, cooldown_seconds: int) -> bool:
    if cooldown_seconds <= 0:
        return True
    last_scan_epoch = state.get("last_scan_epoch")
    if not isinstance(last_scan_epoch, (int, float)):
        return True
    return (_now_utc().timestamp() - float(last_scan_epoch)) >= cooldown_seconds


def _compact_fingerprints(fingerprints: list[str]) -> list[str]:
    if len(fingerprints) <= MAX_FINGERPRINTS:
        return fingerprints
    return fingerprints[-MAX_FINGERPRINTS:]


def _infer_thread_id(path: Path) -> str:
    match = re.search(r"([0-9a-f]{8}-[0-9a-f-]{27})", path.name)
    if match:
        return match.group(1)
    return path.stem


def _parse_rollout(path: Path) -> CodexRollout | None:
    messages: list[CodexMessage] = []
    thread_id = _infer_thread_id(path)

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        if entry.get("type") == "session_meta":
            payload = entry.get("payload", {})
            thread_id = payload.get("id", thread_id)
            continue

        if entry.get("type") != "response_item":
            continue

        payload = entry.get("payload", {})
        if payload.get("type") != "message":
            continue

        role = payload.get("role")
        if role not in {"user", "assistant"}:
            continue

        text = _extract_text_blocks(payload.get("content", []))
        if not text:
            continue

        messages.append(
            CodexMessage(
                role=role,
                text=text,
                phase=payload.get("phase"),
            )
        )

    if not messages:
        return None

    return CodexRollout(path=path, thread_id=thread_id, messages=messages)


def _message_is_noise(message: CodexMessage) -> bool:
    text_lower = message.text.lower()
    if message.role == "user" and text_lower.startswith(NOISY_USER_PREFIXES):
        return True
    if "<turn_aborted>" in text_lower:
        return True
    if message.role == "assistant" and message.phase == "commentary":
        return True
    return False


def _fingerprint(*parts: str) -> str:
    payload = "||".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _import_user_corrections(
    rollout: CodexRollout,
    seen_fingerprints: set[str],
    fingerprint_log: list[str],
) -> int:
    imported = 0

    for message in rollout.messages:
        if message.role != "user" or _message_is_noise(message):
            continue

        patterns = detect_patterns(message.text)
        confidence, category = calculate_confidence(message.text, patterns)
        if confidence < 50 or not should_create_experience(message.text, patterns):
            continue

        fingerprint = _fingerprint(
            rollout.thread_id,
            "correction",
            category or "unknown",
            message.text.lower(),
        )
        if fingerprint in seen_fingerprints:
            continue

        pattern_names = [pattern.type.replace("_", " ") for pattern in patterns]
        result = create_experience(
            summary=f"Codex correction [{category}]: {message.text[:80].strip()}",
            exp_type="gotcha",
            tags=[
                "codex",
                "imported",
                "correction",
                category or "misunderstanding",
            ] + [pattern.type.replace("_", "-") for pattern in patterns[:2]],
            problem="User corrected or redirected the assistant during a Codex session",
            solution=message.text[:200],
            root_cause=f"Detected patterns: {', '.join(pattern_names)}",
            confidence=min(0.95, confidence / 100),
            source=f"codex-import:{rollout.thread_id}",
        )
        if result:
            imported += 1
            seen_fingerprints.add(fingerprint)
            fingerprint_log.append(fingerprint)

    return imported


def _transcript_for_knowledge(rollout: CodexRollout) -> str:
    parts: list[str] = []
    for message in rollout.messages:
        if message.role != "assistant":
            continue
        if _message_is_noise(message):
            continue
        if len(message.text) < 12:
            continue
        if any(marker in message.text.lower() for marker in NOISY_FINDING_MARKERS):
            continue
        parts.append(f"{message.role.title()}: {message.text}")
    return "\n".join(parts)


def _finding_is_noise(content: str) -> bool:
    text_lower = content.lower()
    if any(marker in text_lower for marker in NOISY_FINDING_MARKERS):
        return True
    word_count = len(content.split())
    if word_count < 5 or word_count > 32:
        return True
    if content.count("/") > 6 or content.count("`") > 2:
        return True
    return False


def _import_knowledge_findings(
    rollout: CodexRollout,
    seen_fingerprints: set[str],
    fingerprint_log: list[str],
) -> int:
    transcript = _transcript_for_knowledge(rollout)
    if len(transcript) < 100:
        return 0

    imported = 0
    for finding in extract_knowledge(transcript, max_findings=3):
        if _finding_is_noise(finding.content):
            continue
        fingerprint = _fingerprint(
            rollout.thread_id,
            finding.type,
            finding.content.lower(),
        )
        if fingerprint in seen_fingerprints:
            continue

        result = create_experience(
            summary=f"Codex import [{finding.type}]: {finding.content[:80]}",
            exp_type=finding.type,
            tags=["codex", "imported", finding.type],
            solution=finding.content,
            confidence=0.6,
            source=f"codex-import:{rollout.thread_id}",
        )
        if result:
            imported += 1
            seen_fingerprints.add(fingerprint)
            fingerprint_log.append(fingerprint)

    return imported


def sync_codex_memory(force: bool = False, cooldown_seconds: int = SCAN_COOLDOWN_SECONDS) -> SyncStats:
    """Import durable learnings from recent Codex sessions into local memory."""
    if not codex_import_enabled():
        return SyncStats(imported_count=0, scanned_files=0)

    state = _load_state()
    if not force and not _cooldown_expired(state, cooldown_seconds):
        return SyncStats(imported_count=0, scanned_files=0, skipped_cooldown=True)

    known_files = state.get("files", {})
    if not isinstance(known_files, dict):
        known_files = {}

    seen_list = state.get("seen_fingerprints", [])
    if not isinstance(seen_list, list):
        seen_list = []

    seen_fingerprints = set(str(item) for item in seen_list)
    fingerprint_log = [str(item) for item in seen_list]

    imported_count = 0
    scanned_files = 0
    files_state: dict[str, dict] = {}

    for rollout_path in _rollout_files():
        path_key = str(rollout_path)
        try:
            mtime_ns = rollout_path.stat().st_mtime_ns
        except OSError:
            continue

        scanned_files += 1
        previous = known_files.get(path_key, {})
        files_state[path_key] = {"mtime_ns": mtime_ns}
        if previous.get("mtime_ns") == mtime_ns and not force:
            continue

        rollout = _parse_rollout(rollout_path)
        if rollout is None:
            continue

        imported_count += _import_user_corrections(rollout, seen_fingerprints, fingerprint_log)
        imported_count += _import_knowledge_findings(rollout, seen_fingerprints, fingerprint_log)

    state.update(
        {
            "last_scan_epoch": _now_utc().timestamp(),
            "last_scan_completed_at": _now_utc().isoformat(),
            "last_imported_count": imported_count,
            "files": files_state,
            "seen_fingerprints": _compact_fingerprints(fingerprint_log),
        }
    )
    _save_state(state)

    return SyncStats(imported_count=imported_count, scanned_files=scanned_files)
