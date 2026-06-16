// spend — AI provider spend report slash command
// Formats data for Telegram: emoji, markdown, Mermaid charts via mmdc.
import { spawn } from "node:child_process";
import { writeFile, unlink } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function runProcess(file, args, timeoutMs = 30_000) {
  return new Promise((resolve, reject) => {
    const child = spawn(file, args, { stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => { try { child.kill("SIGTERM"); } catch {} }, timeoutMs);
    child.stdout.on("data", d => { stdout += d; });
    child.stderr.on("data", d => { stderr += d; });
    child.on("error", err => { clearTimeout(timer); reject(err); });
    child.on("close", code => {
      clearTimeout(timer);
      if (code === 0) resolve(stdout.trim());
      else reject(new Error((stderr || stdout || `exited ${code}`).trim()));
    });
  });
}

async function queryJson(python3, scriptPath, dbPath, flags = []) {
  const raw = await runProcess(python3, [scriptPath, "--db", dbPath, "--json", ...flags]);
  return JSON.parse(raw);
}

async function renderChart(mmdSource, mmdc, puppeteerConfig) {
  const id = Math.random().toString(36).slice(2, 9);
  const mmdPath = join(tmpdir(), `spend-${id}.mmd`);
  const pngPath = join(tmpdir(), `spend-${id}.png`);
  await writeFile(mmdPath, mmdSource, "utf8");
  try {
    const args = ["-i", mmdPath, "-o", pngPath, "-b", "white"];
    if (puppeteerConfig) args.push("-p", puppeteerConfig);
    await runProcess(mmdc, args, 45_000);
    return pngPath;
  } finally {
    await unlink(mmdPath).catch(() => {});
  }
}

async function sendChart(pngPath, chartTarget) {
  try {
    await runProcess("openclaw", [
      "message", "send",
      "--channel", "telegram",
      "--target", chartTarget,
      "--media", pngPath,
      "--force-document",
      "--json",
    ], 20_000);
  } finally {
    await unlink(pngPath).catch(() => {});
  }
}

function fmtCost(v) {
  if (v >= 100) return `$${v.toFixed(2)}`;
  if (v >= 1)   return `$${v.toFixed(3)}`;
  if (v > 0)    return `$${v.toFixed(4)}`;
  return "$0";
}

function fmtTokens(n) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000)     return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function shortMonth(m) {
  // "2026-06" -> "Jun '26"
  const [y, mo] = m.split("-");
  const names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  return `${names[parseInt(mo, 10) - 1]} '${y.slice(2)}`;
}

function shortDay(d) {
  // "2026-06-09" -> "Jun 9"
  const [, mo, dd] = d.split("-");
  const names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  return `${names[parseInt(mo, 10) - 1]} ${parseInt(dd, 10)}`;
}

function spendBar(cost, maxCost, width = 12) {
  if (maxCost <= 0) return "";
  const filled = Math.round((cost / maxCost) * width);
  return "█".repeat(filled) + "░".repeat(width - filled);
}

// ---------------------------------------------------------------------------
// Provider parsing
// ---------------------------------------------------------------------------

const PROVIDER_ALIASES = {
  anthropic: "anthropic", anth: "anthropic", claude: "anthropic",
  openai: "openai", oai: "openai", gpt: "openai", codex: "openai", chatgpt: "openai",
  openrouter: "openrouter", or: "openrouter",
};

// Pull a provider token out of the arg list. Returns { provider, rest } where
// rest is the remaining tokens (subcommand + any extras) with the provider removed.
function parseProvider(tokens) {
  let provider = null;
  const rest = [];
  for (const t of tokens) {
    if (provider === null && PROVIDER_ALIASES[t]) provider = PROVIDER_ALIASES[t];
    else rest.push(t);
  }
  return { provider, rest };
}

function provLabel(provider) {
  if (!provider) return "";
  const names = { anthropic: "Anthropic", openai: "OpenAI", openrouter: "OpenRouter" };
  return ` · ${names[provider] || provider}`;
}

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

