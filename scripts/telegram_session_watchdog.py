#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
telegram_session_watchdog.py — внешний watchdog для Telegram userbot и OpenClaw gateway.

Что делает:
- каждые 60 секунд проверяет `:8080/api/health/lite`;
- если userbot деградировал, вызывает restart endpoint через owner-local web API;
- если userbot жив, но gateway `:18789` упал, пытается поднять только gateway;
- пишет все действия в per-account runtime-state, а не в общий `/tmp`.

Почему этот контур важен:
- сам runtime может жить дольше Telegram transport или gateway отдельно;
- owner'у нужен не "полный restart наугад", а узкий self-heal для split-state;
- раньше watchdog бил в endpoint, которого не было, и фактически не лечил runtime.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def _resolve_log_path() -> Path:
    """
    Возвращает per-account log path для watchdog.

    Почему не `/tmp`:
    - на multi-account Mac там легко остаются owner-only файлы другой учётки;
    - launcher уже хранит PID/state в `~/.openclaw/krab_runtime_state`;
    - лог рядом с runtime-state проще чистить и передавать в handoff.
    """
    runtime_state_dir = str(os.getenv("KRAB_RUNTIME_STATE_DIR", "") or "").strip()
    if runtime_state_dir:
        base_dir = Path(runtime_state_dir)
    else:
        base_dir = Path.home() / ".openclaw" / "krab_runtime_state"
    try:
        base_dir.mkdir(parents=True, exist_ok=True)
        return base_dir / "krab_session_watchdog.log"
    except OSError:
        return Path("/tmp/krab_session_watchdog.log")


LOG_PATH = _resolve_log_path()
HEALTH_URL = "http://127.0.0.1:8080/api/health/lite"
RESTART_URL = "http://127.0.0.1:8080/api/krab/restart_userbot"
GATEWAY_HEALTH_URL = "http://127.0.0.1:18789/health"
CHECK_INTERVAL_SEC = 60
MAX_CONSECUTIVE_FAILURES = 3
GATEWAY_RESTART_COOLDOWN_SEC = 180  # не перезапускать шлюз чаще чем раз в 3 минуты
GATEWAY_STARTUP_WAIT_SEC = 8       # ждать старта шлюза (с ретраями)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
GATEWAY_LOG_PATH = PROJECT_ROOT / "openclaw.log"

log = logging.getLogger("telegram_session_watchdog")
log.setLevel(logging.INFO)
# Предотвращаем дублирование при повторном импорте
if not log.handlers:
    _fmt = logging.Formatter("%(asctime)s [watchdog] %(levelname)s %(message)s")
    _sh = logging.StreamHandler(sys.stdout)
    _sh.setFormatter(_fmt)
    _fh = logging.FileHandler(LOG_PATH, mode="a")
    _fh.setFormatter(_fmt)
    log.handlers = [_sh, _fh]
    log.propagate = False  # Не дублировать через root logger

_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    log.info("signal_received signal=%d", signum)
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def _build_write_headers() -> dict[str, str]:
    """Возвращает auth-header для write endpoint'ов, если WEB_API_KEY уже задан в env."""
    headers = {"Content-Type": "application/json"}
    web_api_key = str(os.getenv("WEB_API_KEY", "") or "").strip()
    if web_api_key:
        headers["X-Krab-Web-Key"] = web_api_key
    return headers


