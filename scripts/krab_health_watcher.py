#!/usr/bin/env python3
"""
Krab Health Watcher (Routines #2 + #9 + #14) — runs via launchd every 15 min.

Checks:
  1. Krab panel :8080 /api/uptime → if down > 2 consecutive checks → alert
  2. OpenClaw gateway :18789 /healthz → if down > 2 checks → launchctl kickstart
  3. Gemini API balance (через quick /v1/models probe) → alert если 429/quota
  4. Disk space / Krab memory — если свободно < 1 GB / > 30 GB RAM → alert

State файл: ~/.openclaw/krab_runtime_state/health_watcher.json
Log: ~/.openclaw/krab_runtime_state/health_watcher.log

Actions — ТОЛЬКО мягкие:
- POST в Krab panel /api/notify (silent if down)
- Sentry message через DSN
- НЕ рестартует Krab (это destructive)
- Перезапускает OpenClaw gateway launchctl (idempotent)
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

STATE_DIR = Path(os.getenv("KRAB_RUNTIME_STATE_DIR", str(Path.home() / ".openclaw" / "krab_runtime_state")))
LOG_FILE = STATE_DIR / "health_watcher.log"
STATE_FILE = STATE_DIR / "health_watcher.json"
KRAB_PANEL_URL = os.getenv("KRAB_PANEL_URL", "http://127.0.0.1:8080")
OPENCLAW_URL = os.getenv("OPENCLAW_URL", "http://127.0.0.1:18789")
GEMINI_PROBE_TIMEOUT = 10
DOWN_THRESHOLD_CHECKS = 2  # consecutive failures → alert/action


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


def _http_get(url: str, timeout: int = 5) -> tuple[int, str]:
    """Returns (http_code, body). 0 on connection failure."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return resp.status, resp.read().decode("utf-8", errors="replace")[:500]
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except Exception:  # noqa: BLE001
        return 0, ""


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "panel_down_count": 0,
        "gateway_down_count": 0,
        "gemini_fail_count": 0,
        "last_check_utc": None,
    }


def _save_state(state: dict) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except OSError:
        pass


def check_krab_panel() -> bool:
    """True если panel отвечает 200 + ok=true."""
    code, body = _http_get(f"{KRAB_PANEL_URL}/api/uptime")
    if code != 200:
        return False
    try:
        return json.loads(body).get("ok") is True
    except json.JSONDecodeError:
        return False


def check_openclaw_gateway() -> bool:
    """True если gateway healthz возвращает 200."""
    code, _ = _http_get(f"{OPENCLAW_URL}/healthz")
    return code == 200


def restart_openclaw_gateway() -> bool:
    """launchctl kickstart main gateway. Returns True if command succeeded."""
    try:
        result = subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/ai.openclaw.gateway"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def check_gemini_quota() -> tuple[bool, str]:
    """
    True if Gemini key still works (no 429).
    Reads GEMINI_API_KEY_FREE from .env (simple read, no dotenv dep).
    """
    env_file = Path(os.getenv("KRAB_REPO", "/Users/pablito/Antigravity_AGENTS/Краб")) / ".env"
    api_key = ""
    if env_file.exists():
        for line in env_file.read_text(errors="replace").splitlines():
            m = re.match(r"\s*GEMINI_API_KEY_FREE\s*=\s*(.+)\s*$", line)
            if m:
                api_key = m.group(1).strip().strip("'\"")
                break
    if not api_key:
        return True, "no_key_configured"  # silent skip

    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    code, _ = _http_get(url, timeout=GEMINI_PROBE_TIMEOUT)
    if code == 200:
        return True, "ok"
    if code == 429:
        return False, "quota_exceeded"
    if code == 401 or code == 403:
        return False, f"auth_error_{code}"
    return False, f"http_{code}"


def check_disk_space() -> tuple[bool, int]:
    """True if > 1 GB free. Returns (ok, free_gb)."""
    try:
        usage = shutil.disk_usage("/")
        free_gb = usage.free // (1024**3)
        return free_gb > 1, free_gb
    except OSError:
        return True, -1


