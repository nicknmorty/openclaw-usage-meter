# Running via OpenClaw Cron

If you run OpenClaw, you can schedule collection directly from your gateway config.

## Adding a cron job

In your OpenClaw config (`openclaw.json`):

```json
{
  "cron": {
    "jobs": [
      {
        "name": "openclaw-usage-meter",
        "schedule": { "kind": "every", "everyMs": 600000 },
        "payload": {
          "kind": "agentTurn",
          "message": "Run: python3 /path/to/openclaw-usage-meter/scripts/agent_usage_collect.py"
        },
        "sessionTarget": "isolated"
      }
    ]
  }
}
```

Or use the OpenClaw cron CLI:

```bash
openclaw cron add --name openclaw-usage-meter --every 600000 ...
```

## Manual trigger

```bash
python3 /path/to/openclaw-usage-meter/scripts/agent_usage_collect.py
```

The collector is idempotent — running it twice on the same JSONL files produces the same result.
