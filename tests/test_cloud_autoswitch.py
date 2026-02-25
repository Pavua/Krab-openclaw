# -*- coding: utf-8 -*-
"""
unit-тесты safe autoswitch free→paid (Sprint R23).

Проверяет:
- Autoswitch при quota_or_billing и 429.
- Cooldown: повторный switch не выполняется в период cooldown.
- Sticky paid: после switch флаг sticky_paid устанавливается при _sticky_on_paid=True.
- Concurrent: asyncio.Lock защищает от race condition.
- Без paid ключа: autoswitch возвращает False без ошибок.

Связанные файлы: src/core/openclaw_client.py, tests/test_cloud_tier_state.py
"""

import asyncio
import os
import time

import pytest

from src.core.openclaw_client import OpenClawClient


# ─── Фикстура ─────────────────────────────────────────────────────────────────

def _make_client_with_paid(
    paid_key: str = "paid-ABCDEF",
    cooldown_sec: int = 60,
    sticky: bool = True,
) -> OpenClawClient:
    """Создаёт клиент с free + paid ключами и настраиваемым cooldown."""
    saved = {}
    env = {
        "GEMINI_API_KEY_FREE": "free-ABCDEF",
        "GEMINI_API_KEY_PAID": paid_key,
        "CLOUD_TIER_AUTOSWITCH_COOLDOWN_SEC": str(cooldown_sec),
        "CLOUD_TIER_STICKY_ON_PAID": "1" if sticky else "0",
    }
    try:
        for k, v in env.items():
            saved[k] = os.environ.get(k)
            os.environ[k] = v

        client = OpenClawClient(
            base_url="http://localhost:18789",
            api_key="oc-key",
        )
    finally:
        for k, old_v in saved.items():
            if old_v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old_v

    return client


# ─── Тесты ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_autoswitch_success_when_paid_key_available():
    """try_autoswitch_to_paid() переключает на paid если ключ есть."""
    client = _make_client_with_paid()
    # Принудительно сбрасываем cooldown: last_switch_at в прошлое.
    client._tier_state.last_switch_at = 0.0

    result = await client.try_autoswitch_to_paid(reason="quota_or_billing")

    assert result is True
    assert client._tier_state.active_tier == "paid"
    assert client.active_tier == "paid"
    assert client._tier_state.switch_count == 1
    assert client._tier_state.switch_reason == "quota_or_billing"
    assert client._metrics["tier_switch_total"] == 1


@pytest.mark.asyncio
async def test_autoswitch_skipped_when_no_paid_key():
    """try_autoswitch_to_paid() возвращает False если нет paid ключа."""
    saved = os.environ.get("GEMINI_API_KEY_PAID")
    try:
        os.environ.pop("GEMINI_API_KEY_PAID", None)
        client = OpenClawClient(base_url="http://localhost:18789", api_key="oc-key")
        # Принудительно убираем ключ из gemini_tiers
        client.gemini_tiers.pop("paid", None)
    finally:
        if saved is not None:
            os.environ["GEMINI_API_KEY_PAID"] = saved

    client._tier_state.last_switch_at = 0.0
    result = await client.try_autoswitch_to_paid(reason="quota_or_billing")

    assert result is False
    assert client._tier_state.active_tier != "paid"
    assert client._metrics["tier_switch_total"] == 0


@pytest.mark.asyncio
async def test_autoswitch_idempotent_when_already_paid():
    """try_autoswitch_to_paid() возвращает True если tier уже paid, без switch."""
    client = _make_client_with_paid()
    client._tier_state.active_tier = "paid"
    client._tier_state.switch_count = 0

    result = await client.try_autoswitch_to_paid(reason="quota_or_billing")

    assert result is True
    # switch_count не изменился (early return)
    assert client._tier_state.switch_count == 0


