#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wave 124: HTTP-aware watchdog для OpenClaw Gateway.

Существующий `openclaw_gateway_watchdog.sh` ловит только полный bootout
(отсутствие в `launchctl list`). Этот watchdog дополняет его:

* Probes `http://127.0.0.1:18789/health` каждые 30s.
* После N consecutive fails (по умолчанию 3) считает gateway frozen и
  делает `launchctl kickstart -k gui/$UID/ai.openclaw.gateway` (если
  `--auto-restart`).
* Snapshot состояния (consecutive fails, last healthy ts, recovery
  counter) пишется в state-файл (`~/.openclaw/krab_runtime_state/
  openclaw_watchdog_state.json`).
* Pushes Prometheus gauges/counters через `record_probe_result` и
  `record_restart` (если запускаем in-process; CLI режим — без metrics).

CLI usage::

    python -m scripts.krab_openclaw_watchdog --once          # один цикл
    python -m scripts.krab_openclaw_watchdog --loop          # daemon
    python -m scripts.krab_openclaw_watchdog --auto-restart  # активный

Без `--auto-restart` watchdog только наблюдает (для безопасной обкатки).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

GATEWAY_HEALTH_URL = "http://127.0.0.1:18789/health"
GATEWAY_LABEL = "ai.openclaw.gateway"
DEFAULT_PROBE_INTERVAL_SEC = 30
DEFAULT_FAIL_THRESHOLD = 3
DEFAULT_PROBE_TIMEOUT_SEC = 5
DEFAULT_STATE_PATH = (
    Path.home() / ".openclaw" / "krab_runtime_state" / "openclaw_watchdog_state.json"
)
# Минимальный интервал между kickstart-ами (избегаем restart-loop).
KICKSTART_COOLDOWN_SEC = 120


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------


def probe_gateway(
    url: str = GATEWAY_HEALTH_URL,
    timeout: float = DEFAULT_PROBE_TIMEOUT_SEC,
) -> tuple[bool, str | None]:
    """Один HTTP-probe на /health. Возвращает (healthy, reason_if_fail)."""
    try:
        req = Request(url, headers={"User-Agent": "krab-openclaw-watchdog/1.0"})
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310 — fixed localhost
            status = getattr(resp, "status", None) or resp.getcode()
            if 200 <= int(status) < 300:
                return True, None
            return False, "http_error"
    except HTTPError:
        return False, "http_error"
    except URLError as exc:
        reason_text = str(getattr(exc, "reason", exc)).lower()
        if "refused" in reason_text:
            return False, "connection_refused"
        if "timed out" in reason_text or "timeout" in reason_text:
            return False, "timeout"
        return False, "connection_refused"
    except TimeoutError:
        return False, "timeout"
    except Exception:  # noqa: BLE001
        return False, "exception"


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


def load_state(path: Path) -> dict[str, Any]:
    """Lazy load. Любые ошибки → пустой default."""
    try:
        if not path.exists():
            return _default_state()
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _default_state()


def _default_state() -> dict[str, Any]:
    return {
        "consecutive_fails": 0,
        "last_probe_ts": 0.0,
        "last_healthy_ts": 0.0,
        "last_restart_ts": 0.0,
        "restart_count": 0,
        "last_reason": None,
    }


