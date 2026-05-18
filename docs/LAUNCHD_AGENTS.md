# Krab LaunchAgents Inventory

Last updated: 2026-05-18 (S69 W5)

Total: **34 agents** managing Krab + ecosystem (30 `ai.krab.*` + 4 `com.krab.*`).
Plists live in `~/Library/LaunchAgents/` (per-user, GUI domain `gui/501`).

Common ops:
- Status: `launchctl print gui/501/<label>` (or `launchctl list | grep krab`)
- Restart: `launchctl kickstart -k gui/501/<label>`
- Bootstrap: `launchctl bootstrap gui/501 ~/Library/LaunchAgents/<label>.plist`
- Bootout: `launchctl bootout gui/501/<label>`
- Repo plist sources: `scripts/launchagents/`

---

## Core runtime

### ai.krab.core
- **Plist**: `~/Library/LaunchAgents/ai.krab.core.plist`
- **Schedule**: `RunAtLoad=true`, `KeepAlive={Crashed=true, SuccessfulExit=false}`, `ThrottleInterval=60s`
- **Program**: `venv/bin/python3 -m src.main` (cwd: project root)
- **Logs**: `logs/krab_launchd.{out,err}.log`
- **Purpose**: Main Krab userbot process (Pyrogram MTProto + owner panel :8080 + tasks).
- **Env**: `KRAB_PROACTIVE_ENABLED=1`, captcha keys, `KRAB_LLM_WALL_CLOCK_CAP_SEC=1800`, dedicated Chrome :9222.

### ai.krab.voice-gateway
- **Plist**: `ai.krab.voice-gateway.plist`
- **Schedule**: `RunAtLoad=true`, `KeepAlive={Crashed=true}`, Aqua session only
- **Program**: `anaconda3/python3 -m app.main` (cwd: `Krab Voice Gateway/`)
- **Logs**: `Krab Voice Gateway/logs/voice_gateway.{out,err}.log`
- **Purpose**: Voice Gateway (port 8090) — STT/TTS bridge for KrabVoiceiOS.

### ai.krab.ear.backend
- **Plist**: `ai.krab.ear.backend.plist`
- **Schedule**: `RunAtLoad=true`, `KeepAlive=true`, `ThrottleInterval=5s`, `ExitTimeOut=15s`
- **Program**: `Krab Ear/.venv_krab_ear/python3 backend/service.py` (Unix socket `krabear.sock`)
- **Logs**: `Krab Ear/logs/krab-ear-backend.{out,err}.log`
- **Purpose**: KrabEar STT backend (Whisper-large-v3-mlx + LM Studio LLM postprocess).

### ai.krab.ear.rest
- **Plist**: `ai.krab.ear.rest.plist`
- **Schedule**: `RunAtLoad=true`, `KeepAlive=true`
- **Program**: `Krab Ear/.venv_krab_ear/python3 backend/rest_server.py`
- **Logs**: `Krab Ear/logs/krab-ear-rest.{out,err}.log`
- **Purpose**: KrabEar REST API server (HTTP layer над backend socket).

### ai.krab.cloudflared-tunnel
- **Plist**: `ai.krab.cloudflared-tunnel.plist`
- **Schedule**: `RunAtLoad=true`, `KeepAlive=true`, `ThrottleInterval=10s`
- **Program**: `/opt/homebrew/bin/cloudflared tunnel --url http://127.0.0.1:8080`
- **Logs**: `/tmp/krab_cf_tunnel/tunnel.{log,err.log}`
- **Purpose**: Exposes owner panel :8080 через ephemeral trycloudflare URL для удалённого доступа.

---

## MCP servers

### com.krab.mcp-yung-nagato
- **Plist**: `com.krab.mcp-yung-nagato.plist`
- **Schedule**: `RunAtLoad=true`, `KeepAlive=true`, `ThrottleInterval=5s`
- **Program**: `venv/bin/python scripts/run_telegram_mcp_account.py --session-name kraab --transport sse --port 8011`
- **Logs**: `/tmp/krab-mcp-yung-nagato.{log,err.log}`
- **Purpose**: Telegram MCP server (kraab account) — SSE на :8011.

### com.krab.mcp-p0lrd
- **Plist**: `com.krab.mcp-p0lrd.plist`
- **Schedule**: `RunAtLoad=true`, `KeepAlive=true`, `ThrottleInterval=5s`
- **Program**: `run_telegram_mcp_account.py --session-name p0lrd_desktop --port 8012`
- **Logs**: `/tmp/krab-mcp-p0lrd.{log,err.log}`
- **Purpose**: Telegram MCP server (p0lrd_desktop owner account) — SSE на :8012.

