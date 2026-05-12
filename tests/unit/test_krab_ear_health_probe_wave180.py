# -*- coding: utf-8 -*-
"""Wave 180: тесты IPC probe + not-installed + backoff + env gate."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core import krab_ear_health_probe as ke_module
from src.core.krab_ear_health_probe import (
    KrabEarHealthProbe,
    _is_ke_installed,
    get_snapshot,
    reset_snapshot_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_snapshot():
    reset_snapshot_for_tests()
    yield
    reset_snapshot_for_tests()


# ---------------------------------------------------------------------------
# IPC probe
# ---------------------------------------------------------------------------


def test_ipc_probe_socket_missing_returns_failure_with_reason():
    """Если socket-файла нет — reason=ipc_socket_missing, no exception."""
    probe = KrabEarHealthProbe(
        socket_path="/tmp/wave180_definitely_does_not_exist_socket.sock",
    )
    probe._installed = True  # форсим, что KE installed (тест проверяет IPC ветку)
    # HTTP fallback off (URL = default → _http_explicit=False).
    probe._http_explicit = False
    ok = asyncio.run(probe.probe_once())
    assert ok is False
    snap = get_snapshot()
    assert snap["last_probe_ok"] is False
    assert snap["consecutive_failures"] == 1
    assert snap["failures_by_reason"].get("ipc_socket_missing") == 1


def test_ipc_probe_success_via_live_socket():
    """Запускаем эфемерный unix-socket server отвечающий на ping → probe возвращает True."""
    with tempfile.TemporaryDirectory() as tmpdir:
        sock_path = Path(tmpdir) / "krabear.sock"

        async def _server_handler(reader, writer):
            line = await reader.readline()
            try:
                req = json.loads(line.decode("utf-8"))
            except ValueError:
                req = {}
            response = {
                "id": req.get("id"),
                "ok": True,
                "result": {"status": "ok"},
            }
            writer.write((json.dumps(response) + "\n").encode("utf-8"))
            await writer.drain()
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

        async def _run():
            server = await asyncio.start_unix_server(_server_handler, path=str(sock_path))
            try:
                probe = KrabEarHealthProbe(socket_path=str(sock_path), timeout_sec=2.0)
                probe._installed = True
                probe._http_explicit = False
                return await probe.probe_once()
            finally:
                server.close()
                await server.wait_closed()

        ok = asyncio.run(_run())
        assert ok is True
        snap = get_snapshot()
        assert snap["consecutive_failures"] == 0
        assert snap["last_probe_ok"] is True


def test_ipc_probe_not_ok_payload_records_failure():
    """Сервер ответил, но `ok=False` или status≠ok → failure ipc_not_ok."""
    with tempfile.TemporaryDirectory() as tmpdir:
        sock_path = Path(tmpdir) / "krabear.sock"

        async def _server_handler(reader, writer):
            await reader.readline()
            response = {"id": "x", "ok": False, "result": {"status": "degraded"}}
            writer.write((json.dumps(response) + "\n").encode("utf-8"))
            await writer.drain()
            writer.close()

        async def _run():
            server = await asyncio.start_unix_server(_server_handler, path=str(sock_path))
            try:
                probe = KrabEarHealthProbe(socket_path=str(sock_path), timeout_sec=2.0)
                probe._installed = True
                probe._http_explicit = False
                return await probe.probe_once()
            finally:
                server.close()
                await server.wait_closed()

        ok = asyncio.run(_run())
        assert ok is False
        snap = get_snapshot()
        assert snap["failures_by_reason"].get("ipc_not_ok") == 1


# ---------------------------------------------------------------------------
# Not-installed detection
# ---------------------------------------------------------------------------


def test_is_ke_installed_returns_false_when_all_paths_missing():
    """Если binary + venv + socket_parent все отсутствуют — installed=False."""
    fake_binary = Path("/tmp/_wave180_no_binary_here_blabla")
    fake_venv = Path("/tmp/_wave180_no_venv_here_blabla")
    fake_socket_parent = Path("/tmp/_wave180_no_socket_dir_blabla_xyz")
    assert (
        _is_ke_installed(
            app_binary=fake_binary,
            backend_venv=fake_venv,
            socket_parent=fake_socket_parent,
        )
        is False
    )


def test_is_ke_installed_returns_true_when_socket_dir_exists():
    """Если хотя бы один артефакт есть — installed=True (KE мог быть запущен раньше)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        socket_parent = Path(tmpdir)
        assert (
            _is_ke_installed(
                app_binary=Path("/tmp/_wave180_no_binary"),
                backend_venv=Path("/tmp/_wave180_no_venv"),
                socket_parent=socket_parent,
            )
            is True
        )


def test_probe_skips_when_ke_not_installed():
    """Wave 180: probe_once() возвращает True (no-op) если KE not installed."""
    probe = KrabEarHealthProbe(
        socket_path="/tmp/_wave180_no_socket.sock",
    )
    # Принудительно: не installed.
    with patch.object(probe, "_check_installed", return_value=False):
        ok = asyncio.run(probe.probe_once())
    assert ok is True  # no-op → True
    snap = get_snapshot()
    # Не должно быть зафиксировано failure.
    assert snap["consecutive_failures"] == 0
    assert snap["total_failures"] == 0


