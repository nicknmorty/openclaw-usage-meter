#!/usr/bin/env python3
"""usage_report.py — Companion query tool for agent_usage.sqlite.

Generates human-readable cost/usage reports from the local usage database.
No external dependencies beyond Python stdlib.

Usage:
  python3 scripts/usage_report.py                  # monthly summary (default)
  python3 scripts/usage_report.py --daily           # cost by day (last 30 days)
  python3 scripts/usage_report.py --daily --month 2026-06
  python3 scripts/usage_report.py --model           # cost by model (all time)
  python3 scripts/usage_report.py --today           # today's spend so far
  python3 scripts/usage_report.py --calibrate       # cache write sensitivity analysis
  python3 scripts/usage_report.py --breakdown       # token-type cost breakdown
  python3 scripts/usage_report.py --breakdown --month 2026-06
  python3 scripts/usage_report.py --week             # last 7 days
  python3 scripts/usage_report.py --ytd              # year to date (monthly)
  python3 scripts/usage_report.py --all             # run all report sections

Filters:
  --month YYYY-MM    Limit to a specific month
  --provider NAME    Limit to a specific provider (anthropic|openai|openrouter)
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import os
try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

DEFAULT_DB = Path.home() / ".openclaw" / "usage" / "agent_usage.sqlite"
DEFAULT_ACTUALS_PATH = Path.home() / ".openclaw" / "usage" / "subscriptions.json"

# Optional actual-paid billing config. Keep real billing numbers in a private
# --actuals JSON file instead of committing them to this repository.
# Schema:
# {
#   "providers": {
#     "openai": {"monthly_flat": 20.0, "label": "OpenAI subscription"},
#     "anthropic": {"actuals": {"2026-06": 123.45}, "label": "Anthropic API actual"}
#   }
# }
DEFAULT_ACTUAL_COSTS: dict[str, Any] = {
    "providers": {}
}

# ---------------------------------------------------------------------------
# Reporting timezone
# ---------------------------------------------------------------------------
# event_at is stored as UTC ISO timestamps. Day/week/today buckets should use
# the user's local day, not UTC, so "today" matches wall-clock expectations.
# Resolution order: --tz arg (set into env by main) > SPEND_TZ env > default.
DEFAULT_TZ = "America/Los_Angeles"


def _report_tz():
    name = os.environ.get("SPEND_TZ", DEFAULT_TZ)
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return timezone.utc


def _tz_now() -> datetime:
    """Current time in the reporting timezone."""
    return datetime.now(tz=_report_tz())


def _utc_offset_clause() -> str:
    """SQLite modifier that shifts a UTC timestamp to local reporting time.

    Uses the reporting tz's offset for *now*. Correct for today/week/daily
    windows except across a DST boundary inside the window (rare; the small
    edge error is acceptable for these short ranges).
    """
    off = _tz_now().utcoffset()
    total_min = int(off.total_seconds() // 60) if off else 0
    sign = "+" if total_min >= 0 else "-"
    total_min = abs(total_min)
    return f"{sign}{total_min} minutes"


def local_day_expr(col: str = "event_at") -> str:
    """SQL expression: local (reporting-tz) date string YYYY-MM-DD for a UTC col."""
    return f"substr(datetime({col}, '{_utc_offset_clause()}'),1,10)"


def local_month_expr(col: str = "event_at") -> str:
    """SQL expression: local (reporting-tz) month string YYYY-MM for a UTC col."""
    return f"substr(datetime({col}, '{_utc_offset_clause()}'),1,7)"

# ---------------------------------------------------------------------------
# Provider grouping
# ---------------------------------------------------------------------------
# Maps raw provider values in DB → display group.
# 'system' providers are excluded from cost totals by default.
PROVIDER_GROUPS: dict[str, str] = {
    "anthropic":    "anthropic",
    "openai":       "openai",
    "openai-codex": "openai",   # Codex OAuth via ChatGPT Pro subscription
    "codex":        "openai",   # older codex path
    "openrouter":   "openrouter",
    "openclaw":     "system",   # internal routing (delivery-mirror, gateway-injected)
}

SYSTEM_PROVIDERS = {"system"}


# ---------------------------------------------------------------------------
# Calibration: Anthropic cache write pricing
# ---------------------------------------------------------------------------
# Official Anthropic docs say cache write = 1.25× input rate.
# Empirical calibration against provider billing may show an effective rate
# closer to 0.80× input for some OpenClaw JSONL datasets.
# Hypothesis: OpenClaw records cumulative cache_write_tokens including
# tokens that Anthropic only charges once per cache TTL window.
# CALIBRATION_CW_MULT overrides the per-model cache_write_per_mtok
# when set to a non-None value.
CALIBRATION_CW_MULT: float | None = None  # None = use DB stored costs

# Per-model base input price ($/MTok) for sensitivity analysis.
# Mirrors PRICING_TABLE in agent_usage_collect.py.
_BASE_INPUT_PRICE: dict[str, float] = {
    "claude-opus-4-8":   5.0,
    "claude-opus-4-7":   5.0,
    "claude-opus-4-6":   5.0,
    "claude-opus-4-5":   5.0,
    "claude-sonnet-4-6": 3.0,
    "claude-sonnet-4-5": 3.0,
    "claude-haiku-4-5":  1.0,
    "claude-haiku-3-5":  0.80,
}
_BASE_OUTPUT_PRICE: dict[str, float] = {
    "claude-opus-4-8":   25.0,
    "claude-opus-4-7":   25.0,
    "claude-opus-4-6":   25.0,
    "claude-opus-4-5":   25.0,
    "claude-sonnet-4-6": 15.0,
    "claude-sonnet-4-5": 15.0,
    "claude-haiku-4-5":  5.0,
    "claude-haiku-3-5":  4.0,
}
_BASE_CR_PRICE: dict[str, float] = {
    "claude-opus-4-8":   0.50,
    "claude-opus-4-7":   0.50,
    "claude-opus-4-6":   0.50,
    "claude-opus-4-5":   0.50,
    "claude-sonnet-4-6": 0.30,
    "claude-sonnet-4-5": 0.30,
    "claude-haiku-4-5":  0.10,
    "claude-haiku-3-5":  0.08,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"DB not found: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def group_provider(raw: str | None) -> str:
    if not raw:
        return "unknown"
    return PROVIDER_GROUPS.get(raw.lower(), raw.lower())


def fmt_cost(v: float) -> str:
    if v >= 100:
        return f"${v:,.2f}"
    if v >= 1:
        return f"${v:.3f}"
    return f"${v:.5f}"


def fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def load_actual_costs(path: Path | None) -> dict[str, Any]:
    """Load optional actual-paid billing config."""
    import copy

    data = copy.deepcopy(DEFAULT_ACTUAL_COSTS)
    cfg_path = path or DEFAULT_ACTUALS_PATH
    if not cfg_path.exists():
        return data
    try:
        override = json.loads(cfg_path.read_text())
    except Exception as exc:
        raise ValueError(f"Could not read actual-paid config {cfg_path}: {exc}") from exc
    for provider, values in override.get("providers", {}).items():
        base = data.setdefault("providers", {}).setdefault(provider, {})
        if isinstance(values, dict):
            base.update(values)
    return data


def actual_paid_for(actuals: dict[str, Any], provider: str, month: str) -> tuple[float | None, str]:
    """Return configured actual paid dollars for provider/month, if known."""
    info = actuals.get("providers", {}).get(provider, {})
    label = str(info.get("label") or provider)
    if "actuals" in info and month in info["actuals"]:
        return float(info["actuals"][month]), label
    if "monthly_flat" in info:
        return float(info["monthly_flat"]), label
    return None, label


def _monthly_group_costs(
    conn: sqlite3.Connection, month: str | None = None
) -> dict[str, dict[str, float]]:
    """Return {month: {display_provider: api_equiv_cost}}."""
    cur = conn.cursor()
    mfrag, mparams = month_filter_sql(month)
    cur.execute(f"""
        SELECT substr(event_at,1,7) AS month,
               provider,
               ROUND(SUM(cost_usd),2) AS cost
        FROM usage_events
        WHERE event_at IS NOT NULL {mfrag}
        GROUP BY month, provider
        ORDER BY month, provider
    """, mparams)
    from collections import defaultdict

    result: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))  # type: ignore[assignment]
    for row in cur.fetchall():
        grp = group_provider(row["provider"])
        if grp in SYSTEM_PROVIDERS:
            continue
        result[row["month"]][grp] += row["cost"] or 0.0
    return {month_key: dict(values) for month_key, values in result.items()}


def month_filter_sql(month: str | None, col: str = "event_at") -> tuple[str, list[str]]:
    """Return (WHERE fragment, params) for optional month filter."""
    if month:
        return f"AND substr({col},1,7) = ?", [month]
    return "", []


# Reverse map: display group -> raw provider values stored in DB.
_GROUP_TO_RAW: dict[str, list[str]] = {}
for _raw, _grp in PROVIDER_GROUPS.items():
    _GROUP_TO_RAW.setdefault(_grp, []).append(_raw)


def provider_filter_sql(provider: str | None) -> tuple[str, list[str]]:
    """Return (WHERE fragment, params) for optional provider filter.

    Accepts a display group (anthropic|openai|openrouter) and expands it to
    the raw provider values stored in the DB, or a raw provider value directly.
    """
    if not provider:
        return "", []
    key = provider.strip().lower()
    raws = _GROUP_TO_RAW.get(key, [key])
    placeholders = ",".join("?" for _ in raws)
    return f"AND provider IN ({placeholders})", list(raws)


def today_str() -> str:
    return _tz_now().strftime("%Y-%m-%d")


def _codex_monthly_costs(
    conn: sqlite3.Connection, month: str | None = None
) -> dict[str, float]:
    """Return {month: api_equiv_cost} for openai-codex events."""
    cur = conn.cursor()
    mfrag, mparams = month_filter_sql(month)
    cur.execute(f"""
        SELECT substr(event_at,1,7) AS month,
               ROUND(SUM(cost_usd),2) AS cost
        FROM usage_events
        WHERE provider='openai-codex' AND event_at IS NOT NULL {mfrag}
        GROUP BY month
        ORDER BY month
    """, mparams)
    return {row["month"]: (row["cost"] or 0.0) for row in cur.fetchall()}


def _print_actual_paid_section(
    provider_costs: dict[str, dict[str, float]],
    codex_costs: dict[str, float],
    actuals: dict[str, Any],
    year_prefix: str | None = None,
) -> None:
    """Print actual-paid vs API-equivalent lines for configured billing streams."""
    months = sorted(set(provider_costs) | set(codex_costs))
    if year_prefix:
        months = [m for m in months if m.startswith(year_prefix)]
    if not months:
        return

    print("\n  Actual paid vs API-equivalent:")
    print(f"  {'Month':<10}  {'Stream':<22}  {'Actual paid':<13}  {'API-equiv':<12}  Multiple")
    print("  " + "-" * 76)
    for month in months:
        anthropic_actual, anthropic_label = actual_paid_for(actuals, "anthropic", month)
        if anthropic_actual is not None:
            api_equiv = provider_costs.get(month, {}).get("anthropic", 0.0)
            multiple = api_equiv / anthropic_actual if anthropic_actual else 0.0
            print(f"  {month:<10}  {anthropic_label:<22}  {fmt_cost(anthropic_actual):<13}  {fmt_cost(api_equiv):<12}  {multiple:.2f}x")

        openai_actual, openai_label = actual_paid_for(actuals, "openai", month)
        if openai_actual is not None:
            api_equiv = codex_costs.get(month, 0.0)
            multiple = api_equiv / openai_actual if openai_actual else 0.0
            marker = "  <- high-value" if multiple > 3.0 else ""
            print(f"  {month:<10}  {openai_label:<22}  {fmt_cost(openai_actual):<13}  {fmt_cost(api_equiv):<12}  {multiple:.2f}x{marker}")

        claude_actual, claude_label = actual_paid_for(actuals, "claude_pro", month)
        if claude_actual is not None:
            print(f"  {month:<10}  {claude_label:<22}  {fmt_cost(claude_actual):<13}  {'n/a':<12}  n/a")
    print("  Note: API-equiv is computed from recorded token volume at list pricing.")


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

def report_monthly(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    """Monthly cost summary, broken out by provider group."""
    prov = getattr(args, "provider", None)
    pfrag, pparams = provider_filter_sql(prov)
    suffix = f" — {prov}" if prov else ""
    print(f"\n=== Monthly Cost Summary{suffix} ===")
    print("  " + "{:<10}  {:<12}  {:<13}  Events".format("Month", "Provider", "Cost"))
    print("-" * 72)

    cur = conn.cursor()
    cur.execute("""
        SELECT substr(event_at,1,7) AS month,
               provider,
               ROUND(SUM(cost_usd),2) AS total_cost,
               COUNT(*) AS events,
               GROUP_CONCAT(DISTINCT model) AS models
        FROM usage_events
        WHERE event_at IS NOT NULL """ + pfrag + """
        GROUP BY month, provider
        ORDER BY month, provider
    """, pparams)
    rows = cur.fetchall()

    # Aggregate by (month, display_group)
    from collections import defaultdict
    agg: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {"cost": 0.0, "events": 0, "models": set()})
    for row in rows:
        month = row["month"]
        grp = group_provider(row["provider"])
        if grp in SYSTEM_PROVIDERS:
            continue
        key = (month, grp)
        agg[key]["cost"] += row["total_cost"] or 0
        agg[key]["events"] += row["events"]
        for m in (row["models"] or "").split(","):
            if m.strip():
                agg[key]["models"].add(m.strip())

    # Monthly totals too
    month_totals: dict[str, float] = defaultdict(float)
    for (month, grp), d in sorted(agg.items()):
        month_totals[month] += d["cost"]

    current_month = ""
    for (month, grp), d in sorted(agg.items()):
        if month != current_month:
            if current_month:
                print(f"  {'':10}  {'TOTAL':12}  {fmt_cost(month_totals[current_month]):<13}")
                print()
            current_month = month
        model_str = ", ".join(sorted(d["models"]))[:45]
        print(f"  {month:<10}  {grp:<12}  {fmt_cost(d['cost']):<13}  {d['events']:>8}  {model_str}")

    if current_month:
        print(f"  {'':10}  {'TOTAL':12}  {fmt_cost(month_totals[current_month]):<13}")

    # All-time
    cur.execute("SELECT ROUND(SUM(cost_usd),2) FROM usage_events WHERE 1=1 " + pfrag, pparams)
    alltime = cur.fetchone()[0] or 0
    print(f"\n  All-time total (API list pricing): {fmt_cost(alltime)}")
    print("  Note: OpenAI 'openai-codex' costs reflect subscription-equivalent value,")
    print("        not direct API billing. Configure --actuals for flat monthly charges.")

    _print_actual_paid_section(
        _monthly_group_costs(conn),
        _codex_monthly_costs(conn),
        args.actual_costs,
    )


def report_today(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    """Today's spending so far."""
    today = today_str()
    prov = getattr(args, "provider", None)
    pfrag, pparams = provider_filter_sql(prov)
    suffix = f" — {prov}" if prov else ""
    print(f"\n=== Today ({today} local){suffix} ===")

    cur = conn.cursor()
    cur.execute("""
        SELECT model, provider,
               COUNT(*) AS events,
               SUM(input_tokens) AS inp,
               SUM(output_tokens) AS out,
               SUM(cache_read_tokens) AS cr,
               SUM(cache_write_tokens) AS cw,
               ROUND(SUM(cost_usd),4) AS cost
        FROM usage_events
        WHERE """ + local_day_expr() + """ = ?
          AND event_at IS NOT NULL """ + pfrag + """
        GROUP BY model, provider
        ORDER BY cost DESC
    """, [today] + pparams)
    rows = cur.fetchall()

    total = 0.0
    has_data = False
    print("  Month         Provider      Cost           Events")
    print("  " + "-" * 70)
    for row in rows:
        grp = group_provider(row["provider"])
        if grp in SYSTEM_PROVIDERS:
            continue
        has_data = True
        tokens = (row["inp"] or 0) + (row["out"] or 0) + (row["cr"] or 0)
        cost = row["cost"] or 0.0
        total += cost
        print(f"  {(row['model'] or 'unknown'):<28}  {grp:<12}  {row['events']:>6}  {fmt_tokens(tokens):>10}  {fmt_cost(cost)}")

    if not has_data:
        print("  No events yet today.")
    else:
        print(f"  {'':28}  {'TOTAL':<12}  {'':>6}  {'':>10}  {fmt_cost(total)}")


