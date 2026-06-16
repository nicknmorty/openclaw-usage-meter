# Roadmap

## Project cost preflight (planned)

**Goal:** Before starting a project, estimate its anticipated API cost from the
model(s) it will use and the expected token volume, and warn the user when a
chosen model is too expensive for the work.

### Inputs
- Target model(s) and provider
- Expected workload shape: number of turns/sessions, average context size,
  cache hit ratio, output length
- Optional budget ceiling (per task / per month)

### Outputs
- Estimated cost range (low/expected/high) using the same pricing table that
  powers the reports (`PRICING_TABLE` in `agent_usage_collect.py`)
- A warning when expected cost exceeds a configurable threshold, with cheaper
  model suggestions that fit the same task class
- A note on cache economics (cache_read is ~10x cheaper than input; long cached
  sessions are dominated by cache_read + cache_write, not raw input)

### Calibration source
- Reuse historical per-model token/cost data from the DB to ground the estimate
  in real usage patterns rather than naive token math
- e.g. "opus-4-8 averaged $X per session of this shape last month"

### Open questions
- Where the preflight runs: standalone CLI (`scripts/preflight.py`) vs an agent
  workflow that reads a project spec
- How workload shape is supplied (manual flags vs inferred from a project brief)
- Threshold defaults and how warnings surface (CLI exit code, Telegram nudge)

## Other backlog
- Anthropic console per-category verification to confirm the 0.75x cache_write
  multiplier mechanism
- Open-source packaging polish and pitch to the OpenClaw team
