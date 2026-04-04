#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Krab Inbox Watcher — ~/Krab_Inbox folder monitor.

Отслеживает появление новых файлов в ~/Krab_Inbox и пересылает их Крабу
через Telegram MCP (кидает сообщение на @yung_nagato с содержимым/ссылкой).

Запуск:
    /path/to/venv/bin/python scripts/krab_inbox_watcher.py

LaunchAgent: ~/Library/LaunchAgents/ai.krab.inbox-watcher.plist
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import urllib.request
from pathlib import Path

from watchdog.events import FileCreatedEvent, FileMovedEvent, FileSystemEventHandler
from watchdog.observers import Observer

INBOX_DIR = Path.home() / "Krab_Inbox"
KRAB_API_BASE = "http://127.0.0.1:8080"
TELEGRAM_CHAT_ID = os.getenv("KRAB_INBOX_TARGET", "@p0lrd")  # получатель уведомлений

# Файлы которые игнорируем (системные / временные)
IGNORE_PATTERNS = {".DS_Store", ".localized", "Thumbs.db"}
IGNORE_PREFIXES = (".", "~$", "~")
# Подождать немного после события, чтобы файл записался полностью
SETTLE_DELAY_SEC = 1.5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [krab-inbox] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("krab_inbox")


def _should_ignore(path: Path) -> bool:
    name = path.name
    if name in IGNORE_PATTERNS:
        return True
    for prefix in IGNORE_PREFIXES:
        if name.startswith(prefix):
            return True
    return False


def _send_to_krab_api(text: str) -> bool:
    """POST к Krab web API /api/notify."""
    payload = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": text}).encode()
    req = urllib.request.Request(
        f"{KRAB_API_BASE}/api/notify",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status < 400
    except Exception as e:
        log.warning("Krab /api/notify недоступен: %s", e)
        return False


def _send_via_mcp_shell(text: str) -> bool:
    """Fallback: отправляем через krab-telegram MCP через CLI если API нет."""
    # Используем Python Telegram MCP client если установлен
    mcp_script = Path(__file__).parent / "run_telegram_mcp_account.py"
    if not mcp_script.exists():
        return False
    import subprocess
    result = subprocess.run(
        [sys.executable, str(mcp_script), "--send", TELEGRAM_CHAT_ID, "--text", text],
        capture_output=True, timeout=15,
    )
    return result.returncode == 0


def _notify_krab(file_path: Path) -> None:
    """Формирует сообщение и отправляет Крабу."""
    size = file_path.stat().st_size if file_path.exists() else 0
    size_str = f"{size:,} bytes" if size < 1024 else f"{size / 1024:.1f} KB"

    # Пробуем прочитать текстовые файлы прямо в сообщение
    text_content = ""
    text_exts = {".txt", ".md", ".json", ".yaml", ".yml", ".csv", ".log", ".py", ".js", ".sh"}
    if file_path.suffix.lower() in text_exts and size < 4000:
        try:
            text_content = file_path.read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            pass

    if text_content:
        msg = (
            f"📥 Krab_Inbox: новый файл\n"
            f"📄 {file_path.name} ({size_str})\n\n"
            f"{text_content}"
        )
    else:
        msg = (
            f"📥 Krab_Inbox: новый файл\n"
            f"📄 {file_path.name} ({size_str})\n"
            f"📁 {file_path}"
        )

    # Обрезаем до Telegram лимита
    if len(msg) > 4000:
        msg = msg[:3900] + "\n…[обрезано]"

    log.info("Новый файл → Краб: %s (%s)", file_path.name, size_str)

    if not _send_to_krab_api(msg):
        if not _send_via_mcp_shell(msg):
            log.error("Не удалось отправить уведомление Крабу для файла: %s", file_path)
        else:
            log.info("Отправлено через MCP shell fallback")
    else:
        log.info("Отправлено через Krab API")


class InboxHandler(FileSystemEventHandler):
    def __init__(self) -> None:
        self._pending: dict[str, float] = {}

    def _schedule(self, path_str: str) -> None:
        self._pending[path_str] = time.monotonic() + SETTLE_DELAY_SEC

    def on_created(self, event: FileCreatedEvent) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
        if not _should_ignore(path):
            self._schedule(event.src_path)

    def on_moved(self, event: FileMovedEvent) -> None:
        if event.is_directory:
            return
        path = Path(event.dest_path)
        if not _should_ignore(path):
            self._schedule(event.dest_path)

    def flush_ready(self) -> None:
        """Вызывается периодически из главного цикла для обработки готовых файлов."""
        now = time.monotonic()
        ready = [p for p, t in self._pending.items() if now >= t]
        for path_str in ready:
            del self._pending[path_str]
            path = Path(path_str)
            if path.exists():
                try:
                    _notify_krab(path)
                except Exception as e:
                    log.error("Ошибка при обработке %s: %s", path, e)


def main() -> None:
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    log.info("Krab Inbox Watcher запущен. Слежу за %s", INBOX_DIR)

    handler = InboxHandler()
    observer = Observer()
    observer.schedule(handler, str(INBOX_DIR), recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(0.5)
            handler.flush_ready()
    except KeyboardInterrupt:
        log.info("Остановка по Ctrl+C")
    finally:
        observer.stop()
        observer.join()
        log.info("Watcher остановлен.")


if __name__ == "__main__":
    main()
