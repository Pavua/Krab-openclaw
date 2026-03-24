# -*- coding: utf-8 -*-
"""
Умный helper для device-auth логина Codex CLI.

Зачем нужен:
- обычный `codex login --device-auth` печатает URL и одноразовый код в терминал;
- если пользователь вручную открывает не ту страницу, легко словить `invalid_state`;
- этот helper сам открывает правильный device-экран OpenAI, копирует код в буфер
  обмена и оставляет пользователю только один понятный шаг: вставить код.

Связь с проектом:
- вызывается из `Login Codex CLI.command`;
- используется owner-панелью Краба как repair/relogin helper для `codex-cli`.
"""

from __future__ import annotations

import os
import pty
import re
import select
import shutil
import subprocess
import sys
from typing import Final


DEVICE_URL: Final[str] = "https://auth.openai.com/codex/device"
ANSI_RE: Final[re.Pattern[str]] = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
CODE_RE: Final[re.Pattern[str]] = re.compile(r"\b([A-Z0-9]{4,}-[A-Z0-9]{4,})\b")
URL_RE: Final[re.Pattern[str]] = re.compile(r"https://auth\.openai\.com/codex/device\b")


def strip_ansi(text: str) -> str:
    """Убирает ANSI-последовательности, чтобы парсить CLI-вывод без мусора."""
    return ANSI_RE.sub("", text)


def extract_device_code(text: str) -> str:
    """Достаёт одноразовый код device-auth из вывода Codex CLI."""
    match = CODE_RE.search(strip_ansi(text))
    return str(match.group(1) if match else "")


def extract_device_url(text: str) -> str:
    """Достаёт точный device URL из вывода Codex CLI."""
    match = URL_RE.search(strip_ansi(text))
    return str(match.group(0) if match else "")


def _print_local(message: str) -> None:
    """Печатает локальные подсказки helper'а отдельно от вывода `codex`."""
    sys.stdout.write(f"\n[Codex CLI helper] {message}\n")
    sys.stdout.flush()


def _copy_to_clipboard(text: str) -> bool:
    """Копирует код в буфер обмена macOS, чтобы не перепечатывать вручную."""
    if not text:
        return False
    pbcopy_bin = shutil.which("pbcopy") or ""
    if not pbcopy_bin:
        return False
    try:
        subprocess.run(
            [pbcopy_bin],
            input=text,
            text=True,
            check=False,
            capture_output=True,
        )
        return True
    except Exception:
        return False


def _open_browser(url: str) -> bool:
    """Открывает нужную device-auth страницу в браузере текущей macOS-учётки."""
    target = str(url or "").strip() or DEVICE_URL
    try:
        subprocess.run(["open", target], check=False, capture_output=True, text=True)
        return True
    except Exception:
        return False


def run_device_auth() -> int:
    """
    Запускает `codex login --device-auth` в PTY и автоматически помогает пользователю.

    Почему PTY:
    - так Codex CLI печатает живой интерактивный вывод без странной буферизации;
    - мы можем одновременно показать этот вывод в терминале и вытащить код/URL.
    """
    codex_bin = shutil.which("codex") or ""
    if not codex_bin:
        _print_local("Команда `codex` не найдена в PATH.")
        return 127

    master_fd, slave_fd = pty.openpty()
    process = subprocess.Popen(
        [codex_bin, "login", "--device-auth"],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
    )
    os.close(slave_fd)

    buffer = ""
    browser_opened = False
    code_copied = False

    try:
        while True:
            ready, _, _ = select.select([master_fd], [], [], 0.2)
            if master_fd in ready:
                chunk = os.read(master_fd, 4096)
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                sys.stdout.write(text)
                sys.stdout.flush()
                buffer += text

                if not browser_opened:
                    url = extract_device_url(buffer) or DEVICE_URL
                    if _open_browser(url):
                        browser_opened = True
                        _print_local(f"Открыл страницу device-auth: {url}")

                if not code_copied:
                    code = extract_device_code(buffer)
                    if code:
                        copied = _copy_to_clipboard(code)
                        code_copied = True
                        if copied:
                            _print_local(f"Код `{code}` скопирован в буфер обмена. Просто вставь его на странице.")
                        else:
                            _print_local(f"Код для ввода: {code}")

            if process.poll() is not None:
                # Забираем хвост, если он ещё остался в PTY.
                while True:
                    try:
                        chunk = os.read(master_fd, 4096)
                    except OSError:
                        chunk = b""
                    if not chunk:
                        break
                    text = chunk.decode("utf-8", errors="replace")
                    sys.stdout.write(text)
                    sys.stdout.flush()
                    buffer += text
                break
    except KeyboardInterrupt:
        _print_local("Логин прерван пользователем.")
        process.terminate()
        return 130
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass

    return int(process.wait())


def main() -> int:
    """Точка входа для `.command` helper'а."""
    _print_local("Стартую Codex CLI device-auth flow.")
    _print_local(f"Если браузер не открылся сам, открой вручную: {DEVICE_URL}")
    exit_code = run_device_auth()
    _print_local(f"Завершено с кодом: {exit_code}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
