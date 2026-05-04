# -*- coding: utf-8 -*-
"""Wave 19-D: тесты для codex_cli_liveness.py

Покрывают:
- find_codex_processes: возвращает список, матчинг по имени и cmdline
- is_codex_alive: нет процессов → alive=False, активный процесс → alive=True,
                  zombie-статус исключается
- kill_codex_processes: вызывает terminate() / kill() в зависимости от force
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psutil
import pytest

# ---------------------------------------------------------------------------
# Вспомогательные фабрики мок-объектов
# ---------------------------------------------------------------------------


def _make_proc(
    pid: int = 1234,
    name: str = "codex",
    cmdline: list[str] | None = None,
    status: str = psutil.STATUS_RUNNING,
    cpu_percent: float = 10.0,
    rss: int = 100 * 1024 * 1024,
    create_time: float = 1_700_000_000.0,
) -> MagicMock:
    """Создаёт мок psutil.Process с заданными атрибутами."""
    if cmdline is None:
        cmdline = ["node", "/usr/local/bin/codex-cli", "--model", "gpt-5.5"]

    proc = MagicMock(spec=psutil.Process)
    proc.pid = pid
    proc.info = {"pid": pid, "name": name, "cmdline": cmdline}

    # status() → строка
    proc.status.return_value = status

    # cpu_percent() вызывается дважды (baseline + sample)
    proc.cpu_percent.return_value = cpu_percent

    # memory_info() → объект с .rss
    mem = SimpleNamespace(rss=rss)
    proc.memory_info.return_value = mem

    proc.create_time.return_value = create_time
    return proc


# ---------------------------------------------------------------------------
# find_codex_processes
# ---------------------------------------------------------------------------


class TestFindCodexProcesses:
    """Тесты функции find_codex_processes."""

    def test_returns_list_when_no_processes(self):
        """При пустом process_iter возвращает пустой список."""
        with patch("psutil.process_iter", return_value=iter([])):
            from src.integrations.codex_cli_liveness import find_codex_processes

            result = find_codex_processes()
        assert isinstance(result, list)
        assert len(result) == 0

    def test_matches_by_name(self):
        """Процесс с именем 'codex' должен попасть в результат."""
        proc = _make_proc(name="codex", cmdline=["codex"])
        with patch("psutil.process_iter", return_value=iter([proc])):
            from src.integrations.codex_cli_liveness import find_codex_processes

            result = find_codex_processes()
        assert len(result) == 1
        assert result[0] is proc

    def test_matches_by_cmdline(self):
        """Процесс с cmdline содержащей 'codex-cli' должен попасть в результат."""
        proc = _make_proc(
            name="node",  # имя не содержит codex
            cmdline=["node", "/opt/codex-cli/index.js"],
        )
        with patch("psutil.process_iter", return_value=iter([proc])):
            from src.integrations.codex_cli_liveness import find_codex_processes

            result = find_codex_processes()
        assert len(result) == 1

    def test_skips_unrelated_processes(self):
        """Процесс без codex в имени и cmdline не должен попасть."""
        proc = _make_proc(name="python3", cmdline=["python3", "krab.py"])
        with patch("psutil.process_iter", return_value=iter([proc])):
            from src.integrations.codex_cli_liveness import find_codex_processes

            result = find_codex_processes()
        assert len(result) == 0

    def test_tolerates_no_such_process_during_iteration(self):
        """NoSuchProcess при обходе должен быть поглощён, не упасть."""
        proc = _make_proc(name="codex")
        # Симулируем что при обращении к .info процесс уже умер
        type(proc).info = property(
            lambda self: (_ for _ in ()).throw(psutil.NoSuchProcess(pid=proc.pid))
        )

        with patch("psutil.process_iter", return_value=iter([proc])):
            from src.integrations.codex_cli_liveness import find_codex_processes

            result = find_codex_processes()
        # Упасть не должно, просто пропускаем
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# is_codex_alive
# ---------------------------------------------------------------------------


class TestIsCodexAlive:
    """Тесты функции is_codex_alive."""

    def test_no_processes_returns_alive_false(self):
        """Если codex-процессов нет — alive=False."""
        with patch(
            "src.integrations.codex_cli_liveness.find_codex_processes",
            return_value=[],
        ):
            from src.integrations.codex_cli_liveness import is_codex_alive

            result = is_codex_alive()
        assert result["alive"] is False
        assert result["process_count"] == 0
        assert result["active_count"] == 0
        assert result["details"] == []

    def test_active_process_returns_alive_true(self):
        """Процесс с CPU > 0.5 и живым статусом → alive=True."""
        proc = _make_proc(cpu_percent=15.0, status=psutil.STATUS_RUNNING)

        with (
            patch(
                "src.integrations.codex_cli_liveness.find_codex_processes",
                return_value=[proc],
            ),
            patch("time.sleep"),
        ):  # не спим в тестах
            from src.integrations.codex_cli_liveness import is_codex_alive

            result = is_codex_alive(min_cpu_percent=0.5, sample_window_sec=0.1)

        assert result["alive"] is True
        assert result["active_count"] == 1
        assert result["process_count"] == 1
        assert len(result["details"]) == 1
        assert result["details"][0]["cpu_percent"] == 15.0

    def test_zombie_status_excluded_from_active(self):
        """Процесс со статусом ZOMBIE не считается активным."""
        proc = _make_proc(cpu_percent=5.0, status=psutil.STATUS_ZOMBIE)

        with (
            patch(
                "src.integrations.codex_cli_liveness.find_codex_processes",
                return_value=[proc],
            ),
            patch("time.sleep"),
        ):
            from src.integrations.codex_cli_liveness import is_codex_alive

            result = is_codex_alive(min_cpu_percent=0.5, sample_window_sec=0.1)

        assert result["alive"] is False
        assert result["active_count"] == 0
        # Процесс всё равно попадает в details для диагностики
        assert result["process_count"] == 1

    def test_stopped_status_excluded_from_active(self):
        """Процесс со статусом STOPPED не считается активным."""
        proc = _make_proc(cpu_percent=5.0, status=psutil.STATUS_STOPPED)

        with (
            patch(
                "src.integrations.codex_cli_liveness.find_codex_processes",
                return_value=[proc],
            ),
            patch("time.sleep"),
        ):
            from src.integrations.codex_cli_liveness import is_codex_alive

            result = is_codex_alive(min_cpu_percent=0.5)

        assert result["alive"] is False
        assert result["active_count"] == 0

    def test_low_cpu_not_counted_as_active(self):
        """Процесс с CPU < min_cpu_percent не считается активным."""
        proc = _make_proc(cpu_percent=0.1, status=psutil.STATUS_SLEEPING)

        with (
            patch(
                "src.integrations.codex_cli_liveness.find_codex_processes",
                return_value=[proc],
            ),
            patch("time.sleep"),
        ):
            from src.integrations.codex_cli_liveness import is_codex_alive

            result = is_codex_alive(min_cpu_percent=0.5)

        assert result["alive"] is False
        assert result["active_count"] == 0


# ---------------------------------------------------------------------------
# kill_codex_processes
# ---------------------------------------------------------------------------


class TestKillCodexProcesses:
    """Тесты функции kill_codex_processes."""

    def test_graceful_calls_terminate_not_kill(self):
        """По умолчанию (force=False) вызывается terminate(), не kill()."""
        proc = _make_proc(pid=9999)

        with patch(
            "src.integrations.codex_cli_liveness.find_codex_processes",
            return_value=[proc],
        ):
            from src.integrations.codex_cli_liveness import kill_codex_processes

            result = kill_codex_processes(force=False)

        proc.terminate.assert_called_once()
        proc.kill.assert_not_called()
        assert 9999 in result["killed"]
        assert result["errors"] == []

    def test_force_calls_kill_not_terminate(self):
        """При force=True вызывается kill(), а не terminate()."""
        proc = _make_proc(pid=8888)

        with patch(
            "src.integrations.codex_cli_liveness.find_codex_processes",
            return_value=[proc],
        ):
            from src.integrations.codex_cli_liveness import kill_codex_processes

            result = kill_codex_processes(force=True)

        proc.kill.assert_called_once()
        proc.terminate.assert_not_called()
        assert 8888 in result["killed"]

    def test_no_processes_returns_empty(self):
        """Если процессов нет — возвращает пустые списки."""
        with patch(
            "src.integrations.codex_cli_liveness.find_codex_processes",
            return_value=[],
        ):
            from src.integrations.codex_cli_liveness import kill_codex_processes

            result = kill_codex_processes()

        assert result["killed"] == []
        assert result["errors"] == []

    def test_access_denied_logged_in_errors(self):
        """AccessDenied при kill добавляется в errors, не поднимает exception."""
        proc = _make_proc(pid=7777)
        proc.terminate.side_effect = psutil.AccessDenied(pid=7777)

        with patch(
            "src.integrations.codex_cli_liveness.find_codex_processes",
            return_value=[proc],
        ):
            from src.integrations.codex_cli_liveness import kill_codex_processes

            result = kill_codex_processes(force=False)

        assert result["killed"] == []
        assert len(result["errors"]) == 1
        assert "7777" in result["errors"][0]
