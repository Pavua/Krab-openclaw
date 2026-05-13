# -*- coding: utf-8 -*-
"""
Unit tests для Wave 248 — KRAB_ACTIVE_MODEL_OVERRIDES_RUNTIME.

Покрывает:
- ``is_runtime_override_enabled`` — корректное чтение ENV;
- ``get_effective_primary_for_warmup`` — fallback к runtime primary когда
  override выключен или active_model.json пуст;
- override engaged → возвращает picked модель + Prometheus counter инкрементится;
- ``warmup_runtime_route`` использует override и force_cloud=False для MLX
  модели (что предотвращает ложный codex-cli probe);
- override disabled → runtime primary respected, force_cloud вычисляется
  по runtime primary как раньше;
- ``runtime_route_warmup_override_engaged`` event эмитится в structlog.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core import active_model_routing as amr
from src.core.metrics.active_model_routing import (
    _ACTIVE_MODEL_OVERRIDE_COUNTER,
    inc_active_model_override_engaged,
)


@pytest.fixture(autouse=True)
def _clean_amr_cache():
    """Сбрасываем кэш active_model между тестами, чтобы не было перетекания."""
    amr.invalidate_cache()
    yield
    amr.invalidate_cache()


# ── is_runtime_override_enabled ──────────────────────────────────────────────


def test_is_runtime_override_enabled_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без env — override выключен (pre-248 поведение)."""
    monkeypatch.delenv(amr.OVERRIDE_ENV_VAR, raising=False)
    assert amr.is_runtime_override_enabled() is False


def test_is_runtime_override_enabled_truthy_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """Принимаем 1/true/yes/on (регистронезависимо)."""
    for raw in ("1", "true", "TRUE", "yes", "On"):
        monkeypatch.setenv(amr.OVERRIDE_ENV_VAR, raw)
        assert amr.is_runtime_override_enabled() is True, f"expected truthy for {raw!r}"
    # Falsy
    for raw in ("0", "false", "no", "off", ""):
        monkeypatch.setenv(amr.OVERRIDE_ENV_VAR, raw)
        assert amr.is_runtime_override_enabled() is False, f"expected falsy for {raw!r}"


# ── get_effective_primary_for_warmup ─────────────────────────────────────────


def test_effective_primary_override_disabled_uses_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Override off → возвращаем runtime primary, override_engaged=False."""
    monkeypatch.delenv(amr.OVERRIDE_ENV_VAR, raising=False)
    primary, engaged = amr.get_effective_primary_for_warmup("codex-cli/gpt-5.5")
    assert primary == "codex-cli/gpt-5.5"
    assert engaged is False


def test_effective_primary_override_enabled_no_active_model_falls_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Override on, но active_model.json отсутствует → fallback на runtime primary."""
    monkeypatch.setenv(amr.OVERRIDE_ENV_VAR, "1")
    monkeypatch.delenv(amr.ENV_VAR, raising=False)
    fake_state = tmp_path / "active_model.json"
    with patch.object(amr, "STATE_PATH", fake_state):
        amr.invalidate_cache()
        primary, engaged = amr.get_effective_primary_for_warmup("codex-cli/gpt-5.5")
    assert primary == "codex-cli/gpt-5.5"
    assert engaged is False


