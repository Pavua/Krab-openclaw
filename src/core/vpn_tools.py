# -*- coding: utf-8 -*-
"""
vpn_tools.py — read-only инструменты VPN x-ui панели для LLM Krab.

Экспортирует 4 native tools для интеграции с MCP manifest:
  vpn_list_clients  — все клиенты (через `list_clients.command`)
  vpn_get_config    — vless-конфиг клиента (через `get_client_config.command`)
  vpn_panel_health  — HTTP-health x-ui панели + cert (HTTP probe из Krab)
  vpn_traffic_stats — статистика трафика клиента (read-only sqlite)

Архитектура (после refactor):
- `vpn_list_clients` и `vpn_get_config` делегируют helper-скриптам в репозитории VPN
  (`/Users/pablito/Antigravity_AGENTS/VPN/*.command`). Эти скрипты используют
  `vpn_bot.build_vless_link()` как single source of truth для Reality params,
  что исключает drift между Krab и VPN-ботом.
- `vpn_panel_health` остаётся HTTP probe (не VPN-логика, а сетевая проверка
  со стороны Krab; helper-скрипт для этого не нужен).
- `vpn_traffic_stats` остаётся read-only sqlite read из `client_traffics`
  (helper-скрипты не отдают трафик; Reality params здесь не задействованы,
  поэтому drift-риска нет).

Конфиг через env:
  KRAB_VPN_HELPERS_DIR  — каталог с .command скриптами (default `/Users/pablito/Antigravity_AGENTS/VPN`)
  KRAB_VPN_DB_PATH      — путь к x-ui.db для traffic_stats (default `<HELPERS>/config/x-ui.db`)
  KRAB_VPN_PANEL_URL    — URL панели (default `https://localhost:54321/`)

Все методы возвращают JSON-friendly dict; при отсутствии скриптов/БД —
`{"ok": False, "error": "..."}` (graceful for tests).
"""

from __future__ import annotations

import json
import os
import sqlite3
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from structlog import get_logger

from .subprocess_env import clean_subprocess_env

logger = get_logger(__name__)

# Дефолтный каталог с helper-скриптами VPN-репозитория.
_DEFAULT_HELPERS_DIR = Path("/Users/pablito/Antigravity_AGENTS/VPN")

# Дефолтный URL панели для health-check.
_DEFAULT_PANEL_URL = "https://localhost:54321/"

# Имена helper-скриптов в каталоге KRAB_VPN_HELPERS_DIR.
_LIST_CLIENTS_SCRIPT = "list_clients.command"
_GET_CONFIG_SCRIPT = "get_client_config.command"

# Таймаут для subprocess-вызовов (sec).
_SUBPROCESS_TIMEOUT = 10


# ------------------------------------------------------------------
# Tool schemas (OpenAI/MCP-совместимый формат)
# ------------------------------------------------------------------

