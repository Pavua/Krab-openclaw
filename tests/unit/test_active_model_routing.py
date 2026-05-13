# -*- coding: utf-8 -*-
"""Wave 230: тесты реального routing'а Краба на backend, выбранный в /admin/models.

Покрытие
--------
1. mlx-local-kv4/* → resolve_active_target возвращает MLX :8088
2. cloud (google/...) → resolve остаётся на gateway, model="openclaw"
3. openclaw/main → resolve остаётся на gateway, model="openclaw"
4. ENV KRAB_PRIMARY_MODEL_ID перекрывает файл
5. set_active_model пишет JSON атомарно и сбрасывает кэш
6. invalidate_cache → следующий read пере-загружает файл
7. inc_active_model_switch увеличивает счётчик
8. is_mlx_local_model / is_openclaw_model edge-cases
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core import active_model_routing as amr
from src.core.metrics import active_model_routing as amr_metrics


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Изолируем JSON-state в tmp_path, чтобы тесты не трогали ~/.openclaw/."""
    monkeypatch.setattr(amr, "STATE_PATH", tmp_path / "active_model.json")
    amr.invalidate_cache()
    # ENV не задан по умолчанию — каждый тест явно ставит свой.
    monkeypatch.delenv(amr.ENV_VAR, raising=False)
    monkeypatch.delenv("MLX_LOCAL_KV4_URL", raising=False)
    monkeypatch.delenv("OPENCLAW_URL", raising=False)
    yield
    amr.invalidate_cache()


# ── 1. mlx-local-kv4/* routing ──────────────────────────────────────────────


def test_resolve_active_target_mlx_local_short_id():
    """mlx-local-kv4/gemma-4-26b → (http://127.0.0.1:8088, short_id)."""
    amr.set_active_model("mlx-local-kv4/gemma-4-26b", by="test")

    base_url, model = amr.resolve_active_target(
        default_base_url="http://127.0.0.1:18789",
        default_model="openclaw",
    )

    assert base_url == "http://127.0.0.1:8088"
    assert model == "mlx-local-kv4/gemma-4-26b"


# ── 2. cloud (google/*) routing ─────────────────────────────────────────────


def test_resolve_active_target_cloud_remains_on_gateway():
    """Cloud-модели (google/...) остаются на gateway, model="openclaw"."""
    amr.set_active_model("google/gemini-3-pro-preview", by="test")

    base_url, model = amr.resolve_active_target(
        default_base_url="http://127.0.0.1:18789",
        default_model="openclaw",
    )

    assert base_url == "http://127.0.0.1:18789"
    # Cloud-модель НЕ должна попасть в payload — gateway сам решит,
    # иначе он 400'нёт unknown model name.
    assert model == "openclaw"


# ── 3. openclaw/* routing ───────────────────────────────────────────────────


def test_resolve_active_target_openclaw_remains_on_gateway():
    """openclaw / openclaw/main → gateway + model="openclaw"."""
    amr.set_active_model("openclaw/main", by="test")

    base_url, model = amr.resolve_active_target(
        default_base_url="http://127.0.0.1:18789",
        default_model="openclaw",
    )

    assert base_url == "http://127.0.0.1:18789"
    assert model == "openclaw"


# ── 4. ENV override ─────────────────────────────────────────────────────────


def test_env_override_beats_file(monkeypatch: pytest.MonkeyPatch):
    """KRAB_PRIMARY_MODEL_ID имеет приоритет над файлом."""
    amr.set_active_model("google/gemini-3-pro-preview", by="test")
    monkeypatch.setenv(amr.ENV_VAR, "mlx-local-kv4/gemma-4-26b")

    assert amr.get_active_model_id() == "mlx-local-kv4/gemma-4-26b"
    base_url, model = amr.resolve_active_target(
        default_base_url="http://127.0.0.1:18789",
    )
    assert base_url == "http://127.0.0.1:8088"
    assert model == "mlx-local-kv4/gemma-4-26b"


# ── 5. file persist + atomic write ──────────────────────────────────────────


def test_set_active_model_writes_atomic_json():
    """set_active_model() пишет валидный JSON с полным payload."""
    amr.set_active_model("mlx-local-kv4/gemma-4-26b", by="owner_panel", reason="test")

    raw = amr.STATE_PATH.read_text(encoding="utf-8")
    parsed = json.loads(raw)
    assert parsed["model"] == "mlx-local-kv4/gemma-4-26b"
    assert parsed["switched_by"] == "owner_panel"
    assert parsed["reason"] == "test"
    assert isinstance(parsed["switched_at"], (int, float))