function formatToday(data, provider) {
  const lines = [`💸 *Today — ${data.date}*${provLabel(provider)}\n`];
  if (!data.rows.length) {
    lines.push("No events yet today.");
  } else {
    const maxCost = Math.max(...data.rows.map(r => r.cost));
    for (const r of data.rows) {
      const bar = spendBar(r.cost, maxCost);
      lines.push(`\`${r.model}\``);
      lines.push(`  ${bar} ${fmtCost(r.cost)}`);
      lines.push(`  ${fmtTokens(r.tokens)} tokens · ${r.events} events`);
    }
    lines.push(`\n*Total today:* ${fmtCost(data.total)}`);
  }
  return lines.join("\n");
}

function formatMonthly(data, provider) {
  const lines = [`💸 *AI Spend — Monthly*${provLabel(provider)}\n`];
  for (const m of data.months) {
    const provParts = Object.entries(m.providers)
      .sort((a, b) => b[1] - a[1])
      .map(([p, c]) => `${p[0].toUpperCase()}${p.slice(1)} ${fmtCost(c)}`)
      .join(" · ");
    lines.push(`*${shortMonth(m.month)}* — ${fmtCost(m.total)}`);
    lines.push(`  └ ${provParts}`);
  }
  lines.push(`\n💰 *All-time:* ${fmtCost(data.alltime)}`);
  lines.push(`_OpenAI figures = ChatGPT Pro sub-equivalent, not direct API_`);
  return lines.join("\n");
}

function formatWeek(data, provider) {
  const lines = [`💸 *Last 7 Days*${provLabel(provider)} · ${fmtCost(data.total)}\n${data.start} → ${data.end}\n`];
  const maxCost = Math.max(...data.rows.map(r => r.total), 0.01);
  for (const r of data.rows) {
    const bar = spendBar(r.total, maxCost);
    const provParts = [
      r.anthropic > 0 ? `Anth ${fmtCost(r.anthropic)}` : null,
      r.openai > 0    ? `OAI ${fmtCost(r.openai)}` : null,
      r.openrouter > 0 ? `OR ${fmtCost(r.openrouter)}` : null,
    ].filter(Boolean).join(" · ");
    lines.push(`*${shortDay(r.day)}* ${bar} ${fmtCost(r.total)}`);
    if (provParts) lines.push(`  ${provParts}`);
  }
  return lines.join("\n");
}

function formatYtd(data, provider) {
  const lines = [`💸 *Year to Date — ${data.year}*${provLabel(provider)}\n`];
  for (const m of data.months) {
    const provParts = Object.entries(m.providers)
      .sort((a, b) => b[1] - a[1])
      .map(([p, c]) => `${p[0].toUpperCase()}${p.slice(1)} ${fmtCost(c)}`)
      .join(" · ");
    lines.push(`*${shortMonth(m.month)}* — ${fmtCost(m.total)}`);
    lines.push(`  └ ${provParts}`);
  }
  lines.push(`\n💰 *YTD total:* ${fmtCost(data.total)}`);
  lines.push(`_OpenAI figures = ChatGPT Pro sub-equivalent_`);
  return lines.join("\n");
}

function formatModel(data, provider) {
  const lines = [`💸 *Cost by Model — All Time*${provLabel(provider)}\n`];
  const maxCost = Math.max(...data.rows.map(r => r.cost), 0.01);
  for (const r of data.rows.slice(0, 15)) {
    if (r.cost < 0.01) continue;
    const bar = spendBar(r.cost, maxCost);
    lines.push(`\`${r.model}\` _(${r.provider})_`);
    lines.push(`  ${bar} ${fmtCost(r.cost)} · ${fmtTokens(r.tokens)} tok`);
  }
  lines.push(`\n💰 *Total:* ${fmtCost(data.total)}`);
  return lines.join("\n");
}

// ---------------------------------------------------------------------------
// Mermaid chart builders
// ---------------------------------------------------------------------------

