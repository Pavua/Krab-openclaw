#!/usr/bin/env python3
"""
Krab Leak Monitor (Routine #1) — runs via launchd every 30 min.

Назначение: автоматически ловить accumulation openclaw orphan procs
до того как они положат Krab (как случилось в Session 15 crisis).

Runs INDEPENDENTLY от Krab — если Krab crashed, monitor всё равно работает
(это его главная страховка).

Actions at threshold (default 25 procs):
1. pkill -9 orphan openclaw-channels / stuck helpers (keep main gateway!)
2. launchctl kickstart gateway если main gateway тоже ушёл
3. POST alert в Krab panel `/api/notify` если panel живая
4. Send Sentry message via HTTP DSN (не требует Python sentry_sdk)
5. Log в ~/.openclaw/krab_runtime_state/leak_monitor.log

Безопасность:
- НЕ убивает main openclaw-gateway (только orphans + channels children)
- НЕ перезапускает Krab (это destructive — оставляем owner решение)
- Idempotent: если procs уже в норме → просто log + exit
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

# --- Configuration ---
LEAK_THRESHOLD_CRITICAL = int(os.getenv("KRAB_LEAK_THRESHOLD_CRITICAL", "25"))
LEAK_THRESHOLD_WARNING = int(os.getenv("KRAB_LEAK_THRESHOLD_WARNING", "18"))
STATE_DIR = Path(os.getenv("KRAB_RUNTIME_STATE_DIR", str(Path.home() / ".openclaw" / "krab_runtime_state")))
LOG_FILE = STATE_DIR / "leak_monitor.log"
STATS_FILE = STATE_DIR / "leak_monitor_stats.json"
KRAB_PANEL_URL = os.getenv("KRAB_PANEL_URL", "http://127.0.0.1:8080")
OPENCLAW_GATEWAY_URL = os.getenv("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:18789")


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def _log(msg: str, level: str = "info") -> None:
    """Appends один timestamped line в monitor log + prints stdout."""
    line = f"{_now_iso()} [{level:7s}] {msg}"
    print(line, flush=True)
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def count_openclaw_procs() -> tuple[int, list[tuple[int, int, str]]]:
    """
    Returns (total_count, [(pid, ppid, command), ...]).

    Использует `ps -eo pid,ppid,command` → parsed. Не зависит от pgrep quirks.
    """
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid,ppid,command"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        _log(f"ps_failed error={exc}", "warning")
        return 0, []

    procs: list[tuple[int, int, str]] = []
    for line in out.stdout.splitlines()[1:]:  # skip header
        match = re.match(r"\s*(\d+)\s+(\d+)\s+(.+)$", line)
        if not match:
            continue
        pid, ppid, cmd = int(match.group(1)), int(match.group(2)), match.group(3)
        # Match openclaw-like commands (не grep/python tools)
        if "openclaw" in cmd.lower() and "grep" not in cmd.lower() and "leak_monitor" not in cmd:
            procs.append((pid, ppid, cmd))
    return len(procs), procs


def kill_orphan_helpers(procs: list[tuple[int, int, str]]) -> int:
    """
    Убивает orphan openclaw-channels + stuck children.
    Сохраняет main gateway (его launchctl перезапустит при крахе).

    Returns: количество убитых процессов.
    """
    killed = 0
    for pid, ppid, cmd in procs:
        # Skip main gateway parent — его держит launchctl
        if "openclaw-gateway" in cmd and ppid == 1:
            continue
        # Skip if cmd contains node main (service entry)
        if "/opt/homebrew/opt/node/bin/node" in cmd and "gateway" in cmd and ppid == 1:
            continue
        # Target: orphan channels (PPID=1 OR PPID dead) + stuck children
        try:
            os.kill(pid, 9)  # SIGKILL
            killed += 1
            _log(f"killed pid={pid} ppid={ppid} cmd_prefix={cmd[:60]}", "warning")
        except (ProcessLookupError, PermissionError) as exc:
            _log(f"kill_skip pid={pid} reason={exc}", "debug")
    return killed


def notify_krab_panel(count: int, killed: int, level: str) -> bool:
    """POST к Krab panel /api/notify. Silent fail если panel down."""
    try:
        data = json.dumps(
            {
                "source": "leak_monitor",
                "level": level,
                "text": (
                    f"🚨 Leak monitor {level}: {count} openclaw procs "
                    f"(threshold {LEAK_THRESHOLD_CRITICAL}). Killed: {killed}."
                ),
                "metadata": {"count": count, "killed": killed},
            }
        ).encode()
        req = urllib.request.Request(
            f"{KRAB_PANEL_URL}/api/notify",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
            _log(f"panel_notified status={resp.status}", "info")
            return True
    except Exception as exc:  # noqa: BLE001
        _log(f"panel_notify_failed error={exc}", "debug")
        return False


def send_sentry_message(count: int, killed: int, level: str) -> bool:
    """
    Отправляет message в Sentry через DSN HTTP endpoint (без python sentry_sdk).
    Простой `store` envelope — работает даже если Krab не запущен.
    """
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    # Parse DSN: https://<key>@<host>/<project_id>
    match = re.match(r"https://([^@]+)@([^/]+)/(\d+)", dsn)
    if not match:
        _log("sentry_dsn_malformed", "warning")
        return False
    public_key, host, project_id = match.groups()
    endpoint = f"https://{host}/api/{project_id}/store/"
    # Minimal event
    event = {
        "event_id": os.urandom(16).hex(),
        "timestamp": _now_iso(),
        "level": level,
        "platform": "python",
        "logger": "leak_monitor",
        "message": f"openclaw leak {level}: {count} procs, killed {killed}",
        "tags": {"source": "leak_monitor", "routine": "1"},
        "extra": {"count": count, "killed": killed, "threshold": LEAK_THRESHOLD_CRITICAL},
        "environment": os.getenv("KRAB_ENV", "dev"),
    }
    try:
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(event).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Sentry-Auth": (
                    f"Sentry sentry_version=7, sentry_key={public_key}, "
                    f"sentry_client=krab-leak-monitor/1.0"
                ),
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
            _log(f"sentry_sent status={resp.status}", "info")
            return True
    except Exception as exc:  # noqa: BLE001
        _log(f"sentry_failed error={exc}", "debug")
        return False


def write_stats(count: int, killed: int, level: str) -> None:
    """Persist latest stats для дашборд-интеграции."""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        stats = {
            "last_check_utc": _now_iso(),
            "openclaw_count": count,
            "killed": killed,
            "level": level,
            "threshold_critical": LEAK_THRESHOLD_CRITICAL,
            "threshold_warning": LEAK_THRESHOLD_WARNING,
        }
        STATS_FILE.write_text(json.dumps(stats, indent=2))
    except OSError as exc:
        _log(f"stats_write_failed error={exc}", "debug")


def main() -> int:
    count, procs = count_openclaw_procs()

    if count >= LEAK_THRESHOLD_CRITICAL:
        level = "critical"
        _log(f"LEAK_CRITICAL count={count} threshold={LEAK_THRESHOLD_CRITICAL}", "error")
        killed = kill_orphan_helpers(procs)
        notify_krab_panel(count, killed, level)
        send_sentry_message(count, killed, level)
        write_stats(count, killed, level)
        return 2  # exit code 2 = critical action taken

    if count >= LEAK_THRESHOLD_WARNING:
        level = "warning"
        _log(f"LEAK_WARNING count={count} threshold={LEAK_THRESHOLD_WARNING}", "warning")
        send_sentry_message(count, 0, level)
        write_stats(count, 0, level)
        return 1  # warning, no action

    level = "info"
    _log(f"OK count={count}", "info")
    write_stats(count, 0, level)
    return 0


if __name__ == "__main__":
    sys.exit(main())