### com.krab.mcp-hammerspoon
- **Plist**: `com.krab.mcp-hammerspoon.plist`
- **Schedule**: `RunAtLoad=true`, `KeepAlive=true`, `ThrottleInterval=5s`
- **Program**: `venv/bin/python -m src.mcp_hammerspoon_server`
- **Logs**: `/tmp/krab-mcp-hammerspoon.{log,err.log}`
- **Purpose**: Hammerspoon MCP — window management через SSE :8013.

### com.krab.tor-daemon
- **Plist**: `com.krab.tor-daemon.plist`
- **Schedule**: `RunAtLoad=true`, `KeepAlive=true`, `ThrottleInterval=10s`
- **Program**: `/opt/homebrew/bin/tor`
- **Logs**: `logs/tor.log`
- **Purpose**: Tor SOCKS5 proxy для `integrations/tor_bridge.py` (deprecated per Wave 50).

---

## Maintenance (scheduled)

### ai.krab.daily-maintenance
- **Schedule**: daily `02:07`, `KeepAlive=false`, `LowPriorityIO=true`
- **Program**: `/usr/bin/python3 scripts/krab_daily_maintenance.py`
- **Logs**: `/tmp/krab_daily_maintenance_stderr.log`
- **Purpose**: archive.db backup (keep 7d) + log rotation (>100MB → .gz).

### ai.krab.db-backup-daily (Wave 44-D)
- **Schedule**: daily `02:00`
- **Program**: `venv/bin/python scripts/krab_db_backup.py`
- **Logs**: `/tmp/krab_db_backup.{log,err.log}`
- **Purpose**: Backup kraab.session / archive.db / runs.sqlite. Retention 14 days (`KRAB_DB_BACKUP_RETENTION_DAYS=14`).

### ai.krab.backup-retention (Wave 172)
- **Schedule**: daily `03:00`
- **Program**: `venv/bin/python scripts/krab_backup_retention_sweep.py --json`
- **Logs**: `logs/backup_retention.{out,err}.log`
- **Purpose**: Sweeps `~/.openclaw/krab_memory/backups/` + `~/.openclaw/backups/`. keep_recent=3, max_age=14d.

### ai.krab.workspace-backup
- **Schedule**: daily `04:00`
- **Program**: `bash scripts/workspace_backup.sh`
- **Logs**: `/tmp/krab_backup.{log,err.log}`
- **Purpose**: tar.gz workspace + runtime_state + models.json, 30-day rotation.

### ai.krab.workspace-gc (S62 W1)
- **Schedule**: daily `04:00`, `LowPriorityIO=true`
- **Program**: `venv/bin/python scripts/krab_workspace_gc.py --execute`
- **Logs**: `/tmp/krab-workspace-gc.{out,err}.log`
- **Purpose**: Sweep stale git worktrees in `.claude/worktrees/`, zombie claude sessions, temp dirs.

### ai.krab.log-rotation
- **Schedule**: every 6h (`StartInterval=21600`)
- **Program**: `bash scripts/log_rotation.sh`
- **Logs**: `/tmp/krab_log_rotation.{log,err.log}`
- **Purpose**: gzip logs >50MB, delete .gz >30d, truncate live logs >500MB.

### ai.krab.inbox-cleanup (Wave 34-C)
- **Schedule**: daily `04:00`
- **Program**: `venv/bin/python scripts/inbox_cleanup_stale.py`
- **Logs**: `/tmp/krab_inbox_cleanup.log`
- **Purpose**: Auto-ack stale open inbox items >7d (info_alert / weekly_digest / cron_acked / proactive_alert).

### ai.krab.autotables-weekly (Wave 29-A)
- **Schedule**: Sunday `03:00` (`Weekday=0`)
- **Program**: `venv/bin/python scripts/refresh_claude_md_autotables.py`
- **Logs**: `/tmp/krab_autotables.{log,err.log}`
- **Purpose**: Refresh auto-generated tables в `CLAUDE.md` (endpoints/handlers/commands counts).

### ai.krab.nightly-audit (Wave 40-A, 65-B)
- **Schedule**: daily `03:00`, `RunAtLoad=true` (catch-up missed nights post-sleep)
- **Program**: `venv/bin/python scripts/krab_nightly_audit.py`
- **Logs**: `/tmp/krab_nightly_audit.{log,err.log}`
- **Purpose**: 8-dim self-audit (process health, DB integrity, bypass perf, memory trend, disk, inbox bloat, OAuth, zombies). Markdown report → Saved Messages only on warn/critical.

