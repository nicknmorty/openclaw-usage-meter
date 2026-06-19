#!/usr/bin/env python3
"""Exercise collection/reporting against public-safe fixture JSONL."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_AGENTS = REPO_ROOT / "tests" / "fixtures" / "openclaw-agents"
EXPECTED_COST = 0.00081


def run(args: list[str]) -> str:
    proc = subprocess.run(
        args,
        cwd=REPO_ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return proc.stdout


def assert_equal(actual: object, expected: object, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def assert_close(actual: float, expected: float, label: str, tolerance: float = 0.00000001) -> None:
    if abs(actual - expected) > tolerance:
        raise AssertionError(f"{label}: expected {expected}, got {actual}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="usage-meter-fixture-") as td:
        tmp = Path(td)
        fixture_copy = tmp / "agents"
        shutil.copytree(FIXTURE_AGENTS, fixture_copy)
        db_path = tmp / "agent_usage.sqlite"
        workspace = tmp / "workspace"
        workspace.mkdir()

        collect_raw = run([
            sys.executable,
            "scripts/agent_usage_collect.py",
            "--db",
            str(db_path),
            "--agents-dir",
            str(fixture_copy),
            "--workspace",
            str(workspace),
        ])
        collect = json.loads(collect_raw)
        assert_equal(collect["inserted_usage_events"], 1, "inserted usage events")
        assert_equal(collect["stored_assistant_turns"], 1, "stored assistant turns")
        assert_equal(collect["stored_total_tokens"], 470, "stored total tokens")

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            event = conn.execute("SELECT * FROM usage_events").fetchone()
            if event is None:
                raise AssertionError("expected one usage event")
            assert_equal(event["provider"], "anthropic", "event provider")
            assert_equal(event["model"], "claude-sonnet-4-6", "event model")
            assert_equal(event["input_tokens"], 100, "input tokens")
            assert_equal(event["output_tokens"], 20, "output tokens")
            assert_equal(event["cache_read_tokens"], 300, "cache read tokens")
            assert_equal(event["cache_write_tokens"], 50, "cache write tokens")
            assert_equal(event["total_tokens"], 470, "total tokens")
            assert_equal(event["cost_source"], "computed", "cost source")
            assert_close(float(event["cost_usd"]), EXPECTED_COST, "computed cost")

            counter = conn.execute("SELECT * FROM session_counters").fetchone()
            if counter is None:
                raise AssertionError("expected one session counter row")
            assert_equal(counter["user_messages"], 1, "user messages")
            assert_equal(counter["assistant_turns"], 1, "assistant turns")
            assert_equal(counter["total_tokens"], 470, "counter total tokens")
            assert_close(float(counter["cost_usd"]), EXPECTED_COST, "counter cost")
        finally:
            conn.close()

        report_raw = run([
            sys.executable,
            "scripts/usage_report.py",
            "--db",
            str(db_path),
            "--model",
            "--json",
        ])
        report = json.loads(report_raw)
        assert_equal(report["report"], "model", "report kind")
        assert_equal(len(report["rows"]), 1, "report row count")
        row = report["rows"][0]
        assert_equal(row["model"], "claude-sonnet-4-6", "report model")
        assert_equal(row["provider"], "anthropic", "report provider")
        assert_equal(row["events"], 1, "report events")
        assert_equal(row["tokens"], 470, "report tokens")

    print("fixture-collection: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
