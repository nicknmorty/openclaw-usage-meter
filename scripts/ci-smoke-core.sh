#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd -- "$script_dir/.." && pwd -P)"

cd "$repo_root"

echo "ci-smoke-core: Python syntax"
python3 -m py_compile \
  scripts/agent_usage_collect.py \
  scripts/usage_report.py \
  scripts/fetch_openai_usage.py

echo "ci-smoke-core: ruff"
python3 -m ruff check .

echo "ci-smoke-core: Node extension syntax"
node --check extension/index.js

echo "ci-smoke-core: CLI help"
python3 scripts/agent_usage_collect.py --help >/dev/null
python3 scripts/usage_report.py --help >/dev/null
python3 scripts/fetch_openai_usage.py --help >/dev/null

echo "ci-smoke-core: clean"
