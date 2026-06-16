# OpenClaw JSONL Format Notes

Findings from live inspection of `~/.openclaw/agents/*/sessions/*.jsonl` (2026-06-09).

## Event structure (assistant turn)

```json
{
  "type": "message",
  "id": "<uuid-short>",
  "parentId": "<parent-id>",
  "timestamp": "2026-06-09T23:07:09.309Z",
  "message": {
    "role": "assistant",
    "content": [ ... ],
    "api": "anthropic-messages",
    "provider": "anthropic",
    "model": "claude-sonnet-4-6",
    "usage": {
      "input": 1,
      "output": 773,
      "cacheRead": 49913,
      "cacheWrite": 2569,
      "totalTokens": 53256,
      "cost": {
        "input": 0,
        "output": 0,
        "cacheRead": 0,
        "cacheWrite": 0,
        "total": 0
      }
    },
    "stopReason": "toolUse",
    "timestamp": 1781046413593,
    "responseId": "msg_016ZtXTakvrJYsZmq4a8xKKi"
  }
}
```

## Key findings

### Cost is always zero

OpenClaw writes `cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0}` for every event
regardless of provider. OpenClaw does not compute or store API costs itself.

**Implication:** The `cost_source='jsonl'` path in `agent_usage_collect.py` will never fire with
the current OpenClaw version. All cost estimates come from the pricing table (`computed`) or
back-fill (`repaired`). This is the intended design of this tool — we compute costs ourselves
from authoritative token counts.

The `jsonl` cost_source path is disabled by default and kept only for forward compatibility with
potential future OpenClaw versions that may start writing real costs. Set
`USAGE_TRUST_JSONL_COST=1` to opt into trusting non-zero JSONL costs.

### `input` token count quirk

When a session uses prompt caching heavily, `usage.input` is recorded as `1` even though
`cacheRead + cacheWrite` account for most of the context. This appears to be OpenClaw
normalizing the "new input tokens not from cache" to 1 for highly-cached sessions.

**Implication:** For cost computation on Anthropic events, `input_tokens` is effectively
ignored when cache tokens dominate. Cost is driven by `cache_read_tokens` and `cache_write_tokens`.
The $0.30/MTok cache read rate (0.1× sonnet input) is much cheaper than the $3/MTok input rate,
which is why actual bills are lower than naive `input × rate` estimates.

### Token count fields

| JSONL field | DB column | Notes |
|-------------|-----------|-------|
| `usage.input` | `input_tokens` | "new" tokens; often 1 for cached sessions |
| `usage.output` | `output_tokens` | Generated tokens; always accurate |
| `usage.cacheRead` | `cache_read_tokens` | Context served from cache; cheap |
| `usage.cacheWrite` | `cache_write_tokens` | Cache writes (5-min TTL); moderately expensive |
| `usage.totalTokens` | `total_tokens` | Sum of all above |

### Provider field

Mapped from JSONL `message.provider` → DB `provider` column:
- `anthropic` — Anthropic direct API
- `openai` — OpenAI direct API or subscription path (non-Codex)
- `openai-codex` — Codex OAuth sessions (ChatGPT Pro subscription)
- `openrouter` — OpenRouter
- `openclaw` — Internal delivery/routing events (excluded from cost totals)

## Pricing table coverage

All cost computation is table-driven. If a model is not in the pricing table:
- `cost_source='zero'` is recorded
- `cost_usd=0` is stored
- Run `--repair-costs` after adding the model to back-fill events

To check for missing pricing entries after collection:
```bash
python3 scripts/usage_report.py --model | grep '\$0.0000'
```
