# Claude Code Hooks API Reference

Extracted from https://code.claude.com/docs/en/hooks on 2026-03-15.

## SessionStart

**Fires when:** A session begins or resumes.
**Matcher values:** `startup`, `resume`, `clear`, `compact`

**Input fields:** `session_id`, `transcript_path`, `cwd`, `permission_mode`, `hook_event_name`, `source`, `model`

**Output:** Can return `additionalContext` via:
```json
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "My additional context here"
  }
}
```

## SessionEnd

**Fires when:** A session terminates.
**Matcher values:** `clear`, `logout`, `prompt_input_exit`, `bypass_permissions_disabled`, `other`

**Input fields:** `session_id`, `transcript_path`, `cwd`, `permission_mode`, `hook_event_name`, `reason`

**CRITICAL: Default timeout is 1.5 seconds.** Can be extended via `CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS` env var. Background processes (`&`) are essential for anything that takes longer.

**No decision control** -- cannot block session termination.

## PreCompact

**Fires when:** Before context compaction.
**Matcher values:** `manual`, `auto`

**Input fields:** `session_id`, `transcript_path`, `cwd`, `permission_mode`, `hook_event_name`, `trigger`, `custom_instructions`

**No decision control** -- cannot block compaction.

## Hook Configuration Format

Hooks are defined in `~/.claude/settings.json` (global) or `.claude/settings.json` (project):

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/script.sh",
            "timeout": 10,
            "statusMessage": "Loading memory context..."
          }
        ]
      }
    ]
  }
}
```

## Key Notes
- Command hooks receive JSON on stdin
- Output JSON to stdout for hookSpecificOutput
- Exit code 0 = success, exit code 2 = block (for events that support it)
- `async: true` runs hook in background without blocking
- SessionEnd/PreCompact/PostCompact only support `type: "command"` hooks
