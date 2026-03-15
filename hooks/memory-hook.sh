#!/usr/bin/env bash
# Claude Code memory hook — triggered by SessionStart, SessionEnd, and PreCompact.
# Reads hook event JSON from stdin and runs the appropriate memory pipeline step.
#
# Install: add this to ~/.claude/settings.json (see README for full config).

set -euo pipefail

# Resolve the rollyourownmemory project root (parent of hooks/)
MEMORY_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Read stdin JSON into a variable
INPUT=$(cat)

# Extract event name and contextual fields using Python (available on all supported platforms)
EVENT=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('hook_event_name',''))" 2>/dev/null || echo "")
SOURCE=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('source', d.get('reason', d.get('trigger',''))))" 2>/dev/null || echo "")
SESSION_ID=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null || echo "")

log() {
    echo "[memory-hook] $(date +%H:%M:%S) $*" >&2
}

case "$EVENT" in
    SessionStart)
        log "Session starting (source=$SOURCE, session=$SESSION_ID)"
        # Generate memory context and return it as additionalContext
        CONTEXT=$(cd "$MEMORY_ROOT" && python3 src/inject.py --stdout --max-tokens 2000 2>/dev/null || echo "")
        if [ -n "$CONTEXT" ]; then
            # Output JSON with hookSpecificOutput.additionalContext for Claude to receive
            python3 -c "
import json, sys
context = sys.stdin.read()
print(json.dumps({
    'hookSpecificOutput': {
        'hookEventName': 'SessionStart',
        'additionalContext': context
    }
}))
" <<< "$CONTEXT"
        fi
        ;;

    SessionEnd)
        log "Session ending (reason=$SOURCE, session=$SESSION_ID)"
        # CRITICAL: SessionEnd has 1.5s default timeout — background processes are essential
        cd "$MEMORY_ROOT" && python3 src/ingest.py --quiet 2>/dev/null &
        cd "$MEMORY_ROOT" && python3 src/embed.py --quiet 2>/dev/null &
        ;;

    PreCompact)
        log "Pre-compaction (trigger=$SOURCE, session=$SESSION_ID)"
        # Ingest synchronously first to capture current conversation
        cd "$MEMORY_ROOT" && python3 src/ingest.py --quiet 2>/dev/null || true
        # Distill in background — extract facts before compaction strips detail
        cd "$MEMORY_ROOT" && python3 src/distill.py run --llm --limit 3 2>/dev/null &
        ;;

    *)
        log "Unknown event: $EVENT"
        ;;
esac

exit 0
