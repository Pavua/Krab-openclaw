# -*- coding: utf-8 -*-
"""
Network admin router — Wave 179 (Session 48).

Owner-side панель ``/admin/network`` + JSON API для здоровья Telegram
MTProto-соединения. Объединяет:
  • Pyrogram session info (name, DC, user_id, auth_date, is_connected),
  • Wave 63-A GetState pts probe snapshot (server_pts, dispatch staleness),
  • Wave 142 Pyrogram disconnect counter (по сессиям),
  • Wave 37-A heartbeat timestamps + split-brain detection,
  • Wave 121 FloodWait active gauge + Wave 110-ish counter history,
  • Live ping (``GetState`` invoke с замером RTT),
  • DNS-проверку для core.telegram.org / t.me.

Endpoints (READY):
- GET  /api/admin/network/status     — JSON snapshot всех сигналов.
- POST /api/admin/network/ping       — write: дёргает GetState, измеряет RTT.
- POST /api/admin/network/dns_check  — read-only DNS-резолв (heavy = в thread).
- GET  /admin/network                 — HTML страница, polling 10s.

Безопасность:
- Owner panel биндится на 127.0.0.1, read-only endpoint без auth.
- POST ping требует ``ctx.assert_write_access`` (триггерит сетевой вызов).
- POST dns_check — read-only по эффекту, но классифицирован как write для
  безопасности от внешних триггеров (DNS = по сети).

Match style of ``cron_admin_router.py`` + ``db_admin_router.py``.
"""

from __future__ import annotations

import asyncio
import socket
import time
from typing import Any

from fastapi import APIRouter, Header, Query
from fastapi.responses import HTMLResponse

from src.core.logger import get_logger

from ._context import RouterContext

_logger = get_logger(__name__)

# ── Конфигурация ────────────────────────────────────────────────────────────

# Список DNS-имён, проверяемых в /api/admin/network/dns_check.
_DNS_CHECK_HOSTS: tuple[str, ...] = (
    "core.telegram.org",
    "t.me",
    "api.telegram.org",
    "149.154.167.50",  # Telegram DC2 IP — sanity (вернётся как есть).
)

# Таймаут отдельной DNS-резолюции.
_DNS_RESOLVE_TIMEOUT_SEC: float = 3.0

# Таймаут MTProto-ping (GetState invoke).
_PING_TIMEOUT_SEC: float = 8.0

# Окно по умолчанию для "disconnects за 24h" (информационно — мы храним
# только running total). Возвращаем оба числа, UI решает что показать.
_DISCONNECT_WINDOW_HOURS: int = 24

# Telegram public DC адреса (для outbound-теста). Используем DC2 как
# дефолтный (Telegram routing main).
_TG_DC_HOST: str = "149.154.167.50"
_TG_DC_PORT: int = 443
_TG_OUTBOUND_TIMEOUT_SEC: float = 4.0


# ── Helpers ─────────────────────────────────────────────────────────────────


def _safe_getattr(obj: Any, attr: str, default: Any = None) -> Any:
    """Получает атрибут не валясь на любых ошибках (None, AttributeError, и т.п.)."""
    try:
        return getattr(obj, attr, default)
    except Exception:  # noqa: BLE001
        return default


async def _safe_await(coro_or_value: Any, *, timeout: float = 5.0) -> Any:
    """Если значение coroutine — ждём с таймаутом; иначе возвращаем как есть."""
    if asyncio.iscoroutine(coro_or_value):
        try:
            return await asyncio.wait_for(coro_or_value, timeout=timeout)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            return None
    return coro_or_value


def _format_age_seconds(ts: float | None) -> float | None:
    """Возвращает age в секундах от ts до now, или None если ts невалиден."""
    if ts is None or ts <= 0:
        return None
    try:
        age = time.time() - float(ts)
        if age < 0:
            return 0.0
        return round(age, 1)
    except (TypeError, ValueError):
        return None


def _iso_or_none(ts: float | None) -> str | None:
    """ISO-формат timestamp в UTC, или None."""
    if ts is None or ts <= 0:
        return None
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(ts)))
    except (TypeError, ValueError):
        return None


# ── Session info (Pyrogram storage) ─────────────────────────────────────────


