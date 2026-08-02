#!/bin/bash
# Session Summary - Stop hook (Evolving Lite)
# Creates session summary when meaningful work was done.
# Tier 2: Only active from session 3+.
# Bash 3 compatible. No jq dependency.
#
# Anti-spam: Max 1 summary per 30 minutes.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
session_id="${CLAUDE_SESSION_ID:-$$}"

# Shared temp namespace (see common.py evolving_tmp_dir): bash `/tmp` and
# Python's tempfile.gettempdir() can diverge on Windows, so both pin here.
RUNTIME_DIR="${EVOLVING_TMP:-${PLUGIN_ROOT}/_runtime}"
mkdir -p "$RUNTIME_DIR" 2>/dev/null

_write_sentinel() {
  local status="$1"
  echo "{\"hook\":\"session-summary\",\"ts\":$(date +%s),\"status\":\"${status}\"}" > "${RUNTIME_DIR}/evolving-lite-sentinel-session-summary-${session_id}.json" 2>/dev/null
}

counter_file="${PLUGIN_ROOT}/_memory/.session-count"
session_count=0
if [ -f "$counter_file" ]; then
  session_count=$(cat "$counter_file" 2>/dev/null | tr -cd '0-9')
  [ -z "$session_count" ] && session_count=0
fi

# Tier 2 gate: skip if < 3 sessions
if [ "$session_count" -lt 3 ]; then
  _write_sentinel "skip-tier"
  exit 0
fi

# Drain stdin. This hook does not use the payload, but Claude Code writes it to
# our stdin regardless, and leaving it unread risks SIGPIPE on the writer. The
# value is deliberately discarded - do not reintroduce an unused variable here.
cat >/dev/null 2>&1 || true

sessions_dir="${PLUGIN_ROOT}/_memory/sessions"
mkdir -p "$sessions_dir"

# Anti-spam: Check last summary time
last_summary=""
if [ -d "$sessions_dir" ]; then
  last_summary=$(ls -1t "$sessions_dir"/session-*.md 2>/dev/null | head -1) || true
fi

if [ -n "$last_summary" ]; then
  # Get modification time (macOS stat -f, Linux stat -c)
  last_time=$(stat -f %m "$last_summary" 2>/dev/null || stat -c %Y "$last_summary" 2>/dev/null) || last_time=0
  now=$(date +%s)
  diff=$((now - last_time))

  if [ "$diff" -lt 1800 ]; then
    _write_sentinel "skip-antispam"
    exit 0
  fi
fi

# Generate summary
timestamp=$(date +%Y-%m-%d-%H%M%S)
date_readable=$(date +"%Y-%m-%d %H:%M:%S")
summary_file="${sessions_dir}/session-${timestamp}.md"

cat > "$summary_file" << SUMMARY
# Session Summary - ${date_readable}

Session: ${session_count}

## Work Done
(Auto-generated placeholder - Claude will fill this with actual session content)

## Status
- Session ended normally

## Next Steps
(To be filled by system-boot on next session start)
SUMMARY

_write_sentinel "ok"
exit 0
