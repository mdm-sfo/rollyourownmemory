# Ingestion Format Analysis

## Factory.ai JSONL Format

**Location:** `~/.factory/sessions/<encoded-cwd>/<session-uuid>.jsonl`

**Directory encoding:** Similar to Claude Code but does NOT use double-hyphen escaping for literal hyphens. All hyphens are path separators. Example: `-home-matthewmurray-claude-memory` decodes to `/home/matthewmurray/claude/memory` (not `/home/matthewmurray/claude-memory`). **Project derivation uses `session_start.cwd` instead of `derive_project()`** to avoid this encoding ambiguity. The `derive_project()` function is for Claude Code paths only.

**Record types:**
- `session_start` — first line. Fields: `id` (session UUID), `title`, `cwd`, `version`, `callingSessionId` (optional, for subagent chains)
- `message` — conversation turns. Fields: `id`, `timestamp` (ISO 8601), `message.role` (user/assistant), `message.content[]` (blocks: text, tool_use, tool_result, thinking)
- `todo_state` — todo snapshots. Skip.
- `session_end` — last line. Skip.

**Content blocks in message.content[]:**
- `{type: "text", text: "..."}` — extractable text (same format as Claude Code)
- `{type: "tool_use", ...}` — skip
- `{type: "tool_result", ...}` — skip
- `{type: "thinking", ...}` — encrypted, skip

**extract_text_content() compatibility:** YES — Factory uses identical content block format to Claude Code.

## Codex CLI JSONL Format

**Session location:** `~/.codex/sessions/<YYYY>/<MM>/<DD>/rollout-<timestamp>-<uuid>.jsonl`
**History location:** `~/.codex/history.jsonl`

**Session record types:**
- `session_meta` — first line. Fields: `payload.id` (session UUID), `payload.cwd` (project path, NOT encoded), `payload.originator`, `payload.cli_version`
- `response_item` — model interactions. Fields: `timestamp` (ISO 8601), `payload.type` (message/reasoning/function_call/function_call_output/web_search_call), `payload.role` (user/assistant/developer), `payload.content[]`, `payload.phase` (commentary/final_answer)
- `event_msg` — event notifications. Skip.
- `turn_context` — per-turn settings. Skip.

**Content blocks in payload.content[]:**
- `{type: "input_text", text: "..."}` — user input (NOT "text" type!)
- `{type: "output_text", text: "..."}` — assistant output (NOT "text" type!)
- Other types — skip

**extract_text_content() compatibility:** NO — Codex uses `input_text`/`output_text` instead of `text`. Need to extend extraction or create Codex-specific extractor.

**History format:**
```json
{"session_id": "uuid", "ts": 1773246172, "text": "user prompt"}
```
- `ts` is epoch SECONDS (not milliseconds like Claude Code's history.jsonl)
- Always role=user

## Key Differences from Claude Code

| Aspect | Claude Code | Factory | Codex |
|--------|------------|---------|-------|
| Content block type | `text` | `text` | `input_text`/`output_text` |
| History timestamp | epoch ms | N/A | epoch seconds |
| Project derivation | encoded dir name | `session_start.cwd` | `payload.cwd` direct path |
| Session ID source | `sessionId` field | `session_start.id` | `session_meta.payload.id` |
| Dir structure | `projects/<encoded>/` | `sessions/<encoded>/` | `sessions/<YYYY>/<MM>/<DD>/` |

## Source Volumes

- Factory: 324 JSONL files across 5 project directories
- Codex: 5 session files + 1 history.jsonl
- Claude Code: existing (hundreds of sessions)
