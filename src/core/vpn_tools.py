# -*- coding: utf-8 -*-
"""
vpn_tools.py — read-only инструменты VPN x-ui панели для LLM Krab.

Экспортирует 4 native tools для интеграции с MCP manifest:
  vpn_list_clients  — все клиенты с vless-ссылкой и трафиком
  vpn_get_config    — vless-конфиг конкретного клиента
  vpn_panel_health  — HTTP-health x-ui панели + cert
  vpn_traffic_stats — статистика трафика клиента (up/down/limit/percent)

Архитектура:
- `VPNToolsAdapter` открывает SQLite в read-only режиме (`mode=ro`).
- Singleton `vpn_tools` лениво подбирает путь к БД через
  `configure_default_path()` или env `KRAB_VPN_DB_PATH`.
- Все методы возвращают JSON-friendly dict; при отсутствии БД —
  `{"ok": False, "error": "db_unavailable"}` (graceful for tests).

Schema x-ui.db (используется):
  inbounds(id, enable, remark, port, protocol, settings, stream_settings, listen)
  client_traffics(email, enable, up, down, total, expiry_time)

vless-link собирается из inbounds.settings.clients[].id (uuid)
+ stream_settings.realitySettings (publicKey, serverNames, shortIds).
"""

from __future__ import annotations

import json
import os
import sqlite3
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from structlog import get_logger

logger = get_logger(__name__)

# Дефолтный путь к x-ui.db (используется fallback'ом при отсутствии env).
_DEFAULT_DB_PATH = Path("/Users/pablito/Antigravity_AGENTS/VPN/config/x-ui.db")

# Дефолтный публичный хост — берётся из env, иначе placeholder.
_DEFAULT_PUBLIC_HOST = "vpn.example.com"

# Дефолтный URL панели для health-check.
_DEFAULT_PANEL_URL = "https://localhost:54321/"


# ------------------------------------------------------------------
# Tool schemas (OpenAI/MCP-совместимый формат)
# ------------------------------------------------------------------

VPN_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "vpn_list_clients",
        "description": (
            "List all VPN clients from the x-ui panel database (read-only). "
            "Returns name, vless link, traffic used (GB), expiry timestamp, enabled flag."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_disabled": {
                    "type": "boolean",
                    "description": "Include disabled clients in the result.",
                    "default": False,
                },
            },
            "required": [],
        },
    },
    {
        "name": "vpn_get_config",
        "description": (
            "Get the VLESS config link for a specific VPN client by name (email). "
            "Returns vless:// link, port, traffic stats."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "client_name": {
                    "type": "string",
                    "description": "Client name (matches client.email in x-ui).",
                },
            },
            "required": ["client_name"],
        },
    },
    {
        "name": "vpn_panel_health",
        "description": (
            "HTTP health-check of the x-ui VPN panel. Returns ok/http_status/cert_valid/last_check."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Override panel URL (default from env).",
                },
            },
            "required": [],
        },
    },
    {
        "name": "vpn_traffic_stats",
        "description": (
            "Traffic statistics for a VPN client: up/down bytes, total limit, percent used."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "client_name": {
                    "type": "string",
                    "description": "Client name (matches client.email in x-ui).",
                },
            },
            "required": ["client_name"],
        },
    },
]


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _build_vless_link(inbound_row: dict[str, Any], client: dict[str, Any], public_host: str) -> str:
    """Собрать vless:// link для Reality-инбаунда (см. vpn_bot.build_vless_link).

    inbound_row: распарсенная строка inbounds (port + распарсенные stream/settings).
    client: один элемент settings.clients (содержит id, flow, email).
    """
    stream = inbound_row.get("stream", {}) or {}
    rs = stream.get("realitySettings", {}) or {}

    uuid_ = client.get("id", "")
    flow = client.get("flow", "")
    sni_list = rs.get("serverNames") or ["localhost"]
    sni = sni_list[0] if sni_list else "localhost"
    pubkey = (rs.get("settings", {}) or {}).get("publicKey") or rs.get("publicKey") or ""
    fp = (rs.get("settings", {}) or {}).get("fingerprint", "chrome") or "chrome"
    spider = (rs.get("settings", {}) or {}).get("spiderX", "/") or "/"
    short_ids = client.get("shortIds") or rs.get("shortIds") or [""]
    sid = short_ids[0] if short_ids else ""

    params = [
        ("type", stream.get("network", "tcp")),
        ("security", stream.get("security", "reality")),
        ("pbk", pubkey),
        ("fp", fp),
        ("sni", sni),
        ("sid", sid),
        ("spx", spider),
    ]
    if flow:
        params.append(("flow", flow))

    qs = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in params if v)
    remark = client.get("email", inbound_row.get("remark", "vpn"))
    return f"vless://{uuid_}@{public_host}:{inbound_row['port']}?{qs}#{urllib.parse.quote(remark)}"


