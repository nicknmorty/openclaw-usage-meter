#!/usr/bin/env python3
"""Fetch OpenAI organization usage via Admin API.

Pulls completions usage (tokens, requests) and costs from
https://api.openai.com/v1/organization/usage/completions
and https://api.openai.com/v1/organization/costs
for a given date range.

Requires OPENAI_ADMIN_KEY (sk-admin-*) with api.usage.read scope.

Usage:
  python3 scripts/fetch_openai_usage.py
  python3 scripts/fetch_openai_usage.py --start 2026-03-01 --end 2026-06-09
  python3 scripts/fetch_openai_usage.py --save-json /tmp/openai_usage.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def load_admin_key() -> str:
    """Load OPENAI_ADMIN_KEY from env or ~/.openclaw/.env."""
    key = os.environ.get("OPENAI_ADMIN_KEY", "")
    if key:
        return key
    env_path = Path.home() / ".openclaw" / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            raw = line.strip().lstrip("#").strip()
            if raw.startswith("OPENAI_ADMIN_KEY="):
                return raw.split("=", 1)[1].strip()
    return ""


def api_get(path: str, params: dict, admin_key: str) -> dict:
    url = "https://api.openai.com" + path + "?" + urlencode(params)
    req = Request(url, headers={
        "Authorization": f"Bearer {admin_key}",
        "Content-Type": "application/json",
    })
    try:
        resp = urlopen(req, timeout=30)
        return json.loads(resp.read())
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"error": f"HTTP {e.code}: {body[:200]}"}
    except URLError as e:
        return {"error": str(e)}


def paginate(path: str, base_params: dict, admin_key: str) -> list:
    """Paginate through all results using next_page cursor."""
    params = dict(base_params)
    params["limit"] = 31  # max for 1d bucket_width
    all_data = []
    pages = 0
    while True:
        d = api_get(path, params, admin_key)
        if "error" in d:
            print(f"  API error: {d['error']}", file=sys.stderr)
            break
        items = d.get("data", [])
        all_data.extend(items)
        pages += 1
        if pages % 5 == 0:
            print(f"  ... fetched {len(all_data)} items ({pages} pages)", file=sys.stderr)
        next_page = d.get("next_page")
        if not next_page or not d.get("has_more"):
            break
        params["page"] = next_page
        time.sleep(0.15)
    print(f"  Done: {len(all_data)} total items, {pages} pages", file=sys.stderr)
    return all_data


def fetch_completions(admin_key: str, start_ts: int, end_ts: int) -> list:
    print("Fetching completions usage (grouped by model)...", file=sys.stderr)
    return paginate(
        "/v1/organization/usage/completions",
        {
            "start_time": start_ts,
            "end_time": end_ts,
            "bucket_width": "1d",
            "group_by[]": "model",
        },
        admin_key,
    )


def fetch_costs(admin_key: str, start_ts: int, end_ts: int) -> list:
    print("Fetching cost data (grouped by model)...", file=sys.stderr)
    return paginate(
        "/v1/organization/costs",
        {
            "start_time": start_ts,
            "end_time": end_ts,
            "bucket_width": "1d",
            "group_by[]": "line_item",
        },
        admin_key,
    )


def aggregate_completions(buckets: list) -> tuple[dict, dict, dict]:
    """Returns (daily, model_totals, monthly) dicts."""
    daily: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(
        lambda: {"input": 0, "output": 0, "cached": 0, "requests": 0}
    ))

    for bucket in buckets:
        day = bucket.get("start_time_iso", "")[:10]
        if not day:
            ts = bucket.get("start_time", 0)
            day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        for r in bucket.get("results", []):
            model = r.get("model") or "unknown"
            details = r.get("input_tokens_details") or {}
            daily[day][model]["input"]    += r.get("input_tokens", 0)
            daily[day][model]["output"]   += r.get("output_tokens", 0)
            daily[day][model]["cached"]   += details.get("cached_tokens", 0)
            daily[day][model]["requests"] += r.get("num_model_requests", 0)

    # Model totals
    model_totals: dict[str, dict] = defaultdict(
        lambda: {"input": 0, "output": 0, "cached": 0, "requests": 0}
    )
    for day_data in daily.values():
        for model, stats in day_data.items():
            for k in stats:
                model_totals[model][k] += stats[k]

    # Monthly totals
    monthly: dict[str, dict] = defaultdict(
        lambda: {"input": 0, "output": 0, "cached": 0, "requests": 0}
    )
    for day, day_data in daily.items():
        month = day[:7]
        for stats in day_data.values():
            for k in stats:
                monthly[month][k] += stats[k]

    return dict(daily), dict(model_totals), dict(monthly)


def aggregate_costs(buckets: list) -> tuple[dict, dict]:
    """Returns (daily_costs, monthly_costs) dicts keyed by date/month."""
    daily_costs: dict[str, float] = defaultdict(float)
    monthly_costs: dict[str, float] = defaultdict(float)

    for bucket in buckets:
        day = bucket.get("start_time_iso", "")[:10]
        if not day:
            ts = bucket.get("start_time", 0)
            day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        month = day[:7]
        for r in bucket.get("results", []):
            amount = r.get("amount") or {}
            cost = float(amount.get("value", 0))
            daily_costs[day] += cost
            monthly_costs[month] += cost

    return dict(daily_costs), dict(monthly_costs)


def fmt_tok(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n/1e9:.2f}B"
    if n >= 1_000_000:
        return f"{n/1e6:.1f}M"
    if n >= 1_000:
        return f"{n/1e3:.1f}K"
    return str(n)


def print_report(
    model_totals: dict,
    monthly: dict,
    monthly_costs: dict,
    daily: dict,
    daily_costs: dict,
) -> None:
    # --- Model totals ---
    print("\n=== Token Usage by Model (all time) ===")
    print(f"{'Model':<40} {'Requests':>10} {'Input':>12} {'Output':>10} {'Cached':>10}")
    print("-" * 88)
    for model, s in sorted(model_totals.items(), key=lambda x: -x[1]["input"]):
        if s["requests"] == 0:
            continue
        cached_pct = f"({s['cached']*100//s['input']}%" if s["input"] else ""
        print(f"{model:<40} {s['requests']:>10,} {fmt_tok(s['input']):>12} "
              f"{fmt_tok(s['output']):>10} {fmt_tok(s['cached']):>10} {cached_pct}")

    # --- Monthly summary ---
    print("\n=== Monthly Summary ===")
    print(f"{'Month':<10} {'Requests':>10} {'Input':>12} {'Output':>10} {'Cached':>10} {'API Cost $':>12}")
    print("-" * 72)
    total_cost = 0.0
    total_req = 0
    for month in sorted(monthly):
        s = monthly[month]
        cost = monthly_costs.get(month, 0.0)
        total_cost += cost
        total_req += s["requests"]
        cost_str = f"${cost:.2f}" if cost > 0 else "—"
        print(f"{month:<10} {s['requests']:>10,} {fmt_tok(s['input']):>12} "
              f"{fmt_tok(s['output']):>10} {fmt_tok(s['cached']):>10} {cost_str:>12}")
    print(f"{'TOTAL':<10} {total_req:>10,} {'':>12} {'':>10} {'':>10} ${total_cost:.2f}")

    # --- Daily breakdown (last 30 days with activity) ---
    active_days = [(d, v) for d, v in daily.items()
                   if any(s["requests"] > 0 for s in v.values())]
    active_days.sort(key=lambda x: x[0])
    if active_days:
        print("\n=== Daily Breakdown (days with activity) ===")
        print(f"{'Date':<12} {'Requests':>10} {'Input':>12} {'Output':>10} {'Cached':>10} {'API Cost $':>12}")
        print("-" * 72)
        for day, day_data in active_days:
            reqs = sum(s["requests"] for s in day_data.values())
            inp  = sum(s["input"]    for s in day_data.values())
            out  = sum(s["output"]   for s in day_data.values())
            cach = sum(s["cached"]   for s in day_data.values())
            cost = daily_costs.get(day, 0.0)
            cost_str = f"${cost:.4f}" if cost > 0 else "—"
            models_str = ", ".join(sorted(set(day_data.keys())))[:40]
            print(f"{day:<12} {reqs:>10,} {fmt_tok(inp):>12} {fmt_tok(out):>10} "
                  f"{fmt_tok(cach):>10} {cost_str:>12}  {models_str}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch OpenAI org usage via Admin API")
    ap.add_argument("--start", default="2026-03-01", help="Start date YYYY-MM-DD")
    ap.add_argument("--end",   default=None,         help="End date YYYY-MM-DD (default: today)")
    ap.add_argument("--save-json", type=Path, default=None, help="Save raw JSON to path")
    ap.add_argument("--no-costs", action="store_true", help="Skip cost endpoint")
    args = ap.parse_args()

    admin_key = load_admin_key()
    if not admin_key:
        print("ERROR: OPENAI_ADMIN_KEY not found in env or ~/.openclaw/.env", file=sys.stderr)
        return 1
    print(f"Using key: {admin_key[:12]}... (len={len(admin_key)})", file=sys.stderr)

    start_dt = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if args.end:
        end_dt = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        end_dt = datetime.now(tz=timezone.utc)
    start_ts = int(start_dt.timestamp())
    end_ts   = int(end_dt.timestamp())

    print(f"Date range: {start_dt.date()} to {end_dt.date()}", file=sys.stderr)

    # Fetch completions
    comp_buckets = fetch_completions(admin_key, start_ts, end_ts)
    daily, model_totals, monthly = aggregate_completions(comp_buckets)

    # Fetch costs
    daily_costs: dict = {}
    monthly_costs: dict = {}
    if not args.no_costs:
        cost_buckets = fetch_costs(admin_key, start_ts, end_ts)
        daily_costs, monthly_costs = aggregate_costs(cost_buckets)

    # Print report
    print_report(model_totals, monthly, monthly_costs, daily, daily_costs)

    # Save JSON
    if args.save_json:
        output = {
            "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
            "start": args.start,
            "end": end_dt.strftime("%Y-%m-%d"),
            "completions_buckets": comp_buckets,
            "daily": daily,
            "model_totals": model_totals,
            "monthly": monthly,
            "daily_costs": daily_costs,
            "monthly_costs": monthly_costs,
        }
        args.save_json.write_text(json.dumps(output, indent=2))
        print(f"\nRaw data saved to {args.save_json}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
