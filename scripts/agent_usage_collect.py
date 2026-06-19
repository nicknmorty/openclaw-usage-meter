#!/usr/bin/env python3
"""Collect OpenClaw agent/session usage into a durable SQLite database.

Source: ~/.openclaw/agents/*/sessions/*.jsonl and sessions.json indexes.
Destination default: ~/.openclaw/usage/agent_usage.sqlite

Idempotent: each session event is keyed by source file + JSONL line number + event id.
Message content is never stored; only routing/session metadata and usage counters are stored.

Cost computation:
  When the JSONL event writes cost=0 but token counts are non-zero, the collector
  falls back to computing cost from a local pricing table. This handles providers
  that do not emit usable cost data in their events. Current OpenClaw JSONL cost
  fields are observed as zero, so direct JSONL cost trust is disabled by default
  and only exists behind USAGE_TRUST_JSONL_COST=1 for future compatibility.
  Cost source is tracked: 'jsonl' (explicitly trusted JSONL cost), 'computed'
  (estimated from pricing table), 'repaired' (back-filled by --repair-costs).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

DEFAULT_AGENTS_DIR = Path.home() / ".openclaw" / "agents"
DEFAULT_DB = Path.home() / ".openclaw" / "usage" / "agent_usage.sqlite"
SCHEMA_VERSION = 2
TRUST_JSONL_COST = os.environ.get("USAGE_TRUST_JSONL_COST") == "1"


# ---------------------------------------------------------------------------
# Pricing table
# All prices are USD per million tokens ($/MTok).
# Sources:
#   Anthropic: https://docs.anthropic.com/en/about-claude/pricing (fetched 2026-06-09)
#   OpenAI: https://openai.com/api/pricing/ (fetched 2026-06-09)
# cache_write_per_mtok is the 5-minute cache write price.
# cache_read_per_mtok is the cache hit/refresh price.
# For OpenAI models, cache_write_per_mtok=0 (no explicit cache write fee).
# Anthropic cache_write multiplier: 0.80x base input rate (empirically calibrated
# by comparing computed estimates with provider billing; official docs say 1.25x).
# Verify with: python3 scripts/usage_report.py --calibrate --month YYYY-MM --actual AMOUNT
# ---------------------------------------------------------------------------

class ModelPrice(NamedTuple):
    input_per_mtok: float
    output_per_mtok: float
    cache_write_per_mtok: float  # 0 if provider doesn't charge cache writes
    cache_read_per_mtok: float
    provider_hint: str  # 'anthropic' | 'openai' | 'unknown'


# Keyed by canonical model ID prefix (longest match wins).
# Anthropic canonical IDs: claude-opus-4-8, claude-sonnet-4-6, etc.
# OpenAI canonical IDs: gpt-5.5, gpt-5.4, gpt-5.4-mini, etc.
PRICING_TABLE: dict[str, ModelPrice] = {
    # ---- Anthropic ----
    # Opus 4.8 / 4.7 / 4.6 / 4.5: $5 input, $25 output
    # Cache write (5-min): $4.00/MTok (0.80x base); cache read: $0.50/MTok (0.1x base)
    "claude-opus-4-8":          ModelPrice(5.0,  25.0, 4.00, 0.50, "anthropic"),
    "claude-opus-4-7":          ModelPrice(5.0,  25.0, 4.00, 0.50, "anthropic"),
    "claude-opus-4-6":          ModelPrice(5.0,  25.0, 4.00, 0.50, "anthropic"),
    "claude-opus-4-5":          ModelPrice(5.0,  25.0, 4.00, 0.50, "anthropic"),
    # Opus 4.1 / 4.0 (deprecated): $15 input, $75 output
    "claude-opus-4-1":          ModelPrice(15.0, 75.0, 12.00, 1.50, "anthropic"),
    "claude-opus-4-20250514":   ModelPrice(15.0, 75.0, 12.00, 1.50, "anthropic"),  # alias
    "claude-opus-4-0":          ModelPrice(15.0, 75.0, 12.00, 1.50, "anthropic"),
    # Sonnet 4.6 / 4.5 / 4.0: $3 input, $15 output
    "claude-sonnet-4-6":        ModelPrice(3.0,  15.0, 2.40, 0.30, "anthropic"),
    "claude-sonnet-4-5":        ModelPrice(3.0,  15.0, 2.40, 0.30, "anthropic"),
    "claude-sonnet-4-20250514": ModelPrice(3.0,  15.0, 2.40, 0.30, "anthropic"),  # alias
    "claude-sonnet-4-0":        ModelPrice(3.0,  15.0, 2.40, 0.30, "anthropic"),
    # Haiku 4.5: $1 input, $5 output
    "claude-haiku-4-5":         ModelPrice(1.0,   5.0, 0.80, 0.10, "anthropic"),
    # Haiku 3.5 (retired except Bedrock/Vertex): $0.80 input, $4 output
    "claude-haiku-3-5":         ModelPrice(0.80,  4.0, 0.64, 0.08, "anthropic"),
    # ---- OpenAI ----
    # GPT-5.5: $5 input, $30 output, $0.50 cached input
    "gpt-5.5":       ModelPrice(5.0,   30.0, 0.0, 0.50,  "openai"),
    # GPT-5.4: $2.50 input, $15 output, $0.25 cached input
    "gpt-5.4":       ModelPrice(2.50,  15.0, 0.0, 0.25,  "openai"),
    # GPT-5.4-mini: $0.75 input, $4.50 output, $0.075 cached input
    "gpt-5.4-mini":  ModelPrice(0.75,   4.5, 0.0, 0.075, "openai"),
    # GPT-5.3-codex (Codex model - approximate, treat like gpt-5.4)
    "gpt-5.3-codex": ModelPrice(1.75,  14.0, 0.0, 0.175, "openai"),  # verified OR API 2026-06-09
    "gpt-5.3-chat":  ModelPrice(1.75,  14.0, 0.0, 0.175, "openai"),  # verified OR API 2026-06-09
    "gpt-5.2-chat":  ModelPrice(1.75,  14.0, 0.0, 0.175, "openai"),  # verified OR API 2026-06-09
    "gpt-5.2-codex": ModelPrice(1.75,  14.0, 0.0, 0.175, "openai"),  # verified OR API 2026-06-09
    # GPT-4o (legacy): $2.50 input, $10 output, $1.25 cached input
    "gpt-4o":        ModelPrice(2.50,  10.0, 0.0, 1.25,  "openai"),
    # GPT-5.2 (placeholder; price unconfirmed - treat like gpt-5.4)
    "gpt-5.2":       ModelPrice(1.75,  14.0, 0.0, 0.175, "openai"),  # verified OR API 2026-06-09

    # ---------------------------------------------------------------------------
    # OpenRouter-specific models (provider='openrouter').
    # These are models accessed via openrouter.ai — prices from OR API 2026-06-09.
    # Note: Anthropic and OpenAI models accessed via OR use their existing direct
    # entries above (normalization strips the anthropic/ and openai/ prefixes).
    # Only non-Anthropic/OpenAI models need explicit entries here.
    # Free tier (:free suffix) entries are all zeros so compute_cost_from_tokens
    # returns 0.0 and repair correctly skips them.
    # ---------------------------------------------------------------------------

    # ---- Google / Gemini (via OpenRouter) ----
    "google/gemini-2.5-pro":              ModelPrice(1.25,  10.0,  0.375,  0.125,  "openrouter"),
    "google/gemini-2.5-pro-preview":      ModelPrice(1.25,  10.0,  0.375,  0.125,  "openrouter"),
    "google/gemini-2.5-flash":            ModelPrice(0.30,   2.5,  0.0833, 0.03,   "openrouter"),
    "google/gemini-2.5-flash-lite":       ModelPrice(0.10,   0.4,  0.0833, 0.01,   "openrouter"),
    "google/gemini-3.1-flash-lite":       ModelPrice(0.25,   1.5,  0.0833, 0.025,  "openrouter"),
    "google/gemini-3.1-pro-preview":      ModelPrice(2.00,  12.0,  0.375,  0.20,   "openrouter"),
    "google/gemini-3.5-flash":            ModelPrice(1.50,   9.0,  0.0833, 0.15,   "openrouter"),
    # Gemma (via OR)
    "google/gemma-4-31b-it":              ModelPrice(0.12,   0.36, 0.0,    0.09,   "openrouter"),
    "google/gemma-4-31b-it:free":         ModelPrice(0.0,    0.0,  0.0,    0.0,    "openrouter"),
    "google/gemma-4-26b-a4b-it":          ModelPrice(0.06,   0.33, 0.0,    0.0,    "openrouter"),
    "google/gemma-4-26b-a4b-it:free":     ModelPrice(0.0,    0.0,  0.0,    0.0,    "openrouter"),
    "google/gemma-3-27b-it":              ModelPrice(0.08,   0.16, 0.0,    0.0,    "openrouter"),
    "google/gemma-3-12b-it":              ModelPrice(0.05,   0.15, 0.0,    0.0,    "openrouter"),

    # ---- Meta Llama (via OpenRouter) ----
    "meta-llama/llama-4-maverick":        ModelPrice(0.15,   0.60, 0.0,    0.0,    "openrouter"),
    "meta-llama/llama-4-scout":           ModelPrice(0.10,   0.30, 0.0,    0.0,    "openrouter"),
    "meta-llama/llama-3.3-70b-instruct":  ModelPrice(0.10,   0.32, 0.0,    0.0,    "openrouter"),
    "meta-llama/llama-3.3-70b-instruct:free": ModelPrice(0.0, 0.0,  0.0,   0.0,    "openrouter"),
    "meta-llama/llama-3.1-70b-instruct":  ModelPrice(0.40,   0.40, 0.0,    0.0,    "openrouter"),
    "meta-llama/llama-3.2-3b-instruct":   ModelPrice(0.051,  0.335,0.0,    0.0,    "openrouter"),
    "meta-llama/llama-3.2-3b-instruct:free": ModelPrice(0.0, 0.0,  0.0,   0.0,    "openrouter"),

    # ---- DeepSeek (via OpenRouter) ----
    "deepseek/deepseek-r1-0528":          ModelPrice(0.50,   2.15, 0.0,    0.35,   "openrouter"),
    "deepseek/deepseek-r1":               ModelPrice(0.70,   2.50, 0.0,    0.0,    "openrouter"),
    "deepseek/deepseek-chat-v3.1":        ModelPrice(0.21,   0.79, 0.0,    0.13,   "openrouter"),
    "deepseek/deepseek-chat-v3-0324":     ModelPrice(0.20,   0.77, 0.0,    0.135,  "openrouter"),
    "deepseek/deepseek-v4-pro":           ModelPrice(0.435,  0.87, 0.0,    0.0036, "openrouter"),
    "deepseek/deepseek-v4-flash":         ModelPrice(0.098,  0.197,0.0,    0.0197, "openrouter"),
    "deepseek/deepseek-v3.2":             ModelPrice(0.229,  0.343,0.0,    0.0,    "openrouter"),
    "deepseek/deepseek-r1-distill-llama-70b": ModelPrice(0.70, 0.80,0.0,  0.0,    "openrouter"),

    # ---- xAI / Grok (via OpenRouter) ----
    "x-ai/grok-4.20":                     ModelPrice(1.25,   2.50, 0.0,    0.20,   "openrouter"),
    "x-ai/grok-4.3":                      ModelPrice(1.25,   2.50, 0.0,    0.20,   "openrouter"),
    "x-ai/grok-build-0.1":               ModelPrice(1.00,   2.00, 0.0,    0.20,   "openrouter"),

    # ---- Qwen (via OpenRouter) ----
    "qwen/qwen3-coder":                   ModelPrice(0.22,   1.80, 0.0,    0.0,    "openrouter"),
    "qwen/qwen3-coder:free":              ModelPrice(0.0,    0.0,  0.0,    0.0,    "openrouter"),
    "qwen/qwen3-coder-plus":              ModelPrice(0.65,   3.25, 0.8125, 0.13,   "openrouter"),
    "qwen/qwen3-coder-flash":             ModelPrice(0.195,  0.975,0.2437, 0.039,  "openrouter"),
    "qwen/qwen3-235b-a22b":               ModelPrice(0.455,  1.82, 0.0,    0.0,    "openrouter"),
    "qwen/qwen3-max":                     ModelPrice(0.78,   3.90, 0.975,  0.156,  "openrouter"),
    "qwen/qwen3-max-thinking":            ModelPrice(0.78,   3.90, 0.0,    0.0,    "openrouter"),
    "qwen/qwen3-32b":                     ModelPrice(0.08,   0.28, 0.0,    0.0,    "openrouter"),
    "qwen/qwen3-next-80b-a3b-instruct":   ModelPrice(0.09,   1.10, 0.0,    0.0,    "openrouter"),
    "qwen/qwen3-next-80b-a3b-instruct:free": ModelPrice(0.0, 0.0,  0.0,   0.0,    "openrouter"),
    "qwen/qwen3.7-max":                   ModelPrice(1.25,   3.75, 1.5625, 0.25,   "openrouter"),
    "qwen/qwen3.7-plus":                  ModelPrice(0.40,   1.60, 0.50,   0.08,   "openrouter"),
    "qwen/qwen-2.5-72b-instruct":         ModelPrice(0.36,   0.40, 0.0,    0.0,    "openrouter"),

    # ---- Moonshot / Kimi (via OpenRouter) ----
    "moonshotai/kimi-k2.6":               ModelPrice(0.68,   3.41, 0.0,    0.34,   "openrouter"),
    "moonshotai/kimi-k2.6:free":          ModelPrice(0.0,    0.0,  0.0,    0.0,    "openrouter"),
    "moonshotai/kimi-k2.5":               ModelPrice(0.40,   1.90, 0.0,    0.09,   "openrouter"),
    "moonshotai/kimi-k2":                 ModelPrice(0.57,   2.30, 0.0,    0.0,    "openrouter"),

    # ---- NVIDIA Nemotron (via OpenRouter) ----
    "nvidia/nemotron-3-ultra-550b-a55b":  ModelPrice(0.50,   2.50, 0.0,    0.15,   "openrouter"),
    "nvidia/nemotron-3-ultra-550b-a55b:free": ModelPrice(0.0,0.0,  0.0,   0.0,    "openrouter"),
    "nvidia/nemotron-3-super-120b-a12b":  ModelPrice(0.09,   0.45, 0.0,    0.0,    "openrouter"),
    "nvidia/nemotron-3-super-120b-a12b:free": ModelPrice(0.0,0.0,  0.0,   0.0,    "openrouter"),
    "nvidia/nemotron-3-nano-30b-a3b":     ModelPrice(0.05,   0.20, 0.0,    0.0,    "openrouter"),
    "nvidia/nemotron-3-nano-30b-a3b:free": ModelPrice(0.0,   0.0,  0.0,   0.0,    "openrouter"),

    # ---- Mistral (via OpenRouter) ----
    "mistralai/mistral-large-2512":       ModelPrice(0.50,   1.50, 0.0,    0.05,   "openrouter"),
    "mistralai/mistral-medium-3.1":       ModelPrice(0.40,   2.00, 0.0,    0.04,   "openrouter"),
    "mistralai/mistral-medium-3":         ModelPrice(0.40,   2.00, 0.0,    0.04,   "openrouter"),
    "mistralai/codestral-2508":           ModelPrice(0.30,   0.90, 0.0,    0.03,   "openrouter"),
    "mistralai/devstral-2512":            ModelPrice(0.40,   2.00, 0.0,    0.04,   "openrouter"),
    "mistralai/mistral-small-3.2-24b-instruct": ModelPrice(0.075,0.20,0.0, 0.0,   "openrouter"),
    "mistralai/mistral-nemo":             ModelPrice(0.02,   0.03, 0.0,    0.0,    "openrouter"),

    # ---- Nousresearch Hermes (via OpenRouter) ----
    "nousresearch/hermes-3-llama-3.1-405b": ModelPrice(1.00, 1.00, 0.0,   0.0,    "openrouter"),
    "nousresearch/hermes-3-llama-3.1-405b:free": ModelPrice(0.0,0.0,0.0,  0.0,    "openrouter"),
    "nousresearch/hermes-4-405b":         ModelPrice(1.00,   3.00, 0.0,    0.0,    "openrouter"),
    "nousresearch/hermes-4-70b":          ModelPrice(0.13,   0.40, 0.0,    0.0,    "openrouter"),

    # ---- OpenAI via OR — oss models not covered by direct openai/ prefix stripping ----
    "gpt-oss-120b":                       ModelPrice(0.039,  0.18, 0.0,    0.0,    "openrouter"),
    "gpt-oss-120b:free":                  ModelPrice(0.0,    0.0,  0.0,    0.0,    "openrouter"),
    "gpt-oss-20b":                        ModelPrice(0.029,  0.14, 0.0,    0.0,    "openrouter"),
    "gpt-oss-20b:free":                   ModelPrice(0.0,    0.0,  0.0,    0.0,    "openrouter"),

    # ---- Cohere (via OpenRouter) ----
    "cohere/command-a":                   ModelPrice(2.50,  10.0,  0.0,    0.0,    "openrouter"),
    "cohere/command-r-plus-08-2024":      ModelPrice(2.50,  10.0,  0.0,    0.0,    "openrouter"),
    "cohere/command-r-08-2024":           ModelPrice(0.15,   0.60, 0.0,    0.0,    "openrouter"),
}


def _normalize_model_id(model_id: str) -> str:
    """Normalize model ID for pricing lookup."""
    # Lowercase, strip provider prefix
    m = model_id.lower().strip()
    # Strip known provider prefixes
    for prefix in ("anthropic/", "openai/", "openrouter/", "anthropic."):
        if m.startswith(prefix):
            m = m[len(prefix):]
            break
    # Strip -vN:0 Bedrock suffixes
    m = re.sub(r"-v\d+:\d+$", "", m)
    # Strip @date Vertex suffixes
    m = re.sub(r"@\d{8}$", "", m)
    # Strip -YYYYMMDD date suffixes (e.g. claude-sonnet-4-20250514)
    m = re.sub(r"-20\d{6}$", "", m)
    # Normalize model version separators: opus-4.8 → opus-4-8
    m = m.replace(".", "-")
    return m


# Pre-computed normalized pricing table for O(1) lookups
_NORM_PRICING: dict[str, ModelPrice] = {}

def _build_norm_pricing() -> None:
    for key, price in PRICING_TABLE.items():
        _NORM_PRICING[_normalize_model_id(key)] = price

_build_norm_pricing()


def get_model_pricing(model_id: str, _provider: str = "") -> ModelPrice | None:
    """Return ModelPrice for a model, or None if unknown.

    Both the lookup key and PRICING_TABLE keys are normalized before comparison,
    so gpt-5.5 (dots) correctly maps to the gpt-5.5 entry after normalization.
    The provider argument is reserved for future provider-aware disambiguation.
    """
    if not model_id:
        return None
    norm = _normalize_model_id(model_id)
    # Exact match
    if norm in _NORM_PRICING:
        return _NORM_PRICING[norm]
    # Longest prefix match
    best_key = ""
    for key in _NORM_PRICING:
        if norm.startswith(key) and len(key) > len(best_key):
            best_key = key
    if best_key:
        return _NORM_PRICING[best_key]
    return None


def compute_cost_from_tokens(
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    model_id: str,
    provider: str = "",
) -> float:
    """Estimate cost from token counts using the pricing table.

    Returns 0.0 if no pricing is available for the model.
    For OpenAI models, cache_write_tokens is ignored (no write fee).
    """
    pricing = get_model_pricing(model_id, provider)
    if pricing is None:
        return 0.0
    return compute_cost_with_pricing(
        input_tokens,
        output_tokens,
        cache_read_tokens,
        cache_write_tokens,
        pricing,
    )


def compute_cost_with_pricing(
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    pricing: ModelPrice,
) -> float:
    """Estimate cost from token counts and a known pricing row."""
    mtok = 1_000_000.0
    cost = (
        input_tokens * pricing.input_per_mtok / mtok
        + output_tokens * pricing.output_per_mtok / mtok
        + cache_read_tokens * pricing.cache_read_per_mtok / mtok
        + cache_write_tokens * pricing.cache_write_per_mtok / mtok
    )
    return round(cost, 8)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def parse_dt(value: str | None) -> str | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value).astimezone(timezone.utc).isoformat()
    except Exception:
        return None


def ms_to_iso(value: Any) -> str | None:
    try:
        if value is None:
            return None
        return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc).isoformat()
    except Exception:
        return None


def intish(v: Any) -> int:
    try:
        return int(v or 0)
    except Exception:
        return 0


def cost_from_usage(usage: dict[str, Any]) -> float:
    """Extract cost from a usage dict as written by OpenClaw in JSONL."""
    cost = usage.get("cost")
    if isinstance(cost, dict):
        return float(cost.get("total") or 0)
    if isinstance(cost, (int, float)):
        return float(cost)
    return float(usage.get("estimatedCostUsd") or usage.get("costUsd") or 0)


def usage_counts(usage: dict[str, Any]) -> dict[str, int | float]:
    return {
        "input_tokens": intish(usage.get("input") or usage.get("inputTokens")),
        "output_tokens": intish(usage.get("output") or usage.get("outputTokens")),
        "cache_read_tokens": intish(usage.get("cacheRead") or usage.get("cacheReadTokens")),
        "cache_write_tokens": intish(usage.get("cacheWrite") or usage.get("cacheWriteTokens")),
        "total_tokens": intish(usage.get("totalTokens") or usage.get("total")),
        "cost_usd": cost_from_usage(usage),
    }


def resolved_cost(
    counts: dict[str, Any],
    model_id: str,
    provider: str,
) -> tuple[float, str]:
    """Return (cost_usd, cost_source) for an event.

    cost_source values:
      'jsonl'    - non-zero JSONL cost was explicitly trusted
      'computed' - cost was 0 in JSONL; computed from pricing table
      'zero'     - cost is 0 because no tokens were used or the priced model is free
      'unknown'  - tokens were used, but no pricing table entry exists
    """
    raw = float(counts["cost_usd"])
    if raw > 0 and TRUST_JSONL_COST:
        return raw, "jsonl"
    # Current OpenClaw writes cost=0 in JSONL. Even if a future event writes a
    # non-zero value, keep using locally auditable pricing unless explicitly opted in.
    has_tokens = (
        counts["input_tokens"] > 0
        or counts["output_tokens"] > 0
        or counts["cache_read_tokens"] > 0
        or counts["cache_write_tokens"] > 0
    )
    if has_tokens:
        pricing = get_model_pricing(model_id, provider)
        if pricing is None:
            return 0.0, "unknown"
        computed = compute_cost_with_pricing(
            counts["input_tokens"],
            counts["output_tokens"],
            counts["cache_read_tokens"],
            counts["cache_write_tokens"],
            pricing,
        )
        if computed > 0:
            return computed, "computed"
    return 0.0, "zero"


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(errors="replace"))
    except Exception:
        return None


DEFAULT_CONTACT_LABELS = Path.home() / ".openclaw" / "usage" / "contact-labels.json"


def load_contact_labels(workspace: Path, contacts_path: Path | None = None) -> dict[str, str]:
    """Load optional user labels without shipping environment-specific identities."""
    labels: dict[str, str] = {}
    cfg_path = contacts_path or DEFAULT_CONTACT_LABELS
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text())
            raw_labels = data.get("labels", data) if isinstance(data, dict) else {}
            if isinstance(raw_labels, dict):
                labels.update({str(k): str(v) for k, v in raw_labels.items()})
        except Exception:
            pass

    contacts_dir = workspace / "memory" / "contacts"
    for path in contacts_dir.glob("telegram-*.md"):
        tid = path.stem.replace("telegram-", "")
        try:
            text = path.read_text(errors="replace")
        except Exception:
            continue
        m = re.search(r"^- \*\*Name:\*\*\s*(.+)$", text, re.M)
        if m:
            labels[tid] = m.group(1).strip()
    return labels


def build_index(agents_dir: Path) -> dict[str, dict[str, Any]]:
    by_file: dict[str, dict[str, Any]] = {}
    for sessions_json in agents_dir.glob("*/sessions/sessions.json"):
        data = read_json(sessions_json)
        if not isinstance(data, dict):
            continue
        agent = sessions_json.parents[1].name
        for session_key, rec in data.items():
            if not isinstance(rec, dict):
                continue
            f = rec.get("sessionFile")
            if not f:
                continue
            entry = dict(rec)
            entry["agent"] = agent
            entry["sessionKey"] = session_key
            by_file[str(Path(f))] = entry
    return by_file


def extract_runtime_sender(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in text.split("```json")[1:]:
        block = part.split("```", 1)[0]
        try:
            obj = json.loads(block)
        except Exception:
            continue
        for key in ("sender_id", "sender", "chat_id", "message_id"):
            if key in obj:
                out[key] = str(obj[key])
    return out


def user_from_meta(
    meta: dict[str, Any],
    runtime_sender: dict[str, str],
    contact_labels: dict[str, str],
) -> dict[str, str]:
    origin = meta.get("origin") if isinstance(meta.get("origin"), dict) else {}
    delivery = meta.get("deliveryContext") if isinstance(meta.get("deliveryContext"), dict) else {}
    label = str(origin.get("label") or meta.get("label") or "")
    from_id = str(origin.get("from") or runtime_sender.get("sender_id") or delivery.get("to") or meta.get("lastTo") or "unknown")
    user_id = runtime_sender.get("sender_id") or from_id.replace("telegram:", "")
    if runtime_sender.get("sender"):
        label = f"{runtime_sender['sender']} id:{runtime_sender.get('sender_id','')}".strip()
    if user_id in contact_labels:
        label = f"{contact_labels[user_id]} id:{user_id}"
    session_key = str(meta.get("sessionKey") or "")
    if (not runtime_sender.get("sender_id")) and (":cron:" in session_key or label.startswith("Cron:")):
        user_id = "system:cron"
        label = "Automation / cron"
    if not label:
        label = user_id or "unknown"
    return {
        "user_id": user_id or "unknown",
        "user_label": label,
        "chat_id": runtime_sender.get("chat_id") or str(origin.get("to") or delivery.get("to") or meta.get("lastTo") or ""),
        "channel": str(origin.get("provider") or delivery.get("channel") or meta.get("lastChannel") or ""),
    }


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS users (
          user_id TEXT PRIMARY KEY,
          user_label TEXT,
          channel TEXT,
          first_seen_at TEXT,
          last_seen_at TEXT
        );

        CREATE TABLE IF NOT EXISTS sessions (
          session_id TEXT PRIMARY KEY,
          agent TEXT NOT NULL,
          session_key TEXT,
          source_file TEXT UNIQUE NOT NULL,
          user_id TEXT,
          user_label TEXT,
          chat_id TEXT,
          channel TEXT,
          provider TEXT,
          model TEXT,
          started_at TEXT,
          latest_at TEXT,
          is_checkpoint INTEGER NOT NULL DEFAULT 0,
          source_mtime INTEGER,
          source_size INTEGER,
          collected_at TEXT NOT NULL,
          FOREIGN KEY(user_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS usage_events (
          event_key TEXT PRIMARY KEY,
          session_id TEXT NOT NULL,
          source_file TEXT NOT NULL,
          line_no INTEGER NOT NULL,
          event_id TEXT,
          event_at TEXT,
          role TEXT,
          provider TEXT,
          model TEXT,
          input_tokens INTEGER NOT NULL DEFAULT 0,
          output_tokens INTEGER NOT NULL DEFAULT 0,
          cache_read_tokens INTEGER NOT NULL DEFAULT 0,
          cache_write_tokens INTEGER NOT NULL DEFAULT 0,
          total_tokens INTEGER NOT NULL DEFAULT 0,
          cost_usd REAL NOT NULL DEFAULT 0,
          cost_source TEXT NOT NULL DEFAULT 'unknown',
          FOREIGN KEY(session_id) REFERENCES sessions(session_id)
        );

        CREATE TABLE IF NOT EXISTS session_counters (
          session_id TEXT PRIMARY KEY,
          user_messages INTEGER NOT NULL DEFAULT 0,
          assistant_turns INTEGER NOT NULL DEFAULT 0,
          tool_results INTEGER NOT NULL DEFAULT 0,
          input_tokens INTEGER NOT NULL DEFAULT 0,
          output_tokens INTEGER NOT NULL DEFAULT 0,
          cache_read_tokens INTEGER NOT NULL DEFAULT 0,
          cache_write_tokens INTEGER NOT NULL DEFAULT 0,
          total_tokens INTEGER NOT NULL DEFAULT 0,
          cost_usd REAL NOT NULL DEFAULT 0,
          updated_at TEXT NOT NULL,
          FOREIGN KEY(session_id) REFERENCES sessions(session_id)
        );

        DROP VIEW IF EXISTS v_usage_by_agent_user;
        DROP VIEW IF EXISTS v_usage_by_day_agent_user;

        CREATE VIEW v_usage_by_agent_user AS
        SELECT
          s.agent,
          COALESCE(s.user_id, 'unknown') AS user_id,
          MAX(COALESCE(s.user_label, s.user_id, 'unknown')) AS user_label,
          COUNT(DISTINCT s.session_id) AS sessions,
          SUM(c.user_messages) AS user_messages,
          SUM(c.assistant_turns) AS assistant_turns,
          SUM(c.tool_results) AS tool_results,
          SUM(c.input_tokens) AS input_tokens,
          SUM(c.output_tokens) AS output_tokens,
          SUM(c.cache_read_tokens) AS cache_read_tokens,
          SUM(c.cache_write_tokens) AS cache_write_tokens,
          SUM(c.total_tokens) AS total_tokens,
          SUM(c.cost_usd) AS cost_usd,
          MAX(s.latest_at) AS latest_at
        FROM sessions s
        JOIN session_counters c ON c.session_id = s.session_id
        WHERE s.is_checkpoint = 0
        GROUP BY s.agent, COALESCE(s.user_id, 'unknown');

        CREATE VIEW v_usage_by_day_agent_user AS
        SELECT
          substr(COALESCE(e.event_at, s.latest_at), 1, 10) AS day,
          s.agent,
          COALESCE(s.user_id, 'unknown') AS user_id,
          MAX(COALESCE(s.user_label, s.user_id, 'unknown')) AS user_label,
          COUNT(*) AS assistant_events,
          SUM(e.input_tokens) AS input_tokens,
          SUM(e.output_tokens) AS output_tokens,
          SUM(e.cache_read_tokens) AS cache_read_tokens,
          SUM(e.cache_write_tokens) AS cache_write_tokens,
          SUM(e.total_tokens) AS total_tokens,
          SUM(e.cost_usd) AS cost_usd
        FROM usage_events e
        JOIN sessions s ON s.session_id = e.session_id
        WHERE s.is_checkpoint = 0
        GROUP BY day, s.agent, COALESCE(s.user_id, 'unknown');
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    # Ensure index on usage_events(session_id) for fast session cost rebuilds
    conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_events_session_id ON usage_events(session_id)")
    conn.commit()
    # Schema migration: add cost_source column if missing (v1 → v2)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(usage_events)")}
    if "cost_source" not in cols:
        conn.execute("ALTER TABLE usage_events ADD COLUMN cost_source TEXT NOT NULL DEFAULT 'unknown'")
    conn.commit()
    return conn


def repair_costs(
    conn: sqlite3.Connection,
    dry_run: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """Back-fill cost for events stored with cost=0 but non-zero tokens.

    Also clamps negative-cost rows to 0 (corrupt data safeguard).
    Recomputes session_counters.cost_usd from updated event totals.
    """
    now = datetime.now(timezone.utc).isoformat()

    # 1. Clamp negative cost rows
    bad_rows = conn.execute(
        "SELECT event_key, model, provider, cost_usd FROM usage_events WHERE cost_usd < -0.001"
    ).fetchall()
    neg_fixed = 0
    for event_key, model, provider, bad_cost in bad_rows:
        if verbose:
            print(f"  [repair] Clamping negative cost: {event_key} model={model} cost={bad_cost:.2f} → 0")
        if not dry_run:
            conn.execute(
                "UPDATE usage_events SET cost_usd=0, cost_source='clamped' WHERE event_key=?",
                (event_key,),
            )
        neg_fixed += 1

    # 2. Compute costs for events with tokens but cost=0
    zero_events = conn.execute(
        """SELECT event_key, model, provider, input_tokens, output_tokens,
                  cache_read_tokens, cache_write_tokens
             FROM usage_events
             WHERE cost_usd = 0
               AND (input_tokens > 0 OR output_tokens > 0
                    OR cache_read_tokens > 0 OR cache_write_tokens > 0)"""
    ).fetchall()
    costs_computed = 0
    costs_skipped = 0
    for event_key, model, provider, inp, out, cr, cw in zero_events:
        c = compute_cost_from_tokens(inp, out, cr, cw, model, provider)
        if c > 0:
            if verbose:
                print(f"  [repair] Computed cost for {event_key}: model={model} → ${c:.6f}")
            if not dry_run:
                conn.execute(
                    "UPDATE usage_events SET cost_usd=?, cost_source='repaired' WHERE event_key=?",
                    (c, event_key),
                )
            costs_computed += 1
        else:
            costs_skipped += 1

    # 3. Rebuild session_counters.cost_usd from events
    if not dry_run and (neg_fixed > 0 or costs_computed > 0):
        conn.execute(
            """UPDATE session_counters
               SET cost_usd = (
                 SELECT COALESCE(SUM(e.cost_usd), 0)
                 FROM usage_events e
                 WHERE e.session_id = session_counters.session_id
               ),
               updated_at = ?""",
            (now,),
        )
        conn.commit()

    return {
        "negative_clamped": neg_fixed,
        "costs_computed": costs_computed,
        "costs_skipped_no_pricing": costs_skipped,
        "dry_run": dry_run,
    }


def recalibrate_costs(
    conn: sqlite3.Connection,
    dry_run: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """Re-compute cost_usd for events that should follow the pricing table.

    Use this after updating the PRICING_TABLE to propagate new prices to previously
    computed events or to backfill unknown rows after adding provider pricing.
    Events with cost_source='jsonl' were explicitly trusted via
    USAGE_TRUST_JSONL_COST=1 and are left untouched.
    """
    now = datetime.now(timezone.utc).isoformat()

    target_events = conn.execute(
        """SELECT event_key, model, provider, input_tokens, output_tokens,
                  cache_read_tokens, cache_write_tokens, cost_usd
             FROM usage_events
             WHERE cost_source IN ('repaired', 'computed')
                OR cost_source = 'unknown'"""
    ).fetchall()

    updated = 0
    skipped_no_pricing = 0
    skipped_unchanged = 0

    for event_key, model, provider, inp, out, cr, cw, old_cost in target_events:
        new_cost = compute_cost_from_tokens(inp, out, cr, cw, model, provider)
        if new_cost == 0.0 and (inp + out + cr + cw) > 0:
            skipped_no_pricing += 1
            continue
        if abs(new_cost - old_cost) < 1e-8:
            skipped_unchanged += 1
            continue
        if verbose:
            print(f"  [recalib] {event_key}: model={model} ${old_cost:.6f} → ${new_cost:.6f}")
        if not dry_run:
            conn.execute(
                "UPDATE usage_events SET cost_usd=?, cost_source='repaired' WHERE event_key=?",
                (new_cost, event_key),
            )
        updated += 1

    if not dry_run and updated > 0:
        conn.execute(
            """UPDATE session_counters
               SET cost_usd = (
                 SELECT COALESCE(SUM(e.cost_usd), 0)
                 FROM usage_events e
                 WHERE e.session_id = session_counters.session_id
               ),
               updated_at = ?""",
            (now,),
        )
        conn.commit()

    return {
        "recalibrated": updated,
        "skipped_no_pricing": skipped_no_pricing,
        "skipped_unchanged": skipped_unchanged,
        "dry_run": dry_run,
    }


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------

def collect(
    db_path: Path,
    agents_dir: Path,
    workspace: Path,
    contacts_path: Path | None = None,
    include_checkpoints: bool = False,
) -> dict[str, Any]:
    conn = connect(db_path)
    index = build_index(agents_dir)
    contact_labels = load_contact_labels(workspace, contacts_path)
    now = datetime.now(timezone.utc).isoformat()
    seen_sessions = 0
    inserted_events = 0
    updated_sessions = 0

    for path in sorted(agents_dir.glob("*/sessions/*.jsonl")):
        is_checkpoint = ".checkpoint." in path.name
        if is_checkpoint and not include_checkpoints:
            continue
        agent = path.parents[1].name
        meta = index.get(str(path), {})
        session_id = str(meta.get("sessionId") or path.stem)
        session_key = str(meta.get("sessionKey") or "")
        stat = path.stat()
        existing = conn.execute(
            "SELECT session_id FROM sessions WHERE source_file=?", (str(path),)
        ).fetchone()
        if existing and existing[0]:
            session_id = str(existing[0])
        runtime_sender: dict[str, str] = {}
        heartbeat_like = False
        started = None
        latest = None
        provider = str(meta.get("modelProvider") or "")
        model = str(meta.get("model") or "")
        user_messages = assistant_turns = tool_results = 0
        totals: dict[str, int | float] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
        }
        assistant_events: list[tuple[Any, ...]] = []

        try:
            lines = path.read_text(errors="replace").splitlines()
        except Exception:
            continue

        # First pass: collect runtime sender and timestamps.
        parsed: list[tuple[int, dict[str, Any]]] = []
        for line_no, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            parsed.append((line_no, ev))
            ts = parse_dt(ev.get("timestamp") or ev.get("ts"))
            if ts:
                latest = max(latest, ts) if latest else ts
                started = min(started, ts) if started else ts
            if ev.get("type") == "custom_message" and ev.get("customType") == "openclaw.runtime-context":
                runtime_content = str(ev.get("content") or "")
                runtime_sender.update(extract_runtime_sender(runtime_content))
                if "HEARTBEAT.md" in runtime_content or "HEARTBEAT_OK" in runtime_content:
                    heartbeat_like = True
            if ev.get("type") == "model_change":
                provider = str(ev.get("provider") or provider)
                model = str(ev.get("modelId") or model)

        latest = latest or ms_to_iso(meta.get("updatedAt"))
        started = started or ms_to_iso(meta.get("sessionStartedAt"))
        user = user_from_meta(meta, runtime_sender, contact_labels)

        # Second pass: extract role counters and assistant usage events.
        for line_no, ev in parsed:
            msg = ev.get("message") if isinstance(ev.get("message"), dict) else {}
            role = msg.get("role")
            if role == "user":
                user_messages += 1
            elif role == "assistant":
                assistant_turns += 1
                usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else {}
                counts = usage_counts(usage)
                event_provider = str(msg.get("provider") or ev.get("provider") or provider or "")
                event_model = str(msg.get("model") or ev.get("modelId") or model or "")
                cost, cost_src = resolved_cost(counts, event_model, event_provider)
                for k in totals:
                    if k == "cost_usd":
                        totals[k] = float(totals[k]) + cost
                    else:
                        totals[k] = int(totals[k]) + int(counts[k])  # type: ignore[arg-type]
                event_at = parse_dt(ev.get("timestamp") or ev.get("ts"))
                event_id = str(ev.get("id") or "")
                event_key = f"{path}:{line_no}:{event_id}"
                assistant_events.append((
                    event_key, session_id, str(path), line_no, event_id, event_at, role,
                    event_provider, event_model,
                    int(counts["input_tokens"]), int(counts["output_tokens"]),
                    int(counts["cache_read_tokens"]), int(counts["cache_write_tokens"]),
                    int(counts["total_tokens"]), cost, cost_src,
                ))
            elif role == "toolResult":
                tool_results += 1

        if user["user_id"] == "unknown" and not user.get("chat_id") and heartbeat_like:
            user["user_id"] = "system:heartbeat"
            user["user_label"] = "Heartbeat / automation"
        elif user["user_id"] == "unknown" and not user.get("chat_id") and user_messages <= 1:
            user["user_id"] = "system:automation"
            user["user_label"] = "Automation / system"

        conn.execute(
            """INSERT INTO users(user_id,user_label,channel,first_seen_at,last_seen_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 user_label=excluded.user_label,
                 channel=COALESCE(excluded.channel, users.channel),
                 last_seen_at=MAX(COALESCE(users.last_seen_at,''), COALESCE(excluded.last_seen_at,''))""",
            (user["user_id"], user["user_label"], user["channel"],
             started or latest or now, latest or started or now),
        )
        conn.execute(
            """INSERT INTO sessions(session_id,agent,session_key,source_file,user_id,user_label,
                                    chat_id,channel,provider,model,started_at,latest_at,is_checkpoint,
                                    source_mtime,source_size,collected_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(session_id) DO UPDATE SET
                 agent=excluded.agent, session_key=excluded.session_key,
                 source_file=excluded.source_file,
                 user_id=excluded.user_id, user_label=excluded.user_label,
                 chat_id=excluded.chat_id, channel=excluded.channel,
                 provider=excluded.provider, model=excluded.model,
                 started_at=COALESCE(sessions.started_at, excluded.started_at),
                 latest_at=excluded.latest_at, is_checkpoint=excluded.is_checkpoint,
                 source_mtime=excluded.source_mtime, source_size=excluded.source_size,
                 collected_at=excluded.collected_at""",
            (session_id, agent, session_key, str(path), user["user_id"], user["user_label"],
             user["chat_id"], user["channel"], provider, model, started, latest,
             int(is_checkpoint), int(stat.st_mtime), int(stat.st_size), now),
        )
        before = conn.total_changes
        conn.executemany(
            """INSERT OR IGNORE INTO usage_events(
                 event_key,session_id,source_file,line_no,event_id,event_at,role,provider,model,
                 input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,total_tokens,
                 cost_usd,cost_source)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            assistant_events,
        )
        inserted_events += conn.total_changes - before
        conn.execute(
            """INSERT INTO session_counters(
                 session_id,user_messages,assistant_turns,tool_results,input_tokens,output_tokens,
                 cache_read_tokens,cache_write_tokens,total_tokens,cost_usd,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(session_id) DO UPDATE SET
                 user_messages=excluded.user_messages,
                 assistant_turns=excluded.assistant_turns,
                 tool_results=excluded.tool_results,
                 input_tokens=excluded.input_tokens,
                 output_tokens=excluded.output_tokens,
                 cache_read_tokens=excluded.cache_read_tokens,
                 cache_write_tokens=excluded.cache_write_tokens,
                 total_tokens=excluded.total_tokens,
                 cost_usd=excluded.cost_usd,
                 updated_at=excluded.updated_at""",
            (session_id, user_messages, assistant_turns, tool_results,
             totals["input_tokens"], totals["output_tokens"],
             totals["cache_read_tokens"], totals["cache_write_tokens"],
             totals["total_tokens"], totals["cost_usd"], now),
        )
        seen_sessions += 1
        updated_sessions += 1

    conn.commit()
    summary = conn.execute(
        """SELECT COUNT(*), COALESCE(SUM(user_messages),0), COALESCE(SUM(assistant_turns),0),
                  COALESCE(SUM(total_tokens),0), COALESCE(SUM(cost_usd),0)
             FROM session_counters c
             JOIN sessions s ON s.session_id=c.session_id
             WHERE s.is_checkpoint=0"""
    ).fetchone()
    conn.close()
    return {
        "db": str(db_path),
        "agents_dir": str(agents_dir),
        "collected_at": now,
        "scanned_sessions": seen_sessions,
        "updated_sessions": updated_sessions,
        "inserted_usage_events": inserted_events,
        "stored_sessions": int(summary[0]),
        "stored_user_messages": int(summary[1]),
        "stored_assistant_turns": int(summary[2]),
        "stored_total_tokens": int(summary[3]),
        "stored_cost_usd": round(float(summary[4]), 8),
        "checkpoints_included": include_checkpoints,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Collect OpenClaw agent usage into SQLite")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--agents-dir", type=Path, default=DEFAULT_AGENTS_DIR)
    ap.add_argument("--workspace", type=Path, default=Path.cwd())
    ap.add_argument(
        "--contacts",
        type=Path,
        default=None,
        help="Optional contact-label JSON (default ~/.openclaw/usage/contact-labels.json)",
    )
    ap.add_argument(
        "--include-checkpoints",
        action="store_true",
        help="Include checkpoint JSONL snapshots; usually duplicates usage",
    )
    ap.add_argument(
        "--repair-costs",
        action="store_true",
        help=(
            "Back-fill cost_usd for events with tokens but cost=0, "
            "clamp negative rows, and rebuild session_counters.cost_usd. "
            "Run after upgrading pricing table or after initial collection."
        ),
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="With --repair-costs: show what would change without writing.",
    )
    ap.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print per-event details during --repair-costs or --recalibrate.",
    )
    ap.add_argument(
        "--recalibrate",
        action="store_true",
        help=(
            "Re-compute cost_usd for repaired/computed/unknown events using "
            "current PRICING_TABLE. Use after updating pricing table multipliers "
            "or adding provider pricing. Leaves JSONL-trusted events untouched."
        ),
    )
    args = ap.parse_args()
    db_path = args.db.expanduser()
    agents_dir = args.agents_dir.expanduser()
    workspace = args.workspace.expanduser()
    contacts_path = args.contacts.expanduser() if args.contacts else None

    if args.repair_costs:
        conn = connect(db_path)
        result = repair_costs(conn, dry_run=args.dry_run, verbose=args.verbose)
        conn.close()
        print(json.dumps(result, indent=2))
        return 0

    if args.recalibrate:
        conn = connect(db_path)
        result = recalibrate_costs(conn, dry_run=args.dry_run, verbose=args.verbose)
        conn.close()
        print(json.dumps(result, indent=2))
        return 0

    summary = collect(db_path, agents_dir, workspace, contacts_path, args.include_checkpoints)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