def _bytes_to_gb(n: int | float | None) -> float:
    """Перевод байт → GB с округлением до 3 знаков."""
    if not n:
        return 0.0
    return round(float(n) / (1024**3), 3)


# ------------------------------------------------------------------
# VPNToolsAdapter
# ------------------------------------------------------------------


class VPNToolsAdapter:
    """Read-only адаптер к x-ui.db + HTTP health-check панели.

    Singleton-pattern (см. `vpn_tools` ниже). storage_path и public_host
    инжектируются через `configure_default_path()` в bootstrap или
    автоматически из env (KRAB_VPN_DB_PATH / VPN_PUBLIC_HOST).
    """

    def __init__(
        self,
        *,
        db_path: Path | None = None,
        public_host: str | None = None,
        panel_url: str | None = None,
    ) -> None:
        self._db_path: Path | None = db_path
        self._public_host: str = public_host or os.environ.get(
            "VPN_PUBLIC_HOST", _DEFAULT_PUBLIC_HOST
        )
        self._panel_url: str = panel_url or os.environ.get("KRAB_VPN_PANEL_URL", _DEFAULT_PANEL_URL)

    # ---- Configuration --------------------------------------------------

    def configure_default_path(
        self,
        db_path: Path,
        *,
        public_host: str | None = None,
        panel_url: str | None = None,
    ) -> None:
        """Bootstrap-инициализация singleton'а."""
        self._db_path = db_path
        if public_host:
            self._public_host = public_host
        if panel_url:
            self._panel_url = panel_url
        logger.info(
            "vpn_tools_configured",
            db_path=str(db_path),
            public_host=self._public_host,
            db_exists=db_path.exists(),
        )

    def _resolve_db_path(self) -> Path | None:
        """Лениво найти путь: explicit → env → fallback."""
        if self._db_path is not None:
            return self._db_path
        env_path = os.environ.get("KRAB_VPN_DB_PATH", "").strip()
        if env_path:
            self._db_path = Path(env_path)
            return self._db_path
        if _DEFAULT_DB_PATH.exists():
            self._db_path = _DEFAULT_DB_PATH
            return self._db_path
        return None

    def _open_ro(self) -> sqlite3.Connection | None:
        """Открыть sqlite в read-only режиме. None если файла нет."""
        path = self._resolve_db_path()
        if path is None or not path.exists():
            logger.warning("vpn_tools_db_missing", path=str(path) if path else None)
            return None
        try:
            uri = f"file:{path}?mode=ro"
            return sqlite3.connect(uri, uri=True)
        except sqlite3.Error as exc:
            logger.error(
                "vpn_tools_db_open_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None

    # ---- Internal: enumerate inbounds + clients -------------------------

    def _enumerate_clients(self, include_disabled: bool = False) -> list[dict[str, Any]]:
        """Прошерстить inbounds, развернуть все client-объекты с трафиком.

        Возвращает список dict: name, inbound_id, port, protocol, enabled,
        client (raw json), inbound (parsed row), traffic (row из client_traffics).
        """
        conn = self._open_ro()
        if conn is None:
            return []
        try:
            inbounds = conn.execute(
                "SELECT id, enable, remark, port, protocol, settings, stream_settings, listen "
                "FROM inbounds"
            ).fetchall()
            traffics_raw = conn.execute(
                "SELECT email, enable, up, down, total, expiry_time FROM client_traffics"
            ).fetchall()
        finally:
            conn.close()

        # Индекс трафика по email — O(1) lookup при сборке.
        traffics_by_email: dict[str, dict[str, Any]] = {}
        for email, t_enable, up, down, total, expiry in traffics_raw:
            traffics_by_email[email] = {
                "email": email,
                "enable": bool(t_enable),
                "up": int(up or 0),
                "down": int(down or 0),
                "total": int(total or 0),
                "expiry_time": int(expiry or 0),
            }

        result: list[dict[str, Any]] = []
        for inb in inbounds:
            inb_id, inb_enable, remark, port, protocol, settings_json, stream_json, listen = inb
            if not include_disabled and not inb_enable:
                continue
            try:
                settings = json.loads(settings_json or "{}")
                stream = json.loads(stream_json or "{}")
            except json.JSONDecodeError as exc:
                logger.warning(
                    "vpn_tools_inbound_json_invalid",
                    inbound_id=inb_id,
                    error=str(exc),
                )
                continue

            inbound_row = {
                "id": inb_id,
                "enable": bool(inb_enable),
                "remark": remark,
                "port": port,
                "protocol": protocol,
                "listen": listen,
                "stream": stream,
            }
            for client in settings.get("clients", []) or []:
                email = client.get("email", "")
                traffic = traffics_by_email.get(email, {})
                if not include_disabled and not traffic.get("enable", True):
                    continue
                result.append(
                    {
                        "name": email,
                        "inbound_id": inb_id,
                        "port": port,
                        "protocol": protocol,
                        "enabled": bool(inb_enable) and traffic.get("enable", True),
                        "client": client,
                        "inbound": inbound_row,
                        "traffic": traffic,
                    }
                )
        return result

    # ---- Public tool API ------------------------------------------------

    async def list_clients(self, include_disabled: bool = False) -> dict[str, Any]:
        """vpn_list_clients: все клиенты с vless-ссылкой и трафиком."""
        path = self._resolve_db_path()
        if path is None or not path.exists():
            logger.warning("vpn_list_clients_db_unavailable")
            return {"ok": False, "error": "db_unavailable", "clients": []}

        try:
            entries = self._enumerate_clients(include_disabled=include_disabled)
        except sqlite3.Error as exc:
            logger.error(
                "vpn_list_clients_db_error",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}

        clients: list[dict[str, Any]] = []
        for entry in entries:
            try:
                vless = _build_vless_link(entry["inbound"], entry["client"], self._public_host)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "vpn_list_clients_link_failed",
                    name=entry["name"],
                    error=str(exc),
                )
                vless = ""
            traffic = entry["traffic"]
            traffic_used = int(traffic.get("up", 0)) + int(traffic.get("down", 0))
            clients.append(
                {
                    "name": entry["name"],
                    "vless_link": vless,
                    "traffic_used_gb": _bytes_to_gb(traffic_used),
                    "expires_at": traffic.get("expiry_time", 0),
                    "enabled": entry["enabled"],
                }
            )
        return {"ok": True, "count": len(clients), "clients": clients}

    async def get_config(self, client_name: str) -> dict[str, Any]:
        """vpn_get_config: vless-конфиг по имени клиента."""
        if not client_name or not isinstance(client_name, str):
            return {"ok": False, "error": "empty_client_name"}

        path = self._resolve_db_path()
        if path is None or not path.exists():
            return {"ok": False, "error": "db_unavailable"}

        try:
            entries = self._enumerate_clients(include_disabled=True)
        except sqlite3.Error as exc:
            return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}

        for entry in entries:
            if entry["name"] == client_name:
                try:
                    vless = _build_vless_link(entry["inbound"], entry["client"], self._public_host)
                except Exception as exc:  # noqa: BLE001
                    return {
                        "ok": False,
                        "error": f"link_build_failed: {exc}",
                        "error_type": type(exc).__name__,
                    }
                traffic = entry["traffic"]
                return {
                    "ok": True,
                    "name": entry["name"],
                    "vless_link": vless,
                    "port": entry["port"],
                    "protocol": entry["protocol"],
                    "enabled": entry["enabled"],
                    "expires_at": traffic.get("expiry_time", 0),
                    "traffic_used_gb": _bytes_to_gb(
                        int(traffic.get("up", 0)) + int(traffic.get("down", 0))
                    ),
                }
        return {"ok": False, "error": "not_found", "client_name": client_name}

    async def panel_health(self, url: str | None = None) -> dict[str, Any]:
        """vpn_panel_health: HTTP-проверка панели + cert valid."""
        target = url or self._panel_url
        if not target:
            return {"ok": False, "error": "no_panel_url"}

        cert_valid = True
        http_status = 0
        last_check = int(time.time())
        try:
            ctx = ssl.create_default_context()
            req = urllib.request.Request(target, method="HEAD")
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                http_status = getattr(resp, "status", 0) or resp.getcode() or 0
            ok = 200 <= http_status < 500  # 4xx (auth) тоже признак "панель жива"
        except urllib.error.HTTPError as exc:
            http_status = exc.code
            ok = 200 <= exc.code < 500
        except ssl.SSLError as exc:
            cert_valid = False
            ok = False
            logger.warning("vpn_panel_health_ssl_error", error=str(exc))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            ok = False
            logger.warning(
                "vpn_panel_health_failed",
                target=target,
                error=str(exc),
                error_type=type(exc).__name__,
            )

        return {
            "ok": ok,
            "http_status": http_status,
            "cert_valid": cert_valid,
            "last_check": last_check,
            "url": target,
        }

    async def traffic_stats(self, client_name: str) -> dict[str, Any]:
        """vpn_traffic_stats: трафик клиента + percent от лимита."""
        if not client_name or not isinstance(client_name, str):
            return {"ok": False, "error": "empty_client_name"}

        path = self._resolve_db_path()
        if path is None or not path.exists():
            return {"ok": False, "error": "db_unavailable"}

        conn = self._open_ro()
        if conn is None:
            return {"ok": False, "error": "db_unavailable"}
        try:
            row = conn.execute(
                "SELECT email, up, down, total, enable, expiry_time "
                "FROM client_traffics WHERE email = ?",
                (client_name,),
            ).fetchone()
        except sqlite3.Error as exc:
            return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}
        finally:
            conn.close()

        if row is None:
            return {"ok": False, "error": "not_found", "client_name": client_name}

        _, up, down, total, enable, expiry = row
        up_b = int(up or 0)
        down_b = int(down or 0)
        limit_b = int(total or 0)
        used_b = up_b + down_b
        # total=0 в x-ui означает "безлимит" — percent тогда 0.
        percent = round(used_b / limit_b * 100, 2) if limit_b > 0 else 0.0
        return {
            "ok": True,
            "client_name": client_name,
            "up_bytes": up_b,
            "down_bytes": down_b,
            "limit": limit_b,
            "percent_used": percent,
            "enabled": bool(enable),
            "expires_at": int(expiry or 0),
        }


# ------------------------------------------------------------------
# Singleton + dispatcher
# ------------------------------------------------------------------

vpn_tools = VPNToolsAdapter()


_TOOL_HANDLERS = {
    "vpn_list_clients": lambda args: vpn_tools.list_clients(
        include_disabled=bool(args.get("include_disabled", False)),
    ),
    "vpn_get_config": lambda args: vpn_tools.get_config(
        client_name=args.get("client_name", ""),
    ),
    "vpn_panel_health": lambda args: vpn_tools.panel_health(
        url=args.get("url"),
    ),
    "vpn_traffic_stats": lambda args: vpn_tools.traffic_stats(
        client_name=args.get("client_name", ""),
    ),
}


async def dispatch_vpn_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Вызов VPN-tool по имени. JSON-friendly dict в ответе."""
    handler = _TOOL_HANDLERS.get(tool_name)
    if handler is None:
        return {"ok": False, "error": f"unknown_tool:{tool_name}"}
    if not isinstance(arguments, dict):
        arguments = {}
    return await handler(arguments)


def is_vpn_tool(tool_name: str) -> bool:
    """True если tool_name принадлежит VPN-набору."""
    return tool_name in _TOOL_HANDLERS
