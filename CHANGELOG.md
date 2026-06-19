# Changelog

All notable changes to `openclaw-usage-meter` are documented here.

This project uses semantic versioning for public releases.

## [Unreleased]

### Added

- Added GitHub Actions CI for public smoke checks and public hygiene auditing.
- Added local `scripts/ci-smoke.sh` and `scripts/public-audit.sh` gates for
  contributors and releases.
- Added a public-safe OpenClaw JSONL fixture and fixture collection/reporting
  assertion script.
- Added a clean archive checkout smoke test so CI verifies tracked-file
  usability.
- Added CI/CD documentation in `docs/ci.md`.
- Added dedicated SQLite setup documentation in `docs/database-setup.md`.
- Added README links for database setup, release history, and public/private
  deployment guidance.

### Changed

- Kept CLI help text from expanding default private config paths into local
  usernames.
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
