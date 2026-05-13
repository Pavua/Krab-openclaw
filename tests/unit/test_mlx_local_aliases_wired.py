# -*- coding: utf-8 -*-
"""Wave 225: проверка wiring alias-резолвера в openclaw_client.

Wave 222 завёл standalone module `src.core.mlx_local_aliases`, но resolver
не был подключён к фактическому send-pipeline. Wave 225 (`Wave 225: wire
mlx_local_aliases resolver в openclaw_client`) добавил вызов
`_resolve_mlx_local_model_in_payload` рядом с `_apply_mlx_disable_thinking`
в обоих send-сайтах (gateway + direct LM-Studio fallback) и метрику
`krab_mlx_local_alias_resolved_total{result}`.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.core.metrics.mlx_local_aliases import (
    _MLX_LOCAL_ALIAS_COUNTER,
    inc_mlx_local_alias_resolved,
)
from src.core.mlx_local_aliases import _DEFAULT_ALIASES  # type: ignore[attr-defined]
from src.openclaw_client import _resolve_mlx_local_model_in_payload


@pytest.fixture(autouse=True)
def _reset_alias_counter() -> None:
    """Каждый тест работает с чистым in-memory счётчиком."""
    _MLX_LOCAL_ALIAS_COUNTER.clear()


def _pick_known_alias() -> tuple[str, str]:
    short, full = next(iter(_DEFAULT_ALIASES.items()))
    return short, full


def test_resolver_hit_replaces_model_id_for_mlx_local_url() -> None:
    """Backend MLX local + известный alias → payload переписан, hit++."""
    short, full = _pick_known_alias()
    payload: dict[str, Any] = {"model": short, "messages": []}

    _resolve_mlx_local_model_in_payload(payload, base_url="http://127.0.0.1:8088")

    assert payload["model"] == full
    assert _MLX_LOCAL_ALIAS_COUNTER.get("hit") == 1
    assert _MLX_LOCAL_ALIAS_COUNTER.get("miss", 0) == 0


def test_resolver_miss_when_short_id_unknown_on_mlx_local_url() -> None:
    """Backend MLX local + неизвестный short_id → payload не меняем, miss++."""
    payload: dict[str, Any] = {"model": "mlx-local-kv4/totally-unknown", "messages": []}

    _resolve_mlx_local_model_in_payload(payload, base_url="http://127.0.0.1:8088")

    assert payload["model"] == "mlx-local-kv4/totally-unknown"
    assert _MLX_LOCAL_ALIAS_COUNTER.get("miss") == 1
    assert _MLX_LOCAL_ALIAS_COUNTER.get("hit", 0) == 0


def test_resolver_passthrough_for_cloud_url() -> None:
    """Backend — облако (gateway :18789) → passthrough++, payload нетронут."""
    payload: dict[str, Any] = {"model": "openclaw", "messages": []}

    _resolve_mlx_local_model_in_payload(payload, base_url="http://127.0.0.1:18789")

    assert payload["model"] == "openclaw"
    assert _MLX_LOCAL_ALIAS_COUNTER.get("passthrough") == 1
    assert _MLX_LOCAL_ALIAS_COUNTER.get("hit", 0) == 0


def test_resolver_noop_when_payload_model_missing() -> None:
    """Нет поля `model` → ничего не делаем, метрика не растёт."""
    payload: dict[str, Any] = {"messages": []}

    _resolve_mlx_local_model_in_payload(payload, base_url="http://127.0.0.1:8088")

    assert "model" not in payload
    assert _MLX_LOCAL_ALIAS_COUNTER == {}


def test_metric_helper_records_all_results() -> None:
    """`inc_mlx_local_alias_resolved` корректно различает hit/miss/passthrough."""
    inc_mlx_local_alias_resolved(result="hit")
    inc_mlx_local_alias_resolved(result="miss")
    inc_mlx_local_alias_resolved(result="passthrough")
    inc_mlx_local_alias_resolved(result="hit")

    assert _MLX_LOCAL_ALIAS_COUNTER == {"hit": 2, "miss": 1, "passthrough": 1}


def test_resolver_does_not_break_wave221_thinking_payload() -> None:
    """Wave 221 patch уже выставил chat_template_args — resolver его не трогает."""
    short, full = _pick_known_alias()
    payload: dict[str, Any] = {
        "model": short,
        "messages": [],
        "chat_template_args": {"enable_thinking": False},
    }

    _resolve_mlx_local_model_in_payload(payload, base_url="http://127.0.0.1:8088")

    assert payload["model"] == full
    assert payload["chat_template_args"] == {"enable_thinking": False}
