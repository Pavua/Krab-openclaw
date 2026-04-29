# -*- coding: utf-8 -*-
"""Unit-тесты VPN read-only tools (subprocess wrappers + sqlite traffic).

После refactor 29.04.2026:
- vpn_list_clients / vpn_get_config делегируют helper-скриптам VPN-репо;
  в тестах подменяем `subprocess.run` mock'ом, JSON-stdout эмулирует output скриптов.
- vpn_panel_health — HTTP probe через urllib.urlopen mock (без изменений).
- vpn_traffic_stats — read-only sqlite read; здесь делаем мини-БД фикстурой.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core.vpn_tools import (
    VPN_TOOL_SCHEMAS,
    VPNToolsAdapter,
    dispatch_vpn_tool,
    is_vpn_tool,
)

# ------------------------------------------------------------------
# Helpers: фейковый helpers_dir + фейковый x-ui.db для traffic_stats
# ------------------------------------------------------------------


def _make_fake_helpers(dir_: Path) -> None:
    """Создаёт пустые исполняемые файлы — нужны только для existence-check."""
    dir_.mkdir(parents=True, exist_ok=True)
    for name in ("list_clients.command", "get_client_config.command"):
        fp = dir_ / name
        fp.write_text("#!/bin/sh\nexit 0\n")
        fp.chmod(0o755)


def _make_traffic_db(path: Path) -> None:
    """Минимальная БД только с client_traffics (для traffic_stats)."""
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
            """
            CREATE TABLE client_traffics (
                id integer PRIMARY KEY AUTOINCREMENT,
                inbound_id integer, enable numeric, email text,
                up integer, down integer, expiry_time integer, total integer
            );
            """
        )
        # alice — активный, лимит 10 GB, потребление ~1 GB.
        conn.execute(
            "INSERT INTO client_traffics (inbound_id, enable, email, up, down, expiry_time, total) "
            "VALUES (1, 1, 'alice', 500000000, 500000000, 1800000000, 10737418240)",
        )
        # bob — отключён, без лимита.
        conn.execute(
            "INSERT INTO client_traffics (inbound_id, enable, email, up, down, expiry_time, total) "
            "VALUES (1, 0, 'bob', 0, 0, 0, 0)",
        )
        conn.commit()
    finally:
        conn.close()


# Эмулирует CompletedProcess от subprocess.run.
class _FakeProc:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@pytest.fixture
def helpers_dir(tmp_path: Path) -> Path:
    d = tmp_path / "VPN"
    _make_fake_helpers(d)
    return d


@pytest.fixture
def traffic_db(tmp_path: Path) -> Path:
    db = tmp_path / "x-ui.db"
    _make_traffic_db(db)
    return db


@pytest.fixture
def adapter(helpers_dir: Path, traffic_db: Path) -> VPNToolsAdapter:
    return VPNToolsAdapter(helpers_dir=helpers_dir, db_path=traffic_db)


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


def test_list_clients_basic(adapter: VPNToolsAdapter) -> None:
    """list_clients делегирует helper-скрипту и фильтрует disabled клиентов."""
    helper_payload = {
        "ok": True,
        "count": 2,
        "clients": [
            {
                "email": "alice",
                "uuid": "uuid-alice",
                "inbound_id": 1,
                "inbound": "Reality-Main",
                "port": 443,
                "inbound_enabled": True,
                "client_enabled": True,
                "meta": {"tg": "@alice", "notes": "main"},
            },
            {
                "email": "bob",
                "uuid": "uuid-bob",
                "inbound_id": 1,
                "inbound": "Reality-Main",
                "port": 443,
                "inbound_enabled": True,
                "client_enabled": False,
            },
        ],
    }
    fake = _FakeProc(stdout=json.dumps(helper_payload))

    with patch("src.core.vpn_tools.subprocess.run", return_value=fake) as mocked:
        result = asyncio.run(adapter.list_clients())

    assert result["ok"] is True
    assert result["count"] == 1
    only = result["clients"][0]
    assert only["name"] == "alice"
    assert only["uuid"] == "uuid-alice"
    assert only["port"] == 443
    assert only["enabled"] is True
    assert only["meta"] == {"tg": "@alice", "notes": "main"}

    # subprocess.run был вызван с правильным скриптом.
    call_args = mocked.call_args
    cmd = call_args.args[0]
    assert cmd[0].endswith("list_clients.command")
    assert call_args.kwargs.get("timeout") == 10
    assert "env" in call_args.kwargs

    # include_disabled=True — bob тоже виден.
    with patch("src.core.vpn_tools.subprocess.run", return_value=fake):
        result_all = asyncio.run(adapter.list_clients(include_disabled=True))
    assert result_all["count"] == 2
    names = {c["name"] for c in result_all["clients"]}
    assert names == {"alice", "bob"}


def test_get_config_existing_and_missing(adapter: VPNToolsAdapter) -> None:
    """get_config: разворачивает helper output (success / not_found / empty)."""
    success_payload = {
        "ok": True,
        "email": "alice",
        "vless_link": "vless://uuid-alice@vpn.test.com:443?type=tcp&security=reality&pbk=PUBKEY123#alice",
        "inbound": "Reality-Main",
        "port": 443,
        "uuid": "uuid-alice",
        "flow": "xtls-rprx-vision",
    }
    with patch(
        "src.core.vpn_tools.subprocess.run",
        return_value=_FakeProc(stdout=json.dumps(success_payload)),
    ) as mocked:
        ok = asyncio.run(adapter.get_config("alice"))

    assert ok["ok"] is True
    assert ok["name"] == "alice"
    assert ok["vless_link"].startswith("vless://uuid-alice@vpn.test.com:443?")
    assert ok["flow"] == "xtls-rprx-vision"
    cmd = mocked.call_args.args[0]
    assert cmd[0].endswith("get_client_config.command")
    assert cmd[1:] == ["alice", "--json"]

    # not found — helper отдаёт ok=false с явной формулировкой.
    notfound_payload = {"ok": False, "error": "client 'nobody' not found in x-ui.db"}
    with patch(
        "src.core.vpn_tools.subprocess.run",
        return_value=_FakeProc(stdout=json.dumps(notfound_payload), returncode=2),
    ):
        missing = asyncio.run(adapter.get_config("nobody"))
    assert missing["ok"] is False
    assert missing["error"] == "not_found"
    assert missing["client_name"] == "nobody"

    # пустое имя — даже не доходим до helper'а.
    empty = asyncio.run(adapter.get_config(""))
    assert empty["ok"] is False
    assert empty["error"] == "empty_client_name"


def test_panel_health_mocked() -> None:
    """panel_health возвращает HTTP-статус через urlopen mock."""
    a = VPNToolsAdapter(panel_url="https://panel.test/")

    class _FakeResp:
        status = 401

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def getcode(self):
            return 401

    with patch("urllib.request.urlopen", return_value=_FakeResp()):
        result = asyncio.run(a.panel_health())

    # 401 → панель жива (auth required), но ok=True (200..500).
    assert result["ok"] is True
    assert result["http_status"] == 401
    assert result["cert_valid"] is True
    assert result["url"] == "https://panel.test/"
    assert isinstance(result["last_check"], int)


def test_traffic_stats_compute_percent(adapter: VPNToolsAdapter) -> None:
    """traffic_stats остаётся sqlite read и считает percent_used корректно."""
    result = asyncio.run(adapter.traffic_stats("alice"))
    assert result["ok"] is True
    assert result["client_name"] == "alice"
    assert result["up_bytes"] == 500000000
    assert result["down_bytes"] == 500000000
    assert result["limit"] == 10737418240
    # 1_000_000_000 / 10_737_418_240 ≈ 9.31%
    assert result["percent_used"] == pytest.approx(9.31, abs=0.05)
    assert result["used_gb"] == pytest.approx(0.931, abs=0.01)
    assert result["enabled"] is True

    # bob: limit=0 (безлимит) → percent=0.0
    bob = asyncio.run(adapter.traffic_stats("bob"))
    assert bob["ok"] is True
    assert bob["limit"] == 0
    assert bob["percent_used"] == 0.0
    assert bob["enabled"] is False

    # missing
    missing = asyncio.run(adapter.traffic_stats("ghost"))
    assert missing["ok"] is False
    assert missing["error"] == "not_found"


def test_missing_helpers_graceful(tmp_path: Path) -> None:
    """Отсутствие helpers_dir и БД даёт graceful error без exceptions."""
    a = VPNToolsAdapter(
        helpers_dir=tmp_path / "no_such_dir",
        db_path=tmp_path / "no_such_db.db",
    )

    # helpers_dir есть, но скриптов нет — другая ветка graceful.
    empty_dir = tmp_path / "empty_helpers"
    empty_dir.mkdir()
    a2 = VPNToolsAdapter(helpers_dir=empty_dir, db_path=tmp_path / "no_such_db.db")

    listing = asyncio.run(a.list_clients())
    assert listing["ok"] is False
    assert "helpers_dir" in listing["error"] or "helper_missing" in listing["error"]
    assert listing["clients"] == []

    listing2 = asyncio.run(a2.list_clients())
    assert listing2["ok"] is False
    assert "helper_missing" in listing2["error"]

    cfg = asyncio.run(a.get_config("alice"))
    assert cfg["ok"] is False
    assert "helpers_dir" in cfg["error"] or "helper_missing" in cfg["error"]

    stats = asyncio.run(a.traffic_stats("alice"))
    assert stats["ok"] is False
    assert stats["error"] == "db_unavailable"

    # Subprocess timeout → graceful.
    helpers_real = tmp_path / "helpers_real"
    _make_fake_helpers(helpers_real)
    a3 = VPNToolsAdapter(helpers_dir=helpers_real)
    timeout_exc = subprocess.TimeoutExpired(cmd="list_clients.command", timeout=10)
    with patch("src.core.vpn_tools.subprocess.run", side_effect=timeout_exc):
        timed_out = asyncio.run(a3.list_clients())
    assert timed_out["ok"] is False
    assert timed_out["error"] == "helper_timeout"


def test_manifest_entries_valid() -> None:
    """Schema entries валидны: имена, описания, JSON-Schema input."""
    names = {s["name"] for s in VPN_TOOL_SCHEMAS}
    assert names == {
        "vpn_list_clients",
        "vpn_get_config",
        "vpn_panel_health",
        "vpn_traffic_stats",
    }
    for schema in VPN_TOOL_SCHEMAS:
        assert isinstance(schema["description"], str) and schema["description"]
        assert schema["inputSchema"]["type"] == "object"
        assert "properties" in schema["inputSchema"]
        assert "required" in schema["inputSchema"]

    # Dispatcher: known + unknown
    assert is_vpn_tool("vpn_list_clients") is True
    assert is_vpn_tool("not_a_tool") is False

    unknown = asyncio.run(dispatch_vpn_tool("nope", {}))
    assert unknown["ok"] is False
    assert "unknown_tool" in unknown["error"]