---

## Health / monitoring

### ai.krab.health-watcher
- **Schedule**: every 15 min (`StartInterval=900`), `RunAtLoad=true`
- **Program**: `/usr/bin/python3 scripts/krab_health_watcher.py`
- **Logs**: `/tmp/krab_health_watcher_stderr.log`
- **Purpose**: Panel :8080 ping + OpenClaw gateway health (auto-kickstart) + Gemini quota + disk safety.

### ai.krab.gateway-watchdog
- **Schedule**: every 5 min (`StartInterval=300`)
- **Program**: `bash scripts/openclaw_gateway_watchdog.sh`
- **Logs**: `/tmp/krab_gateway_watchdog/{stdout,stderr}.log`
- **Purpose**: External watchdog для `ai.openclaw.gateway`. Если LaunchAgent отсутствует → reload + Telegram alert.

### ai.krab.coexistence-monitor
- **Schedule**: every 60s, `RunAtLoad=true`
- **Program**: `venv/bin/python scripts/krab_ear_coexistence_monitor.py`
- **Logs**: `/tmp/krab_coexistence_monitor.{out,err}.log`
- **Purpose**: Monitor Krab Ear coexistence (RAM pressure, model load conflicts).

### ai.krab.ear-watcher
- **Schedule**: every 15 min (`StartInterval=900`), `LowPriorityIO=true`
- **Program**: `/usr/bin/python3 scripts/krab_ear_watcher.py`
- **Logs**: `/tmp/krab_ear_watcher_stderr.log`
- **Purpose**: Krab Ear health (Swift agent + Python backend). Silent если Ear off, alert если LaunchAgent loaded но process down.

### ai.krab.db-lock-monitor (S23 backlog #2)
- **Schedule**: hourly (`StartInterval=3600`), `RunAtLoad=true`
- **Program**: `bash scripts/db_lock_monitor.sh`
- **Logs**: `/tmp/krab_db_lock_monitor/{launchd.out,launchd.err,run}.log`
- **Purpose**: Scans `krab_launchd.out.log` за "database is locked" в sliding 60-min window. >5 events/hour → Telegram alert (cooldown 6h).

### ai.krab.leak-monitor (Routine #1, Wave 65-A)
- **Schedule**: every 30 min (`StartInterval=1800`), `RunAtLoad=true`
- **Program**: `/usr/bin/python3 scripts/krab_leak_monitor.py`
- **Logs**: `~/.openclaw/krab_runtime_state/leak_monitor.log`, stderr `/tmp/krab_leak_monitor_stderr.log`
- **Purpose**: Detect leaked Chrome / openclaw subprocesses. Thresholds warn=18, critical=25. Independent of Krab — works even when Krab crashed.

### ai.krab.memory-baseline
- **Schedule**: every 60s, `RunAtLoad=true`, `LowPriorityIO=true`
- **Program**: `venv/bin/python scripts/memory_baseline_collector.py`
- **Logs**: `/tmp/memory_baseline_collector.{out,err}.log`
- **Purpose**: Passive memory baseline collector (RSS/swap snapshots).

### ai.krab.backend-log-scanner
- **Schedule**: every 4h (`StartInterval=14400`), `RunAtLoad=true`
- **Program**: `/usr/bin/python3 scripts/krab_backend_log_scanner.py`
- **Logs**: `/tmp/krab_backend_log_scanner_stderr.log`
- **Purpose**: Scan `openclaw.log` за anomaly patterns (errors, timeouts, SIGTERM loops, FloodWait, LLM timeouts). Sentry DSN alerting.

### ai.krab.bypass-perf-alert (Wave 35-B, 206)
- **Schedule**: every 15 min, `RunAtLoad=true`
- **Program**: `venv/bin/python scripts/bypass_perf_alert_check.py`
- **Logs**: `/tmp/krab_bypass_perf_alert_{stdout,stderr}.log`
- **Purpose**: p95 latency + fail_rate guard на `bypass_perf.jsonl`. Thresholds: cli p95>60s, vertex p95>30s, fail_rate>10%. Debounce 1h.

