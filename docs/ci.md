# CI/CD

The public CI pipeline protects two boundaries:

1. The code still runs as a generic public tool.
2. The repository does not grow private environment leaks over time.

GitHub Actions runs on pull requests, pushes to `main`, and manual dispatch.
Workflow actions are pinned to commit SHAs, jobs have 10-minute timeouts, and
rapid pushes cancel older in-progress runs for the same ref.

The thin caller `.github/workflows/ci.yml` delegates to the local reusable
workflow `.github/workflows/ci-standard.yml`. The reusable workflow accepts a
`profile` input (`public` or `private`), though this public repo currently calls
it with `profile: public`.

## Jobs

### Smoke

The reusable workflow runs the smoke matrix on Python 3.9 and 3.12.

The core smoke gate (`scripts/ci-smoke-core.sh`) checks:

- Python syntax for the three core scripts
- Ruff lint checks
- optional Node extension syntax
- CLI help commands

The local smoke hook (`scripts/ci-smoke-local.sh`) checks usage-meter-specific
behavior:

- empty-collection database creation in a temporary directory
- JSON report generation from the temporary database
- fixture collection/reporting against public-safe OpenClaw JSONL
- clean archive checkout usability through `scripts/test-clean-clone.sh`

`scripts/ci-smoke.sh` remains the local aggregate wrapper for contributors.

Run it locally:

```bash
python3 -m pip install ruff==0.12.0
scripts/ci-smoke.sh
```

Prefer installing Ruff in a virtual environment for local runs. CI installs the
pinned Ruff version before calling the smoke script.

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

`scripts/test-fixture-collection.py` asserts that collection stores provider-
shaped usage events, computes known model cost from token counts, ignores
OpenAI cache-write tokens for cost computation, marks unpriced model cost as
unknown, backfills priced `unknown` rows during recalibration, keeps prefix-only
model ID collisions unknown, updates session counters, and exposes the expected
model rows through JSON reporting.

## Clean Clone

`scripts/test-clean-clone.sh` builds a temporary `git archive` checkout and runs
syntax, help, and fixture checks from that clean tracked-file tree. This catches
missing tracked files that would not show up when running from a dirty local
working tree.
