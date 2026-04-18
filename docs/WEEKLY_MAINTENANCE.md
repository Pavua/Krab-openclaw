# Krab Weekly Maintenance

## Schedule

Every **Sunday at 03:00 local time** (PST/PDT).

## Steps

1. **`maintenance_weekly.py --execute`** — VACUUM `archive.db` + log rotation + Chrome cache cleanup (files older than 7 days)
2. **`backup_archive_db.py`** — Create gzipped backup of memory database
3. **`sync_docs.py`** — Regenerate `COMMANDS_CHEATSHEET.md` + `docs/README.md`

## Setup

### Copy plist to LaunchAgents

```bash
cp /Users/pablito/Antigravity_AGENTS/Краб/scripts/launchagents/ai.krab.maintenance.plist \
   ~/Library/LaunchAgents/

launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.krab.maintenance.plist
```

### Verify load

```bash
launchctl list | grep ai.krab.maintenance
```

## Manual run

Trigger immediately (don't wait for Sunday):

```bash
launchctl kickstart -k gui/$(id -u)/ai.krab.maintenance
```

## Disable

Stop weekly execution:

```bash
launchctl bootout gui/$(id -u)/ai.krab.maintenance
```

## Output

- **`logs/maintenance.log`** — stdout from all three scripts
- **`logs/maintenance.err.log`** — stderr

Check logs after Sunday 03:00:

```bash
tail -100 /Users/pablito/Antigravity_AGENTS/Краб/logs/maintenance.log
```

## Verify success

After maintenance run (check Monday morning):

- **Memory DB compacted?** `du -sh ~/.openclaw/krab_memory/` should be stable/smaller
- **Docs refreshed?** `ls -l docs/COMMANDS_CHEATSHEET.md` — mtime should be ~Sunday 03:00
- **Old backups gzipped?** `ls ~/.openclaw/krab_memory/backups/` should show `.gz` files

## Troubleshooting

### Not running?

Check if loaded:

```bash
launchctl print gui/$(id -u)/ai.krab.maintenance
```

Check recent errors:

```bash
log stream --predicate 'eventMessage contains "ai.krab.maintenance"' --level debug
```

### Permission denied?

Ensure venv PATH exists:

```bash
ls -l /Users/pablito/Antigravity_AGENTS/Краб/venv/bin/python
```

### Database lock?

If Krab is running during maintenance and locks `archive.db`, operation will wait. No harm, will retry next week.

## Configuration

To adjust schedule, edit `StartCalendarInterval`:

| Parameter | Value | Meaning |
|-----------|-------|---------|
| `Weekday` | 0 | Sunday (0=Sunday, 1=Monday, …, 6=Saturday) |
| `Hour` | 3 | 03:00 |
| `Minute` | 0 | 00 seconds |

Then reload:

```bash
launchctl bootout gui/$(id -u)/ai.krab.maintenance
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.krab.maintenance.plist
```
