# -*- coding: utf-8 -*-
"""AGE-8 regression tests — `memory_doctor.run_repairs` не блокирует event loop.

Linear AGE-8 (High priority, 21-04-2026): `subprocess.run(...)` в async
`run_repairs()` фактически блокировал asyncio event loop на время выполнения
subprocess'а (~секунды для sqlite recovery, ~minutes для backfill embeddings).

Fix: commit 7697788 — все subprocess вызовы заменены на async-friendly
`_run_command_capture` через `asyncio.create_subprocess_exec`.

Этот файл добавляет sentinel-тест: пока `run_repairs` идёт через долгий
subprocess (200 ms sleep), параллельная asyncio задача должна успевать
тикать каждые ~10 ms, что доказывает event loop НЕ блокируется.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_run_repairs_does_not_block_event_loop(monkeypatch, tmp_path: Path) -> None:
    """Sentinel concurrent task должна продолжать тикать пока run_repairs идёт.

    Если бы run_repairs использовал blocking subprocess.run, sentinel заморозился бы
    на 200ms — мы бы увидели max_gap > 0.15s. С async create_subprocess_exec
    event loop свободен крутиться → max_gap ~10ms.
    """
    import src.core.memory_doctor as memory_doctor

    db_path = tmp_path / "archive.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, text TEXT)")
    conn.commit()
    conn.close()

    # Заменяем _run_command_capture на async-функцию которая спит 200ms
    # имитируя долгий subprocess (backfill embeddings / launchctl kickstart).
    async def slow_fake_run(argv: list[str], *, timeout_sec: float) -> tuple[int, str, str]:
        await asyncio.sleep(0.2)
        return 0, "done", ""

    monkeypatch.setattr(memory_doctor, "_run_command_capture", slow_fake_run)
    monkeypatch.setattr(memory_doctor, "_find_python", lambda: sys.executable)

    # Sentinel: тикает каждые 10ms, замеряет max gap между tick'ами.
    ticks: list[float] = []
    stop_event = asyncio.Event()

    async def sentinel() -> None:
        loop = asyncio.get_running_loop()
        while not stop_event.is_set():
            ticks.append(loop.time())
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=0.01)
            except TimeoutError:
                pass

    sentinel_task = asyncio.create_task(sentinel())

    # Триггерим оба subprocess-action (backfill + MCP restart) → 2× 200ms подвисания
    # если bug вернулся.
    result = await memory_doctor.run_repairs(
        checks={
            "encoded_ratio": {"status": "fail"},
            "mcp_reachable": {"status": "fail"},
        },
        db_path=db_path,
    )

    stop_event.set()
    await sentinel_task

    # Sanity: оба subprocess-action отработали успешно.
    actions = [item["action"] for item in result["repairs"]]
    assert "backfill_embeddings" in actions
    assert "restart_mcp_yung_nagato" in actions

    # Главная проверка: max gap между тиками < 100ms.
    # blocking subprocess.run заморозил бы loop на 200ms → max_gap > 0.15s.
    assert len(ticks) >= 3, f"Sentinel не тикал достаточно раз: {len(ticks)}"
    gaps = [ticks[i + 1] - ticks[i] for i in range(len(ticks) - 1)]
    max_gap = max(gaps)
    assert max_gap < 0.1, (
        f"Event loop был заблокирован на {max_gap:.3f}s > 0.1s — "
        f"возможно вернулся blocking subprocess.run в memory_doctor.run_repairs(). "
        f"Все gaps: {[round(g, 4) for g in gaps]}"
    )


def test_memory_doctor_source_has_no_blocking_subprocess() -> None:
    """Static guard: src/core/memory_doctor.py НЕ должен содержать blocking subprocess вызовы."""
    source = (Path(__file__).resolve().parents[2] / "src" / "core" / "memory_doctor.py").read_text(
        encoding="utf-8"
    )
    # subprocess.run / .call / .check_output / .check_call — все блокирующие
    for forbidden in (
        "subprocess.run(",
        "subprocess.call(",
        "subprocess.check_output(",
        "subprocess.check_call(",
    ):
        assert forbidden not in source, (
            f"AGE-8 regression: blocking '{forbidden}' вернулся в memory_doctor.py. "
            f"Используй `_run_command_capture` / `asyncio.create_subprocess_exec`."
        )
