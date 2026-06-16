# spend — OpenClaw slash command extension

Registers `/spend`, a Telegram-formatted spend report backed by `usage_report.py`.
Emits emoji/markdown summaries plus optional Mermaid bar charts.

## Install

Copy `index.js` (and `package.json`, `openclaw.plugin.json`) into your OpenClaw
extensions directory, e.g. `~/.openclaw/extensions/spend/`, then enable it in
`openclaw.json` under `extensions.entries`:

```json
"spend": {
  "enabled": true,
  "config": {
    "scriptPath": "/abs/path/to/scripts/usage_report.py",
    "collectScriptPath": "/abs/path/to/scripts/agent_usage_collect.py",
    "dbPath": "/home/you/.openclaw/usage/agent_usage.sqlite",
    "python3": "python3",
    "chartTarget": "<telegram chat id for charts, optional>"
  }
}
```

Add `"spend"` to `extensions.allow`, then restart the Gateway.

## Usage

```
/spend                     all-time monthly summary
/spend today               today's spend
/spend week                last 7 days
/spend month [YYYY-MM]     daily breakdown for a month
/spend ytd                 year-to-date
/spend model               cost by model
/spend collect             fresh collection then today
/spend help                subcommands

Provider filter (optional, combine with any window):
/spend anthropic           anthropic-only monthly
/spend openai week         openai-only last 7 days
/spend anthropic ytd       anthropic-only year-to-date
```

Providers accepted: `anthropic` (aliases: anth, claude), `openai`
(aliases: oai, gpt, codex, chatgpt), `openrouter` (alias: or).

## Config keys

| Key | Default | Purpose |
|-----|---------|---------|
| `scriptPath` | bundled path | usage_report.py location |
| `collectScriptPath` | bundled path | agent_usage_collect.py location |
| `dbPath` | `~/.openclaw/usage/agent_usage.sqlite` | SQLite DB |
| `python3` | `python3` | Python interpreter |
| `mmdc` | mmdc in PATH | Mermaid CLI for charts |
| `puppeteerConfig` | unset | optional puppeteer config for mmdc |
| `chartTarget` | unset | Telegram chat id; charts skipped if unset |