VPN_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "vpn_list_clients",
        "description": (
            "List all VPN clients via the VPN repo helper script. "
            "Returns email, uuid, inbound, port, enabled flags, and meta (TG/notes) if present."
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
            "Get the VLESS config link for a specific VPN client by name (email) "
            "via the VPN repo helper. Returns vless:// link, port, uuid, inbound."
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


def _bytes_to_gb(n: int | float | None) -> float:
    """Перевод байт → GB с округлением до 3 знаков."""
    if not n:
        return 0.0
    return round(float(n) / (1024**3), 3)


# ------------------------------------------------------------------
# VPNToolsAdapter
# ------------------------------------------------------------------


class VPNToolsAdapter:
    """Thin адаптер: helper-скрипты + HTTP probe + read-only sqlite для трафика.

    Singleton-pattern (см. `vpn_tools` ниже). storage_path и helpers_dir
    инжектируются через `configure_default_path()` в bootstrap или
    автоматически из env (KRAB_VPN_HELPERS_DIR / KRAB_VPN_DB_PATH).
    """

    def __init__(
        self,
        *,
        helpers_dir: Path | None = None,
        db_path: Path | None = None,
        panel_url: str | None = None,
    ) -> None:
        self._helpers_dir: Path | None = helpers_dir
        self._db_path: Path | None = db_path
        self._panel_url: str = panel_url or os.environ.get("KRAB_VPN_PANEL_URL", _DEFAULT_PANEL_URL)

    # ---- Configuration --------------------------------------------------

    def configure_default_path(
        self,
        helpers_dir: Path,
        *,
        db_path: Path | None = None,
        panel_url: str | None = None,
    ) -> None:
        """Bootstrap-инициализация singleton'а.

        helpers_dir обязателен (каталог с .command скриптами VPN-репо).
        db_path для traffic_stats; если не передан — `<helpers_dir>/config/x-ui.db`.
        """
        self._helpers_dir = helpers_dir
        if db_path is not None:
            self._db_path = db_path
        if panel_url:
            self._panel_url = panel_url
        logger.info(
            "vpn_tools_configured",
            helpers_dir=str(helpers_dir),
            db_path=str(self._db_path) if self._db_path else None,
            panel_url=self._panel_url,
            helpers_exist=helpers_dir.exists(),
        )

    def _resolve_helpers_dir(self) -> Path | None:
        """Лениво найти helpers_dir: explicit → env → fallback."""
        if self._helpers_dir is not None:
            return self._helpers_dir
        env_dir = os.environ.get("KRAB_VPN_HELPERS_DIR", "").strip()
        if env_dir:
            self._helpers_dir = Path(env_dir)
            return self._helpers_dir
        if _DEFAULT_HELPERS_DIR.exists():
            self._helpers_dir = _DEFAULT_HELPERS_DIR
            return self._helpers_dir
        return None

    def _resolve_db_path(self) -> Path | None:
        """Лениво найти путь к x-ui.db: explicit → env → <helpers_dir>/config/x-ui.db."""
        if self._db_path is not None:
            return self._db_path
        env_path = os.environ.get("KRAB_VPN_DB_PATH", "").strip()
        if env_path:
            self._db_path = Path(env_path)
            return self._db_path
        helpers = self._resolve_helpers_dir()
        if helpers is not None:
            candidate = helpers / "config" / "x-ui.db"
            if candidate.exists():
                self._db_path = candidate
                return self._db_path
        return None

    # ---- Internal: subprocess runner -----------------------------------

    def _run_helper(self, script_name: str, *args: str) -> dict[str, Any]:
        """Запустить helper-скрипт VPN-репо и распарсить JSON-stdout.

        Возвращает либо распарсенный dict (если helper отдал JSON), либо
        `{"ok": False, "error": "..."}` при сбое.
        """
        helpers = self._resolve_helpers_dir()
        if helpers is None:
            logger.warning("vpn_tools_helpers_dir_missing")
            return {"ok": False, "error": "helpers_dir_unavailable"}
        script = helpers / script_name
        if not script.exists():
            logger.warning(
                "vpn_tools_helper_missing",
                script=script_name,
                path=str(script),
            )
            return {"ok": False, "error": f"helper_missing:{script_name}"}

        cmd = [str(script), *args]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_SUBPROCESS_TIMEOUT,
                env=clean_subprocess_env(),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            logger.error(
                "vpn_tools_helper_timeout",
                script=script_name,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return {"ok": False, "error": "helper_timeout", "script": script_name}
        except (OSError, ValueError) as exc:
            logger.error(
                "vpn_tools_helper_spawn_failed",
                script=script_name,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return {
                "ok": False,
                "error": str(exc),
                "error_type": type(exc).__name__,
            }

        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        # Helper'ы при ошибке тоже могут писать JSON в stdout (см. emit_error
        # в get_client_config.command), поэтому пробуем распарсить даже при rc != 0.
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError as exc:
            logger.warning(
                "vpn_tools_helper_non_json",
                script=script_name,
                returncode=proc.returncode,
                stderr=stderr[:500],
                stdout_head=stdout[:200],
                error=str(exc),
            )
            return {
                "ok": False,
                "error": "helper_non_json",
                "script": script_name,
                "returncode": proc.returncode,
                "stderr": stderr.strip()[:500],
            }
        if not isinstance(parsed, dict):
            return {
                "ok": False,
                "error": "helper_unexpected_payload",
                "script": script_name,
            }
        return parsed

    # ---- Public tool API ------------------------------------------------

    async def list_clients(self, include_disabled: bool = False) -> dict[str, Any]:
        """vpn_list_clients: делегируем list_clients.command, фильтруем enabled."""
        payload = self._run_helper(_LIST_CLIENTS_SCRIPT)
        if not payload.get("ok"):
            # Помимо ошибок прокидываем пустой clients для обратной совместимости.
            payload.setdefault("clients", [])
            return payload

        raw_clients = payload.get("clients") or []
        filtered: list[dict[str, Any]] = []
        for item in raw_clients:
            if not isinstance(item, dict):
                continue
            inbound_enabled = bool(item.get("inbound_enabled", True))
            client_enabled = bool(item.get("client_enabled", True))
            enabled = inbound_enabled and client_enabled
            if not include_disabled and not enabled:
                continue
            filtered.append(
                {
                    "name": item.get("email", ""),
                    "email": item.get("email", ""),
                    "uuid": item.get("uuid", ""),
                    "inbound": item.get("inbound", ""),
                    "inbound_id": item.get("inbound_id"),
                    "port": item.get("port"),
                    "enabled": enabled,
                    "meta": item.get("meta"),
                }
            )
        return {"ok": True, "count": len(filtered), "clients": filtered}

    async def get_config(self, client_name: str) -> dict[str, Any]:
        """vpn_get_config: делегируем get_client_config.command --json."""
        if not client_name or not isinstance(client_name, str):
            return {"ok": False, "error": "empty_client_name"}

        payload = self._run_helper(_GET_CONFIG_SCRIPT, client_name, "--json")
        if not payload.get("ok"):
            # Helper отдаёт {"ok": false, "error": "client 'X' not found in x-ui.db"}.
            err = str(payload.get("error", "")).lower()
            if "not found" in err:
                return {
                    "ok": False,
                    "error": "not_found",
                    "client_name": client_name,
                }
            return payload

        return {
            "ok": True,
            "name": payload.get("email", client_name),
            "email": payload.get("email", client_name),
            "vless_link": payload.get("vless_link", ""),
            "port": payload.get("port"),
            "inbound": payload.get("inbound", ""),
            "uuid": payload.get("uuid", ""),
            "flow": payload.get("flow", ""),
            "meta": payload.get("meta"),
        }

    async def panel_health(self, url: str | None = None) -> dict[str, Any]:
        """vpn_panel_health: HTTP-проверка панели + cert valid (без helper'а)."""
        target = url or self._panel_url
        if not target:
            return {"ok": False, "error": "no_panel_url"}

        cert_valid = True
        http_status = 0
        last_check = int(time.time())
        ok = False
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
        """vpn_traffic_stats: трафик клиента + percent от лимита (read-only sqlite).

        Helper-скрипты не отдают трафик, поэтому здесь сохраняем прямой read.
        Reality params не задействованы → drift-риска нет.
        """
        if not client_name or not isinstance(client_name, str):
            return {"ok": False, "error": "empty_client_name"}

        path = self._resolve_db_path()
        if path is None or not path.exists():
            return {"ok": False, "error": "db_unavailable"}

        try:
            uri = f"file:{path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
        except sqlite3.Error as exc:
            logger.error(
                "vpn_traffic_stats_db_open_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}

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
            "used_gb": _bytes_to_gb(used_b),
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
