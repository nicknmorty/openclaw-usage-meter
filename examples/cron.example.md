# Cron Example

Run collection every 10 minutes with a generic local checkout path:

```cron
*/10 * * * * python3 /home/user/apps/agent-usage-collector/scripts/agent_usage_collect.py
```

If your deployment uses OpenClaw cron or another scheduler, keep the real target
chat IDs, paths, and delivery settings in a private overlay document.
