# Calibration Notes

## Why calibration matters

OpenClaw JSONL events record token counts from provider API responses, but write `cost=0` for every event (current OpenClaw does not compute costs). So in practice **all costs are computed locally** from token counts × the pricing table (`cost_source='computed'` or `'repaired'`).

The `cost_source='jsonl'` path exists for forward-compatibility if a future OpenClaw version starts writing real costs, but it does not fire today.

The pricing table is maintained in `scripts/agent_usage_collect.py`. Provider pricing changes, so calibration against real bills is important.

## Anthropic cache write pricing

**Current calibrated multiplier:** 0.80× input rate (i.e., `cache_write_per_mtok = input_per_mtok × 0.80`)

Official Anthropic docs state cache write costs 1.25× base input rate. Some OpenClaw JSONL datasets may fit actual provider billing better with a lower effective multiplier:

| Multiplier | Estimate | Delta |
|-----------|---------|-------|
| 1.25× (official) | highest estimate | compare with bill |
| 1.00× | middle estimate | compare with bill |
| 0.80× (default) | lower estimate | compare with bill |

**Hypothesis:** OpenClaw over-records `cache_write_tokens` — it may record the full context size rather than what Anthropic actually charges per the 5-minute cache TTL window. Mechanism is unconfirmed; verify via Anthropic console per-category breakdown.

## Running calibration

```bash
# Pull your actual bill amount from Anthropic console, then:
python3 scripts/usage_report.py --calibrate --month YYYY-MM --actual YOUR_BILL_AMOUNT
```

This outputs a sensitivity table across multiplier values so you can find the best fit.

Once you find the right multiplier, update `cache_write_per_mtok` for each Anthropic model in `scripts/agent_usage_collect.py`, then:

```bash
python3 scripts/agent_usage_collect.py --recalibrate
```

## OpenAI direct API

Cost is available exactly via the OpenAI organization admin API. Use `scripts/fetch_openai_usage.py` to pull it and compare with the DB.

## OpenAI subscription equivalent

For ChatGPT Pro / Codex OAuth sessions, JSONL token counts are real (from the API response), but cost in JSONL is $0 (subscription path, no per-token charge). The DB computes subscription-equivalent cost by applying API list rates to the real token counts. This is what you would pay if you were on a PPU plan instead.

## OpenRouter

Prices fetched live from `https://openrouter.ai/api/v1/models`. `:free` models are explicitly priced at $0.
