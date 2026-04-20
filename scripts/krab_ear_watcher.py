#!/usr/bin/env python3
"""
Krab Ear Health Watcher (Routine #3) — runs via launchd every 15 min.

Покрывает параллельный проект Krab Ear (Swift agent + Python backend
voice transcription) который пользователь только начинает использовать.

Checks:
  1. Krab Ear Swift agent process (KrabEarAgent) — alive?
  2. Python backend — Unix socket IPC path exists + responsive?
  3. Krab Ear panel endpoint (если настроен KRAB_EAR_PANEL_URL)
  4. Launchctl service state (если ai.krab.ear.* loaded)

Actions:
  - POST alert в Krab panel /api/notify если Ear умер unexpectedly
  - Sentry message через DSN
  - НЕ рестартует автоматом (Ear ON-DEMAND по hotkey — не всегда должен быть up)
  - Но если LaunchAgent loaded AND процесса нет > 2 checks → warning

Это MONITOR ONLY — не aggressive restart, т.к. Ear интерактивный.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

STATE_DIR = Path(os.getenv("KRAB_RUNTIME_STATE_DIR", str(Path.home() / ".openclaw" / "krab_runtime_state")))
LOG_FILE = STATE_DIR / "krab_ear" / "ear_watcher.log"
STATE_FILE = STATE_DIR / "krab_ear" / "ear_watcher.json"
EAR_SOCKET_PATH = Path(
    os.getenv("KRAB_EAR_IPC_SOCKET", str(Path.home() / ".krab_ear" / "ipc.sock"))
)
KRAB_PANEL_URL = os.getenv("KRAB_PANEL_URL", "http://127.0.0.1:8080")
KRAB_EAR_PANEL_URL = os.getenv("KRAB_EAR_PANEL_URL", "").strip()  # opt-in
DOWN_THRESHOLD_CHECKS = 2


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def _log(msg: str, level: str = "info") -> None:
    line = f"{_now_iso()} [{level:7s}] {msg}"
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "swift_agent_down_count": 0,
        "python_backend_down_count": 0,
        "panel_down_count": 0,
        "last_check_utc": None,
    }


def _save_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except OSError:
        pass


def check_swift_agent() -> bool:
    """True если KrabEarAgent process запущен."""
    try:
        out = subprocess.run(
            ["pgrep", "-f", "KrabEarAgent"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return bool(out.stdout.strip())
    except (subprocess.TimeoutExpired, OSError):
        return False


def check_python_backend() -> bool:
    """True если Unix socket существует (backend listening)."""
    return EAR_SOCKET_PATH.exists()


def check_panel() -> bool:
    """Optional Ear panel check (если настроен URL)."""
    if not KRAB_EAR_PANEL_URL:
        return True  # Not configured → skip
    try:
        with urllib.request.urlopen(KRAB_EAR_PANEL_URL, timeout=3) as resp:  # noqa: S310
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


def check_launchagent_state() -> tuple[bool, str]:
    """
    Returns (loaded, state_str).
    Если ai.krab.ear.* в launchctl list — loaded.
    """
    try:
        out = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        # Только реальные Ear сервисы, НЕ наш watcher (ai.krab.ear-watcher).
        # Ear services: ai.krab.ear.agent, ai.krab.ear.rest, com.krab.ear.*
        ear_patterns = ("ai.krab.ear.", "com.krab.ear.", "ai.krab.ear\t")
        for line in out.stdout.splitlines():
            # Skip watcher'ы — они monitor Ear, не part of Ear
            if "ear-watcher" in line or "ear_watcher" in line:
                continue
            if any(p in line for p in ear_patterns):
                return True, line.strip()
        return False, "not_loaded"
    except (subprocess.TimeoutExpired, OSError):
        return False, "launchctl_failed"


def notify(text: str, level: str = "warning") -> None:
    """Send alert через Krab panel + Sentry."""
    try:
        data = json.dumps({"source": "ear_watcher", "level": level, "text": text}).encode()
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
                    "logger": "ear_watcher",
                    "message": text,
                    "tags": {"source": "ear_watcher", "project": "krab_ear"},
                    "environment": os.getenv("KRAB_ENV", "dev"),
                }
                req = urllib.request.Request(
                    f"https://{host}/api/{project_id}/store/",
                    data=json.dumps(event).encode(),
                    headers={
                        "Content-Type": "application/json",
                        "X-Sentry-Auth": (
                            f"Sentry sentry_version=7, sentry_key={public_key}, "
                            "sentry_client=krab-ear-watcher/1.0"
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

    # 1. Swift agent
    swift_ok = check_swift_agent()
    if swift_ok:
        state["swift_agent_down_count"] = 0
    else:
        state["swift_agent_down_count"] += 1
    checks["swift_agent_ok"] = swift_ok

    # 2. Python backend (socket)
    python_ok = check_python_backend()
    if python_ok:
        state["python_backend_down_count"] = 0
    else:
        state["python_backend_down_count"] += 1
    checks["python_backend_ok"] = python_ok

    # 3. Panel (optional)
    panel_ok = check_panel()
    if panel_ok:
        state["panel_down_count"] = 0
    else:
        state["panel_down_count"] += 1
    checks["panel_ok"] = panel_ok
    checks["panel_configured"] = bool(KRAB_EAR_PANEL_URL)

    # 4. Launchd state
    lc_loaded, lc_state = check_launchagent_state()
    checks["launchagent_loaded"] = lc_loaded
    checks["launchagent_state"] = lc_state

    # Decision: alert только если LaunchAgent LOADED but процесс down 2+ checks.
    # Escalation: alert 1 раз при первом crossing threshold (2→3), потом silent
    # пока не recovery (state reset) ИЛИ до следующего escalation threshold (8, 24, 72)
    # — чтобы не spam'ить каждые 15 мин.
    # (Если ear НЕ loaded — user его выключил специально, не alert)
    alert_needed = False
    alert_text_parts = []

    def _should_alert(count: int, prev_count: int) -> bool:
        """Alert на FIRST threshold cross + escalation milestones."""
        # First crossing 2→3 — initial alert
        if prev_count < DOWN_THRESHOLD_CHECKS <= count:
            return True
        # Escalation milestones (hourly → 4h → 24h @ 15-min intervals)
        for milestone in (8, 24, 96):  # 2h, 6h, 24h down
            if prev_count < milestone <= count:
                return True
        return False

    prev_state = state.copy()
    if lc_loaded:
        if not swift_ok and _should_alert(
            state["swift_agent_down_count"], prev_state.get("swift_agent_down_count_last_alert", -1)
        ):
            alert_text_parts.append(
                f"Swift agent down {state['swift_agent_down_count']}×"
            )
            alert_needed = True
            state["swift_agent_down_count_last_alert"] = state["swift_agent_down_count"]
        if not python_ok and _should_alert(
            state["python_backend_down_count"],
            prev_state.get("python_backend_down_count_last_alert", -1),
        ):
            alert_text_parts.append(
                f"Python backend socket missing {state['python_backend_down_count']}×"
            )
            alert_needed = True
            state["python_backend_down_count_last_alert"] = state["python_backend_down_count"]
        if (
            KRAB_EAR_PANEL_URL
            and not panel_ok
            and _should_alert(
                state["panel_down_count"], prev_state.get("panel_down_count_last_alert", -1)
            )
        ):
            alert_text_parts.append(f"Ear panel down {state['panel_down_count']}×")
            alert_needed = True
            state["panel_down_count_last_alert"] = state["panel_down_count"]

    # Reset alert markers при recovery
    if swift_ok:
        state.pop("swift_agent_down_count_last_alert", None)
    if python_ok:
        state.pop("python_backend_down_count_last_alert", None)
    if panel_ok:
        state.pop("panel_down_count_last_alert", None)

    if alert_needed:
        text = "🎙️ Krab Ear health issue: " + "; ".join(alert_text_parts)
        _log(text, "error")
        notify(text, "error")

    state["last_check_utc"] = _now_iso()
    state["last_checks"] = checks
    _save_state(state)

    status = (
        f"swift={'✅' if swift_ok else '❌'}({state['swift_agent_down_count']}) "
        f"python={'✅' if python_ok else '❌'}({state['python_backend_down_count']}) "
        f"lc_loaded={lc_loaded} "
        f"panel={'✅' if panel_ok else '❌'}{'(n/a)' if not KRAB_EAR_PANEL_URL else ''}"
    )
    _log(f"OK {status} alerted={alert_needed}", "info")

    return 2 if alert_needed else 0


if __name__ == "__main__":
    sys.exit(main())
