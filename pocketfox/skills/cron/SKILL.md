---
name: cron
description: Schedule reminders and recurring tasks.
---

# Cron

Use the `cron_schedule` tool to schedule reminders or recurring tasks.

## Two Modes

1. **Reminder** - message is sent directly to user
2. **Task** - message is a task description, agent executes and sends result

## Examples

Fixed reminder:
```
cron_schedule(action="add", message="Time to take a break!", every_seconds=1200)
```

Dynamic task (agent executes each time):
```
cron_schedule(action="add", message="Check outfox/pocketfox GitHub stars and report", every_seconds=600)
```

List/remove:
```
cron_schedule(action="list")
cron_schedule(action="remove", job_id="abc123")
```

## Time Expressions

| User says | Parameters |
|-----------|------------|
| every 20 minutes | every_seconds: 1200 |
| every hour | every_seconds: 3600 |
| every day at 8am | cron_expr: "0 8 * * *" |
| weekdays at 5pm | cron_expr: "0 17 * * 1-5" |
