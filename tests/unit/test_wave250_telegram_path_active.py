# -*- coding: utf-8 -*-
"""Wave 250: per-call async hook resolve_telegram_path_model() tests.

Покрытие:
    1. force_all=1 + active присутствует → active wins.
    2. preferred задан + force_all выкл → None (caller оставляет preferred).
    3. active присутствует + preferred пуст → active wins (source).
    4. MLX backend + force_cloud=True → skip_force_cloud_remap=True.
    5. Hook disabled через env → None независимо от active.
    6. active_model.json пуст/missing → None.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.active_model_routing import (
    TelegramPathPickResult,
    resolve_telegram_path_model,
)


def _write_active(tmp_path: Path, model: str) -> Path:
    """Хелпер: пишет active_model.json в tmp_path и возвращает путь."""
    target = tmp_path / "active_model.json"
    target.write_text(json.dumps({"model": model}), encoding="utf-8")
    return target


@pytest.mark.asyncio
async def test_force_all_paths_active_wins_over_preferred(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Когда KRAB_ACTIVE_MODEL_FORCE_ALL_PATHS=1 и active есть — active побеждает
    даже при наличии preferred."""
    target = _write_active(tmp_path, "mlx-local-kv4/gemma-4-26b")
    monkeypatch.setenv("KRAB_ACTIVE_MODEL_FORCE_ALL_PATHS", "1")
    monkeypatch.delenv("KRAB_ACTIVE_MODEL_TELEGRAM_PATH_ENABLED", raising=False)

    result = await resolve_telegram_path_model(
        preferred="codex-cli/gpt-5.5",
        force_cloud=False,
        path=target,
    )
    assert isinstance(result, TelegramPathPickResult)
    assert result.model == "mlx-local-kv4/gemma-4-26b"
    assert result.source == "active_model_file"


@pytest.mark.asyncio
async def test_preferred_wins_when_force_all_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """force_all выключен + явный preferred → hook возвращает None
    (caller оставит preferred как есть)."""
    target = _write_active(tmp_path, "mlx-local-kv4/gemma-4-26b")
    monkeypatch.delenv("KRAB_ACTIVE_MODEL_FORCE_ALL_PATHS", raising=False)

    result = await resolve_telegram_path_model(
        preferred="google/gemini-3-pro-preview",
        force_cloud=True,
        path=target,
    )
    assert result is None


@pytest.mark.asyncio
async def test_active_wins_when_preferred_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """preferred пустой → active побеждает даже без force_all."""
    target = _write_active(tmp_path, "google/gemini-3-pro-preview")
    monkeypatch.delenv("KRAB_ACTIVE_MODEL_FORCE_ALL_PATHS", raising=False)

    result = await resolve_telegram_path_model(
        preferred="",
        force_cloud=False,
        path=target,
    )
    assert isinstance(result, TelegramPathPickResult)
    assert result.model == "google/gemini-3-pro-preview"
    assert result.source == "active_model_file"
    assert result.skip_force_cloud_remap is False


@pytest.mark.asyncio
async def test_mlx_backend_skips_force_cloud_remap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Local backend (MLX) + force_cloud=True → skip_force_cloud_remap=True,
    чтобы downstream pipeline не подменил MLX на cloud candidate."""
    target = _write_active(tmp_path, "mlx-local-kv4/gemma-4-26b")
    monkeypatch.setenv("KRAB_ACTIVE_MODEL_FORCE_ALL_PATHS", "1")

    result = await resolve_telegram_path_model(
        preferred="",
        force_cloud=True,
        path=target,
    )
    assert result is not None
    assert result.skip_force_cloud_remap is True
    # cloud backend в той же ситуации → skip остаётся False.
    target2 = _write_active(tmp_path, "google/gemini-3-pro-preview")
    result2 = await resolve_telegram_path_model(
        preferred="",
        force_cloud=True,
        path=target2,
    )
    assert result2 is not None
    assert result2.skip_force_cloud_remap is False


@pytest.mark.asyncio
async def test_hook_disabled_via_env_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KRAB_ACTIVE_MODEL_TELEGRAM_PATH_ENABLED=0 → hook молчит."""
    target = _write_active(tmp_path, "mlx-local-kv4/gemma-4-26b")
    monkeypatch.setenv("KRAB_ACTIVE_MODEL_TELEGRAM_PATH_ENABLED", "0")
    monkeypatch.setenv("KRAB_ACTIVE_MODEL_FORCE_ALL_PATHS", "1")

    result = await resolve_telegram_path_model(
        preferred="",
        force_cloud=False,
        path=target,
    )
    assert result is None


@pytest.mark.asyncio
async def test_missing_active_file_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """active_model.json отсутствует / пустой payload → None (caller fallback)."""
    monkeypatch.delenv("KRAB_ACTIVE_MODEL_FORCE_ALL_PATHS", raising=False)
    monkeypatch.delenv("KRAB_ACTIVE_MODEL_TELEGRAM_PATH_ENABLED", raising=False)
    missing = tmp_path / "active_model.json"
    # файл не создаём
    result = await resolve_telegram_path_model(
        preferred="",
        force_cloud=False,
        path=missing,
    )
    assert result is None
