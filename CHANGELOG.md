# Changelog

## Unreleased

### Added
- Automatic Codex Desktop session import from `~/.codex/sessions/` into the shared experience store
- Shared correction and transcript extraction helpers so Claude-native hooks and Codex import use the same learning heuristics

### Fixed
- Experience IDs now include microseconds to prevent same-second writes from overwriting each other
- Codex import state is tracked locally without being committed


## v1.0.0 (2026-03-17)

Initial release.

### Features
- 4 feedback loops: LEARN, HEAL, EVOLVE, CONTEXT
- 3-tier progressive activation (Safety -> Learning -> Deep)
- 15 slash commands
- 5 specialized agents
- 2 auto-activating skills (system-boot, evolution-guide)
- 10 hook scripts (4 Tier 1, 3 Tier 2, 3 Tier 3)
- 10-tier bash security system
- Hook health sentinel with per-hook verification
- 20 pre-warmed experiences
- 25 context routes
- 12 curated patterns
- 5 behavioral rules
- Session counter with double-increment guard
- Evolution changelog (/evolution command)
- Version check (/evolving-update command)

### Architecture
- Claude Code plugin (hooks/hooks.json, no settings.json modification)
- Portable paths via setup.sh (replaces ${CLAUDE_PLUGIN_ROOT} placeholders with absolute paths)
- common.py shared utilities (sentinel, session counter, safe JSON, experience creation)
- Fail-open hooks (never block tool execution)
- Bash 3.2 compatible shell scripts
- Python 3.10+ stdlib only (no pip dependencies)
