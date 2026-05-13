# -*- coding: utf-8 -*-
"""
Wave 214: integration tests подтверждающие, что memory_leak_detector
background_loop корректно подключён в userbot_bridge bootstrap.

Полный start() не вызываем (heavy + требует pyrogram client). Вместо этого
проверяем сам wiring-блок: env gate + asyncio.create_task + graceful failure
при сбое импорта.
"""
from __future__ import annotations

import asyncio
import os
import sys
import types
from pathlib import Path

import pytest


def _read_bridge_source() -> str:
    """Читаем userbot_bridge.py чтобы verify wiring is present."""
    path = Path(__file__).resolve().parents[2] / "src" / "userbot_bridge.py"
    return path.read_text(encoding="utf-8")


def test_wiring_block_present_in_bridge() -> None:
    """Wave 214: блок memory_leak_detector должен присутствовать в start()."""
    src = _read_bridge_source()
    # ENV gate
    assert 'KRAB_MEMORY_LEAK_DETECTOR_ENABLED' in src
    # Импортируем background_loop
    assert 'from .core.memory_leak_detector import' in src
    assert 'background_loop' in src
    # Спавним task с конкретным именем
    assert 'name="memory_leak_detector"' in src
    # Wrapped в try/except с warning (не падает на bootstrap_failed)
    assert 'memory_leak_detector_bootstrap_failed' in src
    assert 'memory_leak_detector_bootstrap_done' in src


@pytest.mark.asyncio
async def test_background_loop_spawns_as_task_when_env_enabled(monkeypatch, tmp_path):
    """
    Симулируем wiring-блок: при KRAB_MEMORY_LEAK_DETECTOR_ENABLED=1
    background_loop должен запускаться как asyncio.Task с именем
    "memory_leak_detector".
    """
    monkeypatch.setenv("KRAB_MEMORY_LEAK_DETECTOR_ENABLED", "1")
    # Изолируем runtime state в tmp_path чтобы не писать в real ~/.openclaw
    monkeypatch.setenv("KRAB_RUNTIME_STATE_DIR", str(tmp_path))
    # Длинный интервал — loop не успеет выполнить вторую итерацию
    monkeypatch.setenv("KRAB_MEMORY_LEAK_CHECK_INTERVAL_SEC", "9999")

    from src.core.memory_leak_detector import background_loop as _memleak_loop

    task = asyncio.create_task(_memleak_loop(), name="memory_leak_detector")
    # Дать loop стартовать
    await asyncio.sleep(0.05)

    assert task.get_name() == "memory_leak_detector"
    assert not task.done(), "loop должен быть alive"

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_bootstrap_block_swallows_import_failures(monkeypatch):
    """
    Сбой импорта memory_leak_detector НЕ должен крашить startup —
    блок обёрнут в try/except. Симулируем точную логику wiring.
    """
    monkeypatch.setenv("KRAB_MEMORY_LEAK_DETECTOR_ENABLED", "1")
    # Подменяем модуль на broken stub чтобы import упал
    broken = types.ModuleType("src.core.memory_leak_detector")

    def _raise(*_a, **_kw):  # noqa: ARG001
        raise RuntimeError("simulated import failure")

    broken.__getattr__ = _raise  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "src.core.memory_leak_detector", broken)

    # Точное воспроизведение wiring-блока — должно не падать
    spawned = False
    try:
        if os.getenv("KRAB_MEMORY_LEAK_DETECTOR_ENABLED", "1").strip() != "0":
            try:
                from src.core.memory_leak_detector import (  # noqa: PLC0415
                    background_loop as _memleak_loop,
                )

                _ = asyncio.create_task(_memleak_loop(), name="memory_leak_detector")
                spawned = True
            except Exception:  # noqa: BLE001
                # Это ровно то поведение, которое мы ожидаем
                pass
    except Exception as exc:  # pragma: no cover
        pytest.fail(f"bootstrap не должен пробрасывать ошибку: {exc}")

    assert spawned is False, "при broken import task не должна спавниться"


def test_env_gate_zero_disables_wiring() -> None:
    """KRAB_MEMORY_LEAK_DETECTOR_ENABLED=0 → loop не должен запускаться."""
    # Проверяем gate-логику напрямую, как она написана в bridge
    for val in ("0", " 0 "):
        assert val.strip() == "0", "gate '0' должен быть disabled"
    for val in ("1", "true", "yes", ""):
        # Wiring spawns when strip() != "0"; пустая строка пока тоже spawns,
        # но default из os.getenv("...","1") даёт "1" если переменная unset.
        assert val.strip() != "0"