async def _collect_session_info(userbot: Any) -> dict[str, Any]:
    """Достаёт инфо о Pyrogram session: name, DC, user_id, auth_date,
    is_connected. Best-effort — любая ошибка → поле None.
    """
    info: dict[str, Any] = {
        "session_name": None,
        "dc_id": None,
        "user_id": None,
        "is_bot": None,
        "test_mode": None,
        "auth_date_ts": None,
        "auth_date_iso": None,
        "is_connected": None,
        "client_present": False,
    }
    if userbot is None:
        return info

    client = _safe_getattr(userbot, "client", None) or _safe_getattr(userbot, "app", None)
    if client is None:
        return info
    info["client_present"] = True

    # session_name — из самого клиента (Pyrogram сохраняет name в Client).
    info["session_name"] = _safe_getattr(client, "name", None) or _safe_getattr(
        client, "session_name", None
    )

    # is_connected — pyrofork держит .is_connected на клиенте.
    is_connected = _safe_getattr(client, "is_connected", None)
    if is_connected is None:
        is_connected = _safe_getattr(client, "is_initialized", None)
    info["is_connected"] = bool(is_connected) if is_connected is not None else None

    # storage метаданные (dc_id/date/user_id/...).
    # Pyrofork SQLiteStorage держит атрибуты как async getter-функции:
    # ``await storage.dc_id()`` возвращает значение. У моков может быть
    # sync-атрибут — fallback на прямой доступ.
    storage = _safe_getattr(client, "storage", None)
    if storage is not None:
        for src_attr, dst_key in (
            ("dc_id", "dc_id"),
            ("user_id", "user_id"),
            ("is_bot", "is_bot"),
            ("test_mode", "test_mode"),
            ("date", "auth_date_ts"),
        ):
            raw = _safe_getattr(storage, src_attr, None)
            value: Any = None
            if callable(raw):
                try:
                    called = raw()
                except Exception:  # noqa: BLE001
                    called = None
                value = await _safe_await(called, timeout=2.0)
            else:
                value = raw
            info[dst_key] = value

    # Conversion: auth_date_ts → ISO.
    auth_ts = info.get("auth_date_ts")
    if isinstance(auth_ts, (int, float)) and auth_ts > 0:
        info["auth_date_iso"] = _iso_or_none(float(auth_ts))

    return info


# ── GetState pts probe snapshot ─────────────────────────────────────────────


def _collect_get_state_snapshot(userbot: Any) -> dict[str, Any]:
    """Wave 63-A: возвращает текущее состояние pts probe без нового invoke.

    Просто читаем последние сохранённые значения с owner. Если нет
    атрибутов — возвращаем явные None'ы.
    """
    snapshot: dict[str, Any] = {
        "last_server_pts": None,
        "last_seen_update_id": None,
        "last_dispatcher_tick_ts": None,
        "last_dispatcher_tick_age_sec": None,
        "dispatcher_tick_count": None,
    }
    if userbot is None:
        return snapshot

    last_pts = _safe_getattr(userbot, "_last_server_pts", None)
    if last_pts is not None:
        try:
            snapshot["last_server_pts"] = int(last_pts)
        except (TypeError, ValueError):
            snapshot["last_server_pts"] = None

    last_uid = _safe_getattr(userbot, "_last_seen_update_id", None)
    if last_uid is not None:
        try:
            snapshot["last_seen_update_id"] = int(last_uid)
        except (TypeError, ValueError):
            snapshot["last_seen_update_id"] = None

    tick_ts = _safe_getattr(userbot, "_last_dispatcher_tick_ts", None)
    if isinstance(tick_ts, (int, float)) and tick_ts > 0:
        snapshot["last_dispatcher_tick_ts"] = float(tick_ts)
        snapshot["last_dispatcher_tick_age_sec"] = _format_age_seconds(float(tick_ts))

    tick_count = _safe_getattr(userbot, "_dispatcher_tick_count", None)
    if tick_count is not None:
        try:
            snapshot["dispatcher_tick_count"] = int(tick_count)
        except (TypeError, ValueError):
            snapshot["dispatcher_tick_count"] = None

    return snapshot


# ── Heartbeat snapshot ──────────────────────────────────────────────────────


