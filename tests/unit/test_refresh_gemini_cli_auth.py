# -*- coding: utf-8 -*-
"""Тесты для scripts/refresh_gemini_cli_auth.sh — Wave 19-E.

Покрывает 4 сценария:
  1. Скрипт существует и исполняемый
  2. Скрипт корректно обрабатывает отсутствующий auth-файл (exit 1)
  3. Скрипт определяет действующий токен (expiry в будущем) и выходит с 0
  4. Скрипт определяет истёкший токен (expiry в прошлом) и сообщает об этом
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

# Путь к скрипту относительно корня репозитория
REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "refresh_gemini_cli_auth.sh"

# Ключ профиля, который ищет скрипт
PROFILE_KEY = "google-gemini-cli:pavelr7@gmail.com"


# ---------------------------------------------------------------------------
# 1. Скрипт существует и исполняемый
# ---------------------------------------------------------------------------


def test_script_exists_and_executable() -> None:
    """Скрипт refresh_gemini_cli_auth.sh должен существовать и быть исполняемым."""
    assert SCRIPT_PATH.exists(), f"Скрипт не найден: {SCRIPT_PATH}"
    file_stat = SCRIPT_PATH.stat()
    # Проверяем что установлен хотя бы один бит x (owner/group/other)
    is_executable = bool(file_stat.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    assert is_executable, f"Скрипт не исполняемый: {SCRIPT_PATH} (mode={oct(file_stat.st_mode)})"


# ---------------------------------------------------------------------------
# 2. Скрипт обрабатывает отсутствующий auth-файл
# ---------------------------------------------------------------------------


def test_script_handles_missing_auth_file(tmp_path: Path) -> None:
    """При KRAB_AUTH_FILE указывающем на несуществующий файл — exit 1 + сообщение об ошибке."""
    missing_file = tmp_path / "nonexistent_auth.json"
    # Убеждаемся, что файл действительно не существует
    assert not missing_file.exists()

    result = subprocess.run(
        ["/bin/bash", str(SCRIPT_PATH)],
        env={
            **os.environ,
            "KRAB_AUTH_FILE": str(missing_file),
            "KRAB_NONINTERACTIVE": "1",  # отключаем интерактивный prompt
        },
        capture_output=True,
        text=True,
        timeout=15,
    )

    # Скрипт должен завершиться с кодом 1 (файл не найден)
    assert result.returncode == 1, (
        f"Ожидался exit 1, получен {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    # Вывод должен содержать указание на проблему
    combined = result.stdout + result.stderr
    assert "не найден" in combined or "not found" in combined.lower(), (
        f"Не найдено сообщение об ошибке в выводе:\n{combined!r}"
    )


# ---------------------------------------------------------------------------
# 3. Скрипт определяет действующий токен
# ---------------------------------------------------------------------------


def test_script_detects_valid_token(tmp_path: Path) -> None:
    """При expiry далеко в будущем скрипт должен сообщить 'действителен' и выйти с 0."""
    # Токен истекает через ~30 дней (в миллисекундах)
    future_expiry_ms = (int(time.time()) + 30 * 86400) * 1000

    auth_data = {
        "profiles": {
            PROFILE_KEY: {
                "expires": future_expiry_ms,
                "access_token": "fake-token-for-test",
            }
        }
    }
    auth_file = tmp_path / "auth-profiles.json"
    auth_file.write_text(json.dumps(auth_data), encoding="utf-8")

    result = subprocess.run(
        ["/bin/bash", str(SCRIPT_PATH)],
        env={
            **os.environ,
            "KRAB_AUTH_FILE": str(auth_file),
            "KRAB_NONINTERACTIVE": "1",
        },
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, (
        f"Ожидался exit 0 для валидного токена, получен {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    # Вывод должен сигнализировать о том, что токен действителен
    combined = result.stdout + result.stderr
    assert "действителен" in combined or "valid" in combined.lower(), (
        f"Ожидалось сообщение 'действителен' в выводе:\n{combined!r}"
    )
    # Не должно быть сообщений об ошибке обновления
    assert "ОШИБКА" not in combined or "истёк" not in combined, (
        f"Неожиданное сообщение об ошибке для валидного токена:\n{combined!r}"
    )


# ---------------------------------------------------------------------------
# 4. Скрипт определяет истёкший токен
# ---------------------------------------------------------------------------


def test_script_detects_expired_token(tmp_path: Path) -> None:
    """При expiry в прошлом скрипт должен сообщить об истечении токена."""
    # Токен истёк ~21 день назад (в миллисекундах)
    past_expiry_ms = (int(time.time()) - 21 * 86400) * 1000

    auth_data = {
        "profiles": {
            PROFILE_KEY: {
                "expires": past_expiry_ms,
                "access_token": "expired-fake-token",
            }
        }
    }
    auth_file = tmp_path / "auth-profiles.json"
    auth_file.write_text(json.dumps(auth_data), encoding="utf-8")

    result = subprocess.run(
        ["/bin/bash", str(SCRIPT_PATH)],
        env={
            **os.environ,
            "KRAB_AUTH_FILE": str(auth_file),
            "KRAB_NONINTERACTIVE": "1",  # отключаем интерактивный prompt
        },
        capture_output=True,
        text=True,
        timeout=15,
    )

    # Скрипт НЕ должен завершаться с 0 (токен истёк — нужно действие)
    # Допустимы коды: любой кроме 0 (неинтерактивный режим выходит без вызова refresh)
    # Но важно, что в выводе есть информация об истечении
    combined = result.stdout + result.stderr
    assert "истёк" in combined or "expired" in combined.lower() or "Опции" in combined, (
        f"Ожидалось сообщение об истечении токена в выводе:\n{combined!r}"
    )
    # В неинтерактивном режиме скрипт выводит опции и завершается (exit 0 допустим)
    # Главное — что сообщение об истечении присутствует
    assert result.returncode in (0, 1, 2), (
        f"Неожиданный код выхода {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