function buildMonthlyChart(data) {
  const months = data.months.slice(-6); // last 6 months
  const labels = months.map(m => `"${shortMonth(m.month)}"`).join(", ");
  const values = months.map(m => m.total.toFixed(2)).join(", ");
  const maxVal = Math.max(...months.map(m => m.total)) * 1.15;
  return `xychart-beta\n    title "Monthly AI Spend (USD)"\n    x-axis [${labels}]\n    y-axis "USD" 0 --> ${Math.ceil(maxVal)}\n    bar [${values}]\n`;
}

function buildWeekChart(data) {
  const labels = data.rows.map(r => `"${shortDay(r.day)}"`).join(", ");
  const values = data.rows.map(r => r.total.toFixed(2)).join(", ");
  const maxVal = Math.max(...data.rows.map(r => r.total)) * 1.15;
  return `xychart-beta\n    title "Last 7 Days — AI Spend (USD)"\n    x-axis [${labels}]\n    y-axis "USD" 0 --> ${Math.ceil(maxVal)}\n    bar [${values}]\n`;
}

function buildYtdChart(data) {
  const labels = data.months.map(m => `"${shortMonth(m.month)}"`).join(", ");
  const values = data.months.map(m => m.total.toFixed(2)).join(", ");
  const maxVal = Math.max(...data.months.map(m => m.total)) * 1.15;
  return `xychart-beta\n    title "${data.year} YTD — AI Spend (USD)"\n    x-axis [${labels}]\n    y-axis "USD" 0 --> ${Math.ceil(maxVal)}\n    bar [${values}]\n`;
}

function buildModelChart(data) {
  const top = data.rows.filter(r => r.cost >= 1).slice(0, 10);
  const labels = top.map(r => `"${r.model.replace(/claude-|gpt-/g, "").slice(0, 12)}"`).join(", ");
  const values = top.map(r => r.cost.toFixed(2)).join(", ");
  const maxVal = Math.max(...top.map(r => r.cost)) * 1.15;
  return `xychart-beta\n    title "Cost by Model (USD)"\n    x-axis [${labels}]\n    y-axis "USD" 0 --> ${Math.ceil(maxVal)}\n    bar [${values}]\n`;
}

const USAGE_TEXT = `/spend [provider] [subcommand]
  today       — today's spending
  week        — last 7 days by day
  month       — this month daily breakdown
  ytd         — year-to-date monthly summary
  all/[none]  — all-time monthly summary
  model       — cost by model, all time
  collect     — run fresh collection then show today
  help        — this message

Provider filter (optional, combine with any time window):
  anthropic | openai | openrouter
  e.g. /spend anthropic week · /spend openai ytd · /spend anthropic`;

