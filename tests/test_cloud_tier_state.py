# -*- coding: utf-8 -*-
"""
unit-тесты Cloud Tier State (Sprint R23).

Проверяет:
- Начальная инициализация CloudTierState.
- Экспорт get_tier_state_export() не содержит секретов.
- Корректные available_tiers при разных конфигурациях ключей.
"""

import time

import pytest

from src.core.openclaw_client import CloudTierState, OpenClawClient


# ─── Фикстура: клиент только с free tier ──────────────────────────────────────

def _make_client(free_key: str = "free-key-abc", paid_key: str = "") -> OpenClawClient:
    """Создаёт OpenClawClient с заданными ключами через env-подмену."""
    env = {
        "OPENCLAW_BASE_URL": "http://localhost:18789",
    }
    if free_key:
        env["GEMINI_API_KEY_FREE"] = free_key
    if paid_key:
        env["GEMINI_API_KEY_PAID"] = paid_key

    import os
    old = {k: os.environ.get(k) for k in env}
    try:
        for k, v in env.items():
            os.environ[k] = v
        client = OpenClawClient(
            base_url="http://localhost:18789",
            api_key="test-oc-key",
        )
    finally:
        for k, old_v in old.items():
            if old_v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old_v

    return client


# ─── Тесты ────────────────────────────────────────────────────────────────────

def test_cloud_tier_state_dataclass_defaults():
    """CloudTierState создаётся с разумными дефолтами."""
    s = CloudTierState()
    assert s.active_tier == "free"
    assert s.switch_count == 0
    assert s.sticky_paid is False
    assert s.switch_reason == "init"
    assert isinstance(s.last_switch_at, float)


def test_initial_tier_state_free_when_free_key():
    """При GEMINI_API_KEY_FREE клиент инициализируется в free tier."""
    client = _make_client(free_key="free-key-abc", paid_key="")
    assert client._tier_state.active_tier == "free"
    assert client.active_tier == "free"


def test_initial_tier_state_paid_when_only_paid():
    """При только GEMINI_API_KEY_PAID tier = paid."""
    client = _make_client(free_key="", paid_key="paid-key-xyz")
    assert client._tier_state.active_tier == "paid"


def test_initial_tier_state_default_when_no_keys():
    """Без ключей tier = default."""
    client = _make_client(free_key="", paid_key="")
    assert client._tier_state.active_tier == "default"


def test_get_tier_state_export_no_secrets():
    """get_tier_state_export() не содержит API ключей."""
    client = _make_client(free_key="free-key-super-secret", paid_key="paid-key-super-secret")
    export = client.get_tier_state_export()

    # Проверяем структуру
    assert "active_tier" in export
    assert "metrics" in export
    assert "available_tiers" in export
    assert "autoswitch_cooldown_sec" in export

    # Ключи не должны утечь
    export_str = str(export)
    assert "super-secret" not in export_str
    assert "free-key" not in export_str
    assert "paid-key" not in export_str


def test_get_tier_state_export_metrics_structure():
    """Метрики имеют правильную структуру с нулевыми значениями при старте."""
    client = _make_client()
    export = client.get_tier_state_export()
    metrics = export["metrics"]

    expected_keys = {
        "cloud_attempts_total",
        "cloud_failures_total",
        "tier_switch_total",
        "force_cloud_failfast_total",
    }
    assert expected_keys.issubset(set(metrics.keys())), \
        f"Ожидаемые метрики отсутствуют. Есть: {set(metrics.keys())}"
    for key in expected_keys:
        assert metrics[key] == 0, f"Метрика {key} должна быть 0 при старте"


def test_available_tiers_reflect_configured_keys():
    """available_tiers содержит только те tiers, ключи которых настроены."""
    client = _make_client(free_key="free-k", paid_key="paid-k")
    export = client.get_tier_state_export()
    tiers = export["available_tiers"]
    assert "free" in tiers
    assert "paid" in tiers


def test_tier_state_switch_count_initial_zero():
    """switch_count = 0 при инициализации (init не считается как switch)."""
    client = _make_client()
    assert client._tier_state.switch_count == 0


def test_tier_switch_lock_created_lazily():
    """_tier_switch_lock создаётся только при вызове _get_tier_lock()."""
    client = _make_client()
    assert client._tier_switch_lock is None
    lock = client._get_tier_lock()
    assert lock is not None
    # Повторный вызов возвращает тот же объект
    assert client._get_tier_lock() is lock
