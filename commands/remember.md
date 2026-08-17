---
description: Explicitly save something to memory as an experience
argument-hint: [What to remember]
---

Save an explicit memory/experience to the Evolving Lite knowledge base.

## Input: $ARGUMENTS

If empty: "What should I remember? Describe what you learned, decided, or want to keep."

## Process

1. **Classify the type**:
   - `solution` - How you solved a problem
   - `gotcha` - A non-obvious pitfall or trap
   - `pattern` - A reusable approach or technique
   - `technique` - A specific method or tool usage
   - `decision` - An architectural or design decision
   - `preference` - A user preference or convention

2. **Extract key fields**:
   - `summary`: One-line description
   - `problem`: What was the context/challenge (if applicable)
   - `solution`: What worked / what to do
   - `tags`: 2-4 relevant keywords

3. **Save as experience file**:
   Write to `${CLAUDE_PLUGIN_ROOT}/_memory/experiences/exp-{timestamp}.json`,
   where `{timestamp}` is `YYYYmmdd-HHMMSS`.

   **If that file already exists, append `-2`, then `-3`, until you find a free
   name** (`exp-20260317-143022-2.json`). The `correction-detector` hook writes
   into the same directory on the same turn, so a plain timestamp will silently
   overwrite the experience it just saved - the exact loss this suffix exists to
   prevent. The `id` field inside the file must match the filename you chose.

4. **Confirm**: "Saved: {summary} [type: {type}, tags: {tags}]"

## Example

User: `/remember "Always check tsconfig strict mode first when debugging TypeScript type errors"`

Result:
```json
{
  "id": "exp-20260317-143022",
  "type": "gotcha",
  "summary": "Check tsconfig strict mode first for TypeScript type errors",
  "problem": "TypeScript type errors that seem wrong or inconsistent",
  "solution": "Check tsconfig.json strict mode setting before investigating individual type errors",
  "tags": ["typescript", "debugging", "tsconfig"],
  "confidence": 0.8
}
```