def _http_get(url: str, timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _http_post(url: str, timeout: float = 8.0, payload: dict | None = None) -> dict:
    body = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    for key, value in _build_write_headers().items():
        req.add_header(key, value)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _is_userbot_ok(payload: dict) -> bool:
    """True если userbot считается живым по health/lite ответу."""
    state = str(payload.get("telegram_userbot_state") or "").strip().lower()
    session_state = str(payload.get("telegram_session_state") or "").strip().lower()
    # "running" или "connected" — живые состояния
    if state in {"running", "connected", "ok"}:
        return True
    # Если нет явного userbot_state, смотрим на session_state
    if session_state in {"authorized", "connected", "ok"}:
        return True
    return False


def _try_restart_userbot() -> bool:
    """Попытка перезапустить userbot через REST API. Возвращает True при успехе."""
    try:
        resp = _http_post(RESTART_URL)
        ok = bool(resp.get("ok"))
        log.info("restart_userbot_attempt ok=%s resp=%r", ok, resp)
        return ok
    except Exception as exc:
        log.warning("restart_userbot_failed error=%s", exc)
        return False


def _is_gateway_ok(payload: dict) -> bool:
    """True, если OpenClaw gateway реально healthy по `/health`."""
    if not isinstance(payload, dict):
        return False
    if bool(payload.get("ok")):
        return True
    return str(payload.get("status") or "").strip().lower() == "live"


def _resolve_openclaw_bin() -> str:
    """Ищет бинарник OpenClaw в стандартном месте или в PATH."""
    preferred = "/opt/homebrew/bin/openclaw"
    if Path(preferred).exists():
        return preferred
    return str(shutil.which("openclaw") or "")


_last_gateway_restart_at: float = 0.0


def _try_restart_gateway() -> bool:
    """Пытается мягко перезапустить только OpenClaw gateway.

    Улучшения:
    - Cooldown: не перезапускает чаще чем раз в GATEWAY_RESTART_COOLDOWN_SEC.
    - Retry loop: ждёт до GATEWAY_STARTUP_WAIT_SEC с ретраями вместо однократного probe.
    - Проверяет, жив ли шлюз УЖЕ перед тем как убивать.
    """
    global _last_gateway_restart_at

    # Cooldown: не дёргать шлюз слишком часто
    now = time.time()
    if now - _last_gateway_restart_at < GATEWAY_RESTART_COOLDOWN_SEC:
        elapsed = int(now - _last_gateway_restart_at)
        log.info(
            "restart_gateway_cooldown remaining=%ds",
            GATEWAY_RESTART_COOLDOWN_SEC - elapsed,
        )
        return False

    openclaw_bin = _resolve_openclaw_bin()
    if not openclaw_bin:
        log.warning("restart_gateway_skipped reason=openclaw_bin_missing")
        return False

    _last_gateway_restart_at = now

    # Сначала проверим — может шлюз уже поднялся сам (предыдущий Popen мог успеть)
    try:
        payload = _http_get(GATEWAY_HEALTH_URL, timeout=3.0)
        if _is_gateway_ok(payload):
            log.info("restart_gateway_skipped reason=already_alive payload=%r", payload)
            return True
    except Exception:  # noqa: BLE001
        pass  # Шлюз реально мёртв, продолжаем рестарт

    # Мягкая остановка
    try:
        subprocess.run(
            [openclaw_bin, "gateway", "stop"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("restart_gateway_stop_failed error=%s", exc)

    # Жёсткая остановка оставшихся процессов
    try:
        subprocess.run(
            ["pkill", "-f", "openclaw( |$).*gateway( |$)|openclaw-gateway"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError as exc:
        log.warning("restart_gateway_pkill_failed error=%s", exc)

    time.sleep(1.0)  # Дать процессам умереть

    # Запуск нового шлюза
    with GATEWAY_LOG_PATH.open("ab") as stream:
        proc = subprocess.Popen(  # noqa: S603
            [openclaw_bin, "gateway", "run", "--port", "18789"],
            cwd=str(PROJECT_ROOT),
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    log.info("restart_gateway_spawned pid=%s", proc.pid)

    # Retry loop: ждём до GATEWAY_STARTUP_WAIT_SEC с ретраями каждые 2с
    deadline = time.time() + GATEWAY_STARTUP_WAIT_SEC
    attempt = 0
    while time.time() < deadline:
        time.sleep(2.0)
        attempt += 1
        try:
            payload = _http_get(GATEWAY_HEALTH_URL, timeout=3.0)
            if _is_gateway_ok(payload):
                log.info(
                    "restart_gateway_ok pid=%s attempt=%d payload=%r",
                    proc.pid, attempt, payload,
                )
                return True
        except Exception as exc:  # noqa: BLE001
            log.debug("restart_gateway_probe attempt=%d error=%s", attempt, exc)

    log.warning("restart_gateway_failed pid=%s attempts=%d", proc.pid, attempt)
    return False


def run_watchdog() -> None:
    log.info(
        "watchdog_started health_url=%s gateway_health_url=%s interval=%ds",
        HEALTH_URL,
        GATEWAY_HEALTH_URL,
        CHECK_INTERVAL_SEC,
    )
    consecutive_failures = 0
    consecutive_gateway_failures = 0

    while not _shutdown:
        try:
            payload = _http_get(HEALTH_URL)

            if _is_userbot_ok(payload):
                if consecutive_failures > 0:
                    log.info("userbot_recovered after %d failures", consecutive_failures)
                consecutive_failures = 0
                log.debug("health_ok state=%s", payload.get("telegram_userbot_state"))

                try:
                    gateway_payload = _http_get(GATEWAY_HEALTH_URL, timeout=3.0)
                    if _is_gateway_ok(gateway_payload):
                        if consecutive_gateway_failures > 0:
                            log.info(
                                "gateway_recovered after %d failures",
                                consecutive_gateway_failures,
                            )
                        consecutive_gateway_failures = 0
                    else:
                        consecutive_gateway_failures += 1
                        log.warning(
                            "gateway_not_ok consecutive=%d payload=%r",
                            consecutive_gateway_failures,
                            gateway_payload,
                        )
                        if consecutive_gateway_failures >= MAX_CONSECUTIVE_FAILURES:
                            log.error(
                                "gateway_down_threshold_reached consecutive=%d — attempting restart",
                                consecutive_gateway_failures,
                            )
                            _try_restart_gateway()
                            consecutive_gateway_failures = 0
                except Exception as exc:  # noqa: BLE001
                    consecutive_gateway_failures += 1
                    log.warning(
                        "gateway_health_error consecutive=%d error=%s",
                        consecutive_gateway_failures,
                        exc,
                    )
                    if consecutive_gateway_failures >= MAX_CONSECUTIVE_FAILURES:
                        log.error(
                            "gateway_unreachable_threshold_reached consecutive=%d — attempting restart",
                            consecutive_gateway_failures,
                        )
                        _try_restart_gateway()
                        consecutive_gateway_failures = 0
            else:
                consecutive_failures += 1
                state = payload.get("telegram_userbot_state")
                err = payload.get("telegram_userbot_error_code")
                log.warning(
                    "userbot_not_ok consecutive=%d state=%r error_code=%r",
                    consecutive_failures, state, err,
                )

                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    log.error(
                        "userbot_down_threshold_reached consecutive=%d — attempting restart",
                        consecutive_failures,
                    )
                    _try_restart_userbot()
                    # Сбрасываем счётчик после попытки, чтобы не спамить
                    consecutive_failures = 0

        except urllib.error.URLError as exc:
            consecutive_failures += 1
            log.warning("health_check_unreachable consecutive=%d error=%s", consecutive_failures, exc)
        except Exception as exc:
            consecutive_failures += 1
            log.warning("health_check_error consecutive=%d error=%s", consecutive_failures, exc)

        # Ожидание следующей проверки с поддержкой graceful shutdown
        for _ in range(CHECK_INTERVAL_SEC * 2):
            if _shutdown:
                break
            time.sleep(0.5)

    log.info("watchdog_stopped")


if __name__ == "__main__":
    run_watchdog()
