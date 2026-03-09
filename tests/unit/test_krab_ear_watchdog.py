# -*- coding: utf-8 -*-
"""
Тесты watchdog для Krab Ear.

Проверяем базовый контракт:
1) Если IPC-сокет отсутствует — probe должен вернуть ok=false.
2) Если IPC-сокет отвечает на ping — probe должен вернуть ok=true.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

from scripts.krab_ear_watchdog import KrabEarWatchdog


async def _run_fake_ipc_server(socket_path: Path, stop_event: asyncio.Event) -> None:
    """Минимальный IPC backend-стаб, совместимый с watchdog ping."""

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        raw = await reader.readline()
        _ = raw  # В тесте важен только факт запроса, содержимое не критично.
        payload = {"ok": True, "result": {"status": "ok"}}
        writer.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(_handle, path=str(socket_path))
    try:
        await stop_event.wait()
    finally:
        server.close()
        await server.wait_closed()


def _mk_watchdog(tmp_path: Path, socket_path: Path) -> KrabEarWatchdog:
    wd = KrabEarWatchdog(
        ear_dir=tmp_path,
        start_script=tmp_path / "scripts/start_agent.command",
        runtime_bin=tmp_path / "native/runtime/KrabEarAgent",
        socket_path=socket_path,
        interval_sec=2.0,
        fail_threshold=2,
        cooldown_sec=10.0,
        ping_timeout_sec=1.0,
    )
    # Изолируем тест от реальных процессов хоста.
    wd._find_pids = lambda: []  # type: ignore[method-assign]
    return wd


def test_probe_socket_missing(tmp_path: Path) -> None:
    socket_path = tmp_path / "missing.sock"
    wd = _mk_watchdog(tmp_path, socket_path)
    report = asyncio.run(wd.probe())
    assert report["ok"] is False
    assert report["status"] == "socket_missing"
    assert report["process_running"] is False


def test_probe_socket_ok(tmp_path: Path) -> None:
    # Для AF_UNIX на macOS нужен короткий путь (иначе AF_UNIX path too long).
    socket_path = Path(tempfile.gettempdir()) / f"krabear_test_{os.getpid()}_{id(tmp_path)}.sock"
    if socket_path.exists():
        socket_path.unlink()
    stop_event = asyncio.Event()
    wd = _mk_watchdog(tmp_path, socket_path)

    async def _scenario() -> dict:
        server_task = asyncio.create_task(_run_fake_ipc_server(socket_path, stop_event))
        try:
            # Даём серверу подняться.
            await asyncio.sleep(0.05)
            return await wd.probe()
        finally:
            stop_event.set()
            await server_task

    try:
        report = asyncio.run(_scenario())
        assert report["ok"] is True
        assert report["status"] == "ok"
        assert report["process_running"] is False
    finally:
        if socket_path.exists():
            socket_path.unlink()
