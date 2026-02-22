#!/usr/bin/env python3
"""
Проверка доступа к почте (Apple Mail / IMAP / SMTP) для Krab.

Зачем нужен файл:
- Быстро проверяет, готова ли почтовая интеграция без запуска бота.
- Даёт понятный статус по конфигурации и сетевому доступу.

Связь с системой:
- Использует те же переменные окружения, что src/modules/email_manager.py.
- Ничего не отправляет и не изменяет, только проверяет подключение и авторизацию.
"""

from __future__ import annotations

import imaplib
import os
import smtplib
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"


def _load_env_file(path: Path) -> None:
    """Подгружает .env в process env без перезаписи уже заданных переменных."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _mask_login(value: str | None) -> str:
    """Маскирует логин для безопасного вывода в терминал."""
    if not value:
        return "<пусто>"
    if "@" in value:
        name, domain = value.split("@", 1)
        if len(name) <= 2:
            return f"{name[0]}***@{domain}" if name else f"***@{domain}"
        return f"{name[:2]}***@{domain}"
    return value[:2] + "***" if len(value) > 2 else "***"


def _required_config() -> dict[str, str | None]:
    """Собирает обязательные переменные конфигурации почты."""
    return {
        "EMAIL_IMAP_SERVER": os.getenv("EMAIL_IMAP_SERVER"),
        "EMAIL_IMAP_PORT": os.getenv("EMAIL_IMAP_PORT", "993"),
        "EMAIL_SMTP_SERVER": os.getenv("EMAIL_SMTP_SERVER"),
        "EMAIL_SMTP_PORT": os.getenv("EMAIL_SMTP_PORT", "587"),
        "EMAIL_USER": os.getenv("EMAIL_USER"),
        "EMAIL_PASS": os.getenv("EMAIL_PASS"),
    }


def _check_imap(server: str, port: int, user: str, password: str) -> tuple[bool, str]:
    """Проверяет IMAP: SSL connect + login + select inbox."""
    try:
        socket.setdefaulttimeout(15)
        mail = imaplib.IMAP4_SSL(server, port)
        mail.login(user, password)
        status, _ = mail.select("inbox")
        mail.logout()
        if status != "OK":
            return False, f"IMAP select inbox вернул статус: {status}"
        return True, "IMAP OK"
    except Exception as exc:  # noqa: BLE001
        return False, f"IMAP ошибка: {exc}"


def _check_smtp(server: str, port: int, user: str, password: str) -> tuple[bool, str]:
    """Проверяет SMTP: connect + starttls + login + noop."""
    try:
        socket.setdefaulttimeout(15)
        smtp = smtplib.SMTP(server, port, timeout=15)
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(user, password)
        code, _ = smtp.noop()
        smtp.quit()
        if code != 250:
            return False, f"SMTP NOOP вернул код: {code}"
        return True, "SMTP OK"
    except Exception as exc:  # noqa: BLE001
        return False, f"SMTP ошибка: {exc}"


def main() -> int:
    """Точка входа CLI-диагностики почты."""
    _load_env_file(ENV_PATH)
    cfg = _required_config()

    print("📧 Диагностика почтовой интеграции Krab")
    print(f"   Пользователь: {_mask_login(cfg['EMAIL_USER'])}")
    print(f"   IMAP: {cfg['EMAIL_IMAP_SERVER']}:{cfg['EMAIL_IMAP_PORT']}")
    print(f"   SMTP: {cfg['EMAIL_SMTP_SERVER']}:{cfg['EMAIL_SMTP_PORT']}")

    missing = [k for k, v in cfg.items() if not v]
    if missing:
        print("\n❌ Конфигурация неполная. Отсутствуют переменные:")
        for key in missing:
            print(f"   - {key}")
        print("\nПодсказка: заполни их в .env и запусти диагностику снова.")
        return 2

    try:
        imap_port = int(cfg["EMAIL_IMAP_PORT"] or "993")
        smtp_port = int(cfg["EMAIL_SMTP_PORT"] or "587")
    except ValueError:
        print("\n❌ Порты EMAIL_IMAP_PORT / EMAIL_SMTP_PORT должны быть числами.")
        return 2

    imap_ok, imap_msg = _check_imap(
        cfg["EMAIL_IMAP_SERVER"] or "",
        imap_port,
        cfg["EMAIL_USER"] or "",
        cfg["EMAIL_PASS"] or "",
    )
    smtp_ok, smtp_msg = _check_smtp(
        cfg["EMAIL_SMTP_SERVER"] or "",
        smtp_port,
        cfg["EMAIL_USER"] or "",
        cfg["EMAIL_PASS"] or "",
    )

    print("\nРезультаты:")
    print(f" - {'✅' if imap_ok else '❌'} {imap_msg}")
    print(f" - {'✅' if smtp_ok else '❌'} {smtp_msg}")

    if imap_ok and smtp_ok:
        print("\n✅ Почтовая интеграция готова.")
        return 0

    print("\n⚠️ Найдены проблемы. Исправь конфигурацию/доступ и перезапусти проверку.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
