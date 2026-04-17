# -*- coding: utf-8 -*-
"""
Dedicated Chrome instance для Krab/OpenClaw CDP automation.

Запускает Chrome с isolated profile + remote debugging port, чтобы:
- Избежать Remote Debugging Trust Prompt в основном браузере пользователя
- Изолировать автоматизацию (cookies, cache, history не смешиваются)
- Persistent cross-restart — profile сохраняется в /tmp/krab-chrome

Config через env:
    DEDICATED_CHROME_ENABLED=true       — включить auto-launch (default false)
    DEDICATED_CHROME_PROFILE_DIR=...    — путь profile (default /tmp/krab-chrome)
    DEDICATED_CHROME_PORT=9222          — CDP port (default 9222)
    DEDICATED_CHROME_APP=...            — путь Chrome.app (autodetect macOS)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import httpx

from ..core.logger import get_logger
from ..core.subprocess_env import clean_subprocess_env

log = get_logger(__name__)

DEFAULT_PROFILE_DIR = Path("/tmp/krab-chrome")
DEFAULT_CDP_PORT = 9222
CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
]


def find_chrome_binary() -> str | None:
    """Находит исполняемый файл Chrome. Returns path или None."""
    # Явное переопределение через env имеет максимальный приоритет
    env_path = os.environ.get("DEDICATED_CHROME_APP")
    if env_path and Path(env_path).exists():
        return env_path
    for path in CHROME_CANDIDATES:
        if Path(path).exists():
            return path
    # PATH-based fallback (Linux/dev окружения)
    for name in ("google-chrome", "chromium", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def is_dedicated_chrome_running(port: int = DEFAULT_CDP_PORT) -> bool:
    """Проверка что dedicated Chrome уже запущен на нашем CDP-порту."""
    try:
        r = httpx.get(f"http://127.0.0.1:{port}/json/version", timeout=2.0)
        return r.status_code == 200
    except (httpx.RequestError, httpx.HTTPError, OSError):
        return False


def launch_dedicated_chrome(
    profile_dir: Path | None = None,
    port: int | None = None,
    chrome_binary: str | None = None,
) -> tuple[bool, str]:
    """
    Запускает dedicated Chrome в detached mode. Returns (success, message).

    Идемпотентно: если Chrome на данном порту уже работает, возвращает
    (True, "already_running") без spawn'а.
    """
    profile_dir = profile_dir or Path(
        os.environ.get("DEDICATED_CHROME_PROFILE_DIR") or DEFAULT_PROFILE_DIR
    )
    port = port or int(os.environ.get("DEDICATED_CHROME_PORT") or DEFAULT_CDP_PORT)
    chrome_binary = chrome_binary or find_chrome_binary()

    if not chrome_binary:
        log.warning("dedicated_chrome_binary_not_found")
        return False, "chrome_binary_not_found"

    if is_dedicated_chrome_running(port):
        log.info("dedicated_chrome_already_running", port=port)
        return True, "already_running"

    # Создаём профиль, если его ещё нет
    try:
        profile_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.error("dedicated_chrome_profile_mkdir_failed", error=str(exc))
        return False, f"profile_mkdir_failed: {exc}"

    args = [
        chrome_binary,
        f"--user-data-dir={profile_dir}",
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-default-apps",
        "--disable-popup-blocking",
        "--disable-prompt-on-repost",
        "--no-crash-upload",
        "--disable-features=TranslateUI",
        "about:blank",
    ]

    try:
        # Detached — Chrome живёт после Krab restart.
        # start_new_session=True отделяет Chrome от process group Krab, чтобы SIGTERM
        # на stop Краба не уронил браузер.
        proc = subprocess.Popen(  # noqa: S603
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=clean_subprocess_env(),
        )
        log.info(
            "dedicated_chrome_launched",
            pid=proc.pid,
            port=port,
            profile_dir=str(profile_dir),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.error("dedicated_chrome_launch_failed", error=str(exc))
        return False, f"launch_failed: {exc}"

    # Wait до ready (health probe на /json/version)
    deadline = time.monotonic() + 10.0  # 10s timeout — Chrome обычно поднимается 1-3s
    while time.monotonic() < deadline:
        if is_dedicated_chrome_running(port):
            log.info("dedicated_chrome_ready", port=port)
            return True, "launched"
        time.sleep(0.3)

    log.warning("dedicated_chrome_launched_but_not_ready", port=port)
    return False, "launched_but_not_ready"


def get_dedicated_chrome_cdp_url(port: int = DEFAULT_CDP_PORT) -> str:
    """
    Возвращает WebSocket CDP URL dedicated Chrome (из /json/version).

    Пустая строка если Chrome недоступен.
    """
    try:
        r = httpx.get(f"http://127.0.0.1:{port}/json/version", timeout=2.0)
        if r.status_code == 200:
            return str(r.json().get("webSocketDebuggerUrl") or "")
    except (httpx.RequestError, httpx.HTTPError, OSError, ValueError):
        pass
    return ""
