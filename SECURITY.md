# Security Policy

## Supported Versions

Security fixes are handled on the latest public release unless a maintainer
states otherwise.

## Reporting A Vulnerability

Please report suspected vulnerabilities privately to the project maintainers.
Do not open a public issue containing credentials, private paths, chat IDs,
billing details, logs, screenshots, or session data.

## Sensitive Data

This project reads local OpenClaw session metadata and stores usage counters in
SQLite. Treat these files as private:

- OpenClaw JSONL session files
- generated SQLite databases and WAL/SHM files
- contact-label mappings
- actual billing/subscription files
- local cron/deployment notes
- logs, reports, screenshots, and test fixtures from real environments

The public repository should contain only generic code, documentation, tests,
and obviously fake examples.
