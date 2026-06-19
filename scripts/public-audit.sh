#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd -- "$script_dir/.." && pwd -P)"

cd "$repo_root"

failures=0

fail() {
  failures=$((failures + 1))
  printf 'public-audit: FAIL: %s\n' "$*" >&2
}

note() {
  printf 'public-audit: %s\n' "$*"
}

require_clean_tracked_paths() {
  local pattern="$1"
  local label="$2"
  if git ls-files -z | grep -zE "$pattern" >/dev/null; then
    fail "tracked $label files found"
    git ls-files | grep -E "$pattern" >&2 || true
  fi
}

note "checking tracked file hygiene"
require_clean_tracked_paths '(^|/)(__pycache__|\.pytest_cache|\.mypy_cache)(/|$)' "cache"
require_clean_tracked_paths '\.(sqlite|sqlite-wal|sqlite-shm|pyc|pyo|pyd)$' "generated/data"
require_clean_tracked_paths '(^|/)(\.env|[^/]+\.env)$' "environment"
require_clean_tracked_paths '(^|/)(reference|private|overlay)(/|$)' "private overlay"

note "checking public-sensitive strings"
scan_output="$(
  git grep -n -I -i -E \
    'haener|delaney|rpiuser|/home/rpiuser|federal|browns|nick-local|nhaener|gh[pousr]_[A-Za-z0-9]|-----BEGIN|331\.43|489\.99|100/month|20/month|8681554364|6566057320|8702622930' \
    -- . ':!scripts/public-audit.sh' \
  || true
)"

if [ -n "$scan_output" ]; then
  fail "private environment or credential-like strings found"
  printf '%s\n' "$scan_output" >&2
fi

note "checking Telegram placeholders"
telegram_hits="$(
  git grep -n -I -E 'telegram:-?[0-9]{8,}' -- . || true
)"
telegram_unexpected="$(
  printf '%s\n' "$telegram_hits" \
    | grep -v 'telegram:-1001234567890' \
    | grep -v '^$' \
    || true
)"
if [ -n "$telegram_unexpected" ]; then
  fail "non-placeholder Telegram IDs found"
  printf '%s\n' "$telegram_unexpected" >&2
fi

note "checking help output for local path leakage"
help_output="$(
  python3 scripts/agent_usage_collect.py --help
  python3 scripts/usage_report.py --help
  python3 scripts/fetch_openai_usage.py --help
)"
if printf '%s\n' "$help_output" | grep -E '/home/[^ ]+|/Users/[^ ]+' >/dev/null; then
  fail "help output contains expanded local home paths"
  printf '%s\n' "$help_output" | grep -E '/home/[^ ]+|/Users/[^ ]+' >&2 || true
fi

note "checking optional gitleaks scan"
if command -v gitleaks >/dev/null 2>&1; then
  gitleaks detect --source . --no-banner --redact --verbose
else
  note "gitleaks not installed; GitHub Actions runs the dedicated gitleaks action"
fi

if [ "$failures" -gt 0 ]; then
  printf 'public-audit: failed with %s issue(s)\n' "$failures" >&2
  exit 1
fi

note "clean"
