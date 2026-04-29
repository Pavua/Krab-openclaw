# -*- coding: utf-8 -*-
"""Unit-тесты VPN read-only tools (vpn_list_clients/get_config/panel_health/traffic_stats)."""

from __future__ import annotations

import asyncio
import json
import sqlite3
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
# Helpers: создать минимальный x-ui.db фикстурой
# ------------------------------------------------------------------


def _make_xui_db(path: Path) -> None:
    """Создаёт SQLite файл с минимальной x-ui схемой и тестовыми клиентами."""
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
            """
            CREATE TABLE inbounds (
                id integer PRIMARY KEY AUTOINCREMENT,
                enable numeric, remark text, port integer, protocol text,
                settings text, stream_settings text, listen text
            );
            CREATE TABLE client_traffics (
                id integer PRIMARY KEY AUTOINCREMENT,
                inbound_id integer, enable numeric, email text,
                up integer, down integer, expiry_time integer, total integer
            );
            """
        )
        settings = json.dumps(
            {
                "clients": [
                    {
                        "id": "uuid-alice",
                        "email": "alice",
                        "flow": "xtls-rprx-vision",
                    },
                    {
                        "id": "uuid-bob",
                        "email": "bob",
                    },
                ]
            }
        )
        stream = json.dumps(
            {
                "network": "tcp",
                "security": "reality",
                "realitySettings": {
                    "serverNames": ["example.com"],
                    "shortIds": ["abcd"],
                    "settings": {
                        "publicKey": "PUBKEY123",
                        "fingerprint": "chrome",
                        "spiderX": "/",
                    },
                },
            }
        )
        conn.execute(
            "INSERT INTO inbounds (enable, remark, port, protocol, settings, stream_settings, listen) "
            "VALUES (1, 'Reality-Main', 443, 'vless', ?, ?, '')",
            (settings, stream),
        )
        # alice — активный, с лимитом 10 GB и потреблением ~1 GB
        conn.execute(
            "INSERT INTO client_traffics (inbound_id, enable, email, up, down, expiry_time, total) "
            "VALUES (1, 1, 'alice', 500000000, 500000000, 1800000000, 10737418240)",
        )
        # bob — отключён, без лимита
        conn.execute(
            "INSERT INTO client_traffics (inbound_id, enable, email, up, down, expiry_time, total) "
            "VALUES (1, 0, 'bob', 0, 0, 0, 0)",
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def xui_db(tmp_path: Path) -> Path:
    db = tmp_path / "x-ui.db"
    _make_xui_db(db)
    return db


@pytest.fixture
def adapter(xui_db: Path) -> VPNToolsAdapter:
    a = VPNToolsAdapter(db_path=xui_db, public_host="vpn.test.com")
    return a


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


def test_list_clients_basic(adapter: VPNToolsAdapter) -> None:
    """list_clients возвращает только enabled клиентов по умолчанию + vless ссылку."""
    result = asyncio.run(adapter.list_clients())
    assert result["ok"] is True
    assert result["count"] == 1
    client = result["clients"][0]
    assert client["name"] == "alice"
    assert client["vless_link"].startswith("vless://uuid-alice@vpn.test.com:443?")
    assert "pbk=PUBKEY123" in client["vless_link"]
    assert "sni=example.com" in client["vless_link"]
    assert client["enabled"] is True
    assert client["traffic_used_gb"] == pytest.approx(0.931, abs=0.01)
    assert client["expires_at"] == 1800000000

    # include_disabled=True — bob тоже виден
    result_all = asyncio.run(adapter.list_clients(include_disabled=True))
    assert result_all["count"] == 2
    names = {c["name"] for c in result_all["clients"]}
    assert names == {"alice", "bob"}


def test_get_config_existing_and_missing(adapter: VPNToolsAdapter) -> None:
    """get_config: возвращает vless для существующего, ошибку для несуществующего."""
    ok = asyncio.run(adapter.get_config("alice"))
    assert ok["ok"] is True
    assert ok["name"] == "alice"
    assert ok["port"] == 443
    assert ok["protocol"] == "vless"
    assert ok["vless_link"].startswith("vless://uuid-alice@vpn.test.com:443?")
    assert "flow=xtls-rprx-vision" in ok["vless_link"]

    missing = asyncio.run(adapter.get_config("nobody"))
    assert missing["ok"] is False
    assert missing["error"] == "not_found"
    assert missing["client_name"] == "nobody"

    empty = asyncio.run(adapter.get_config(""))
    assert empty["ok"] is False
    assert empty["error"] == "empty_client_name"


def test_panel_health_mocked() -> None:
    """panel_health возвращает HTTP-статус через urlopen mock."""
    a = VPNToolsAdapter(db_path=Path("/nonexistent"), panel_url="https://panel.test/")

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

    # 401 → панель жива (auth required), но ok=True (200..500)
    assert result["ok"] is True
    assert result["http_status"] == 401
    assert result["cert_valid"] is True
    assert result["url"] == "https://panel.test/"
    assert isinstance(result["last_check"], int)


def test_traffic_stats_compute_percent(adapter: VPNToolsAdapter) -> None:
    """traffic_stats считает percent_used от total корректно."""
    result = asyncio.run(adapter.traffic_stats("alice"))
    assert result["ok"] is True
    assert result["client_name"] == "alice"
    assert result["up_bytes"] == 500000000
    assert result["down_bytes"] == 500000000
    assert result["limit"] == 10737418240
    # 1_000_000_000 / 10_737_418_240 ≈ 9.31%
    assert result["percent_used"] == pytest.approx(9.31, abs=0.05)
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


def test_missing_db_graceful(tmp_path: Path) -> None:
    """Отсутствие БД даёт graceful empty list / error без exceptions."""
    a = VPNToolsAdapter(db_path=tmp_path / "does_not_exist.db")

    listing = asyncio.run(a.list_clients())
    assert listing["ok"] is False
    assert listing["error"] == "db_unavailable"
    assert listing["clients"] == []

    cfg = asyncio.run(a.get_config("alice"))
    assert cfg["ok"] is False
    assert cfg["error"] == "db_unavailable"

    stats = asyncio.run(a.traffic_stats("alice"))
    assert stats["ok"] is False
    assert stats["error"] == "db_unavailable"


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
