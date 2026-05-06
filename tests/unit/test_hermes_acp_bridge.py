# -*- coding: utf-8 -*-
"""Tests for HermesACPBridge (Wave 16-B, Hermes Phase B).

Покрывает: health (missing binary, caching), stream stub,
kind, close idempotency, singleton.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.integrations.hermes_acp_bridge import (
    _HEALTH_CACHE_TTL,
    HermesACPBridge,
    get_hermes_bridge,
    get_hermes_bridge_sync,
    reset_hermes_bridge,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_singleton() -> None:
    """Сбрасываем синглтон перед каждым тестом."""
    reset_hermes_bridge()
    yield
    reset_hermes_bridge()


@pytest.fixture()
def bridge_no_binary(tmp_path: Path) -> HermesACPBridge:
    """Bridge с несуществующим бинарём."""
    return HermesACPBridge(binary=str(tmp_path / "nonexistent_hermes"))


@pytest.fixture()
def bridge_with_fake_binary(tmp_path: Path) -> HermesACPBridge:
    """Bridge с исполняемым файлом-заглушкой."""
    fake = tmp_path / "hermes"
    fake.write_bytes(b"#!/bin/sh\nsleep 60\n")
    fake.chmod(0o755)
    return HermesACPBridge(binary=str(fake))


# ---------------------------------------------------------------------------
# test_bridge_health_when_binary_missing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_bridge_health_when_binary_missing(bridge_no_binary: HermesACPBridge) -> None:
    """Если binary не существует — health.is_healthy=False, понятная ошибка."""
    health = await bridge_no_binary.health()
    assert health.is_healthy is False
    assert health.engine == "hermes"
    assert health.error is not None
    assert "not found" in health.error.lower() or "hermes" in health.error


# ---------------------------------------------------------------------------
# test_bridge_health_caching_60s
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_bridge_health_caching_60s(bridge_no_binary: HermesACPBridge) -> None:
    """Два вызова health() в течение 60s -> один probe (кэш работает)."""
    call_count = 0
    original_available = bridge_no_binary._binary_available

    def counting_available() -> bool:
        nonlocal call_count
        call_count += 1
        return original_available()

    bridge_no_binary._binary_available = counting_available  # type: ignore[method-assign]

    await bridge_no_binary.health()
    await bridge_no_binary.health()

    # Второй вызов должен вернуть кэш — _binary_available вызывается 1 раз
    assert call_count == 1


@pytest.mark.asyncio()
async def test_bridge_health_cache_expires(bridge_no_binary: HermesACPBridge) -> None:
    """После истечения TTL — повторный probe."""
    await bridge_no_binary.health()
    # Сдвигаем timestamp кэша в прошлое
    ts, h = bridge_no_binary._healthy_cache  # type: ignore[misc]
    bridge_no_binary._healthy_cache = (ts - _HEALTH_CACHE_TTL - 1, h)

    call_count = 0
    original = bridge_no_binary._binary_available

    def counting() -> bool:
        nonlocal call_count
        call_count += 1
        return original()

    bridge_no_binary._binary_available = counting  # type: ignore[method-assign]
    await bridge_no_binary.health()

    assert call_count == 1  # probe выполнен заново


# ---------------------------------------------------------------------------
# test_bridge_stream_unavailable_yields_finish_chunk
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_bridge_stream_unavailable_yields_finish_chunk(
    bridge_no_binary: HermesACPBridge,
) -> None:
    """Если engine недоступен — stream() выдаёт ровно один error chunk."""
    chunks = []
    async for chunk in bridge_no_binary.stream("hello"):
        chunks.append(chunk)

    assert len(chunks) == 1
    assert chunks[0].chunk_type == "finish"
    assert chunks[0].finish_reason == "engine_unavailable"
    assert "unavailable" in chunks[0].text.lower() or "not found" in chunks[0].text.lower()


# ---------------------------------------------------------------------------
# test_bridge_kind_returns_hermes
# ---------------------------------------------------------------------------


def test_bridge_kind_returns_hermes() -> None:
    """kind property возвращает 'hermes'."""
    bridge = HermesACPBridge(binary="/nonexistent/hermes")
    assert bridge.kind == "hermes"


# ---------------------------------------------------------------------------
# test_bridge_close_idempotent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_bridge_close_idempotent(bridge_no_binary: HermesACPBridge) -> None:
    """Двойной close() не вызывает ошибок."""
    await bridge_no_binary.close()
    await bridge_no_binary.close()  # второй раз — тихо


# ---------------------------------------------------------------------------
# test_get_hermes_bridge_singleton
# ---------------------------------------------------------------------------


def test_get_hermes_bridge_singleton() -> None:
    """get_hermes_bridge_sync() (deprecated) дважды возвращает тот же объект."""
    # Используем sync version для совместимости с sync test context.
    # Async version проверяется в test_wave16p_fixes.py.
    a = get_hermes_bridge_sync()
    b = get_hermes_bridge_sync()
    assert a is b


# ---------------------------------------------------------------------------
# test_bridge_auto_detect_binary_uses_env
# ---------------------------------------------------------------------------


def test_bridge_auto_detect_binary_uses_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KRAB_HERMES_BINARY env переопределяет auto-detect.

    Wave 19-C: _resolve_hermes_binary() требует реальный executable файл,
    поэтому создаём его с +x перед выставлением env.
    """
    import stat

    custom = tmp_path / "my_hermes"
    custom.write_text("#!/bin/bash\necho hermes")
    custom.chmod(custom.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("KRAB_HERMES_BINARY", str(custom))
    bridge = HermesACPBridge()
    assert bridge._binary == str(custom)


def test_bridge_auto_detect_binary_arg_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Аргумент binary= имеет приоритет над env."""
    custom = str(tmp_path / "my_hermes")
    monkeypatch.setenv("KRAB_HERMES_BINARY", "/should/be/ignored")
    bridge = HermesACPBridge(binary=custom)
    assert bridge._binary == custom


# ---------------------------------------------------------------------------
# test subprocess mock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_bridge_ensure_started_mock_subprocess(
    bridge_with_fake_binary: HermesACPBridge,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Если subprocess успешно запускается — health is_healthy=True."""
    # Мокаем create_subprocess_exec чтобы не запускать реальный процесс
    mock_proc = MagicMock()
    mock_proc.returncode = None  # процесс "жив"
    mock_proc.pid = 42

    async def fake_spawn(*args: object, **kwargs: object) -> MagicMock:
        return mock_proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)

    # Wave 16-Q: mock acp connection — без него initialize() требует real streams
    import acp  # noqa: PLC0415

    fake_conn = MagicMock()
    fake_conn.initialize = AsyncMock(return_value=MagicMock())
    fake_conn.close = AsyncMock()
    monkeypatch.setattr(acp, "connect_to_agent", lambda *a, **kw: fake_conn)

    # _binary_available должен вернуть True для fake binary
    health = await bridge_with_fake_binary.health()
    assert health.is_healthy is True
    assert health.engine == "hermes"
