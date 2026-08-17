#!/bin/bash
# Health Sentinel - SessionStart hook
# Checks plugin health, increments session counter, reports status

# Resolve plugin root from this script's location (scripts/ -> hooks/ -> root)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Validate plugin structure
if [[ ! -f "${PLUGIN_ROOT}/.claude-plugin/plugin.json" ]]; then
  echo '{"systemMessage": "WARNING: Evolving Lite plugin.json not found.", "continue": true}'
  exit 0
fi

# Version comes from the manifest, never a second hardcoded copy. The banner
# said v1.0 while plugin.json, the README badge and CHANGELOG all said 1.1.0.
version=$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
  "${PLUGIN_ROOT}/.claude-plugin/plugin.json" 2>/dev/null | head -1)
[[ -z "$version" ]] && version="unknown"

# Ensure memory directories exist
mkdir -p "${PLUGIN_ROOT}/_memory/experiences" "${PLUGIN_ROOT}/_memory/analytics" "${PLUGIN_ROOT}/_memory/sessions" "${PLUGIN_ROOT}/_memory/projects" 2>/dev/null

# Shared temp namespace (see common.py evolving_tmp_dir): bash `/tmp` and
# Python's tempfile.gettempdir() can diverge on Windows, so both pin here.
RUNTIME_DIR="${EVOLVING_TMP:-${PLUGIN_ROOT}/_runtime}"
mkdir -p "$RUNTIME_DIR" 2>/dev/null
# Bound growth: drop session-scoped runtime files older than a week.
find "$RUNTIME_DIR" -type f -mtime +7 -delete 2>/dev/null || true

# Session counter (ONLY place this is incremented)
session_id="${CLAUDE_SESSION_ID:-$$}"
flag_file="${RUNTIME_DIR}/evolving-lite-session-counted-${session_id}"
counter_file="${PLUGIN_ROOT}/_memory/.session-count"
session_count=0
if [[ -f "$counter_file" ]]; then
  session_count=$(cat "$counter_file" 2>/dev/null | tr -cd '0-9')
  [[ -z "$session_count" ]] && session_count=0
fi
if [[ ! -f "$flag_file" ]]; then
  session_count=$((session_count + 1))
  echo "$session_count" > "$counter_file"
  echo "$session_count" > "$flag_file"
fi

# Determine tier
tier=1
tier_label="Safety"
if [[ "$session_count" -ge 10 ]]; then
  tier=3
  tier_label="Deep"
elif [[ "$session_count" -ge 3 ]]; then
  tier=2
  tier_label="Learning"
fi

# Count experiences.
# The find below is recursive, so it already covers _prewarmed/exp-pw-*.json
# (they match exp-*.json). Adding a separate prewarmed count double-counted
# every seed and reported 40 on a stock install that holds 20.
total_exp=0
if [[ -d "${PLUGIN_ROOT}/_memory/experiences" ]]; then
  total_exp=$(find "${PLUGIN_ROOT}/_memory/experiences" -name "exp-*.json" 2>/dev/null | wc -l | tr -cd '0-9')
fi

# Write sentinel
sentinel_file="${RUNTIME_DIR}/evolving-lite-sentinel-health-${session_id}.json"
echo "{\"hook\":\"health-sentinel\",\"ts\":$(date +%s),\"status\":\"ok\",\"session\":\"${session_id}\"}" > "$sentinel_file" 2>/dev/null

echo "{\"systemMessage\": \"Evolving Lite v${version} | Session ${session_count} | Tier ${tier} (${tier_label}) | ${total_exp} experiences\", \"continue\": true}"
exit 0