def save_state(path: Path, state: dict[str, Any]) -> None:
    """Atomic-ish write. Не валим watchdog на IO ошибке."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Kickstart
# ---------------------------------------------------------------------------


def kickstart_gateway(
    label: str = GATEWAY_LABEL,
    *,
    runner: Any = None,
) -> tuple[bool, str]:
    """`launchctl kickstart -k gui/$UID/<label>`.

    runner — для unit-tests (callable(list[str]) → CompletedProcess).
    """
    uid = os.getuid() if hasattr(os, "getuid") else 0
    target = f"gui/{uid}/{label}"
    cmd = ["launchctl", "kickstart", "-k", target]
    try:
        if runner is None:
            result = subprocess.run(  # noqa: S603 — fixed binary
                cmd, capture_output=True, text=True, timeout=15
            )
        else:
            result = runner(cmd)
        if result.returncode == 0:
            return True, "ok"
        return False, (result.stderr or "").strip()[:200] or f"exit={result.returncode}"
    except subprocess.TimeoutExpired:
        return False, "kickstart_timeout"
    except (OSError, FileNotFoundError) as exc:
        return False, f"kickstart_error:{exc}"


# ---------------------------------------------------------------------------
# Watchdog core
# ---------------------------------------------------------------------------


def run_once(
    *,
    state_path: Path = DEFAULT_STATE_PATH,
    fail_threshold: int = DEFAULT_FAIL_THRESHOLD,
    auto_restart: bool = False,
    probe_fn: Any = probe_gateway,
    kickstart_fn: Any = kickstart_gateway,
    now_fn: Any = time.time,
    metrics_module: Any = None,
) -> dict[str, Any]:
    """Один цикл: probe → update state → (опц.) kickstart.

    Возвращает обновлённый state-dict (для тестов / loop).
    """
    state = load_state(state_path)
    now = now_fn()
    healthy, reason = probe_fn()
    state["last_probe_ts"] = now

    # Метрики probe.
    if metrics_module is not None:
        try:
            metrics_module.record_probe_result(healthy=healthy, reason=reason)
        except Exception:  # noqa: BLE001 — fail-safe
            pass

    if healthy:
        state["consecutive_fails"] = 0
        state["last_healthy_ts"] = now
        state["last_reason"] = None
    else:
        state["consecutive_fails"] = int(state.get("consecutive_fails", 0)) + 1
        state["last_reason"] = reason
        # Auto-restart решение: только при превышении threshold + cooldown.
        if (
            auto_restart
            and state["consecutive_fails"] >= fail_threshold
            and (now - float(state.get("last_restart_ts", 0.0))) >= KICKSTART_COOLDOWN_SEC
        ):
            ok, detail = kickstart_fn()
            state["last_restart_ts"] = now
            if ok:
                state["restart_count"] = int(state.get("restart_count", 0)) + 1
                state["consecutive_fails"] = 0  # дать сервису шанс
                state["last_restart_detail"] = "ok"
                if metrics_module is not None:
                    try:
                        metrics_module.record_restart()
                    except Exception:  # noqa: BLE001
                        pass
            else:
                state["last_restart_detail"] = detail

    save_state(state_path, state)
    return state


def run_loop(
    *,
    state_path: Path = DEFAULT_STATE_PATH,
    fail_threshold: int = DEFAULT_FAIL_THRESHOLD,
    interval_sec: int = DEFAULT_PROBE_INTERVAL_SEC,
    auto_restart: bool = False,
) -> None:
    """Бесконечный loop (для daemon-режима / LaunchAgent с KeepAlive)."""
    try:
        from src.core.metrics import openclaw_health as _metrics_module
    except Exception:  # noqa: BLE001
        _metrics_module = None

    while True:
        try:
            run_once(
                state_path=state_path,
                fail_threshold=fail_threshold,
                auto_restart=auto_restart,
                metrics_module=_metrics_module,
            )
        except Exception as exc:  # noqa: BLE001 — never die
            print(f"watchdog_iteration_failed error={exc}", file=sys.stderr)
        time.sleep(interval_sec)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="OpenClaw gateway HTTP watchdog (Wave 124)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Single probe iteration then exit")
    mode.add_argument("--loop", action="store_true", help="Run daemon loop (default)")
    p.add_argument(
        "--auto-restart",
        action="store_true",
        help="launchctl kickstart on N consecutive fails",
    )
    p.add_argument("--threshold", type=int, default=DEFAULT_FAIL_THRESHOLD)
    p.add_argument("--interval", type=int, default=DEFAULT_PROBE_INTERVAL_SEC)
    p.add_argument("--state-path", type=Path, default=DEFAULT_STATE_PATH)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)
    if args.once:
        state = run_once(
            state_path=args.state_path,
            fail_threshold=args.threshold,
            auto_restart=args.auto_restart,
        )
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return 0
    run_loop(
        state_path=args.state_path,
        fail_threshold=args.threshold,
        interval_sec=args.interval,
        auto_restart=args.auto_restart,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
