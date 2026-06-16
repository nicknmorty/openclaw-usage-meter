# SQLite Database Setup

OpenClaw Usage Meter stores collected usage in a local SQLite database. There is
no separate migration command for first use: the collector creates the database,
tables, indexes, and reporting views on the first collection run.

## Default Location

By default, the database is created at:

```text
~/.openclaw/usage/agent_usage.sqlite
```

The collector also creates the parent directory when needed.

## First Run

From the repository checkout:

```bash
python3 scripts/agent_usage_collect.py
```

That command scans OpenClaw JSONL session files from:

```text
~/.openclaw/agents/*/sessions/*.jsonl
```

It then creates or updates the SQLite database. Running the collector again is
safe; rows are deduplicated by stable event keys.

## Custom Paths

Use `--db` when you want the SQLite file somewhere else:

```bash
python3 scripts/agent_usage_collect.py \
  --db /path/to/agent_usage.sqlite
```

If your OpenClaw agent sessions live outside the default location, also pass
`--agents-dir`:

```bash
python3 scripts/agent_usage_collect.py \
  --agents-dir /path/to/openclaw/agents \
  --db /path/to/agent_usage.sqlite
```

Reports use the same default database path. Point reports at a custom database
with:

```bash
python3 scripts/usage_report.py --db /path/to/agent_usage.sqlite
```

## Verify Setup

After the first collection run, confirm the database exists:

```bash
ls -lh ~/.openclaw/usage/agent_usage.sqlite
```

Then generate a report:

```bash
python3 scripts/usage_report.py --today
```

If you have the `sqlite3` CLI installed, you can inspect the schema version and
row counts directly:

```bash
sqlite3 ~/.openclaw/usage/agent_usage.sqlite \
  "SELECT key, value FROM schema_meta; SELECT COUNT(*) AS usage_events FROM usage_events;"
```

## Schema Contents

The database includes:

- `schema_meta` — schema version metadata
- `users` — observed user/contact labels
- `sessions` — collected OpenClaw session files
- `usage_events` — token and cost events
- `session_counters` — per-session rollups
- `v_usage_by_agent_user` — reporting view by agent and user
- `v_usage_by_day_agent_user` — reporting view by day, agent, and user

The current public baseline uses schema version 2.

## Repairing Or Repricing Existing Data

After the first collection, run:

```bash
python3 scripts/agent_usage_collect.py --repair-costs
```

Use `--recalibrate` after changing pricing values in
`scripts/agent_usage_collect.py`:

```bash
python3 scripts/agent_usage_collect.py --recalibrate
```

Preview repair or recalibration changes with `--dry-run`.

## Privacy And Backups

The SQLite database is a generated local artifact and should stay private. It can
contain session identifiers, user/contact labels, provider names, model names,
token counts, and cost estimates. Do not commit `agent_usage.sqlite`, SQLite WAL
files, or private label/config files to a public repository.

For backups, copy the database and any related private config files into your
private backup system:

```bash
cp ~/.openclaw/usage/agent_usage.sqlite /private/backup/location/
```

If the database is actively being written, prefer a SQLite backup command:

```bash
sqlite3 ~/.openclaw/usage/agent_usage.sqlite \
  ".backup '/private/backup/location/agent_usage.sqlite'"
```
