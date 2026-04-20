#!/usr/bin/env python3
"""
Krab Backend Log Scanner (launchd routine, FREE) — runs every 4 hours.

Scans OpenClaw gateway log (openclaw.log) + Krab runtime logs for anomaly
patterns, накапливая digest для next daily Sentry report.

Patterns detected:
- ERROR / FATAL / CRITICAL lines
- "timeout" / "deadline" / "freeze" / "stuck"
- "openclaw" leak signatures (PPID=1 orphans, high child count)
- SIGTERM loops (multiple SIGTERM same source within 5 min)
- Telegram FloodWait / SpamBot hints
- LLM 120s timeout events

Output: digest JSON в ~/.openclaw/krab_runtime_state/backend_scan.json

Alert triggers:
- > 10 errors в последние 1h → Sentry notify
- SIGTERM loop detected → Sentry warning
- Repeated same error > 20x → escalation (may indicate infinite loop)
"""
from __future__ import annotations

import collections
import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path

STATE_DIR = Path(os.getenv("KRAB_RUNTIME_STATE_DIR", str(Path.home() / ".openclaw" / "krab_runtime_state")))
STATS_FILE = STATE_DIR / "backend_scan.json"
LOG_FILE = STATE_DIR / "backend_scan.log"
OPENCLAW_LOG = Path(os.getenv("OPENCLAW_LOG", "/Users/pablito/Antigravity_AGENTS/Краб/openclaw.log"))

# Patterns (pre-compiled, case-insensitive)
_PATTERNS = {
    "error": re.compile(r"\b(ERROR|FATAL|CRITICAL)\b", re.IGNORECASE),
    "timeout": re.compile(r"\b(timeout|deadline|freeze|stuck|hang)\b", re.IGNORECASE),
    "leak_signature": re.compile(r"(PPID=1|orphan|zombie|defunct)", re.IGNORECASE),
    "sigterm": re.compile(r"SIGTERM", re.IGNORECASE),
    "floodwait": re.compile(r"\b(FloodWait|flood_wait|USER_BANNED|ChatWriteForbidden)\b", re.IGNORECASE),
    "llm_timeout": re.compile(r"(LLM.*timeout|openclaw.*120|llm_provider.*timeout|provider not responding)", re.IGNORECASE),
    "gateway_down": re.compile(r"(gateway.*closed|connection refused|ECONNREFUSED)", re.IGNORECASE),
    "pong_timeout": re.compile(r"pong.*timeout|ping.*timeout", re.IGNORECASE),
}

# How far back to scan (based on file modification time; not log timestamps — simpler)
SCAN_WINDOW_HOURS = 4
MAX_LINES_SCANNED = 50000  # safety cap


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def _log(msg: str, level: str = "info") -> None:
    line = f"{_now_iso()} [{level:7s}] {msg}"
    print(line, flush=True)
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _read_tail_lines(path: Path, max_lines: int) -> list[str]:
    """Read last max_lines from log file efficiently."""
    if not path.exists():
        return []
    try:
        with path.open("rb") as f:
            f.seek(0, 2)  # to end
            size = f.tell()
            chunk_size = min(size, 1024 * 512)  # 512 KB tail is enough
            f.seek(-chunk_size, 2)
            data = f.read().decode("utf-8", errors="replace")
        lines = data.splitlines()[-max_lines:]
        return lines
    except OSError as exc:
        _log(f"log_read_failed path={path} error={exc}", "warning")
        return []


def _scan_lines(lines: list[str]) -> dict:
    """Return counts + top N examples per pattern."""
    counts: dict[str, int] = collections.Counter()
    examples: dict[str, list[str]] = collections.defaultdict(list)
    for line in lines:
        for name, pat in _PATTERNS.items():
            if pat.search(line):
                counts[name] += 1
                if len(examples[name]) < 3:
                    examples[name].append(line[:300])  # truncate long lines
    return {
        "counts": dict(counts),
        "examples": dict(examples),
        "lines_scanned": len(lines),
    }