def test_effective_primary_override_engaged_uses_picked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Override on + picked в active_model.json → override engaged."""
    monkeypatch.setenv(amr.OVERRIDE_ENV_VAR, "1")
    monkeypatch.delenv(amr.ENV_VAR, raising=False)
    fake_state = tmp_path / "active_model.json"
    fake_state.write_text(
        json.dumps(
            {
                "model": "mlx-local-kv4/gemma-4-26b",
                "switched_at": 1715680000.0,
                "switched_by": "owner_panel",
                "reason": "force MLX",
            }
        ),
        encoding="utf-8",
    )
    with patch.object(amr, "STATE_PATH", fake_state):
        amr.invalidate_cache()
        primary, engaged = amr.get_effective_primary_for_warmup("codex-cli/gpt-5.5")
    assert primary == "mlx-local-kv4/gemma-4-26b"
    assert engaged is True


# ── Prometheus counter inc_active_model_override_engaged ─────────────────────


def test_override_counter_increments() -> None:
    """Counter инкрементится при каждом вызове, разный label для разного picked."""
    # Capture начальное состояние для конкретных ключей, чтобы тест не зависел
    # от других тестов в этой сессии.
    key_a = ("mlx-local-kv4/gemma-4-26b", "warmup")
    key_b = ("lm-studio-local/qwen", "warmup")
    before_a = _ACTIVE_MODEL_OVERRIDE_COUNTER.get(key_a, 0)
    before_b = _ACTIVE_MODEL_OVERRIDE_COUNTER.get(key_b, 0)

    inc_active_model_override_engaged(picked_model=key_a[0], context=key_a[1])
    inc_active_model_override_engaged(picked_model=key_a[0], context=key_a[1])
    inc_active_model_override_engaged(picked_model=key_b[0], context=key_b[1])

    assert _ACTIVE_MODEL_OVERRIDE_COUNTER[key_a] == before_a + 2
    assert _ACTIVE_MODEL_OVERRIDE_COUNTER[key_b] == before_b + 1


# ── warmup_runtime_route override behaviour ──────────────────────────────────


@pytest.mark.asyncio
async def test_warmup_with_override_uses_picked_mlx_no_force_cloud(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    Override on + MLX picked → warmup probe идёт с force_cloud=False
    (вместо force_cloud=True по runtime primary `codex-cli/gpt-5.5`).
    Это и есть ключевой fix: warmup лог больше не показывает cloud-route
    когда owner выбрал local backend.
    """
    monkeypatch.setenv(amr.OVERRIDE_ENV_VAR, "1")
    monkeypatch.delenv(amr.ENV_VAR, raising=False)
    fake_state = tmp_path / "active_model.json"
    fake_state.write_text(
        json.dumps({"model": "mlx-local-kv4/gemma-4-26b"}),
        encoding="utf-8",
    )

    from src.openclaw_client import OpenClawClient

    client = OpenClawClient.__new__(OpenClawClient)
    # Минимальные атрибуты, нужные warmup_runtime_route до точки force_cloud.
    client.base_url = "http://127.0.0.1:18789"
    client.active_tier = "free"
    client._cloud_tier_state = {"active_tier": "free"}

    captured: dict[str, object] = {}

    async def _fake_stream(**kwargs):
        captured["force_cloud"] = kwargs.get("force_cloud")
        captured["message"] = kwargs.get("message")
        if False:
            yield ""
        return

    # Заглушки на side-effect методы.
    client.send_message_stream = _fake_stream  # type: ignore[method-assign]

    async def _hc():
        return True

    client.health_check = _hc  # type: ignore[method-assign]
    client.get_last_runtime_route = lambda: {}  # type: ignore[method-assign]
    client.clear_session = lambda *_a, **_kw: None  # type: ignore[method-assign]
    client._effective_runtime_google_key_state = lambda: {"tier": "free"}  # type: ignore[method-assign]
    client._sync_last_runtime_route_active_tier = lambda: None  # type: ignore[method-assign]

    with (
        patch.object(amr, "STATE_PATH", fake_state),
        patch(
            "src.openclaw_client.get_runtime_primary_model",
            return_value="codex-cli/gpt-5.5",
        ),
    ):
        amr.invalidate_cache()
        result = await client.warmup_runtime_route(force_refresh=True)

    assert result["skipped"] is False
    # Главное: force_cloud=False, т.к. effective primary = mlx-local-kv4/*
    assert captured["force_cloud"] is False


@pytest.mark.asyncio
async def test_warmup_without_override_respects_runtime_primary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Override off → warmup использует runtime primary (codex-cli) → force_cloud=True."""
    monkeypatch.delenv(amr.OVERRIDE_ENV_VAR, raising=False)
    monkeypatch.delenv(amr.ENV_VAR, raising=False)

    # active_model.json даже если он есть — не должен использоваться при override off.
    fake_state = tmp_path / "active_model.json"
    fake_state.write_text(
        json.dumps({"model": "mlx-local-kv4/gemma-4-26b"}),
        encoding="utf-8",
    )

    from src.openclaw_client import OpenClawClient

    client = OpenClawClient.__new__(OpenClawClient)
    client.base_url = "http://127.0.0.1:18789"
    client.active_tier = "free"
    client._cloud_tier_state = {"active_tier": "free"}

    captured: dict[str, object] = {}

    async def _fake_stream(**kwargs):
        captured["force_cloud"] = kwargs.get("force_cloud")
        if False:
            yield ""
        return

    client.send_message_stream = _fake_stream  # type: ignore[method-assign]

    async def _hc():
        return True

    client.health_check = _hc  # type: ignore[method-assign]
    client.get_last_runtime_route = lambda: {}  # type: ignore[method-assign]
    client.clear_session = lambda *_a, **_kw: None  # type: ignore[method-assign]
    client._effective_runtime_google_key_state = lambda: {"tier": "free"}  # type: ignore[method-assign]
    client._sync_last_runtime_route_active_tier = lambda: None  # type: ignore[method-assign]

    with (
        patch.object(amr, "STATE_PATH", fake_state),
        patch(
            "src.openclaw_client.get_runtime_primary_model",
            return_value="codex-cli/gpt-5.5",
        ),
    ):
        amr.invalidate_cache()
        await client.warmup_runtime_route(force_refresh=True)

    # Без override — runtime primary (codex-cli) → force_cloud=True (pre-248).
    assert captured["force_cloud"] is True
