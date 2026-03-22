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

# Ensure memory directories exist
mkdir -p "${PLUGIN_ROOT}/_memory/experiences" "${PLUGIN_ROOT}/_memory/analytics" "${PLUGIN_ROOT}/_memory/sessions" "${PLUGIN_ROOT}/_memory/projects" 2>/dev/null

# Import recent Codex learnings into the same experience pool when available.
codex_imported=0
if command -v python3 >/dev/null 2>&1 && [[ -f "${PLUGIN_ROOT}/hooks/scripts/import-codex-memory.py" ]]; then
  codex_imported=$(python3 "${PLUGIN_ROOT}/hooks/scripts/import-codex-memory.py" 2>/dev/null | tr -cd '0-9')
  [[ -z "$codex_imported" ]] && codex_imported=0
fi

# Session counter (ONLY place this is incremented)
session_id="${CLAUDE_SESSION_ID:-$$}"
flag_file="/tmp/evolving-lite-session-counted-${session_id}"
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

# Count experiences
exp_count=0
if [[ -d "${PLUGIN_ROOT}/_memory/experiences" ]]; then
  exp_count=$(find "${PLUGIN_ROOT}/_memory/experiences" -name "exp-*.json" 2>/dev/null | wc -l | tr -cd '0-9')
fi
pw_count=0
if [[ -d "${PLUGIN_ROOT}/_memory/experiences/_prewarmed" ]]; then
  pw_count=$(find "${PLUGIN_ROOT}/_memory/experiences/_prewarmed" -name "exp-pw-*.json" 2>/dev/null | wc -l | tr -cd '0-9')
fi
total_exp=$((exp_count + pw_count))
import_suffix=""
if [[ "$codex_imported" -gt 0 ]]; then
  import_suffix=" | Codex +${codex_imported}"
fi

# Write sentinel
sentinel_file="/tmp/evolving-lite-sentinel-health-${session_id}.json"
echo "{\"hook\":\"health-sentinel\",\"ts\":$(date +%s),\"status\":\"ok\",\"session\":\"${session_id}\"}" > "$sentinel_file" 2>/dev/null

echo "{\"systemMessage\": \"Evolving Lite v1.0 | Session ${session_count} | Tier ${tier} (${tier_label}) | ${total_exp} experiences${import_suffix}\", \"continue\": true}"
exit 0