@pytest.mark.asyncio
async def test_autoswitch_cooldown_blocks_repeated_switch():
    """Второй autoswitch в период cooldown НЕ выполняется."""
    client = _make_client_with_paid(cooldown_sec=3600)
    client._tier_state.last_switch_at = 0.0

    # Первый switch — успешен
    r1 = await client.try_autoswitch_to_paid(reason="quota_or_billing")
    assert r1 is True
    assert client._metrics["tier_switch_total"] == 1

    # Сбрасываем tier чтобы проверить cooldown (имитируем сброс обратно на free)
    client._tier_state.active_tier = "free"
    client.active_tier = "free"
    # last_switch_at уже установлен = time.time() при первом switch

    # Второй switch в cooldown — должен вернуть False
    r2 = await client.try_autoswitch_to_paid(reason="quota_or_billing")
    assert r2 is False
    # tier остался free (cooldown сработал)
    assert client._tier_state.active_tier == "free"
    # switch_count не изменился
    assert client._metrics["tier_switch_total"] == 1


@pytest.mark.asyncio
async def test_autoswitch_sticky_paid_flag_set():
    """При sticky_on_paid=True, после autoswitch sticky_paid=True."""
    client = _make_client_with_paid(sticky=True)
    client._tier_state.last_switch_at = 0.0

    await client.try_autoswitch_to_paid(reason="quota_or_billing")

    assert client._tier_state.sticky_paid is True


@pytest.mark.asyncio
async def test_autoswitch_no_sticky_when_disabled():
    """При sticky_on_paid=False, после autoswitch sticky_paid=False."""
    client = _make_client_with_paid(sticky=False)
    client._tier_state.last_switch_at = 0.0

    await client.try_autoswitch_to_paid(reason="quota_or_billing")

    assert client._tier_state.sticky_paid is False


@pytest.mark.asyncio
async def test_autoswitch_concurrent_no_double_switch():
    """Параллельные вызовы try_autoswitch_to_paid() не создают двойных переключений."""
    client = _make_client_with_paid()
    client._tier_state.last_switch_at = 0.0

    # Запускаем 10 параллельных автосвитчей
    results = await asyncio.gather(*[
        client.try_autoswitch_to_paid(reason="quota_or_billing")
        for _ in range(10)
    ])

    # Хотя бы один должен вернуть True
    assert any(results)
    # switch_count == 1 (единственный реальный switch)
    assert client._metrics["tier_switch_total"] == 1, \
        f"Ожидали 1 switch, получили {client._metrics['tier_switch_total']}"


@pytest.mark.asyncio
async def test_reset_cloud_tier_returns_to_free():
    """reset_cloud_tier() возвращает tier на free и снимает sticky."""
    client = _make_client_with_paid()
    # Переключаем на paid
    client._tier_state.last_switch_at = 0.0
    await client.try_autoswitch_to_paid(reason="test")
    assert client._tier_state.active_tier == "paid"

    # Выполняем reset
    result = await client.reset_cloud_tier()

    assert result["ok"] is True
    assert result["previous_tier"] == "paid"
    assert result["new_tier"] == "free"
    assert client._tier_state.active_tier == "free"
    assert client._tier_state.sticky_paid is False
    assert client._tier_state.switch_reason == "manual_reset"
    assert client._metrics["tier_switch_total"] == 2  # switch + reset


@pytest.mark.asyncio
async def test_classify_hint_triggers_autoswitch_on_quota():
    """_classify_provider_probe_hint() правильно помечает quota/429 как triggers_autoswitch."""
    client = _make_client_with_paid()

    quota_hints = [
        "quota exceeded",
        "resource_exhausted",
        "resource exhausted",
        "billing error",
        "out of credits",
        "status 429",
    ]
    for hint in quota_hints:
        classified = client._classify_provider_probe_hint(hint)
        assert classified.get("triggers_autoswitch") is True, \
            f"Hint '{hint}' должен trigger autoswitch, code={classified.get('code')}"


@pytest.mark.asyncio
async def test_classify_hint_no_autoswitch_on_auth_errors():
    """Auth-ошибки (leaked, invalid) НЕ триггерят autoswitch."""
    client = _make_client_with_paid()

    no_switch_hints = [
        "reported as leaked",
        "invalid api key",
        "unauthorized",
        "403 permission denied",
    ]
    for hint in no_switch_hints:
        classified = client._classify_provider_probe_hint(hint)
        assert classified.get("triggers_autoswitch") is False, \
            f"Hint '{hint}' НЕ должен trigger autoswitch, code={classified.get('code')}"
