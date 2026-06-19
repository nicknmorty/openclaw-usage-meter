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

clean_repo="$tmp_dir/openclaw-usage-meter"
mkdir -p "$clean_repo"

git archive --format=tar HEAD | tar -x -C "$clean_repo"

if [ -e "$clean_repo/.git" ]; then
  echo "clean-clone: .git should not be present in archive checkout" >&2
  exit 1
fi

cd "$clean_repo"

echo "clean-clone: Python syntax"
python3 -m py_compile \
  scripts/agent_usage_collect.py \
  scripts/usage_report.py \
  scripts/fetch_openai_usage.py \
  scripts/test-fixture-collection.py

echo "clean-clone: Node extension syntax"
node --check extension/index.js

echo "clean-clone: Shell syntax"
bash -n \
  scripts/ci-smoke.sh \
  scripts/ci-smoke-core.sh \
  scripts/ci-smoke-local.sh \
  scripts/public-audit.sh \
  scripts/test-clean-clone.sh

echo "clean-clone: CLI help"
python3 scripts/agent_usage_collect.py --help >/dev/null
python3 scripts/usage_report.py --help >/dev/null
python3 scripts/fetch_openai_usage.py --help >/dev/null

echo "clean-clone: fixture collection"
python3 scripts/test-fixture-collection.py

echo "clean-clone: clean"