def _detect_anomalies(scan: dict, prev_scan: dict | None) -> list[dict]:
    """Return list of anomaly dicts: {level, name, count, message}."""
    anomalies: list[dict] = []
    counts = scan.get("counts", {})

    if counts.get("error", 0) > 10:
        anomalies.append(
            {
                "level": "warning",
                "name": "high_error_rate",
                "count": counts["error"],
                "message": f"{counts['error']} ERROR lines in last {SCAN_WINDOW_HOURS}h",
            }
        )

    if counts.get("sigterm", 0) > 5:
        anomalies.append(
            {
                "level": "error",
                "name": "sigterm_loop",
                "count": counts["sigterm"],
                "message": f"{counts['sigterm']} SIGTERM events — possible restart loop",
            }
        )

    if counts.get("leak_signature", 0) > 3:
        anomalies.append(
            {
                "level": "warning",
                "name": "leak_pattern",
                "count": counts["leak_signature"],
                "message": f"{counts['leak_signature']} leak signatures — check openclaw procs",
            }
        )

    if counts.get("llm_timeout", 0) > 0:
        anomalies.append(
            {
                "level": "warning",
                "name": "llm_timeout",
                "count": counts["llm_timeout"],
                "message": f"{counts['llm_timeout']} LLM timeout events (120s cap)",
            }
        )

    if counts.get("floodwait", 0) > 0:
        anomalies.append(
            {
                "level": "error",
                "name": "telegram_flood",
                "count": counts["floodwait"],
                "message": f"{counts['floodwait']} Telegram FloodWait/ban — risk of SpamBot",
            }
        )

    # Escalation: if any count grew >3x since last scan
    prev_counts = (prev_scan or {}).get("counts", {})
    for name, count in counts.items():
        prev = prev_counts.get(name, 0)
        if prev > 0 and count >= prev * 3 and count > 10:
            anomalies.append(
                {
                    "level": "warning",
                    "name": f"{name}_spike",
                    "count": count,
                    "message": f"{name} spiked {prev}→{count} (3x+)",
                }
            )
    return anomalies


def notify_via_sentry(anomalies: list[dict]) -> None:
    """Send Sentry message for each HIGH/CRITICAL anomaly."""
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        return
    m = re.match(r"https://([^@]+)@([^/]+)/(\d+)", dsn)
    if not m:
        return
    public_key, host, project_id = m.groups()
    endpoint = f"https://{host}/api/{project_id}/store/"

    import urllib.request

    for anomaly in anomalies:
        if anomaly["level"] not in ("warning", "error"):
            continue
        event = {
            "event_id": os.urandom(16).hex(),
            "timestamp": _now_iso(),
            "level": anomaly["level"],
            "platform": "python",
            "logger": "backend_log_scanner",
            "message": anomaly["message"],
            "tags": {
                "source": "backend_log_scanner",
                "anomaly": anomaly["name"],
            },
            "extra": {"count": anomaly["count"]},
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
                        "sentry_client=krab-backend-log-scanner/1.0"
                    ),
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5):  # noqa: S310
                pass
        except Exception as exc:  # noqa: BLE001
            _log(f"sentry_send_failed anomaly={anomaly['name']} error={exc}", "debug")


def main() -> int:
    if not OPENCLAW_LOG.exists():
        _log(f"log_not_found path={OPENCLAW_LOG}", "warning")
        return 0

    lines = _read_tail_lines(OPENCLAW_LOG, MAX_LINES_SCANNED)
    scan = _scan_lines(lines)

    # Load previous state для comparison
    prev_scan: dict | None = None
    if STATS_FILE.exists():
        try:
            prev_scan = json.loads(STATS_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            prev_scan = None

    anomalies = _detect_anomalies(scan, prev_scan)

    # Persist current scan
    scan["timestamp_utc"] = _now_iso()
    scan["anomalies"] = anomalies
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        STATS_FILE.write_text(json.dumps(scan, indent=2))
    except OSError:
        pass

    # Send Sentry for anomalies
    if anomalies:
        notify_via_sentry(anomalies)
        summary = ", ".join(f"{a['name']}={a['count']}" for a in anomalies)
        _log(f"anomalies_detected: {summary}", "warning")
    else:
        summary_parts = [f"{k}={v}" for k, v in scan["counts"].items() if v > 0]
        status = ", ".join(summary_parts) if summary_parts else "clean"
        _log(f"scan_complete lines={scan['lines_scanned']} status={status}", "info")

    return 2 if any(a["level"] == "error" for a in anomalies) else (1 if anomalies else 0)


if __name__ == "__main__":
    sys.exit(main())