def _collect_heartbeat_snapshot(userbot: Any) -> dict[str, Any]:
    """Wave 37-A: возвращает heartbeat timestamps + split-brain detection.

    split_brain_detected = есть свежий heartbeat (_last_heartbeat_ok_ts
    < 5min) НО давно нет событий (_last_telegram_event_ts > 5min).
    """
    snapshot: dict[str, Any] = {
        "last_telegram_event_ts": None,
        "last_telegram_event_age_sec": None,
        "last_heartbeat_ok_ts": None,
        "last_heartbeat_ok_age_sec": None,
        "split_brain_detected": False,
    }
    if userbot is None:
        return snapshot

    event_ts = _safe_getattr(userbot, "_last_telegram_event_ts", None)
    if isinstance(event_ts, (int, float)) and event_ts > 0:
        snapshot["last_telegram_event_ts"] = float(event_ts)
        snapshot["last_telegram_event_age_sec"] = _format_age_seconds(float(event_ts))

    hb_ts = _safe_getattr(userbot, "_last_heartbeat_ok_ts", None)
    if isinstance(hb_ts, (int, float)) and hb_ts > 0:
        snapshot["last_heartbeat_ok_ts"] = float(hb_ts)
        snapshot["last_heartbeat_ok_age_sec"] = _format_age_seconds(float(hb_ts))

    event_age = snapshot["last_telegram_event_age_sec"]
    hb_age = snapshot["last_heartbeat_ok_age_sec"]
    if (
        isinstance(event_age, (int, float))
        and isinstance(hb_age, (int, float))
        and hb_age < 300.0
        and event_age > 300.0
    ):
        snapshot["split_brain_detected"] = True

    return snapshot


# ── Disconnects counter (Wave 142) ──────────────────────────────────────────


def _collect_disconnects_snapshot() -> dict[str, Any]:
    """Wave 142: read-only snapshot per-session disconnect counter."""
    snapshot: dict[str, Any] = {
        "total": 0,
        "by_session": {},
        "session_label": None,
        "window_hours": _DISCONNECT_WINDOW_HOURS,
    }
    try:
        from src.core.metrics.pyrogram_reconnect import (  # noqa: PLC0415
            _PYROGRAM_DISCONNECTS_COUNTER,
            get_pyrogram_session_label,
        )

        snapshot["by_session"] = dict(_PYROGRAM_DISCONNECTS_COUNTER)
        snapshot["total"] = int(sum(_PYROGRAM_DISCONNECTS_COUNTER.values()))
        try:
            snapshot["session_label"] = get_pyrogram_session_label()
        except Exception:  # noqa: BLE001
            snapshot["session_label"] = None
    except ImportError:
        # Метрика опциональна (если модуль не загружен).
        pass
    return snapshot


# ── FloodWait snapshot (Wave 121) ───────────────────────────────────────────


def _collect_floodwait_snapshot() -> dict[str, Any]:
    """Wave 121: active gauge + counter history.

    Refresh expired deadlines + читаем счётчик из process metrics.
    """
    snapshot: dict[str, Any] = {
        "active": {},
        "active_count": 0,
        "counter_by_caller": {},
        "counter_total": 0,
    }
    # Active gauge (Wave 121).
    try:
        from src.core.metrics.telegram_rate import (  # noqa: PLC0415
            refresh_telegram_rate_limited_active,
        )

        active = refresh_telegram_rate_limited_active()
        snapshot["active"] = {k: v for k, v in active.items() if v == 1}
        snapshot["active_count"] = int(sum(1 for v in active.values() if v == 1))
    except ImportError:
        pass

    # Historical counter.
    try:
        from src.core.metrics.process import (  # noqa: PLC0415
            _TELEGRAM_FLOOD_WAIT_COUNTER,
        )

        snapshot["counter_by_caller"] = dict(_TELEGRAM_FLOOD_WAIT_COUNTER)
        snapshot["counter_total"] = int(sum(_TELEGRAM_FLOOD_WAIT_COUNTER.values()))
    except ImportError:
        pass

    return snapshot


# ── MTProto ping (GetState invoke + RTT) ────────────────────────────────────


async def _measure_mtproto_rtt(
    userbot: Any, *, timeout: float = _PING_TIMEOUT_SEC
) -> dict[str, Any]:
    """Измеряем RTT через ``client.invoke(GetState())``.

    Возвращает {ok, rtt_ms, server_pts, error}. Не бросает.
    """
    result: dict[str, Any] = {
        "ok": False,
        "rtt_ms": None,
        "server_pts": None,
        "error": None,
    }
    if userbot is None:
        result["error"] = "userbot_missing"
        return result

    client = _safe_getattr(userbot, "client", None) or _safe_getattr(userbot, "app", None)
    if client is None:
        result["error"] = "client_missing"
        return result

    invoke = _safe_getattr(client, "invoke", None)
    if invoke is None:
        result["error"] = "invoke_missing"
        return result

    try:
        from pyrogram.raw.functions.updates import GetState  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"pyrogram_import_failed: {exc!s}"
        return result

    started = time.perf_counter()
    try:
        state = await asyncio.wait_for(invoke(GetState()), timeout=timeout)
    except asyncio.TimeoutError:
        result["error"] = "timeout"
        return result
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)[:200]
        return result

    rtt_ms = (time.perf_counter() - started) * 1000.0
    result["ok"] = True
    result["rtt_ms"] = round(rtt_ms, 2)
    result["server_pts"] = int(_safe_getattr(state, "pts", 0) or 0)
    return result


