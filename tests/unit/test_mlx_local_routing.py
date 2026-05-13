# -*- coding: utf-8 -*-
"""
Wave 223: тесты opt-in routing'а long-context задач на локальный MLX :8088.

Покрытие:
- env disabled (default) → всегда cloud
- threshold trigger → mlx-local-kv4 + counter reason=long_context
- task_type trigger → mlx-local-kv4 + counter reason=task_type
- env enabled но условия не сработали → cloud + counter reason=fallback
- both conditions (threshold имеет приоритет)
- override MLX_LOCAL_KV4_URL читается корректно
- threshold parse robustness (нечисловой prompt_tokens)
- custom KRAB_MLX_LOCAL_TASK_TYPES whitelist
"""

from __future__ import annotations

import pytest

from src.core import long_context_router as lcr
from src.core.metrics import long_context_routing as lcr_metrics


@pytest.fixture(autouse=True)
def _clean_env_and_counter(monkeypatch):
    """Сбрасываем env + in-memory counter перед каждым тестом."""
    for key in (
        "KRAB_LONG_CONTEXT_PROVIDER",
        "MLX_LOCAL_KV4_URL",
        "KRAB_LONG_CONTEXT_THRESHOLD_TOKENS",
        "KRAB_MLX_LOCAL_TASK_TYPES",
    ):
        monkeypatch.delenv(key, raising=False)
    lcr_metrics._MLX_LOCAL_ROUTING_COUNTER.clear()
    yield
    lcr_metrics._MLX_LOCAL_ROUTING_COUNTER.clear()


def test_env_disabled_default_returns_cloud():
    """Default — env vars не выставлены → cloud, неважно какие параметры."""
    assert lcr.select_provider_for_task("summarization", 99999) == "cloud"
    assert lcr.select_provider_for_task("rag_retrieval", 50000) == "cloud"
    # И метрика инкрементнулась с reason=fallback
    assert lcr_metrics._MLX_LOCAL_ROUTING_COUNTER.get("fallback") == 2


def test_threshold_trigger_routes_to_mlx_local(monkeypatch):
    """prompt_tokens > threshold → local; reason=long_context."""
    monkeypatch.setenv("KRAB_LONG_CONTEXT_PROVIDER", "mlx-local-kv4")
    # threshold default 8000
    result = lcr.select_provider_for_task("chat", 9000)
    assert result == "mlx-local-kv4"
    assert lcr_metrics._MLX_LOCAL_ROUTING_COUNTER.get("long_context") == 1


def test_task_type_trigger_routes_to_mlx_local(monkeypatch):
    """task_type в whitelist → local; reason=task_type."""
    monkeypatch.setenv("KRAB_LONG_CONTEXT_PROVIDER", "mlx-local-kv4")
    result = lcr.select_provider_for_task("summarization", 100)
    assert result == "mlx-local-kv4"
    assert lcr_metrics._MLX_LOCAL_ROUTING_COUNTER.get("task_type") == 1


def test_env_enabled_but_no_match_returns_cloud(monkeypatch):
    """Env активен но ни threshold ни task_type — возвращаем cloud, reason=fallback."""
    monkeypatch.setenv("KRAB_LONG_CONTEXT_PROVIDER", "mlx-local-kv4")
    result = lcr.select_provider_for_task("chat", 100)
    assert result == "cloud"
    assert lcr_metrics._MLX_LOCAL_ROUTING_COUNTER.get("fallback") == 1


def test_both_conditions_threshold_wins(monkeypatch):
    """Если оба условия true — threshold идёт первым, reason=long_context."""
    monkeypatch.setenv("KRAB_LONG_CONTEXT_PROVIDER", "mlx-local-kv4")
    result = lcr.select_provider_for_task("summarization", 99999)
    assert result == "mlx-local-kv4"
    # Только long_context, не task_type
    assert lcr_metrics._MLX_LOCAL_ROUTING_COUNTER.get("long_context") == 1
    assert lcr_metrics._MLX_LOCAL_ROUTING_COUNTER.get("task_type") is None


def test_custom_task_types_whitelist(monkeypatch):
    """KRAB_MLX_LOCAL_TASK_TYPES override фильтрует whitelist."""
    monkeypatch.setenv("KRAB_LONG_CONTEXT_PROVIDER", "mlx-local-kv4")
    monkeypatch.setenv("KRAB_MLX_LOCAL_TASK_TYPES", "embedding,classify")
    # summarization больше не в whitelist
    assert lcr.select_provider_for_task("summarization", 100) == "cloud"
    # А embedding — теперь да
    assert lcr.select_provider_for_task("embedding", 100) == "mlx-local-kv4"


def test_mlx_local_url_override(monkeypatch):
    """MLX_LOCAL_KV4_URL переопределяет endpoint."""
    assert lcr.get_mlx_local_url() == "http://127.0.0.1:8088"
    monkeypatch.setenv("MLX_LOCAL_KV4_URL", "http://10.0.0.5:9999")
    assert lcr.get_mlx_local_url() == "http://10.0.0.5:9999"


def test_metric_increments_across_calls(monkeypatch):
    """Многократные вызовы корректно копят счётчик по reason-меткам."""
    monkeypatch.setenv("KRAB_LONG_CONTEXT_PROVIDER", "mlx-local-kv4")
    lcr.select_provider_for_task("summarization", 100)  # task_type
    lcr.select_provider_for_task("rag_retrieval", 200)  # task_type
    lcr.select_provider_for_task("chat", 99999)  # long_context
    lcr.select_provider_for_task("chat", 100)  # fallback
    assert lcr_metrics._MLX_LOCAL_ROUTING_COUNTER.get("task_type") == 2
    assert lcr_metrics._MLX_LOCAL_ROUTING_COUNTER.get("long_context") == 1
    assert lcr_metrics._MLX_LOCAL_ROUTING_COUNTER.get("fallback") == 1


def test_threshold_robust_to_bad_tokens(monkeypatch):
    """Нечисловой prompt_tokens не должен ронять роутер."""
    monkeypatch.setenv("KRAB_LONG_CONTEXT_PROVIDER", "mlx-local-kv4")
    # bad tokens → trakts как 0 → не triggers long_context, не в whitelist → cloud
    result = lcr.select_provider_for_task("chat", "not-a-number")  # type: ignore[arg-type]
    assert result == "cloud"


def test_custom_threshold_value(monkeypatch):
    """KRAB_LONG_CONTEXT_THRESHOLD_TOKENS меняет порог."""
    monkeypatch.setenv("KRAB_LONG_CONTEXT_PROVIDER", "mlx-local-kv4")
    monkeypatch.setenv("KRAB_LONG_CONTEXT_THRESHOLD_TOKENS", "1000")
    # 1500 > 1000 → local
    assert lcr.select_provider_for_task("chat", 1500) == "mlx-local-kv4"
    # 500 < 1000 → cloud
    assert lcr.select_provider_for_task("chat", 500) == "cloud"
