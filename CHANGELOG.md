# Changelog

All notable changes to `openclaw-usage-meter` are documented here.

This project uses semantic versioning for public releases.

## [Unreleased]

### Added

- Added GitHub Actions CI for public smoke checks and public hygiene auditing.
- Added local `scripts/ci-smoke.sh` and `scripts/public-audit.sh` gates for
  contributors and releases.
- Added a local reusable `workflow_call` CI workflow with `public`/`private`
  profile support.
- Added Ruff linting to the smoke gate.
- Added a public-safe OpenClaw JSONL fixture and fixture collection/reporting
  assertion script.
- Added a ZAI `glm-5.1` fixture so CI covers a second provider/model shape.
- Added a clean archive checkout smoke test so CI verifies tracked-file
  usability.
- Added CI/CD documentation in `docs/ci.md`.
- Added dedicated SQLite setup documentation in `docs/database-setup.md`.
- Added README links for database setup, release history, and public/private
  deployment guidance.

### Changed

- Kept CLI help text from expanding default private config paths into local
  usernames.
- Pinned CI actions to commit SHAs and added workflow concurrency plus job
  timeouts.
- Split smoke checks into reusable core checks and usage-meter-specific local
  checks.
- Mark token-bearing events with no pricing table entry as `unknown` cost
  instead of conflating them with known zero-cost usage.
- Let `--recalibrate` revisit all `unknown` cost rows so newly priced providers
  can be backfilled later.
- Polished the README around quick start, architecture, core commands, billing
  streams, database setup, private overlays, automation, and extension usage.

## [0.1.0] - 2026-06-16

### Added

- Initial public baseline for `openclaw-usage-meter`.
- SQLite collector for OpenClaw JSONL session usage.
- Usage reports for monthly, daily, weekly, YTD, provider, model, and token-type
  breakdowns.
- Local pricing-table cost computation and repair/recalibration commands.
- OpenAI admin usage fetcher.
- Optional OpenClaw `/spend` extension.
- Public/private repo guidance, security policy, contribution guide, examples,
  and sanitization notes.

[Unreleased]: https://github.com/nicknmorty/openclaw-usage-meter/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/nicknmorty/openclaw-usage-meter/releases/tag/v0.1.0
