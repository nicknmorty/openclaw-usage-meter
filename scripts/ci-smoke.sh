#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd -- "$script_dir/.." && pwd -P)"

cd "$repo_root"

echo "ci-smoke: Python syntax"
python3 -m py_compile \
  scripts/agent_usage_collect.py \
  scripts/usage_report.py \
  scripts/fetch_openai_usage.py

echo "ci-smoke: Node extension syntax"
node --check extension/index.js

echo "ci-smoke: CLI help"
python3 scripts/agent_usage_collect.py --help >/dev/null
python3 scripts/usage_report.py --help >/dev/null
python3 scripts/fetch_openai_usage.py --help >/dev/null

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf -- "$tmp_dir"
}
trap cleanup EXIT

mkdir -p "$tmp_dir/agents"

echo "ci-smoke: empty collection"
python3 scripts/agent_usage_collect.py \
  --db "$tmp_dir/agent_usage.sqlite" \
  --agents-dir "$tmp_dir/agents" \
  --workspace "$tmp_dir" \
  >/dev/null

echo "ci-smoke: JSON report"
python3 scripts/usage_report.py \
  --db "$tmp_dir/agent_usage.sqlite" \
  --json \
  >/dev/null

echo "ci-smoke: clean"