def report_daily(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    """Cost by day."""
    month = getattr(args, "month", None)
    mfrag, mparams = month_filter_sql(month)
    prov = getattr(args, "provider", None)
    pfrag, pparams = provider_filter_sql(prov)

    title = (f"month={month}" if month else "last 30 days") + (f", {prov}" if prov else "")
    print(f"\n=== Daily Cost ({title}) ===")

    day_expr = local_day_expr()
    where = f"WHERE event_at IS NOT NULL {mfrag} {pfrag}"
    if not month:
        where += f" AND {day_expr} >= date('now','-30 days')"

    cur = conn.cursor()
    cur.execute(f"""
        SELECT {day_expr} AS day,
               provider,
               ROUND(SUM(cost_usd),4) AS cost,
               COUNT(*) AS events
        FROM usage_events
        {where}
        GROUP BY day, provider
        ORDER BY day, provider
    """, mparams + pparams)
    rows = cur.fetchall()

    from collections import defaultdict
    day_totals: dict[str, float] = defaultdict(float)
    day_breakdown: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in rows:
        grp = group_provider(row["provider"])
        if grp in SYSTEM_PROVIDERS:
            continue
        day = row["day"]
        cost = row["cost"] or 0.0
        day_totals[day] += cost
        day_breakdown[day][grp] += cost

    print("  Day           Total       anthropic     openai        openrouter")
    print("  " + "-" * 60)
    for day in sorted(day_totals):
        bd = day_breakdown[day]
        print(f"  {day:<12}  {fmt_cost(day_totals[day]):<10}  {fmt_cost(bd.get('anthropic',0.0)):<12}  {fmt_cost(bd.get('openai',0.0)):<12}  {fmt_cost(bd.get('openrouter',0.0))}")




def report_week(conn, args):
    """Cost for the last 7 days (today included)."""
    from datetime import timedelta
    today = _tz_now().date()
    week_start = (today - timedelta(days=6)).isoformat()
    prov = getattr(args, "provider", None)
    pfrag, pparams = provider_filter_sql(prov)
    suffix = f" — {prov}" if prov else ""
    print(f"\n=== Last 7 Days ({week_start} — {today}){suffix} ===")

    cur = conn.cursor()
    cur.execute("""
        SELECT """ + local_day_expr() + """ AS day,
               provider,
               ROUND(SUM(cost_usd),4) AS cost,
               COUNT(*) AS events
        FROM usage_events
        WHERE event_at IS NOT NULL
          AND """ + local_day_expr() + """ >= ? """ + pfrag + """
        GROUP BY day, provider
        ORDER BY day, provider
    """, [week_start] + pparams)
    rows = cur.fetchall()

    from collections import defaultdict
    day_totals = defaultdict(float)
    day_breakdown = defaultdict(lambda: defaultdict(float))
    for row in rows:
        grp = group_provider(row["provider"])
        if grp in SYSTEM_PROVIDERS:
            continue
        day_totals[row["day"]] += row["cost"] or 0.0
        day_breakdown[row["day"]][grp] += row["cost"] or 0.0

    print("  Day           Total       anthropic     openai        openrouter")
    print("  " + "-" * 62)
    week_total = 0.0
    for day in sorted(day_totals):
        bd = day_breakdown[day]
        week_total += day_totals[day]
        print(f"  {day:<12}  {fmt_cost(day_totals[day]):<10}  {fmt_cost(bd.get('anthropic',0.0)):<12}  {fmt_cost(bd.get('openai',0.0)):<12}  {fmt_cost(bd.get('openrouter',0.0))}")
    print("  7-day total   " + fmt_cost(week_total))


def report_ytd(conn, args):
    """Year-to-date cost by month and provider."""
    year = datetime.now(tz=timezone.utc).year
    prov = getattr(args, "provider", None)
    pfrag, pparams = provider_filter_sql(prov)
    suffix = f" — {prov}" if prov else ""
    print(f"\n=== Year to Date ({year}){suffix} ===")
    print("  Month         Provider      Cost           Events")
    print("  " + "-" * 55)

    cur = conn.cursor()
    cur.execute("""
        SELECT substr(event_at,1,7) AS month,
               provider,
               ROUND(SUM(cost_usd),2) AS cost,
               COUNT(*) AS events
        FROM usage_events
        WHERE event_at IS NOT NULL
          AND substr(event_at,1,4) = ? """ + pfrag + """
        GROUP BY month, provider
        ORDER BY month, provider
    """, [str(year)] + pparams)
    rows = cur.fetchall()

    from collections import defaultdict
    agg = defaultdict(lambda: {"cost": 0.0, "events": 0})
    for row in rows:
        grp = group_provider(row["provider"])
        if grp in SYSTEM_PROVIDERS:
            continue
        key = (row["month"], grp)
        agg[key]["cost"] += row["cost"] or 0.0
        agg[key]["events"] += row["events"]

    from collections import defaultdict as dd2
    month_totals = dd2(float)
    for (month, grp), d in agg.items():
        month_totals[month] += d["cost"]

    current_month = ""
    for (month, grp), d in sorted(agg.items()):
        if month != current_month:
            if current_month:
                print(f"  {'':10}  {'TOTAL':<12}  {fmt_cost(month_totals[current_month]):<13}")
            current_month = month
        print(f"  {month:<10}  {grp:<12}  {fmt_cost(d['cost']):<13}  {d['events']}")
    if current_month:
        print(f"  {'':10}  {'TOTAL':<12}  {fmt_cost(month_totals[current_month]):<13}")
    ytd_total = sum(month_totals.values())
    print(f"\n  YTD total: {fmt_cost(ytd_total)}")
    print("  Note: OpenAI costs include ChatGPT Pro subscription-equivalent value.")

    _print_actual_paid_section(
        _monthly_group_costs(conn),
        _codex_monthly_costs(conn),
        args.actual_costs,
        year_prefix=str(year),
    )

def report_model(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    """Cost breakdown by model (all time, or filtered month)."""
    month = getattr(args, "month", None)
    from collections import defaultdict
    mfrag, mparams = month_filter_sql(month)
    prov = getattr(args, "provider", None)
    pfrag, pparams = provider_filter_sql(prov)
    title = (f"month={month}" if month else "all time") + (f", {prov}" if prov else "")
    print(f"\n=== Cost by Model ({title}) ===")

    cur = conn.cursor()
    cur.execute(f"""
        SELECT model, provider,
               COUNT(*) AS events,
               SUM(input_tokens+output_tokens+cache_read_tokens+cache_write_tokens) AS total_tokens,
               ROUND(SUM(cost_usd),4) AS cost
        FROM usage_events
        WHERE event_at IS NOT NULL {mfrag} {pfrag}
        GROUP BY model, provider
    """, mparams + pparams)
    rows = cur.fetchall()

    # Aggregate in Python by (model, display_group) to merge duplicate rows
    # (e.g. gpt-5.5 appearing under both 'openai' and 'openai-codex' providers).
    agg: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {"events": 0, "tokens": 0, "cost": 0.0})
    for row in rows:
        grp = group_provider(row["provider"])
        if grp in SYSTEM_PROVIDERS:
            continue
        key = (row["model"] or "unknown", grp)
        agg[key]["events"] += row["events"]
        agg[key]["tokens"] += row["total_tokens"] or 0
        agg[key]["cost"] += row["cost"] or 0.0

    sorted_rows = sorted(agg.items(), key=lambda x: x[1]["cost"], reverse=True)

    total = 0.0
    print("  Model                           Provider      Events         Tokens    Cost")
    print("  " + "-" * 75)
    for (model, grp), d in sorted_rows:
        cost = round(d["cost"], 2)
        total += cost
        print(f"  {model:<30}  {grp:<12}  {d['events']:>8}  {fmt_tokens(d['tokens']):>10}  {fmt_cost(cost)}")
    print(f"  {'TOTAL':<30}  {'':12}  {'':>8}  {'':>10}  {fmt_cost(total)}")


def report_breakdown(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    """Token-type cost breakdown for Anthropic models (for calibration)."""
    month = getattr(args, "month", None)
    mfrag, mparams = month_filter_sql(month)
    title = f"month={month}" if month else "all time"
    print(f"\n=== Token-Type Cost Breakdown — Anthropic ({title}) ===")
    print("  (Uses DB stored costs; see --calibrate for cache_write sensitivity)")

    cur = conn.cursor()
    cur.execute(f"""
        SELECT model,
               COUNT(*) AS events,
               SUM(input_tokens) AS inp,
               SUM(output_tokens) AS out,
               SUM(cache_read_tokens) AS cr,
               SUM(cache_write_tokens) AS cw,
               ROUND(SUM(cost_usd),4) AS total_cost
        FROM usage_events
        WHERE provider='anthropic' AND event_at IS NOT NULL {mfrag}
        GROUP BY model
        ORDER BY total_cost DESC
    """, mparams)
    rows = cur.fetchall()

    if not rows:
        print("  No Anthropic data for this period.")
        return

    for row in rows:
        model = row["model"]
        inp_p = _BASE_INPUT_PRICE.get(model, None)
        out_p = _BASE_OUTPUT_PRICE.get(model, None)
        cr_p = _BASE_CR_PRICE.get(model, None)
        if not inp_p:
            # Try prefix match
            for key in _BASE_INPUT_PRICE:
                if model and model.startswith(key):
                    inp_p = _BASE_INPUT_PRICE[key]
                    out_p = _BASE_OUTPUT_PRICE[key]
                    cr_p = _BASE_CR_PRICE[key]
                    break

        inp_tok = row["inp"] or 0
        out_tok = row["out"] or 0
        cr_tok = row["cr"] or 0
        cw_tok = row["cw"] or 0
        total = row["total_cost"] or 0.0

        if inp_p:
            inp_cost = inp_tok * inp_p / 1_000_000
            out_cost = out_tok * out_p / 1_000_000
            cr_cost = cr_tok * cr_p / 1_000_000
            cw_cost = total - inp_cost - out_cost - cr_cost
            cw_implied_price = (cw_cost / cw_tok * 1_000_000) if cw_tok else 0
        else:
            inp_cost = out_cost = cr_cost = cw_cost = cw_implied_price = 0

        print(f"\n  {model}  ({row['events']} events, total {fmt_cost(total)})")
        print(f"    input:        {fmt_tokens(inp_tok):>8}  × ${inp_p or '?'}/MTok  = {fmt_cost(inp_cost) if inp_p else '?'}")
        print(f"    output:       {fmt_tokens(out_tok):>8}  × ${out_p or '?'}/MTok  = {fmt_cost(out_cost) if out_p else '?'}")
        print(f"    cache_read:   {fmt_tokens(cr_tok):>8}  × ${cr_p or '?'}/MTok  = {fmt_cost(cr_cost) if inp_p else '?'}")
        print(f"    cache_write:  {fmt_tokens(cw_tok):>8}  (remainder = {fmt_cost(cw_cost) if inp_p else '?'}, implied ${cw_implied_price:.4f}/MTok)")


def report_calibrate(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    """Cache write multiplier sensitivity analysis for Anthropic models."""
    month = getattr(args, "month", None)
    mfrag, mparams = month_filter_sql(month)
    title = f"month={month}" if month else "all time"
    print(f"\n=== Cache Write Calibration — Anthropic ({title}) ===")
    print("  Computes estimated cost at different cache_write multipliers.")
    print("  Enter actual bill from Anthropic console to find the right multiplier.")
    print()

    cur = conn.cursor()
    cur.execute(f"""
        SELECT model,
               SUM(input_tokens) AS inp,
               SUM(output_tokens) AS out,
               SUM(cache_read_tokens) AS cr,
               SUM(cache_write_tokens) AS cw
        FROM usage_events
        WHERE provider='anthropic' AND event_at IS NOT NULL {mfrag}
        GROUP BY model
    """, mparams)
    rows = cur.fetchall()

    if not rows:
        print("  No Anthropic data for this period.")
        return

    # Print token totals
    print("  Token totals (source: JSONL from API responses):")
    for row in rows:
        model = row["model"]
        print(f"    {model}: inp={fmt_tokens(row['inp'] or 0)}, out={fmt_tokens(row['out'] or 0)}, "
              f"cache_read={fmt_tokens(row['cr'] or 0)}, cache_write={fmt_tokens(row['cw'] or 0)}")

    actual = getattr(args, "actual", None)
    if actual is not None:
        cmp_hdr = f"vs ${actual:.2f} actual"
        print(f"  Comparing against actual bill: ${actual:.2f}")
    else:
        cmp_hdr = "(pass --actual AMOUNT to compare)"
        print("  Tip: pass --actual AMOUNT to compare against your Anthropic bill.")
    print()

    header = "  {:>8}  {:>16}  {:>16}".format("cw_mult", "cw_price (opus)", "Estimated Total")
    if actual:
        header += f"  {cmp_hdr:>30}"
    print(header)
    print("  " + "-" * (75 if not actual else 90))

    best_fit_mult = None
    best_fit_delta = float("inf")
    results = []
    for cw_mult in [1.25, 1.00, 0.90, 0.80, 0.75, 0.70, 0.60, 0.50]:
        total = 0.0
        for row in rows:
            model = row["model"]
            inp_p = None
            out_p = None
            cr_p = None
            for key in _BASE_INPUT_PRICE:
                if model and (model == key or model.startswith(key)):
                    inp_p = _BASE_INPUT_PRICE[key]
                    out_p = _BASE_OUTPUT_PRICE[key]
                    cr_p = _BASE_CR_PRICE[key]
                    break
            if not inp_p:
                continue
            inp = row["inp"] or 0
            out = row["out"] or 0
            cr = row["cr"] or 0
            cw = row["cw"] or 0
            total += (inp * inp_p + out * out_p + cr * cr_p + cw * inp_p * cw_mult) / 1_000_000
        if actual is not None and abs(total - actual) < best_fit_delta:
            best_fit_delta = abs(total - actual)
            best_fit_mult = cw_mult
        results.append((cw_mult, total))

    for cw_mult, total in results:
        cw_price_opus = 5.0 * cw_mult
        marker = " ← best fit" if (actual is not None and cw_mult == best_fit_mult) else ""
        if actual is not None:
            delta = total - actual
            print(f"  {cw_mult:>8.2f}  ${cw_price_opus:>14.4f}/MTok  {fmt_cost(total):>16}  {delta:>+16.2f}{marker}")
        else:
            print(f"  {cw_mult:>8.2f}  ${cw_price_opus:>14.4f}/MTok  {fmt_cost(total):>16}{marker}")

    print()
    print("  To recalibrate: pull Anthropic console → June → per-category breakdown")
    print("  Update PRICING_TABLE cache_write_per_mtok in agent_usage_collect.py then")
    print("  re-run: python3 scripts/agent_usage_collect.py --repair-costs")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def report_json(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    """Output structured JSON for a given report type (for programmatic consumers)."""
    from collections import defaultdict
    from datetime import timedelta
    report = None
    prov = getattr(args, "provider", None)
    pfrag, pparams = provider_filter_sql(prov)

    if getattr(args, "today", False):
        today = today_str()
        cur = conn.cursor()
        cur.execute("""
            SELECT model, provider, COUNT(*) AS events,
                   SUM(input_tokens+output_tokens+cache_read_tokens+cache_write_tokens) AS tokens,
                   ROUND(SUM(cost_usd),4) AS cost
            FROM usage_events
            WHERE """ + local_day_expr() + """ = ? AND event_at IS NOT NULL """ + pfrag + """
            GROUP BY model, provider ORDER BY cost DESC
        """, [today] + pparams)
        rows = []
        total = 0.0
        for r in cur.fetchall():
            grp = group_provider(r["provider"])
            if grp in SYSTEM_PROVIDERS:
                continue
            total += r["cost"] or 0
            rows.append({"model": r["model"], "provider": grp,
                         "events": r["events"], "tokens": r["tokens"] or 0, "cost": round(r["cost"] or 0, 4)})
        report = {"report": "today", "date": today, "provider": prov, "rows": rows, "total": round(total, 4)}

    elif getattr(args, "week", False):
        today_date = _tz_now().date()
        week_start = (today_date - timedelta(days=6)).isoformat()
        cur = conn.cursor()
        cur.execute("""
            SELECT """ + local_day_expr() + """ AS day, provider,
                   ROUND(SUM(cost_usd),4) AS cost
            FROM usage_events
            WHERE event_at IS NOT NULL AND """ + local_day_expr() + """ >= ? """ + pfrag + """
            GROUP BY day, provider ORDER BY day, provider
        """, [week_start] + pparams)
        day_totals: dict = defaultdict(float)
        day_breakdown: dict = defaultdict(lambda: defaultdict(float))
        for r in cur.fetchall():
            grp = group_provider(r["provider"])
            if grp in SYSTEM_PROVIDERS:
                continue
            day_totals[r["day"]] += r["cost"] or 0
            day_breakdown[r["day"]][grp] += r["cost"] or 0
        rows = []
        for day in sorted(day_totals):
            bd = day_breakdown[day]
            rows.append({"day": day, "total": round(day_totals[day], 4),
                         "anthropic": round(bd.get("anthropic", 0), 4),
                         "openai": round(bd.get("openai", 0), 4),
                         "openrouter": round(bd.get("openrouter", 0), 4)})
        report = {"report": "week", "start": week_start, "end": str(today_date),
                  "provider": prov, "rows": rows, "total": round(sum(day_totals.values()), 4)}

    elif getattr(args, "ytd", False):
        year = datetime.now(tz=timezone.utc).year
        cur = conn.cursor()
        cur.execute("""
            SELECT substr(event_at,1,7) AS month, provider,
                   ROUND(SUM(cost_usd),2) AS cost
            FROM usage_events
            WHERE event_at IS NOT NULL AND substr(event_at,1,4) = ? """ + pfrag + """
            GROUP BY month, provider ORDER BY month, provider
        """, [str(year)] + pparams)
        agg: dict = defaultdict(lambda: defaultdict(float))
        for r in cur.fetchall():
            grp = group_provider(r["provider"])
            if grp in SYSTEM_PROVIDERS:
                continue
            agg[r["month"]][grp] += r["cost"] or 0
        months = []
        ytd_total = 0.0
        for month in sorted(agg):
            providers = {k: round(v, 2) for k, v in agg[month].items()}
            total = round(sum(providers.values()), 2)
            ytd_total += total
            months.append({"month": month, "providers": providers, "total": total})
        report = {"report": "ytd", "year": year, "provider": prov, "months": months, "total": round(ytd_total, 2)}

    elif getattr(args, "model", False):
        cur = conn.cursor()
        cur.execute("""
            SELECT model, provider,
                   COUNT(*) AS events,
                   SUM(input_tokens+output_tokens+cache_read_tokens+cache_write_tokens) AS tokens,
                   ROUND(SUM(cost_usd),4) AS cost
            FROM usage_events WHERE event_at IS NOT NULL """ + pfrag + """
            GROUP BY model, provider
        """, pparams)
        from collections import defaultdict as _dd
        _agg: dict = _dd(lambda: {"events": 0, "tokens": 0, "cost": 0.0})
        for r in cur.fetchall():
            grp = group_provider(r["provider"])
            if grp in SYSTEM_PROVIDERS:
                continue
            key = (r["model"] or "unknown", grp)
            _agg[key]["events"] += r["events"]
            _agg[key]["tokens"] += r["tokens"] or 0
            _agg[key]["cost"] += r["cost"] or 0.0
        rows = []
        total = 0.0
        for (model, grp), d in sorted(_agg.items(), key=lambda x: x[1]["cost"], reverse=True):
            cost = round(d["cost"], 2)
            total += cost
            rows.append({"model": model, "provider": grp,
                         "events": d["events"], "tokens": d["tokens"], "cost": cost})
        report = {"report": "model", "provider": prov, "rows": rows, "total": round(total, 2)}

    else:  # default: monthly
        cur = conn.cursor()
        cur.execute("""
            SELECT substr(event_at,1,7) AS month, provider,
                   ROUND(SUM(cost_usd),2) AS cost
            FROM usage_events WHERE event_at IS NOT NULL """ + pfrag + """
            GROUP BY month, provider ORDER BY month, provider
        """, pparams)
        agg2: dict = defaultdict(lambda: defaultdict(float))
        for r in cur.fetchall():
            grp = group_provider(r["provider"])
            if grp in SYSTEM_PROVIDERS:
                continue
            agg2[r["month"]][grp] += r["cost"] or 0
        months_data = []
        alltime = 0.0
        for month in sorted(agg2):
            providers = {k: round(v, 2) for k, v in agg2[month].items()}
            total = round(sum(providers.values()), 2)
            alltime += total
            months_data.append({"month": month, "providers": providers, "total": total})
        report = {"report": "monthly", "provider": prov, "months": months_data, "alltime": round(alltime, 2)}

    print(json.dumps(report))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Usage report for agent_usage.sqlite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Path to SQLite DB")
    parser.add_argument("--monthly",   action="store_true", help="Monthly cost summary")
    parser.add_argument("--today",     action="store_true", help="Today's spend so far")
    parser.add_argument("--daily",     action="store_true", help="Cost by day")
    parser.add_argument("--week",      action="store_true", help="Last 7 days")
    parser.add_argument("--ytd",       action="store_true", help="Year-to-date monthly")
    parser.add_argument("--model",     action="store_true", help="Cost by model")
    parser.add_argument("--breakdown", action="store_true", help="Token-type cost breakdown")
    parser.add_argument("--calibrate", action="store_true", help="Cache write calibration")
    parser.add_argument("--json",      action="store_true", help="Output structured JSON (for programmatic use)")
    parser.add_argument("--all",       action="store_true", help="Run all report sections")
    parser.add_argument("--month",     type=str, default=None, metavar="YYYY-MM",
                        help="Limit to specific month")
    parser.add_argument("--provider",  type=str, default=None,
                        help="Limit to provider group (anthropic|openai|openrouter)")
    parser.add_argument("--actual",    type=float, default=None, metavar="AMOUNT",
                        help="Actual Anthropic bill amount for calibration (e.g. --actual 123.45)")
    parser.add_argument("--actuals",   type=Path, default=None, metavar="PATH",
                        help="Actual-paid billing config JSON (default ~/.openclaw/usage/subscriptions.json)")
    parser.add_argument("--tz",        type=str, default=None, metavar="IANA_TZ",
                        help="Reporting timezone for today/week/daily buckets "
                             "(default America/Los_Angeles; env SPEND_TZ)")
    args = parser.parse_args()

    # Resolve reporting timezone: --tz wins, else SPEND_TZ env, else default.
    if args.tz:
        os.environ["SPEND_TZ"] = args.tz
    args.actual_costs = load_actual_costs(args.actuals)

    conn = connect(args.db)

    if args.json:
        report_json(conn, args)
        conn.close()
        return

    # Default: monthly summary
    run_monthly   = args.monthly   or args.all or not any([args.today, args.daily, args.week, args.ytd, args.model, args.breakdown, args.calibrate])
    run_today     = args.today     or args.all
    run_daily     = args.daily     or args.all
    run_model     = args.model     or args.all
    run_breakdown = args.breakdown or args.all
    run_week      = args.week      or args.all
    run_ytd       = args.ytd       or args.all
    run_calibrate = args.calibrate or args.all

    print(f"Agent Usage Report — DB: {args.db}")
    _tzname = os.environ.get("SPEND_TZ", DEFAULT_TZ)
    print(f"Generated: {_tz_now().strftime('%Y-%m-%d %H:%M')} ({_tzname}) "
          f"— day/week buckets are local; month/ytd are UTC")

    if run_monthly:   report_monthly(conn, args)
    if run_today:     report_today(conn, args)
    if run_daily:     report_daily(conn, args)
    if run_model:     report_model(conn, args)
    if run_breakdown: report_breakdown(conn, args)
    if run_week:      report_week(conn, args)
    if run_ytd:       report_ytd(conn, args)
    if run_calibrate: report_calibrate(conn, args)

    conn.close()


if __name__ == "__main__":
    main()
