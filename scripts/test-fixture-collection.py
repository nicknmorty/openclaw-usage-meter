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
EXPECTED_OPENAI_COST = 0.003375
EXPECTED_TOTAL_COST = EXPECTED_COST + EXPECTED_OPENAI_COST
EXPECTED_TOTAL_TOKENS = 13_038


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
        assert_equal(collect["inserted_usage_events"], 3, "inserted usage events")
        assert_equal(collect["stored_assistant_turns"], 3, "stored assistant turns")
        assert_equal(collect["stored_total_tokens"], EXPECTED_TOTAL_TOKENS, "stored total tokens")

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            events = {
                row["provider"]: row
                for row in conn.execute("SELECT * FROM usage_events ORDER BY provider").fetchall()
            }
            assert_equal(set(events), {"anthropic", "openai", "zai"}, "event providers")

            event = events["anthropic"]
            assert_equal(event["provider"], "anthropic", "event provider")
            assert_equal(event["model"], "claude-sonnet-4-6", "event model")
            assert_equal(event["input_tokens"], 100, "input tokens")
            assert_equal(event["output_tokens"], 20, "output tokens")
            assert_equal(event["cache_read_tokens"], 300, "cache read tokens")
            assert_equal(event["cache_write_tokens"], 50, "cache write tokens")
            assert_equal(event["total_tokens"], 470, "total tokens")
            assert_equal(event["cost_source"], "computed", "cost source")
            assert_close(float(event["cost_usd"]), EXPECTED_COST, "computed cost")

            zai_event = events["zai"]
            assert_equal(zai_event["provider"], "zai", "zai event provider")
            assert_equal(zai_event["model"], "glm-5.1", "zai event model")
            assert_equal(zai_event["input_tokens"], 123, "zai input tokens")
            assert_equal(zai_event["output_tokens"], 45, "zai output tokens")
            assert_equal(zai_event["cache_read_tokens"], 0, "zai cache read tokens")
            assert_equal(zai_event["cache_write_tokens"], 0, "zai cache write tokens")
            assert_equal(zai_event["total_tokens"], 168, "zai total tokens")
            assert_equal(zai_event["cost_source"], "unknown", "zai cost source")
            assert_close(float(zai_event["cost_usd"]), 0.0, "zai unknown cost")

            openai_event = events["openai"]
            assert_equal(openai_event["provider"], "openai", "openai event provider")
            assert_equal(openai_event["model"], "gpt-5.4-mini", "openai event model")
            assert_equal(openai_event["input_tokens"], 2000, "openai input tokens")
            assert_equal(openai_event["output_tokens"], 400, "openai output tokens")
            assert_equal(openai_event["cache_read_tokens"], 1000, "openai cache read tokens")
            assert_equal(openai_event["cache_write_tokens"], 9000, "openai cache write tokens")
            assert_equal(openai_event["total_tokens"], 12400, "openai total tokens")
            assert_equal(openai_event["cost_source"], "computed", "openai cost source")
            assert_close(float(openai_event["cost_usd"]), EXPECTED_OPENAI_COST, "openai computed cost")

            counter = conn.execute(
                "SELECT SUM(user_messages) AS user_messages,"
                " SUM(assistant_turns) AS assistant_turns,"
                " SUM(total_tokens) AS total_tokens,"
                " SUM(cost_usd) AS cost_usd"
                " FROM session_counters"
            ).fetchone()
            if counter is None:
                raise AssertionError("expected session counter summary")
            assert_equal(counter["user_messages"], 3, "user messages")
            assert_equal(counter["assistant_turns"], 3, "assistant turns")
            assert_equal(counter["total_tokens"], EXPECTED_TOTAL_TOKENS, "counter total tokens")
            assert_close(float(counter["cost_usd"]), EXPECTED_TOTAL_COST, "counter cost")

            conn.execute(
                "UPDATE usage_events SET cost_usd=0, cost_source='unknown' WHERE provider='openai'"
            )
            conn.execute(
                """INSERT INTO usage_events(
                     event_key, session_id, source_file, line_no, event_id, event_at, role,
                     provider, model, input_tokens, output_tokens, cache_read_tokens,
                     cache_write_tokens, total_tokens, cost_usd, cost_source)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "fixture:prefix-guard",
                    "session-zai",
                    "fixture-prefix-guard",
                    1,
                    "evt_prefix_guard",
                    "2026-06-01T12:20:00+00:00",
                    "assistant",
                    "openai",
                    "gpt-5.4-mini-unpriced-experimental",
                    10,
                    1,
                    0,
                    0,
                    11,
                    0.0,
                    "unknown",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        recalibrate_raw = run([
            sys.executable,
            "scripts/agent_usage_collect.py",
            "--db",
            str(db_path),
            "--recalibrate",
        ])
        recalibrate = json.loads(recalibrate_raw)
        assert_equal(recalibrate["recalibrated"], 1, "recalibrated events")
        assert_equal(recalibrate["skipped_no_pricing"], 2, "recalibrate skipped no pricing")
        assert_equal(recalibrate["skipped_unchanged"], 1, "recalibrate skipped unchanged")

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            openai_event = conn.execute(
                "SELECT cost_usd, cost_source FROM usage_events WHERE provider='openai' AND model='gpt-5.4-mini'"
            ).fetchone()
            if openai_event is None:
                raise AssertionError("expected OpenAI event after recalibrate")
            assert_equal(openai_event["cost_source"], "repaired", "openai recalibrate cost source")
            assert_close(float(openai_event["cost_usd"]), EXPECTED_OPENAI_COST, "openai repaired cost")

            prefix_guard = conn.execute(
                "SELECT cost_usd, cost_source FROM usage_events WHERE event_key='fixture:prefix-guard'"
            ).fetchone()
            if prefix_guard is None:
                raise AssertionError("expected prefix guard event after recalibrate")
            assert_equal(prefix_guard["cost_source"], "unknown", "prefix guard cost source")
            assert_close(float(prefix_guard["cost_usd"]), 0.0, "prefix guard cost")
            conn.execute("DELETE FROM usage_events WHERE event_key='fixture:prefix-guard'")
            conn.commit()
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
        assert_equal(len(report["rows"]), 3, "report row count")
        rows = {(row["provider"], row["model"]): row for row in report["rows"]}

        row = rows[("anthropic", "claude-sonnet-4-6")]
        assert_equal(row["events"], 1, "anthropic report events")
        assert_equal(row["tokens"], 470, "anthropic report tokens")

        zai_row = rows[("zai", "glm-5.1")]
        assert_equal(zai_row["events"], 1, "zai report events")
        assert_equal(zai_row["tokens"], 168, "zai report tokens")
        assert_close(float(zai_row["cost"]), 0.0, "zai report cost")

        openai_row = rows[("openai", "gpt-5.4-mini")]
        assert_equal(openai_row["events"], 1, "openai report events")
        assert_equal(openai_row["tokens"], 12400, "openai report tokens")
        assert_close(float(openai_row["cost"]), 0.0, "openai report rounded cost")

    print("fixture-collection: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
