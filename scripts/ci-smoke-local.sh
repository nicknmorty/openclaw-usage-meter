#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd -- "$script_dir/.." && pwd -P)"

cd "$repo_root"

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf -- "$tmp_dir"
}
trap cleanup EXIT

mkdir -p "$tmp_dir/agents"

echo "ci-smoke-local: empty collection"
python3 scripts/agent_usage_collect.py \
  --db "$tmp_dir/agent_usage.sqlite" \
  --agents-dir "$tmp_dir/agents" \
  --workspace "$tmp_dir" \
  >/dev/null

echo "ci-smoke-local: JSON report"
python3 scripts/usage_report.py \
  --db "$tmp_dir/agent_usage.sqlite" \
  --json \
  >/dev/null

echo "ci-smoke-local: fixture collection"
python3 scripts/test-fixture-collection.py

if [ "${USAGE_METER_SKIP_CLEAN_CLONE:-0}" != "1" ]; then
  echo "ci-smoke-local: clean clone"
  scripts/test-clean-clone.sh
fi

echo "ci-smoke-local: clean"
