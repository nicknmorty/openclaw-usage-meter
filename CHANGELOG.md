# Changelog

All notable changes to `openclaw-usage-meter` are documented here.

This project uses semantic versioning for public releases.

## [Unreleased]

### Added

- Added dedicated SQLite setup documentation in `docs/database-setup.md`.
- Added README links for database setup, release history, and public/private
  deployment guidance.

### Changed

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
