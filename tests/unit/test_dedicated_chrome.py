# -*- coding: utf-8 -*-
"""
Юнит-тесты для src/integrations/dedicated_chrome.py.

Покрывают:
- find_chrome_binary (env override, candidates, PATH fallback)
- is_dedicated_chrome_running (200 OK / RequestError / OSError)
- launch_dedicated_chrome (already running, binary not found, success, timeout)
- get_dedicated_chrome_cdp_url (ws extraction)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from src.integrations import dedicated_chrome as dc

# ---------------------------------------------------------------------------
# find_chrome_binary
# ---------------------------------------------------------------------------


def test_find_chrome_binary_env_override_exists(monkeypatch, tmp_path: Path) -> None:
    """Если DEDICATED_CHROME_APP указывает на существующий файл — вернуть его."""
    fake = tmp_path / "custom-chrome"
    fake.write_text("stub")
    monkeypatch.setenv("DEDICATED_CHROME_APP", str(fake))
    assert dc.find_chrome_binary() == str(fake)


def test_find_chrome_binary_env_override_missing(monkeypatch, tmp_path: Path) -> None:
    """Если DEDICATED_CHROME_APP указывает на несуществующий файл — fallback к candidates."""
    missing = tmp_path / "does-not-exist"
    monkeypatch.setenv("DEDICATED_CHROME_APP", str(missing))
    # Эмулируем, что никаких candidates нет, PATH пустой
    monkeypatch.setattr(dc, "CHROME_CANDIDATES", [])
    monkeypatch.setattr(dc.shutil, "which", lambda name: None)
    assert dc.find_chrome_binary() is None


def test_find_chrome_binary_candidates_first_exists(monkeypatch, tmp_path: Path) -> None:
    """Возвращает первый существующий путь из CHROME_CANDIDATES."""
    monkeypatch.delenv("DEDICATED_CHROME_APP", raising=False)
    exists_path = tmp_path / "exists-chrome"
    exists_path.write_text("stub")
    missing_path = str(tmp_path / "missing-chrome")
    monkeypatch.setattr(dc, "CHROME_CANDIDATES", [missing_path, str(exists_path)])
    assert dc.find_chrome_binary() == str(exists_path)


def test_find_chrome_binary_which_fallback(monkeypatch) -> None:
    """Если candidates нет — fallback через shutil.which."""
    monkeypatch.delenv("DEDICATED_CHROME_APP", raising=False)
    monkeypatch.setattr(dc, "CHROME_CANDIDATES", [])
    monkeypatch.setattr(
        dc.shutil,
        "which",
        lambda name: "/usr/bin/chromium" if name == "chromium" else None,
    )
    assert dc.find_chrome_binary() == "/usr/bin/chromium"


def test_find_chrome_binary_not_found(monkeypatch) -> None:
    """Ничего не нашли — возвращаем None."""
    monkeypatch.delenv("DEDICATED_CHROME_APP", raising=False)
    monkeypatch.setattr(dc, "CHROME_CANDIDATES", [])
    monkeypatch.setattr(dc.shutil, "which", lambda name: None)
    assert dc.find_chrome_binary() is None


# ---------------------------------------------------------------------------
# is_dedicated_chrome_running
# ---------------------------------------------------------------------------


def test_is_running_true(monkeypatch) -> None:
    """httpx.get вернул 200 — Chrome запущен."""
    resp = MagicMock()
    resp.status_code = 200

    def fake_get(url: str, timeout: float = 2.0) -> MagicMock:
        return resp

    monkeypatch.setattr(dc.httpx, "get", fake_get)
    assert dc.is_dedicated_chrome_running(9222) is True


def test_is_running_false_on_request_error(monkeypatch) -> None:
    """httpx.RequestError → Chrome не запущен."""

    def fake_get(url: str, timeout: float = 2.0) -> MagicMock:
        raise httpx.RequestError("connection refused")

    monkeypatch.setattr(dc.httpx, "get", fake_get)
    assert dc.is_dedicated_chrome_running(9222) is False


def test_is_running_false_on_oserror(monkeypatch) -> None:
    """OSError → Chrome не запущен (safe)."""

    def fake_get(url: str, timeout: float = 2.0) -> MagicMock:
        raise OSError("network unreachable")

    monkeypatch.setattr(dc.httpx, "get", fake_get)
    assert dc.is_dedicated_chrome_running(9222) is False


def test_is_running_false_on_non_200(monkeypatch) -> None:
    """Non-200 статус — не рабочий Chrome."""
    resp = MagicMock()
    resp.status_code = 500

    def fake_get(url: str, timeout: float = 2.0) -> MagicMock:
        return resp

    monkeypatch.setattr(dc.httpx, "get", fake_get)
    assert dc.is_dedicated_chrome_running(9222) is False


# ---------------------------------------------------------------------------
# launch_dedicated_chrome
# ---------------------------------------------------------------------------


def test_launch_binary_not_found(monkeypatch) -> None:
    """Нет Chrome binary — возвращаем False."""
    monkeypatch.setattr(dc, "find_chrome_binary", lambda: None)
    ok, status = dc.launch_dedicated_chrome()
    assert ok is False
    assert status == "chrome_binary_not_found"


def test_launch_already_running(monkeypatch, tmp_path: Path) -> None:
    """Если Chrome уже запущен — не spawn'ить, вернуть already_running."""
    monkeypatch.setattr(dc, "find_chrome_binary", lambda: "/bin/chrome")
    monkeypatch.setattr(dc, "is_dedicated_chrome_running", lambda port=9222: True)

    popen_called = {"v": False}

    def fake_popen(*args, **kwargs):  # pragma: no cover - не должен вызываться
        popen_called["v"] = True
        raise AssertionError("Popen не должен вызываться когда Chrome уже запущен")

    monkeypatch.setattr(dc.subprocess, "Popen", fake_popen)
    ok, status = dc.launch_dedicated_chrome(profile_dir=tmp_path / "profile")
    assert ok is True
    assert status == "already_running"
    assert popen_called["v"] is False