function currentMonth() {
  const d = new Date();
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

// ---------------------------------------------------------------------------
// Plugin registration
// ---------------------------------------------------------------------------

export default function register(api) {
  const cfg = api.getConfig?.() || {};
  const extensionDir = dirname(fileURLToPath(import.meta.url));
  const repoRoot = dirname(extensionDir);
  const python3      = cfg.python3 || "python3";
  const scriptPath   = cfg.scriptPath   || join(repoRoot, "scripts", "usage_report.py");
  const collectPath  = cfg.collectScriptPath || join(repoRoot, "scripts", "agent_usage_collect.py");
  const dbPath       = cfg.dbPath       || join(process.env.HOME || ".", ".openclaw", "usage", "agent_usage.sqlite");
  const mmdc         = cfg.mmdc         || "mmdc";
  const puppeteerCfg = cfg.puppeteerConfig || "";
  const chartTarget  = cfg.chartTarget  || "";

  const handler = async (ctx) => {
    const raw = String(ctx?.args || "").trim().toLowerCase();
    const tokens = raw.split(/\s+/).filter(Boolean);
    const { provider, rest } = parseProvider(tokens);
    const sub = rest[0] || "";
    const provFlags = provider ? ["--provider", provider] : [];

    try {
      if (sub === "help" || sub === "--help") return { text: USAGE_TEXT };

      if (sub === "collect") {
        await runProcess(python3, [collectPath], 60_000);
        const data = await queryJson(python3, scriptPath, dbPath, ["--today", ...provFlags]);
        return { text: "✅ Collection done.\n\n" + formatToday(data, provider) };
      }

      let data, text, mmdSource;

      if (sub === "today" || sub === "day") {
        data = await queryJson(python3, scriptPath, dbPath, ["--today", ...provFlags]);
        text = formatToday(data, provider);
        // no chart for today (single-row usually)

      } else if (sub === "week") {
        data = await queryJson(python3, scriptPath, dbPath, ["--week", ...provFlags]);
        text = formatWeek(data, provider);
        mmdSource = buildWeekChart(data);

      } else if (sub === "month") {
        const monthArg = rest[1] && /^\d{4}-\d{2}$/.test(rest[1]) ? rest[1] : currentMonth();
        const plainOut = await runProcess(python3, [scriptPath, "--db", dbPath, "--daily", "--month", monthArg, ...provFlags]);
        // format the plain output into clean text (no ASCII borders)
        text = formatMonthDailyPlain(plainOut, monthArg, provider);

      } else if (sub === "ytd") {
        data = await queryJson(python3, scriptPath, dbPath, ["--ytd", ...provFlags]);
        text = formatYtd(data, provider);
        mmdSource = buildYtdChart(data);

      } else if (sub === "model") {
        data = await queryJson(python3, scriptPath, dbPath, ["--model", ...provFlags]);
        text = formatModel(data, provider);
        mmdSource = buildModelChart(data);

      } else if (sub === "" && provider) {
        // provider-only, e.g. "/spend anthropic" -> monthly for that provider
        data = await queryJson(python3, scriptPath, dbPath, [...provFlags]);
        text = formatMonthly(data, provider);
        mmdSource = buildMonthlyChart(data);

      } else {
        // default: all-time monthly
        data = await queryJson(python3, scriptPath, dbPath, [...provFlags]);
        text = formatMonthly(data, provider);
        mmdSource = buildMonthlyChart(data);
      }

      // Send chart if we have one and a target configured
      if (mmdSource && chartTarget) {
        renderChart(mmdSource, mmdc, puppeteerCfg)
          .then(pngPath => sendChart(pngPath, chartTarget))
          .catch(err => api.logger?.warn?.(`[spend] chart render/send failed: ${err?.message}`));
      }

      return { text };

    } catch (err) {
      return { text: `❌ /spend failed: ${err?.message || String(err)}\nTry /spend help` };
    }
  };

  api.registerCommand({
    name: "spend",
    description: "AI provider spend — /spend [anthropic|openai|openrouter] [today|week|month|ytd|all|model|collect|help]",
    acceptsArgs: true,
    requireAuth: true,
    handler,
  });

  api.logger?.info?.("[spend] Loaded: /spend");
}

// ---------------------------------------------------------------------------
// Month daily plain formatter (strips ASCII table, formats for Telegram)
// ---------------------------------------------------------------------------

function formatMonthDailyPlain(raw, month, provider) {
  const lines = raw.split("\n");
  const dataLines = lines.filter(l =>
    /^\s+\d{4}-\d{2}-\d{2}/.test(l)
  );
  if (!dataLines.length) return `💸 *Daily — ${month}*${provLabel(provider)}\n\nNo data.`;
  const parsed = dataLines.map(l => {
    const parts = l.trim().split(/\s{2,}/);
    return { day: parts[0], total: parts[1], anthropic: parts[2], openai: parts[3] };
  });
  const maxVal = Math.max(...parsed.map(p => parseFloat(p.total?.replace("$","") || 0)), 0.01);
  const out = [`💸 *Daily — ${month}*${provLabel(provider)}\n`];
  for (const p of parsed) {
    const cost = parseFloat(p.total?.replace("$","") || 0);
    const bar = spendBar(cost, maxVal);
    const [, mo, dd] = p.day.split("-");
    const names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    const label = `${names[parseInt(mo,10)-1]} ${parseInt(dd,10)}`;
    out.push(`*${label}* ${bar} ${p.total}`);
  }
  return out.join("\n");
}
