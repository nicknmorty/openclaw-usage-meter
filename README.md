# openclaw-usage-meter

Track the real API cost of [OpenClaw](https://openclaw.ai) agent sessions across all billing streams — Anthropic direct API, OpenAI direct API, OpenAI subscription (ChatGPT Pro), and OpenRouter.

Idempotent collector reads your local OpenClaw JSONL session files, stores events in SQLite with token-level granularity, computes costs from a maintained pricing table, and produces clean reports.

This repository is the generic public product baseline. Private deployment
overlays should keep real labels, billing actuals, local paths, cron targets,
and calibration notes outside this repo.

Current public baseline: `v0.1.0`.

## Why

OpenClaw runs your AI agents. The costs across multiple providers add up in ways that are hard to see without tooling. This gives you:

- **Per-provider monthly totals** vs what you actually paid
- **Subscription value extraction** — if you have a flat-rate subscription, how much API-equivalent usage did you get vs the monthly fee?
- **Model-level breakdown** — which models are actually driving your bill
- **Calibrated estimates** — empirically tuned against real bills (Anthropic cache write pricing, Codex token counting)

## Quick start

```bash
# Install (no external dependencies — pure Python stdlib)
git clone https://github.com/nicknmorty/openclaw-usage-meter.git
cd openclaw-usage-meter

# First collection run (builds DB from existing JSONL sessions)
python3 scripts/agent_usage_collect.py

# Back-fill zero-cost events from pricing table
python3 scripts/agent_usage_collect.py --repair-costs

# View monthly summary
python3 scripts/usage_report.py

# Today's spending
python3 scripts/usage_report.py --today
```

## Requirements

- Python 3.8+
- OpenClaw installed and generating JSONL session files at `~/.openclaw/agents/*/sessions/*.jsonl`
- No external Python packages needed

## Public Product Model

This repo is designed to be released as a generic product, not as a sanitized
export of any one deployment. Keep private deployment overlays in private source
control and promote generic changes into this repo by reviewed patch or
cherry-pick only. Never raw-merge private repository history into the public
repo.

See:

- [docs/sanitization.md](docs/sanitization.md)
- [docs/database-setup.md](docs/database-setup.md)
- [SECURITY.md](SECURITY.md)
- [CONTRIBUTING.md](CONTRIBUTING.md)

## Scripts

### `scripts/agent_usage_collect.py` — main collector

Reads JSONL session files, deduplicates by event key, stores to SQLite.

```bash
python3 scripts/agent_usage_collect.py                    # normal collection
python3 scripts/agent_usage_collect.py --contacts ~/.openclaw/usage/contact-labels.json
python3 scripts/agent_usage_collect.py --repair-costs     # back-fill zero-cost events
python3 scripts/agent_usage_collect.py --recalibrate      # recompute after pricing change
python3 scripts/agent_usage_collect.py --recalibrate --dry-run
```

### `scripts/usage_report.py` — query and report tool

```bash
python3 scripts/usage_report.py                           # monthly summary (default)
python3 scripts/usage_report.py --today                   # today's spending
python3 scripts/usage_report.py --daily --month 2026-06   # cost by day
python3 scripts/usage_report.py --model                   # cost by model (all time)
python3 scripts/usage_report.py --breakdown --month 2026-06  # token-type breakdown
python3 scripts/usage_report.py --calibrate --month 2026-06 --actual YOUR_BILL_AMOUNT
python3 scripts/usage_report.py --week                    # last 7 days
python3 scripts/usage_report.py --ytd                     # year-to-date monthly
python3 scripts/usage_report.py --provider anthropic      # filter to one provider
python3 scripts/usage_report.py --week --provider openai  # combine with any time window
python3 scripts/usage_report.py --all                     # all sections
```

### `scripts/fetch_openai_usage.py` — OpenAI admin API fetcher

Pulls usage data from the OpenAI organization API (requires an admin key with `api.usage.read` scope).

```bash
# Requires OPENAI_ADMIN_KEY env var (sk-admin-* key)
python3 scripts/fetch_openai_usage.py --start 2026-03-01
python3 scripts/fetch_openai_usage.py --start 2026-03-01 --save-json /tmp/openai_usage.json
```

## Provider filtering

Any report can be scoped to a single provider with `--provider`. The value is a
display group that expands to the underlying raw provider values:

| `--provider` | Includes raw providers |
|--------------|------------------------|
| `anthropic`  | `anthropic` |
| `openai`     | `openai`, `openai-codex`, `codex` |
| `openrouter` | `openrouter` |

Combine it with any time window: `--today`, `--week`, `--daily`, `--ytd`,
`--model`, or the default monthly summary. Example: track Anthropic and OpenAI
spend over the same week side by side by running the report twice, once per
provider.

## Database

**Location:** `~/.openclaw/usage/agent_usage.sqlite`

The collector creates the SQLite database, tables, indexes, and views on the
first collection run. See [docs/database-setup.md](docs/database-setup.md) for
custom paths, verification queries, repair/recalibration commands, and privacy
notes.

**Schema (v2):** `event_key`, `session_id`, `source_file`, `event_at`, `role`, `provider`, `model`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `total_tokens`, `cost_usd`, `cost_source`

`cost_source` values:
- `jsonl` — non-zero JSONL cost was explicitly trusted with `USAGE_TRUST_JSONL_COST=1` (forward compatibility only)
- `computed` — cost=0 in JSONL; computed from pricing table at collection time
- `repaired` — back-filled by `--repair-costs` or `--recalibrate`
- `unknown` — pre-schema-v2 (migration default)
- `zero` — cost=0, no pricing table entry found
- `clamped` — was negative, clamped to 0

**Cost provenance:** OpenClaw writes `cost=0` for all observed events in JSONL. By default, all costs are computed locally from the built-in pricing table in `agent_usage_collect.py`. Token counts are authoritative from provider API responses; costs are computed here unless `USAGE_TRUST_JSONL_COST=1` is explicitly set for a future OpenClaw version that writes real costs.

**Contact labels:** The collector can optionally map sender IDs to friendly names with `--contacts ~/.openclaw/usage/labels.local.json`. Keep the real file private; see `examples/labels.example.json` for the schema.

## Cron / automation

The collector is idempotent — safe to run frequently. Example cron job:

```
*/10 * * * * python3 /path/to/openclaw-usage-meter/scripts/agent_usage_collect.py
```

Or via OpenClaw cron (see `docs/openclaw-cron.md`).

## Calibration

Anthropic cache write pricing: official docs say 1.25× input rate, but empirical calibration against real provider bills may show a lower effective multiplier for some OpenClaw JSONL datasets. Hypothesis: OpenClaw may over-record `cache_write_tokens` vs what Anthropic actually charges.

To calibrate against your own bill:
```bash
python3 scripts/usage_report.py --calibrate --month YYYY-MM --actual YOUR_BILL_AMOUNT
```

Then update the `cache_write_per_mtok` values in `scripts/agent_usage_collect.py` and re-run `--recalibrate`.

## Billing streams supported

| Provider | Path | Notes |
|----------|------|-------|
| Anthropic | Direct API (`ANTHROPIC_API_KEY`) | PPU; API-equivalent cost is computed locally and can be compared to actual paid bills |
| OpenAI | Direct API (`OPENAI_API_KEY`) | PPU; API-equivalent cost is computed locally |
| OpenAI | ChatGPT Pro subscription | Token counts from JSONL × API list rates = subscription-equivalent |
| OpenRouter | Direct API | Prices fetched from OR API; `:free` models correctly $0 |

Monthly and YTD reports include an "Actual paid vs API-equivalent" section when you provide private billing data with `--actuals ~/.openclaw/usage/subscriptions.local.json`. See `examples/subscriptions.example.json` for the schema.

## OpenClaw slash command (`/spend`)

An optional OpenClaw extension in `extension/` registers a Telegram-formatted
`/spend` command (emoji summaries + Mermaid charts) backed by `usage_report.py`.
It supports the same time windows and provider filters as the CLI:

```
/spend                  all-time monthly
/spend today | week | month [YYYY-MM] | ytd | model
/spend anthropic week   provider filter + any window
/spend collect          fresh collection then today
```

See `extension/README.md` for install and config.

## License

MIT — see [LICENSE](LICENSE).