def test_launch_success(monkeypatch, tmp_path: Path) -> None:
    """Успешный launch: Popen отработал, health probe OK."""
    monkeypatch.setattr(dc, "find_chrome_binary", lambda: "/bin/chrome")

    running_states = iter([False, True])  # до запуска False, потом True
    monkeypatch.setattr(dc, "is_dedicated_chrome_running", lambda port=9222: next(running_states))

    proc = MagicMock()
    proc.pid = 12345
    popen_args = {}

    def fake_popen(args, **kwargs):
        popen_args["args"] = args
        popen_args["kwargs"] = kwargs
        return proc

    monkeypatch.setattr(dc.subprocess, "Popen", fake_popen)
    # ускоряем test — sleep → no-op
    monkeypatch.setattr(dc.time, "sleep", lambda _s: None)

    profile_dir = tmp_path / "profile"
    ok, status = dc.launch_dedicated_chrome(profile_dir=profile_dir, port=9222)
    assert ok is True
    assert status == "launched"
    # аргументы содержат обязательные флаги
    args = popen_args["args"]
    assert args[0] == "/bin/chrome"
    assert f"--user-data-dir={profile_dir}" in args
    assert "--remote-debugging-port=9222" in args
    assert "--no-first-run" in args
    # detach флаги
    assert popen_args["kwargs"]["start_new_session"] is True
    assert profile_dir.exists()


def test_launch_timeout(monkeypatch, tmp_path: Path) -> None:
    """Popen отработал, но Chrome так и не ответил на health probe → launched_but_not_ready."""
    monkeypatch.setattr(dc, "find_chrome_binary", lambda: "/bin/chrome")
    monkeypatch.setattr(dc, "is_dedicated_chrome_running", lambda port=9222: False)

    proc = MagicMock()
    proc.pid = 999
    monkeypatch.setattr(dc.subprocess, "Popen", lambda *a, **kw: proc)
    monkeypatch.setattr(dc.time, "sleep", lambda _s: None)
    # Накатываем fake monotonic, чтобы deadline прошёл мгновенно
    ticks = iter([0.0, 11.0])  # start, после первой итерации уже > deadline
    monkeypatch.setattr(dc.time, "monotonic", lambda: next(ticks))

    ok, status = dc.launch_dedicated_chrome(profile_dir=tmp_path / "profile")
    assert ok is False
    assert status == "launched_but_not_ready"


def test_launch_popen_failure(monkeypatch, tmp_path: Path) -> None:
    """OSError при Popen → launch_failed."""
    monkeypatch.setattr(dc, "find_chrome_binary", lambda: "/bin/chrome")
    monkeypatch.setattr(dc, "is_dedicated_chrome_running", lambda port=9222: False)

    def boom(*args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(dc.subprocess, "Popen", boom)

    ok, status = dc.launch_dedicated_chrome(profile_dir=tmp_path / "profile")
    assert ok is False
    assert status.startswith("launch_failed:")


def test_launch_env_port_override(monkeypatch, tmp_path: Path) -> None:
    """DEDICATED_CHROME_PORT из env применяется если port не передан."""
    monkeypatch.setenv("DEDICATED_CHROME_PORT", "9333")
    monkeypatch.setattr(dc, "find_chrome_binary", lambda: "/bin/chrome")

    captured = {"port": None}

    def fake_running(port: int = 9222) -> bool:
        captured["port"] = port
        return True  # already_running short-circuit

    monkeypatch.setattr(dc, "is_dedicated_chrome_running", fake_running)
    ok, status = dc.launch_dedicated_chrome(profile_dir=tmp_path / "profile")
    assert ok is True
    assert status == "already_running"
    assert captured["port"] == 9333


# ---------------------------------------------------------------------------
# get_dedicated_chrome_cdp_url
# ---------------------------------------------------------------------------


def test_get_cdp_url_returns_ws(monkeypatch) -> None:
    """/json/version возвращает webSocketDebuggerUrl."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json = lambda: {"webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/abc"}
    monkeypatch.setattr(dc.httpx, "get", lambda url, timeout=2.0: resp)
    assert dc.get_dedicated_chrome_cdp_url() == "ws://127.0.0.1:9222/devtools/browser/abc"


def test_get_cdp_url_empty_on_failure(monkeypatch) -> None:
    """Ошибка запроса — пустая строка."""

    def boom(url: str, timeout: float = 2.0) -> MagicMock:
        raise httpx.RequestError("timeout")

    monkeypatch.setattr(dc.httpx, "get", boom)
    assert dc.get_dedicated_chrome_cdp_url() == ""


@pytest.mark.parametrize("status_code", [404, 500])
def test_get_cdp_url_empty_on_non_200(monkeypatch, status_code: int) -> None:
    """Non-200 статус — пустая строка."""
    resp = MagicMock()
    resp.status_code = status_code
    monkeypatch.setattr(dc.httpx, "get", lambda url, timeout=2.0: resp)
    assert dc.get_dedicated_chrome_cdp_url() == ""