### ai.krab.gcp-quota-poc-watcher
- **Schedule**: every 30 min (`StartInterval=1800`), `RunAtLoad=true`, `ThrottleInterval=300`
- **Program**: `venv/bin/python3 scripts/gcp_quota_poc_watcher.py`
- **Logs**: `/tmp/krab_gcp_quota_poc_watcher.{log,err.log}`
- **Purpose**: GCP quota POC watcher для bonus-credits projects (caramel-anvil etc).

### ai.krab.quota-history (Wave 34-A, 206)
- **Schedule**: hourly (`StartInterval=3600`), `RunAtLoad=true`
- **Program**: `venv/bin/python scripts/quota_history_snapshot.py`
- **Logs**: `logs/quota_history.{out,err}.log`
- **Purpose**: Hourly snapshot quota state → `quota_history.jsonl`.

### ai.krab.inbox-watcher
- **Schedule**: `RunAtLoad=true`, `KeepAlive=true`, `ThrottleInterval=5s`
- **Program**: `venv/bin/python scripts/krab_inbox_watcher.py`
- **Logs**: `/tmp/krab-inbox-watcher.{log,err.log}`
- **Purpose**: Monitor `~/Krab_Inbox` за новыми файлами (fswatch-like daemon).

---

## Sync / OAuth

### ai.krab.oauth-resync (Wave 50-B)
- **Schedule**: every 15 min (`StartInterval=900`), `RunAtLoad=true`
- **Program**: `venv/bin/python scripts/sync_gemini_oauth_to_openclaw.py`
- **Logs**: `/tmp/krab-oauth-resync.{log,err.log}`
- **Purpose**: Mirror Gemini CLI OAuth credentials → OpenClaw. Wave 50-B: force-refresh через Google endpoint когда `expiry_in_min < -60`.

---

## Sentry / observability

### ai.krab.sentry-poll (Wave 23, 05.05)
- **Schedule**: every 5 min (`StartInterval=300`), `RunAtLoad=true`
- **Program**: `bash scripts/sentry_poll_alerts.sh`
- **Logs**: `/tmp/krab_sentry_poll/launchd.{log,err.log}`
- **Purpose**: Poll Sentry API за new issues (replaces webhook — trycloudflare URLs blocked).

### ai.krab.sentry-stale-resolver (Wave 42-A)
- **Schedule**: daily `04:30 UTC`
- **Program**: `venv/bin/python scripts/sentry_stale_resolver.py --no-dry-run`
- **Logs**: `/tmp/krab_sentry_stale_resolver.{log,err.log}`
- **Purpose**: Close issues >7d, one-off (count≤2, 3+d), or matching known_fixed patterns. Log: `~/.openclaw/krab_runtime_state/sentry_resolver.log`.

### ai.krab.sentry-quota-check (Wave 71)
- **Schedule**: Monday `10:00` local (`Weekday=1`)
- **Program**: `venv/bin/python scripts/krab_sentry_quota_check.py`
- **Logs**: `logs/sentry_quota_check.{out,err}.log`
- **Purpose**: Weekly Sentry quota baseline check.

---

## Disabled / archived

Not loaded — `.disabled` или `.disabled_*` suffix:

- `ai.krab.cloudflared-sentry-sync.plist.disabled` — superseded by `sentry-poll`
- `ai.krab.signal-ops-guard.plist.disabled.session28`
- `com.openclaw.krabear.plist.disabled_20260219_0113`

---

## Categorical summary

| Category | Count | Key examples |
|---|---|---|
| Core runtime | 5 | core, voice-gateway, ear.backend, ear.rest, cloudflared-tunnel |
| MCP servers | 4 | mcp-yung-nagato, mcp-p0lrd, mcp-hammerspoon, tor-daemon |
| Maintenance | 8 | daily-maintenance, db-backup-daily, backup-retention, workspace-gc, workspace-backup, log-rotation, inbox-cleanup, autotables-weekly, nightly-audit |
| Health / monitor | 12 | health-watcher, gateway-watchdog, coexistence-monitor, ear-watcher, db-lock-monitor, leak-monitor, memory-baseline, backend-log-scanner, bypass-perf-alert, gcp-quota-poc-watcher, quota-history, inbox-watcher |
| Sync / OAuth | 1 | oauth-resync |
| Sentry | 3 | sentry-poll, sentry-stale-resolver, sentry-quota-check |
| **Total active** | **34** | (30 ai.krab + 4 com.krab) |

Gateway (`ai.openclaw.gateway`) — managed externally через `openclaw gateway`, не входит в Krab plist set, но watched by `ai.krab.gateway-watchdog`.