# ── 6. cache invalidation ───────────────────────────────────────────────────


def test_invalidate_cache_picks_up_external_change():
    """Прямая запись в файл + invalidate_cache → read видит новое значение."""
    amr.set_active_model("google/gemini-3-pro-preview", by="test")
    assert amr.get_active_model_id() == "google/gemini-3-pro-preview"

    # Симулируем внешнее изменение файла (например, manual edit).
    amr.STATE_PATH.write_text(
        json.dumps({"model": "mlx-local-kv4/gemma-4-26b", "switched_at": 1.0}),
        encoding="utf-8",
    )
    # Без invalidate — кэш всё ещё старый.
    assert amr.get_active_model_id() == "google/gemini-3-pro-preview"
    amr.invalidate_cache()
    assert amr.get_active_model_id() == "mlx-local-kv4/gemma-4-26b"


# ── 7. metric counter ───────────────────────────────────────────────────────


def test_inc_active_model_switch_increments_counter():
    """Counter растёт корректно по (from_model, to_model)."""
    before = dict(amr_metrics._ACTIVE_MODEL_SWITCH_COUNTER)
    amr_metrics.inc_active_model_switch(
        from_model="google/gemini-3-pro-preview",
        to_model="mlx-local-kv4/gemma-4-26b",
    )
    key = ("google/gemini-3-pro-preview", "mlx-local-kv4/gemma-4-26b")
    assert amr_metrics._ACTIVE_MODEL_SWITCH_COUNTER.get(key, 0) == before.get(key, 0) + 1


# ── 8. helpers edge-cases ───────────────────────────────────────────────────


def test_is_mlx_local_model_edge_cases():
    """Helper корректно распознаёт mlx-local префикс."""
    assert amr.is_mlx_local_model("mlx-local-kv4/gemma-4-26b") is True
    assert amr.is_mlx_local_model("MLX-LOCAL-KV4/foo") is True  # case-insensitive
    assert amr.is_mlx_local_model("google/gemini-3-pro-preview") is False
    assert amr.is_mlx_local_model("openclaw/main") is False
    assert amr.is_mlx_local_model("") is False
    assert amr.is_mlx_local_model(None) is False


def test_is_openclaw_model_edge_cases():
    """Helper различает openclaw / openclaw/main / прочие."""
    assert amr.is_openclaw_model("openclaw") is True
    assert amr.is_openclaw_model("openclaw/main") is True
    assert amr.is_openclaw_model("openclaw/other") is True
    assert amr.is_openclaw_model("OPENCLAW") is True
    assert amr.is_openclaw_model("google/gemini-3-pro-preview") is False
    assert amr.is_openclaw_model("mlx-local-kv4/foo") is False
    assert amr.is_openclaw_model("") is False
    assert amr.is_openclaw_model(None) is False


# ── 9. fallback when no active model set ────────────────────────────────────


def test_resolve_active_target_no_active_model_keeps_defaults():
    """Если active_model.json нет — возвращаем дефолты без изменений."""
    assert amr.get_active_model_id() is None
    base_url, model = amr.resolve_active_target(
        default_base_url="http://127.0.0.1:18789",
        default_model="openclaw",
    )
    assert base_url == "http://127.0.0.1:18789"
    assert model == "openclaw"


# ── 10. MLX_LOCAL_KV4_URL override ──────────────────────────────────────────


def test_mlx_local_url_env_override(monkeypatch: pytest.MonkeyPatch):
    """ENV MLX_LOCAL_KV4_URL подменяет дефолтный :8088 endpoint."""
    monkeypatch.setenv("MLX_LOCAL_KV4_URL", "http://192.168.1.42:9090")
    amr.set_active_model("mlx-local-kv4/gemma-4-26b", by="test")
    base_url, _ = amr.resolve_active_target(
        default_base_url="http://127.0.0.1:18789",
    )
    assert base_url == "http://192.168.1.42:9090"


def test_set_active_model_empty_id_raises():
    """Пустой model_id отвергается с ValueError."""
    with pytest.raises(ValueError):
        amr.set_active_model("")
    with pytest.raises(ValueError):
        amr.set_active_model("   ")
