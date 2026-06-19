# CI/CD

The public CI pipeline protects two boundaries:

1. The code still runs as a generic public tool.
2. The repository does not grow private environment leaks over time.

GitHub Actions runs on pull requests, pushes to `main`, and manual dispatch.

## Jobs

### Smoke

`.github/workflows/ci.yml` runs `scripts/ci-smoke.sh` on Python 3.9 and 3.12.

The smoke gate checks:

- Python syntax for the three core scripts
- optional Node extension syntax
- CLI help commands
- empty-collection database creation in a temporary directory
- JSON report generation from the temporary database
- fixture collection/reporting against public-safe OpenClaw JSONL

Run it locally:

```bash
scripts/ci-smoke.sh
```

### Public Audit

The public audit job runs GitHub gitleaks scanning, then
`scripts/public-audit.sh`.

The local audit checks:

- tracked cache, database, env, and private overlay files
- private names, local usernames, local paths, token-shaped values, and real IDs
- Telegram IDs except the documented fake placeholder
- CLI help output for expanded local home paths
- optional local `gitleaks` when installed

Run it locally:

```bash
scripts/public-audit.sh
```

## Before Release

Before tagging a public release, run:

```bash
scripts/ci-smoke.sh
scripts/public-audit.sh
git status --short
```

The working tree should be clean except for intentional release edits.

## Fixture Contract

The public fixture under `tests/fixtures/openclaw-agents/` uses fake OpenClaw
session JSONL with boring example content. It must stay free of private names,
chat IDs, local paths, provider keys, billing actuals, and generated databases.

`scripts/test-fixture-collection.py` asserts that collection stores a real usage
event, computes cost from token counts, updates session counters, and exposes
the expected model row through JSON reporting.