# ── DNS check ───────────────────────────────────────────────────────────────


def _resolve_host_sync(host: str) -> dict[str, Any]:
    """Резолвит host через ``socket.gethostbyname`` синхронно. Не бросает."""
    started = time.perf_counter()
    try:
        ip = socket.gethostbyname(host)
        elapsed = (time.perf_counter() - started) * 1000.0
        return {
            "host": host,
            "ok": True,
            "ip": ip,
            "rtt_ms": round(elapsed, 2),
            "error": None,
        }
    except (socket.gaierror, OSError) as exc:
        elapsed = (time.perf_counter() - started) * 1000.0
        return {
            "host": host,
            "ok": False,
            "ip": None,
            "rtt_ms": round(elapsed, 2),
            "error": str(exc)[:200],
        }


async def _dns_check_all(hosts: tuple[str, ...]) -> list[dict[str, Any]]:
    """Параллельно резолвим список hosts через thread executor."""
    tasks = [asyncio.to_thread(_resolve_host_sync, h) for h in hosts]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    cleaned: list[dict[str, Any]] = []
    for h, res in zip(hosts, results):
        if isinstance(res, Exception):
            cleaned.append(
                {"host": h, "ok": False, "ip": None, "rtt_ms": None, "error": str(res)[:200]}
            )
        elif isinstance(res, dict):
            cleaned.append(res)
        else:
            cleaned.append({"host": h, "ok": False, "ip": None, "rtt_ms": None, "error": "unknown"})
    return cleaned


# ── Outbound TCP probe к Telegram DC ────────────────────────────────────────


def _tcp_probe_sync(host: str, port: int, timeout: float) -> dict[str, Any]:
    """Открывает TCP-сокет к (host, port) и сразу закрывает; измеряет RTT."""
    started = time.perf_counter()
    sock = None
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        elapsed = (time.perf_counter() - started) * 1000.0
        return {
            "host": host,
            "port": port,
            "ok": True,
            "rtt_ms": round(elapsed, 2),
            "error": None,
        }
    except (TimeoutError, OSError) as exc:
        elapsed = (time.perf_counter() - started) * 1000.0
        return {
            "host": host,
            "port": port,
            "ok": False,
            "rtt_ms": round(elapsed, 2),
            "error": str(exc)[:200],
        }
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


async def _tcp_probe(host: str, port: int, *, timeout: float) -> dict[str, Any]:
    """Async-обёртка над _tcp_probe_sync через thread executor."""
    return await asyncio.to_thread(_tcp_probe_sync, host, port, timeout)


# ── Userbot resolver ────────────────────────────────────────────────────────


def _resolve_userbot(ctx: RouterContext) -> Any:
    """Достаёт KraabUserbot из ctx.deps или из Wave 70 weakref."""
    userbot = ctx.get_dep("kraab_userbot")
    if userbot is not None:
        return userbot
    try:
        from src.core.metrics.probes import _get_userbot_for_metrics  # noqa: PLC0415

        return _get_userbot_for_metrics()
    except ImportError:
        return None


# ── Router factory ──────────────────────────────────────────────────────────