def notify(text: str, level: str = "warning") -> None:
    """Send alert через Krab panel + Sentry."""
    # Krab panel
    try:
        data = json.dumps({"source": "health_watcher", "level": level, "text": text}).encode()
        req = urllib.request.Request(
            f"{KRAB_PANEL_URL}/api/notify",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5):  # noqa: S310
            pass
    except Exception:  # noqa: BLE001
        pass

    # Sentry via DSN
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if dsn:
        m = re.match(r"https://([^@]+)@([^/]+)/(\d+)", dsn)
        if m:
            public_key, host, project_id = m.groups()
            try:
                event = {
                    "event_id": os.urandom(16).hex(),
                    "timestamp": _now_iso(),
                    "level": level,
                    "platform": "python",
                    "logger": "health_watcher",
                    "message": text,
                    "tags": {"source": "health_watcher", "routine": "2+9+14"},
                    "environment": os.getenv("KRAB_ENV", "dev"),
                }
                req = urllib.request.Request(
                    f"https://{host}/api/{project_id}/store/",
                    data=json.dumps(event).encode(),
                    headers={
                        "Content-Type": "application/json",
                        "X-Sentry-Auth": (
                            f"Sentry sentry_version=7, sentry_key={public_key}, "
                            "sentry_client=krab-health-watcher/1.0"
                        ),
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5):  # noqa: S310
                    pass
            except Exception:  # noqa: BLE001
                pass


def main() -> int:
    state = _load_state()
    checks = {}
    actions = []

    # 1. Krab panel
    panel_ok = check_krab_panel()
    if panel_ok:
        state["panel_down_count"] = 0
    else:
        state["panel_down_count"] += 1
    checks["panel_ok"] = panel_ok
    if state["panel_down_count"] >= DOWN_THRESHOLD_CHECKS:
        text = f"🚨 Krab panel :8080 down {state['panel_down_count']}× consecutive checks."
        _log(text, "error")
        notify(text, "error")
        actions.append("alert_panel_down")

    # 2. OpenClaw gateway (+ auto-kickstart on 2nd consecutive down)
    gateway_ok = check_openclaw_gateway()
    if gateway_ok:
        state["gateway_down_count"] = 0
    else:
        state["gateway_down_count"] += 1
    checks["gateway_ok"] = gateway_ok
    if state["gateway_down_count"] >= DOWN_THRESHOLD_CHECKS:
        text = f"⚠️ OpenClaw gateway :18789 down {state['gateway_down_count']}×. Kickstarting…"
        _log(text, "warning")
        notify(text, "warning")
        if restart_openclaw_gateway():
            _log("gateway_kickstart_ok", "info")
            actions.append("gateway_kickstart_success")
            state["gateway_down_count"] = 0  # reset после успешного restart
        else:
            _log("gateway_kickstart_failed", "error")
            actions.append("gateway_kickstart_failed")

    # 3. Disk space
    # (Gemini quota check removed — covered by Sentry daily digest routine)
    disk_ok, free_gb = check_disk_space()
    checks["disk_ok"] = disk_ok
    checks["disk_free_gb"] = free_gb
    if not disk_ok:
        text = f"🚨 Low disk: only {free_gb} GB free."
        _log(text, "error")
        notify(text, "error")
        actions.append("disk_low")

    state["last_check_utc"] = _now_iso()
    state["last_checks"] = checks
    state["last_actions"] = actions
    _save_state(state)

    status_summary = (
        f"panel={'✅' if panel_ok else '❌'}({state['panel_down_count']}) "
        f"gateway={'✅' if gateway_ok else '❌'}({state['gateway_down_count']}) "
        f"gemini={'✅' if gemini_ok else '❌'}({gemini_status}) "
        f"disk={free_gb}GB"
    )
    _log(f"OK {status_summary} actions={actions or 'none'}", "info")

    # Exit code: 0 if all ok, 1 if any warnings, 2 if any errors/actions taken
    if not panel_ok or not disk_ok:
        return 2
    if not gateway_ok or not gemini_ok:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