def test_check_installed_caches_result_and_updates_snapshot():
    """_check_installed считается один раз и пишет snapshot.installed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        socket_dir = Path(tmpdir)
        with patch.object(ke_module, "_KE_APP_BINARY", Path("/tmp/__missing_binary__")):
            with patch.object(ke_module, "_KE_BACKEND_VENV", Path("/tmp/__missing_venv__")):
                probe = KrabEarHealthProbe(socket_path=str(socket_dir / "krabear.sock"))
                # Первый вызов: socket_dir существует → installed=True.
                assert probe._check_installed() is True
                assert get_snapshot()["installed"] is True
                # Кэширование: второй вызов не должен заглядывать в файловую систему.
                with patch.object(ke_module, "_is_ke_installed") as mock_check:
                    probe._check_installed()
                    mock_check.assert_not_called()


# ---------------------------------------------------------------------------
# Backoff
# ---------------------------------------------------------------------------


def test_backoff_interval_under_threshold_returns_base():
    """consecutive < threshold → interval = base."""
    probe = KrabEarHealthProbe(interval_sec=60)
    ke_module._SNAPSHOT["consecutive_failures"] = 0
    assert probe._backoff_interval() == 60
    ke_module._SNAPSHOT["consecutive_failures"] = 2
    assert probe._backoff_interval() == 60  # ниже порога 3


def test_backoff_interval_grows_exponentially_after_threshold():
    """consecutive ≥ threshold → interval *= 2^(consecutive - threshold)."""
    probe = KrabEarHealthProbe(interval_sec=60)
    # threshold=3, base=60
    ke_module._SNAPSHOT["consecutive_failures"] = 3
    assert probe._backoff_interval() == 60  # k=0 → *1
    ke_module._SNAPSHOT["consecutive_failures"] = 4
    assert probe._backoff_interval() == 120  # k=1 → *2
    ke_module._SNAPSHOT["consecutive_failures"] = 5
    assert probe._backoff_interval() == 240  # k=2 → *4


def test_backoff_interval_capped_at_max():
    """После большого consecutive interval не должен расти выше _BACKOFF_MAX_SEC."""
    probe = KrabEarHealthProbe(interval_sec=60)
    ke_module._SNAPSHOT["consecutive_failures"] = 100
    assert probe._backoff_interval() == ke_module._BACKOFF_MAX_SEC


# ---------------------------------------------------------------------------
# Env gate
# ---------------------------------------------------------------------------


def test_start_respects_health_probe_enabled_env_gate(monkeypatch):
    """KRAB_EAR_HEALTH_PROBE_ENABLED=0 → start() — no-op."""
    monkeypatch.setenv("KRAB_EAR_HEALTH_PROBE_ENABLED", "0")
    monkeypatch.delenv("KRAB_EAR_PROBE_ENABLED", raising=False)

    async def _run():
        probe = KrabEarHealthProbe()
        probe.start()
        return probe._task

    task = asyncio.run(_run())
    assert task is None  # task не создан → loop не запущен


def test_start_falls_back_to_legacy_env_var(monkeypatch):
    """Legacy KRAB_EAR_PROBE_ENABLED тоже работает (для bridge bootstrap compat)."""
    monkeypatch.delenv("KRAB_EAR_HEALTH_PROBE_ENABLED", raising=False)
    monkeypatch.setenv("KRAB_EAR_PROBE_ENABLED", "0")

    async def _run():
        probe = KrabEarHealthProbe()
        probe.start()
        task = probe._task
        # Гарантируем чистый exit если task всё-таки создался.
        if task is not None:
            probe.stop()
            try:
                await asyncio.wait_for(task, timeout=0.5)
            except Exception:  # noqa: BLE001
                pass
        return task

    task = asyncio.run(_run())
    assert task is None


def test_start_default_enabled_when_no_env_set(monkeypatch):
    """Дефолт — enabled (для backward compat)."""
    monkeypatch.delenv("KRAB_EAR_HEALTH_PROBE_ENABLED", raising=False)
    monkeypatch.delenv("KRAB_EAR_PROBE_ENABLED", raising=False)

    async def _run():
        probe = KrabEarHealthProbe(socket_path="/tmp/_wave180_no_sock.sock")
        probe.start()
        task = probe._task
        # Сразу гасим, чтобы тест не висел.
        probe.stop()
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=0.5)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):  # noqa: BLE001
                pass
        return task

    task = asyncio.run(_run())
    assert task is not None  # default → loop запущен


# ---------------------------------------------------------------------------
# Integration: IPC-only mode не алертит на default HTTP URL
# ---------------------------------------------------------------------------


def test_ipc_only_mode_does_not_attempt_http_when_url_is_default(monkeypatch):
    """Когда KRAB_EAR_BACKEND_URL не задан явно — HTTP fallback пропускается.

    Это фикс root-cause Wave 180: probe Wave 79 всегда стучался на 5005, KE = IPC,
    каждые 60s рос consecutive_failures. После фикса: только IPC, reason более точен.
    """
    monkeypatch.delenv("KRAB_EAR_BACKEND_URL", raising=False)
    probe = KrabEarHealthProbe(socket_path="/tmp/_wave180_no_sock.sock")
    probe._installed = True
    assert probe._http_explicit is False

    # Если probe пошёл бы по HTTP — тут бы получили connection_error.
    # Wave 180: должен получить ipc_socket_missing, http код не дёрнут.
    ok = asyncio.run(probe.probe_once())
    assert ok is False
    snap = get_snapshot()
    assert snap["failures_by_reason"].get("ipc_socket_missing") == 1
    assert "connection_error" not in snap["failures_by_reason"]
