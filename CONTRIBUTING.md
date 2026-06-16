# Contributing

Thanks for improving agent-usage-collector.

## Public Product Boundary

This repository is the generic public product. Contributions should avoid
environment-specific assumptions and should not include private deployment
overlays.

Do not commit:

- real names, chat IDs, phone numbers, emails, or account IDs
- host paths such as `/home/<real-user>/...`
- real billing amounts, provider org/project IDs, or private calibration notes
- logs, reports, screenshots, SQLite databases, or generated artifacts
- `.env`, `*.local.json`, `*.local.md`, or private overlay files

Use boring fake examples such as `1234567890`, `Example Agent`,
`/home/user/.openclaw`, and `example.local`.

## Promotion Rule

Private or deployment-specific patches may only move into the public repository
by reviewed patch or cherry-pick. Never raw-merge private repository history
into this public repository.

Tag and release from the public repository only. Private deployments can pin to
a public tag or commit and keep their overlays separately.

## Checks

Before proposing a public release, run the relevant tests plus leak scans such
as `gitleaks` and a custom identity/environment string scan.
