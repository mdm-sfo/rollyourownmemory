# User Testing

**What belongs here:** Testing surface findings, validation approach, resource cost classification.

---

## Validation Surface

- **CLI commands**: `distill.py backfill_embeddings`, `embed.py build --reembed`, `distill.py run --embed-model`, `distill.py run --no-segment`
- **Web API**: `/api/search?type=all` (check for `semantic_facts` key), `/api/ask` (verify semantic context)
- **MCP tools**: `memory_search_facts_semantic` tool registration and output format
- **Frontend**: `static/app.js` rendering of semantic fact cards
- **Unit tests**: pytest test suite (166 existing + new tests)

## Validation Concurrency

Machine: 20 cores, 121GB RAM, ~27GB used at baseline. Headroom: ~66GB (70% = ~46GB usable).
- **pytest**: Single process, ~200MB. Max concurrent: 5 (but 1 is sufficient)
- **CLI verification**: Minimal resources. Max concurrent: 5
- **Web API curl**: Negligible. Max concurrent: 5

All validation for this mission is CLI/API/unit-test based. No browser testing needed.
