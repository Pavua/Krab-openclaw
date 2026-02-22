# -*- coding: utf-8 -*-
"""
Проверки кэша self-профиля для auto-reply.

Цель:
- исключить частые вызовы get_me() на каждый апдейт;
- гарантировать fallback на последний известный self-профиль при ошибках.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.handlers.ai import _SELF_IDENTITY_CACHE_KEY, _resolve_self_identity


@pytest.mark.asyncio
async def test_self_identity_cache_reuses_get_me_result():
    """Повторный вызов должен брать данные из TTL-кэша без нового get_me()."""
    client = AsyncMock()
    client.get_me.return_value = SimpleNamespace(username="KrabBot", id=321)
    deps = {}

    first = await _resolve_self_identity(client, deps)
    second = await _resolve_self_identity(client, deps)

    assert first["username"] == "krabbot"
    assert second["username"] == "krabbot"
    assert first["user_id"] == 321
    assert second["user_id"] == 321
    assert client.get_me.await_count == 1


@pytest.mark.asyncio
async def test_self_identity_cache_falls_back_to_previous_value_on_get_me_error():
    """Если get_me() упал, используется последний валидный self-профиль."""
    client = AsyncMock()
    client.get_me.side_effect = RuntimeError("network down")
    deps = {
        _SELF_IDENTITY_CACHE_KEY: {
            "username": "cached_bot",
            "user_id": 777,
            "fetched_at": 0.0,  # принудительно просрочено, чтобы пойти в refresh-ветку
        }
    }

    resolved = await _resolve_self_identity(client, deps)

    assert resolved["username"] == "cached_bot"
    assert resolved["user_id"] == 777
    assert client.get_me.await_count == 1

