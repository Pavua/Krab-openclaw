# -*- coding: utf-8 -*-
"""
Общая утилита проверки доступности LM Studio (Фаза 2.3).

Используется в main.py (через model_manager), openclaw_client.py и userbot_bridge.py
для единообразной проверки LM Studio по GET /v1/models и разбора JSON.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import httpx

if TYPE_CHECKING:
    from httpx import AsyncClient


async def is_lm_studio_available(
    base_url: str,
    *,
    timeout: float = 30.0,
    client: Optional["AsyncClient"] = None,
) -> bool:
    """
    Проверяет доступность LM Studio по GET {base_url}/v1/models.

    Returns True при status_code == 200, иначе False (включая сетевые ошибки).
    """
    url = f"{base_url.rstrip('/')}/v1/models"
    if client is not None:
        try:
            resp = await client.get(url, timeout=timeout)
            return resp.status_code == 200
        except (httpx.HTTPError, OSError):
            return False
    async with httpx.AsyncClient(timeout=timeout) as ac:
        try:
            resp = await ac.get(url)
            return resp.status_code == 200
        except (httpx.HTTPError, OSError):
            return False


async def fetch_lm_studio_models_list(
    base_url: str,
    *,
    timeout: float = 30.0,
    client: Optional["AsyncClient"] = None,
) -> list[dict]:
    """
    Запрашивает GET {base_url}/v1/models и возвращает список моделей из JSON.

    Ответ LM Studio: {"data": [{"id": "...", "name": "...", ...}, ...]}.
    Возвращает data.get("data", []) при успехе, иначе [].
    """
    url = f"{base_url.rstrip('/')}/v1/models"
    if client is not None:
        try:
            resp = await client.get(url, timeout=timeout)
            if resp.status_code != 200:
                return []
            data = resp.json()
            return list(data.get("data", []))
        except (httpx.HTTPError, OSError, ValueError):
            return []
    async with httpx.AsyncClient(timeout=timeout) as ac:
        try:
            resp = await ac.get(url, timeout=timeout)
            if resp.status_code != 200:
                return []
            data = resp.json()
            return list(data.get("data", []))
        except (httpx.HTTPError, OSError, ValueError):
            return []
