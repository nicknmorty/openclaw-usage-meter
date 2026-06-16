# openclaw-usage-meter 🦞

Track what your OpenClaw agents actually cost.

[OpenClaw](https://openclaw.ai) writes rich JSONL session logs, but real spend is
spread across providers, subscriptions, API keys, and local reports.
`openclaw-usage-meter` turns those logs into a durable SQLite ledger with
token-level detail, computed cost estimates, and human-readable reports.

Current public baseline: `v0.1.0`.

## What It Does

- Collects OpenClaw JSONL usage events into SQLite
- Deduplicates safely, so collection can run on a schedule
- Computes API-equivalent cost from provider/model pricing
- Reports daily, weekly, monthly, YTD, provider, model, and token-type spend
- Supports private actual-paid billing overlays for subscription comparison
- Includes an optional OpenClaw `/spend` extension
- Uses only the Python standard library for the core CLI tools

## Quick Start

```bash
# Install
git clone https://github.com/nicknmorty/openclaw-usage-meter.git
cd openclaw-usage-meter

# First collection run: creates ~/.openclaw/usage/agent_usage.sqlite
python3 scripts/agent_usage_collect.py

# Back-fill zero-cost events from the pricing table
python3 scripts/agent_usage_collect.py --repair-costs

# View reports
python3 scripts/usage_report.py
python3 scripts/usage_report.py --today
```

## Requirements

- Python 3.8+
- OpenClaw session JSONL files at `~/.openclaw/agents/*/sessions/*.jsonl`
- No external Python packages for collection/reporting

## How It Works

```
OpenClaw JSONL sessions
        │
        ▼
scripts/agent_usage_collect.py
        │
        ▼
~/.openclaw/usage/agent_usage.sqlite
        │
        ├── scripts/usage_report.py
        └── optional extension/ /spend command
```

The collector reads assistant usage events from local OpenClaw sessions, stores
them in SQLite, computes costs from the built-in pricing table, and maintains
per-session rollups for fast reporting.

## Core Commands

### Collect Usage

```bash
python3 scripts/agent_usage_collect.py
python3 scripts/agent_usage_collect.py --db /path/to/agent_usage.sqlite
python3 scripts/agent_usage_collect.py --agents-dir /path/to/openclaw/agents
python3 scripts/agent_usage_collect.py --contacts ~/.openclaw/usage/contact-labels.json
python3 scripts/agent_usage_collect.py --repair-costs
python3 scripts/agent_usage_collect.py --recalibrate
python3 scripts/agent_usage_collect.py --recalibrate --dry-run
```

### Report Usage

```bash
python3 scripts/usage_report.py
python3 scripts/usage_report.py --today
python3 scripts/usage_report.py --week
python3 scripts/usage_report.py --daily --month 2026-06
python3 scripts/usage_report.py --model
python3 scripts/usage_report.py --breakdown --month 2026-06
python3 scripts/usage_report.py --provider anthropic
python3 scripts/usage_report.py --week --provider openai
python3 scripts/usage_report.py --ytd
python3 scripts/usage_report.py --all
```

### Fetch OpenAI Admin Usage

Requires an OpenAI admin key with `api.usage.read` scope.

```bash
OPENAI_ADMIN_KEY=sk-admin-... python3 scripts/fetch_openai_usage.py --start 2026-03-01
python3 scripts/fetch_openai_usage.py --start 2026-03-01 --save-json /tmp/openai_usage.json
```

## Billing Streams

| Provider | Path | Notes |
|----------|------|-------|
| Anthropic | Direct API | API-equivalent cost is computed locally and can be calibrated against actual bills |
| OpenAI | Direct API | API-equivalent cost is computed locally |
| OpenAI | ChatGPT Pro / Codex OAuth | Token counts from JSONL × API list rates = subscription-equivalent value |
| OpenRouter | Direct API | Prices can be fetched from OpenRouter metadata; `:free` models stay $0 |

Any report can be scoped with `--provider`:

| `--provider` | Raw providers included |
|--------------|------------------------|
| `anthropic` | `anthropic` |
| `openai` | `openai`, `openai-codex`, `codex` |
| `openrouter` | `openrouter` |

## SQLite Database

Default location:

```text
~/.openclaw/usage/agent_usage.sqlite
```

The first collection run creates the SQLite database, schema metadata, tables,
indexes, and reporting views. Use `--db` to store it elsewhere.

Current schema version: `2`.

Primary tables/views:

| Name | Purpose |
|------|---------|
| `schema_meta` | Schema version metadata |
| `users` | Observed user/contact labels |
| `sessions` | Collected OpenClaw session files |
| `usage_events` | Token and cost events |
| `session_counters` | Per-session rollups |
| `v_usage_by_agent_user` | Agent/user rollup view |
| `v_usage_by_day_agent_user` | Daily agent/user rollup view |

See [docs/database-setup.md](docs/database-setup.md) for setup, custom paths,
verification queries, repair/recalibration commands, and privacy notes.

## Cost Provenance

OpenClaw currently writes `cost=0` for observed JSONL usage events. By default,
this project computes costs locally from the built-in pricing table in
`scripts/agent_usage_collect.py`.

`cost_source` values:

| Value | Meaning |
|-------|---------|
| `jsonl` | Non-zero JSONL cost was explicitly trusted with `USAGE_TRUST_JSONL_COST=1` |
| `computed` | Cost was computed from the pricing table at collection time |
| `repaired` | Cost was back-filled by `--repair-costs` or `--recalibrate` |
| `unknown` | Pre-schema-v2 migration default |
| `zero` | No pricing table entry was found |
| `clamped` | Negative cost was clamped to `0` |

Token counts come from provider API responses recorded in OpenClaw JSONL logs.
Costs are estimates unless you compare them with actual provider bills.

## Private Overlays

Keep private deployment data out of the public repo:

- contact labels
- billing actuals
- local paths
- cron targets
- calibration notes
- generated SQLite databases and WAL/SHM files

Examples live in [examples/](examples/):

| File | Use |
|------|-----|
| `labels.example.json` | Friendly labels for user/contact IDs |
| `config.example.json` | Generic local config shape |
| `calibration.example.json` | Pricing/calibration notes |
| `subscriptions.example.json` | Actual-paid subscription overlays |
| `cron.example.md` | Cron setup example |

Monthly and YTD reports include an "Actual paid vs API-equivalent" section when
you provide private billing data with `--actuals`.

## Automation

The collector is idempotent and safe to run frequently.

```cron
*/10 * * * * python3 /path/to/openclaw-usage-meter/scripts/agent_usage_collect.py
```

OpenClaw cron setup is documented in [docs/openclaw-cron.md](docs/openclaw-cron.md).

## Optional `/spend` Command

The optional OpenClaw extension in [extension/](extension/) registers a
Telegram-formatted `/spend` command backed by `scripts/usage_report.py`.

```text
/spend
/spend today
/spend week
/spend month 2026-06
/spend ytd
/spend model
/spend anthropic week
/spend collect
```

See [extension/README.md](extension/README.md) for install and config.

## Docs

| Doc | What it covers |
|-----|----------------|
| [docs/database-setup.md](docs/database-setup.md) | SQLite setup, verification, schema, backups |
| [docs/openclaw-jsonl-format.md](docs/openclaw-jsonl-format.md) | JSONL fields and DB mapping |
| [docs/openclaw-cron.md](docs/openclaw-cron.md) | OpenClaw cron setup |
| [docs/calibration.md](docs/calibration.md) | Cost calibration against actual bills |
| [docs/sanitization.md](docs/sanitization.md) | Public/private repo model |
| [docs/roadmap.md](docs/roadmap.md) | Planned improvements |
| [CHANGELOG.md](CHANGELOG.md) | Release history |

## Public Product Model

This repository is the generic public product baseline, not a sanitized export
of one private deployment.

Private deployment overlays should stay in private source control. Promote
generic changes into this public repo by reviewed patch or cherry-pick only.
Never raw-merge private repository history into the public repo.

## Security

Generated databases, labels, local billing actuals, and provider keys are
private artifacts. See [SECURITY.md](SECURITY.md) before sharing logs, reports,
screenshots, or database files.

## Contributing

Bug reports, provider pricing fixes, docs improvements, and small focused pull
requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).
