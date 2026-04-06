# Security

## Reporting Vulnerabilities

If you find a security issue, please open a GitHub issue or contact the maintainer directly.

## Known Static Scanner False Positives

Some automated MCP/plugin directory scanners flag the following files as suspicious. These are **false positives** - keyword matches in configuration data, not executable code.

### `_graph/cache/context-router.json`

Scanners may flag this file for "outbound network request" or similar.

**Reality**: This file contains keyword-to-knowledge routing rules that Claude Code loads on-demand. The words `request`, `response`, `fetch`, `api`, `endpoint` appear as **routing keywords** (e.g., when the user mentions "api" the LLM loads api-related knowledge). There is no network code in this file. It is pure JSON data read as context by the LLM, not executed.

### `hooks/scripts/delegation-enforcer.py`

Scanners may flag this file for containing strings like `rm -rf`, `drop database`, `delete all` in the `CRITICAL_KEYWORDS` list.

**Reality**: This is the **opposite** of a vulnerability. These strings are in a list of dangerous keywords that `is_destructive()` checks against user prompts to **prevent** auto-delegation when the user is asking for destructive operations. The keywords are never executed - they are pattern-matched against user input as a safety gate. Removing them would make the plugin less safe.

## How to Verify

```bash
# Verify context-router.json is only loaded, never executed as code:
grep -rn "context-router" --include="*.py" --include="*.sh" .
# Returns: only doc references, no code imports

# Verify CRITICAL_KEYWORDS usage in delegation-enforcer.py:
grep -n "CRITICAL_KEYWORDS" hooks/scripts/delegation-enforcer.py
# Shows definition + single usage in is_destructive() pattern match
```

## For Directory Scanners

If you operate an MCP/plugin directory and your scanner flags this repo, please review the context above and consider whitelisting these specific findings. Happy to provide technical details on request.
