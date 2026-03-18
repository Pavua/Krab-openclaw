#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Session Watchdog.

Каждые 60 секунд проверяет /api/health/lite:
- если telegram_userbot_state != "running"/"connected" — пытается перезапустить userbot
- 3 неудачи подряд → запись в лог (процесс продолжает мониторить)

Запуск: python scripts/telegram_session_watchdog.py
Лог: /tmp/krab_session_watchdog.log (stdout тоже дублируется туда)
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
import urllib.error
import urllib.request

LOG_PATH = "/tmp/krab_session_watchdog.log"
HEALTH_URL = "http://127.0.0.1:8080/api/health/lite"
RESTART_URL = "http://127.0.0.1:8080/api/krab/restart_userbot"
CHECK_INTERVAL_SEC = 60
MAX_CONSECUTIVE_FAILURES = 3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [watchdog] %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_PATH, mode="a"),
    ],
)
log = logging.getLogger("telegram_session_watchdog")

_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    log.info("signal_received signal=%d", signum)
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def _http_get(url: str, timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _http_post(url: str, timeout: float = 8.0) -> dict:
    req = urllib.request.Request(url, data=b"{}", method="POST")
    req.add_header("Content-Type", "application/json")
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


def run_watchdog() -> None:
    log.info("watchdog_started health_url=%s interval=%ds", HEALTH_URL, CHECK_INTERVAL_SEC)
    consecutive_failures = 0

    while not _shutdown:
        try:
            payload = _http_get(HEALTH_URL)

            if _is_userbot_ok(payload):
                if consecutive_failures > 0:
                    log.info("userbot_recovered after %d failures", consecutive_failures)
                consecutive_failures = 0
                log.debug("health_ok state=%s", payload.get("telegram_userbot_state"))
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