def build_network_admin_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с endpoints управления network/MTProto-инфо."""
    router = APIRouter(tags=["network-admin"])

    # ── GET /api/admin/network/status ────────────────────────────────────────

    @router.get("/api/admin/network/status")
    async def network_status() -> dict[str, Any]:
        """JSON-snapshot всех network/MTProto-сигналов.

        Не дёргает MTProto — только читает уже собранные значения.
        Время сборки snapshot'а возвращается в ``snapshot_ts``.
        """
        userbot = _resolve_userbot(ctx)
        started = time.perf_counter()

        session_info = await _collect_session_info(userbot)
        get_state_snapshot = _collect_get_state_snapshot(userbot)
        heartbeat_snapshot = _collect_heartbeat_snapshot(userbot)
        disconnects_snapshot = _collect_disconnects_snapshot()
        floodwait_snapshot = _collect_floodwait_snapshot()

        # Health-flag сводный: ok если есть свежий event (< 5 мин) и нет split-brain.
        event_age = heartbeat_snapshot.get("last_telegram_event_age_sec")
        is_connected = session_info.get("is_connected")
        split_brain = heartbeat_snapshot.get("split_brain_detected") or False
        if is_connected is True and isinstance(event_age, (int, float)) and event_age < 300:
            health = "ok"
        elif split_brain:
            health = "split_brain"
        elif is_connected is False:
            health = "disconnected"
        else:
            health = "degraded"

        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 2)
        return {
            "ok": True,
            "health": health,
            "snapshot_ts": time.time(),
            "snapshot_iso": _iso_or_none(time.time()),
            "build_ms": elapsed_ms,
            "session": session_info,
            "get_state": get_state_snapshot,
            "heartbeat": heartbeat_snapshot,
            "disconnects": disconnects_snapshot,
            "floodwait": floodwait_snapshot,
            "userbot_present": userbot is not None,
        }

    # ── POST /api/admin/network/ping ────────────────────────────────────────

    @router.post("/api/admin/network/ping")
    async def network_ping(
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
        outbound: bool = Query(default=False, description="Дополнительно: TCP-проба к Telegram DC"),
    ) -> dict[str, Any]:
        """Дёргает ``GetState`` через MTProto, возвращает RTT.

        Write-access required: каждый вызов идёт по сети к Telegram → не хотим
        чтобы внешние агенты случайно DOS-или соединение.
        """
        ctx.assert_write_access(x_krab_web_key, token)
        userbot = _resolve_userbot(ctx)

        result = await _measure_mtproto_rtt(userbot, timeout=_PING_TIMEOUT_SEC)
        _logger.info(
            "network_admin.ping",
            ok=result.get("ok"),
            rtt_ms=result.get("rtt_ms"),
            error=result.get("error"),
        )
        payload: dict[str, Any] = {
            "ok": result.get("ok", False),
            "rtt_ms": result.get("rtt_ms"),
            "server_pts": result.get("server_pts"),
            "error": result.get("error"),
            "ts": time.time(),
        }
        if outbound:
            payload["outbound"] = await _tcp_probe(
                _TG_DC_HOST,
                _TG_DC_PORT,
                timeout=_TG_OUTBOUND_TIMEOUT_SEC,
            )
        return payload

    # ── POST /api/admin/network/dns_check ───────────────────────────────────

    @router.post("/api/admin/network/dns_check")
    async def network_dns_check(
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict[str, Any]:
        """Резолвим все Telegram DNS-имена через thread executor.

        Read-only по эффекту, но классифицирован как write — не хотим чтобы
        внешние агенты могли триггерить DNS-запросы по нашей публичной сети.
        """
        ctx.assert_write_access(x_krab_web_key, token)
        results = await _dns_check_all(_DNS_CHECK_HOSTS)
        failures = [r for r in results if not r.get("ok")]
        _logger.info(
            "network_admin.dns_check",
            checked=len(results),
            failed=len(failures),
        )
        return {
            "ok": len(failures) == 0,
            "checked": len(results),
            "failed": len(failures),
            "results": results,
            "ts": time.time(),
        }

    # ── GET /admin/network — HTML page ──────────────────────────────────────

    @router.get("/admin/network", response_class=HTMLResponse)
    async def network_admin_page() -> HTMLResponse:
        """HTML страница со снимком network state (polling 10s)."""
        return HTMLResponse(_NETWORK_ADMIN_PAGE_HTML)

    return router


# ── HTML страница /admin/network ─────────────────────────────────────────────
# Клиентский JS строит DOM через createElement/textContent — никакого
# innerHTML с внешними данными (XSS-safe).

_NETWORK_ADMIN_PAGE_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Krab · Network Admin</title>
    <style>
        :root {
            --bg: #0d0d0d;
            --card-bg: #121212;
            --border: #2a2a2a;
            --text: #e0e0e0;
            --text-muted: #888888;
            --accent: #7dd3fc;
            --ok: #22c55e;
            --warn: #facc15;
            --err: #ef4444;
        }
        * { box-sizing: border-box; }
        body {
            margin: 0;
            font-family: system-ui, -apple-system, BlinkMacSystemFont,
                "Segoe UI", Roboto, sans-serif;
            background-color: var(--bg);
            color: var(--text);
            line-height: 1.4;
        }
        .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, monospace; }
        header {
            display: flex; justify-content: space-between; align-items: center;
            padding: 12px 24px;
            background: #000; border-bottom: 1px solid var(--border);
        }
        header h1 { margin: 0; font-size: 1.4rem; }
        header .meta { color: var(--text-muted); font-size: 0.85rem; }
        main { padding: 16px 24px; display: grid; gap: 14px; }
        .row {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
            gap: 14px;
        }
        .panel {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 12px 14px;
        }
        .panel h2 {
            margin: 0 0 10px 0;
            font-size: 0.95rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }
        .kv { display: grid; grid-template-columns: 160px 1fr; row-gap: 6px; column-gap: 12px; font-size: 0.9rem; }
        .kv dt { color: var(--text-muted); font-weight: 500; }
        .kv dd { margin: 0; word-break: break-word; }
        .badge {
            display: inline-block; padding: 2px 8px;
            border-radius: 4px; font-size: 0.75rem; font-weight: 500;
        }
        .badge-ok { background: rgba(34,197,94,0.15); color: var(--ok); }
        .badge-warn { background: rgba(250,204,21,0.15); color: var(--warn); }
        .badge-err { background: rgba(239,68,68,0.15); color: var(--err); }
        .badge-muted { background: rgba(255,255,255,0.06); color: var(--text-muted); }
        button {
            background: rgba(125,211,252,0.1);
            border: 1px solid var(--accent);
            color: var(--accent);
            padding: 5px 12px;
            font-size: 0.8rem;
            border-radius: 4px;
            cursor: pointer;
            margin-right: 6px;
            font-family: inherit;
        }
        button:hover { background: rgba(125,211,252,0.2); }
        button:disabled { opacity: 0.5; cursor: not-allowed; }
        .actions { margin-bottom: 8px; }
        .err-banner {
            color: var(--err);
            padding: 10px 12px;
            background: rgba(239,68,68,0.08);
            border-radius: 4px;
            font-size: 0.85rem;
        }
        .info-banner {
            color: var(--accent);
            padding: 10px 12px;
            background: rgba(125,211,252,0.06);
            border-radius: 4px;
            font-size: 0.85rem;
        }
        table.dns-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.85rem;
        }
        table.dns-table th, table.dns-table td {
            text-align: left;
            padding: 5px 8px;
            border-bottom: 1px solid var(--border);
        }
        table.dns-table th {
            color: var(--text-muted);
            text-transform: uppercase;
            font-size: 0.72rem;
            letter-spacing: 0.04em;
        }
    </style>
</head>
<body>
    <header>
        <h1>🛰️ Krab · Network Admin</h1>
        <div class="meta">Polling каждые 10 сек · <span id="last-update">—</span></div>
    </header>
    <main>
        <div class="actions">
            <button id="btn-ping">📡 MTProto ping</button>
            <button id="btn-ping-outbound">🌐 Ping + TCP DC</button>
            <button id="btn-dns">🔎 DNS check</button>
            <span id="health-badge"></span>
        </div>
        <div id="err-banner"></div>
        <div id="action-result"></div>

        <div class="row">
            <div class="panel" id="panel-session">
                <h2>Pyrogram session</h2>
                <dl class="kv" id="kv-session"></dl>
            </div>
            <div class="panel" id="panel-heartbeat">
                <h2>Heartbeat (Wave 37-A)</h2>
                <dl class="kv" id="kv-heartbeat"></dl>
            </div>
            <div class="panel" id="panel-getstate">
                <h2>GetState pts (Wave 63-A)</h2>
                <dl class="kv" id="kv-getstate"></dl>
            </div>
        </div>
        <div class="row">
            <div class="panel" id="panel-disconnects">
                <h2>Disconnects (Wave 142)</h2>
                <dl class="kv" id="kv-disconnects"></dl>
            </div>
            <div class="panel" id="panel-floodwait">
                <h2>FloodWait (Wave 121)</h2>
                <dl class="kv" id="kv-floodwait"></dl>
            </div>
        </div>
        <div class="panel" id="panel-dns">
            <h2>DNS results (последний run)</h2>
            <div id="dns-results">
                <div class="info-banner">Запусти "DNS check" для проверки core.telegram.org / t.me.</div>
            </div>
        </div>
    </main>
    <script>
        function fmtAge(sec) {
            if (sec === null || sec === undefined) return '—';
            if (sec < 60) return sec.toFixed(1) + 's';
            if (sec < 3600) return (sec / 60).toFixed(1) + 'm';
            if (sec < 86400) return (sec / 3600).toFixed(1) + 'h';
            return (sec / 86400).toFixed(1) + 'd';
        }
        function mkBadge(text, cls) {
            const span = document.createElement('span');
            span.className = 'badge ' + cls;
            span.textContent = text;
            return span;
        }
        function setKV(dlId, entries) {
            const dl = document.getElementById(dlId);
            while (dl.firstChild) dl.removeChild(dl.firstChild);
            for (const [key, value] of entries) {
                const dt = document.createElement('dt');
                dt.textContent = key;
                const dd = document.createElement('dd');
                if (value instanceof Node) dd.appendChild(value);
                else dd.textContent = (value === null || value === undefined) ? '—' : String(value);
                if (key === 'session_name' || key === 'last_server_pts' || key === 'last_seen_update_id') {
                    dd.className = 'mono';
                }
                dl.appendChild(dt);
                dl.appendChild(dd);
            }
        }
        function healthBadge(h) {
            if (h === 'ok') return mkBadge('OK', 'badge-ok');
            if (h === 'split_brain') return mkBadge('SPLIT-BRAIN', 'badge-err');
            if (h === 'disconnected') return mkBadge('DISCONNECTED', 'badge-err');
            return mkBadge('DEGRADED', 'badge-warn');
        }
        function renderSession(s) {
            const isConn = s.is_connected;
            const connBadge = (isConn === true)
                ? mkBadge('connected', 'badge-ok')
                : (isConn === false)
                    ? mkBadge('disconnected', 'badge-err')
                    : mkBadge('unknown', 'badge-muted');
            setKV('kv-session', [
                ['session_name', s.session_name],
                ['DC ID', s.dc_id],
                ['user_id', s.user_id],
                ['is_bot', s.is_bot],
                ['test_mode', s.test_mode],
                ['auth_date', s.auth_date_iso],
                ['is_connected', connBadge],
            ]);
        }
        function renderHeartbeat(h) {
            const sbBadge = h.split_brain_detected
                ? mkBadge('SPLIT-BRAIN', 'badge-err')
                : mkBadge('no', 'badge-ok');
            setKV('kv-heartbeat', [
                ['last_telegram_event', fmtAge(h.last_telegram_event_age_sec) + ' ago'],
                ['last_heartbeat_ok', fmtAge(h.last_heartbeat_ok_age_sec) + ' ago'],
                ['split_brain', sbBadge],
            ]);
        }
        function renderGetState(g) {
            setKV('kv-getstate', [
                ['last_server_pts', g.last_server_pts],
                ['last_seen_update_id', g.last_seen_update_id],
                ['dispatcher_tick_count', g.dispatcher_tick_count],
                ['last_dispatcher_tick', fmtAge(g.last_dispatcher_tick_age_sec) + ' ago'],
            ]);
        }
        function renderDisconnects(d) {
            const entries = [['total', String(d.total)], ['session_label', d.session_label || '—']];
            const bySession = d.by_session || {};
            const keys = Object.keys(bySession);
            if (keys.length === 0) {
                entries.push(['by_session', '— (clean)']);
            } else {
                for (const k of keys) entries.push(['  ' + k, String(bySession[k])]);
            }
            setKV('kv-disconnects', entries);
        }
        function renderFloodwait(f) {
            const entries = [
                ['active_count', String(f.active_count || 0)],
                ['counter_total', String(f.counter_total || 0)],
            ];
            const active = f.active || {};
            const activeKeys = Object.keys(active);
            if (activeKeys.length === 0) {
                entries.push(['active', '— (none)']);
            } else {
                for (const k of activeKeys) entries.push(['🔥 ' + k, 'rate_limited']);
            }
            const cnt = f.counter_by_caller || {};
            const cntKeys = Object.keys(cnt).sort((a, b) => cnt[b] - cnt[a]).slice(0, 5);
            for (const k of cntKeys) entries.push(['top ' + k, String(cnt[k])]);
            setKV('kv-floodwait', entries);
        }
        function renderDnsResults(payload) {
            const container = document.getElementById('dns-results');
            while (container.firstChild) container.removeChild(container.firstChild);
            const table = document.createElement('table');
            table.className = 'dns-table';
            const thead = document.createElement('thead');
            const trh = document.createElement('tr');
            for (const h of ['Host', 'OK', 'IP', 'RTT', 'Error']) {
                const th = document.createElement('th');
                th.textContent = h;
                trh.appendChild(th);
            }
            thead.appendChild(trh);
            table.appendChild(thead);
            const tbody = document.createElement('tbody');
            for (const r of (payload.results || [])) {
                const tr = document.createElement('tr');
                const tdHost = document.createElement('td');
                tdHost.className = 'mono';
                tdHost.textContent = r.host;
                tr.appendChild(tdHost);
                const tdOk = document.createElement('td');
                tdOk.appendChild(r.ok ? mkBadge('OK', 'badge-ok') : mkBadge('FAIL', 'badge-err'));
                tr.appendChild(tdOk);
                const tdIp = document.createElement('td');
                tdIp.className = 'mono';
                tdIp.textContent = r.ip || '—';
                tr.appendChild(tdIp);
                const tdRtt = document.createElement('td');
                tdRtt.textContent = (r.rtt_ms !== null && r.rtt_ms !== undefined) ? (r.rtt_ms + ' ms') : '—';
                tr.appendChild(tdRtt);
                const tdErr = document.createElement('td');
                tdErr.textContent = r.error || '—';
                tr.appendChild(tdErr);
                tbody.appendChild(tr);
            }
            table.appendChild(tbody);
            container.appendChild(table);
        }
        function showActionResult(msg, isError) {
            const box = document.getElementById('action-result');
            while (box.firstChild) box.removeChild(box.firstChild);
            const div = document.createElement('div');
            div.className = isError ? 'err-banner' : 'info-banner';
            div.textContent = msg;
            box.appendChild(div);
        }
        async function postAdmin(url) {
            const res = await fetch(url, { method: 'POST' });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || ('HTTP ' + res.status));
            return data;
        }
        async function triggerPing(withOutbound) {
            const btnA = document.getElementById('btn-ping');
            const btnB = document.getElementById('btn-ping-outbound');
            btnA.disabled = true; btnB.disabled = true;
            try {
                const url = '/api/admin/network/ping' + (withOutbound ? '?outbound=true' : '');
                const data = await postAdmin(url);
                let msg = '📡 Ping ' + (data.ok ? 'OK' : 'FAIL') +
                    ': rtt=' + (data.rtt_ms !== null ? (data.rtt_ms + ' ms') : '—') +
                    ', server_pts=' + (data.server_pts !== null ? data.server_pts : '—');
                if (data.error) msg += ', error=' + data.error;
                if (data.outbound) {
                    msg += ' | outbound TCP ' + data.outbound.host + ':' + data.outbound.port +
                        ' = ' + (data.outbound.ok ? 'OK ' + data.outbound.rtt_ms + 'ms' : 'FAIL: ' + data.outbound.error);
                }
                showActionResult(msg, !data.ok);
                fetchStatus();
            } catch (e) {
                showActionResult('Ping ошибка: ' + e.message, true);
            } finally {
                btnA.disabled = false; btnB.disabled = false;
            }
        }
        async function triggerDns() {
            const btn = document.getElementById('btn-dns');
            btn.disabled = true;
            try {
                const data = await postAdmin('/api/admin/network/dns_check');
                renderDnsResults(data);
                showActionResult('DNS check: ' + (data.failed || 0) + ' fail из ' + (data.checked || 0), data.failed > 0);
            } catch (e) {
                showActionResult('DNS ошибка: ' + e.message, true);
            } finally {
                btn.disabled = false;
            }
        }
        async function fetchStatus() {
            const errBanner = document.getElementById('err-banner');
            errBanner.textContent = '';
            try {
                const res = await fetch('/api/admin/network/status');
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const data = await res.json();
                const hb = document.getElementById('health-badge');
                while (hb.firstChild) hb.removeChild(hb.firstChild);
                hb.appendChild(healthBadge(data.health));
                renderSession(data.session || {});
                renderHeartbeat(data.heartbeat || {});
                renderGetState(data.get_state || {});
                renderDisconnects(data.disconnects || {});
                renderFloodwait(data.floodwait || {});
                document.getElementById('last-update').textContent =
                    new Date().toLocaleTimeString('ru-RU', { hour12: false });
            } catch (e) {
                const div = document.createElement('div');
                div.className = 'err-banner';
                div.textContent = 'Ошибка загрузки: ' + e.message;
                errBanner.appendChild(div);
            }
        }
        document.getElementById('btn-ping').addEventListener('click', () => triggerPing(false));
        document.getElementById('btn-ping-outbound').addEventListener('click', () => triggerPing(true));
        document.getElementById('btn-dns').addEventListener('click', triggerDns);
        fetchStatus();
        setInterval(fetchStatus, 10000);
    </script>
</body>
</html>
"""


__all__ = ["build_network_admin_router"]
