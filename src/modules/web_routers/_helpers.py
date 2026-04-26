# -*- coding: utf-8 -*-
"""
Promoted helpers для router модулей — Phase 2 foundation (Session 25).

Module-level functions, не зависящие от WebApp instance. Используются
RouterContext через delegating-методы и могут быть импортированы напрямую
из routers.

Обе функции совместимы с existing call sites в ``WebApp`` и читают
конфигурацию из env (``WEB_API_KEY``, ``WEB_PUBLIC_BASE_URL``, ``WEB_HOST``).

См. ``docs/CODE_SPLITS_PLAN.md`` § "Phase 2 advanced" → RouterContext infra.
"""

from __future__ import annotations

import os

from fastapi import HTTPException


def get_web_api_key() -> str:
    """Возвращает текущее значение ``WEB_API_KEY`` (может быть пустым)."""
    return os.getenv("WEB_API_KEY", "").strip()


def get_public_base_url(default_port: int = 8080) -> str:
    """Возвращает внешний base URL панели.

    Приоритет:
    1. ``WEB_PUBLIC_BASE_URL`` — explicit override (без trailing slash).
    2. ``http://{WEB_HOST или 127.0.0.1}:{default_port}``.
    """
    explicit = os.getenv("WEB_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    display_host = os.getenv("WEB_HOST", "127.0.0.1").strip() or "127.0.0.1"
    return f"http://{display_host}:{default_port}"


def assert_write_access(header_key: str, token: str) -> None:
    """Проверяет доступ к write-эндпоинтам web API.

    Если ``WEB_API_KEY`` не установлен — открытый доступ (no-op).
    Иначе сверяет либо header (``X-Krab-Web-Key``), либо query param ``token``
    с expected value. Несовпадение → ``HTTPException(403)``.
    """
    expected = get_web_api_key()
    if not expected:
        return

    provided = (header_key or "").strip() or (token or "").strip()
    if provided != expected:
        raise HTTPException(status_code=403, detail="forbidden: invalid WEB_API_KEY")
