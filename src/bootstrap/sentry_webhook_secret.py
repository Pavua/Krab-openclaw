# -*- coding: utf-8 -*-
"""
Bootstrap SENTRY_WEBHOOK_SECRET при старте runtime.

Политика безопасности: endpoint `/api/hooks/sentry` обязан иметь HMAC-secret,
иначе любой знающий публичный URL может слать произвольные payload.

Если secret отсутствует в окружении и в `.env` — генерируем
`secrets.token_urlsafe(32)` и дописываем в `.env`, затем прокидываем
в `os.environ`, чтобы web_app при старте сразу увидел его.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

_ENV_KEY = "SENTRY_WEBHOOK_SECRET"


def _default_env_path() -> Path:
    # src/bootstrap/sentry_webhook_secret.py → repo root
    return Path(__file__).resolve().parents[2] / ".env"


def _read_env_secret(env_path: Path) -> str:
    """Ищет SENTRY_WEBHOOK_SECRET= в .env (если файл существует)."""
    if not env_path.exists():
        return ""
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith(f"{_ENV_KEY}="):
                value = stripped.split("=", 1)[1].strip()
                # Снимаем кавычки если есть
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                elif value.startswith("'") and value.endswith("'"):
                    value = value[1:-1]
                return value
    except OSError:
        return ""
    return ""


def ensure_sentry_webhook_secret(env_path: Path | None = None) -> str:
    """Гарантирует наличие SENTRY_WEBHOOK_SECRET в os.environ.

    Алгоритм:
    1. Если os.environ[_ENV_KEY] непустой — возвращаем его.
    2. Иначе читаем .env — если там есть непустое значение, прокидываем в env.
    3. Иначе генерируем token_urlsafe(32), дописываем в .env, ставим в env.

    Возвращает итоговый secret (никогда не пустая строка после вызова).
    """
    path = env_path or _default_env_path()

    current = os.getenv(_ENV_KEY, "").strip()
    if current:
        return current

    from_file = _read_env_secret(path).strip()
    if from_file:
        os.environ[_ENV_KEY] = from_file
        return from_file

    # Генерируем новый secret
    new_secret = secrets.token_urlsafe(32)
    try:
        # Если файла нет — создаём; иначе append
        existing = ""
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if existing and not existing.endswith("\n"):
                existing += "\n"
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
        block = (
            "\n# Sentry webhook HMAC secret (auto-generated — rotate via "
            "/api/hooks/sentry/secret/rotate)\n"
            f"{_ENV_KEY}={new_secret}\n"
        )
        path.write_text(existing + block, encoding="utf-8")
        os.environ[_ENV_KEY] = new_secret
        logger.info(
            "sentry_webhook_secret_generated",
            env_path=str(path),
            hint="Add this secret to Sentry Internal Integration webhook config",
            secret_preview=f"{new_secret[:6]}…{new_secret[-4:]}",
        )
    except OSError as exc:
        # Файл недоступен — всё равно ставим в os.environ, чтобы endpoint не упал,
        # но залогируем warning: при рестарте secret потеряется.
        os.environ[_ENV_KEY] = new_secret
        logger.warning(
            "sentry_webhook_secret_write_failed",
            env_path=str(path),
            error=str(exc),
            note="secret present in process env but NOT persisted to .env",
        )
    return new_secret


def rotate_sentry_webhook_secret(env_path: Path | None = None) -> str:
    """Генерирует новый secret, перезаписывает строку в .env, ставит в os.environ."""
    path = env_path or _default_env_path()
    new_secret = secrets.token_urlsafe(32)

    lines: list[str] = []
    if path.exists():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []

    replaced = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{_ENV_KEY}="):
            lines[i] = f"{_ENV_KEY}={new_secret}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{_ENV_KEY}={new_secret}")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        logger.warning("sentry_webhook_secret_rotate_write_failed", error=str(exc))

    os.environ[_ENV_KEY] = new_secret
    logger.info(
        "sentry_webhook_secret_rotated",
        secret_preview=f"{new_secret[:6]}…{new_secret[-4:]}",
    )
    return new_secret
