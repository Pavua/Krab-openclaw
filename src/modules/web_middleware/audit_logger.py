# -*- coding: utf-8 -*-
"""Wave 122: owner-panel append-only audit log middleware.

Цель — forensic-видимость "кто, куда, когда" на owner-панели `:8080`.
При гипотетическом leak'е `.env` credentials нужен timeline доступа,
чтобы понять scope incident'а: какие endpoints дёрнули, с какого IP,
с каким префиксом auth-ключа.

Архитектура:
    BaseHTTPMiddleware → перехватывает request/response пару,
    записывает строку в SQLite (`~/.openclaw/krab_runtime_state/owner_panel_audit.db`).
    Append-only: только INSERT, схема единая, без миграций.

Schema:
    CREATE TABLE owner_panel_audit (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_unix      REAL NOT NULL,
        method       TEXT NOT NULL,
        path         TEXT NOT NULL,
        status       INTEGER NOT NULL,
        auth_prefix  TEXT,
        client_ip    TEXT,
        duration_ms  REAL NOT NULL
    );
    CREATE INDEX idx_owner_panel_audit_ts ON owner_panel_audit (ts_unix DESC);

Skip-policy:
    EXEMPT_PATHS — высокочастотные мониторинговые endpoints не пишутся
    (GET /metrics, /health/* и т.п.) — иначе таблица распухнет
    мегабайтами/день без forensic-ценности.

Env-gate:
    KRAB_OWNER_PANEL_AUDIT_ENABLED=1 (default-ON) — выключение требует
    явного `=0` для аварийного отключения.

Тестируемость:
    `storage` injection (AuditStorage instance) — позволяет юнит-тестам
    использовать in-memory SQLite или временный файл без env-зависимости.
"""

from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.core.metrics.audit_log import record_request

# Пути, исключённые из audit log: высокочастотные monitoring/health.
# Forensic-ценность близка к нулю, объёмы записей — большие.
EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "/metrics",
        "/health",
        "/healthz",
        "/api/health/lite",
        "/api/v1/health",
    }
)

DEFAULT_DB_PATH: Path = Path("~/.openclaw/krab_runtime_state/owner_panel_audit.db").expanduser()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS owner_panel_audit (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_unix      REAL NOT NULL,
    method       TEXT NOT NULL,
    path         TEXT NOT NULL,
    status       INTEGER NOT NULL,
    auth_prefix  TEXT,
    client_ip    TEXT,
    duration_ms  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_owner_panel_audit_ts
    ON owner_panel_audit (ts_unix DESC);
"""


class AuditStorage:
    """Append-only SQLite storage для audit log записей.

    Hot-path: один INSERT per request. Wrap в try/except — никогда не
    ломаем response даже если диск недоступен.
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path is None:
            self._db_path: Path = DEFAULT_DB_PATH
        else:
            self._db_path = Path(str(db_path))
        # Для in-memory ":memory:" — Path не нужен, но parent.mkdir на этом
        # пути безвреден (Path(":memory:").parent == Path(".") существует).
        if str(self._db_path) != ":memory:":
            try:
                self._db_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        # Для in-memory нужно держать одно соединение (иначе таблица теряется).
        self._memory_conn: sqlite3.Connection | None = None
        if str(self._db_path) == ":memory:":
            self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._memory_conn.executescript(_SCHEMA)
            self._memory_conn.commit()
        else:
            self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        """Возвращает соединение (для memory — singleton, для file — новое)."""
        if self._memory_conn is not None:
            return self._memory_conn
        conn = sqlite3.connect(str(self._db_path), timeout=2.0)
        return conn

    def _ensure_schema(self) -> None:
        """Создаёт таблицу и индекс при первой инициализации."""
        try:
            conn = sqlite3.connect(str(self._db_path), timeout=2.0)
            try:
                conn.executescript(_SCHEMA)
                conn.commit()
            finally:
                conn.close()
        except sqlite3.Error:
            # Schema bootstrap не должен ронять middleware init.
            pass

    def record(
        self,
        ts_unix: float,
        method: str,
        path: str,
        status: int,
        auth_prefix: str | None,
        client_ip: str | None,
        duration_ms: float,
    ) -> None:
        """Append-only INSERT. Никогда не raise'ит наружу."""
        try:
            if self._memory_conn is not None:
                self._memory_conn.execute(
                    "INSERT INTO owner_panel_audit "
                    "(ts_unix, method, path, status, auth_prefix, client_ip, duration_ms) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        ts_unix,
                        method,
                        path,
                        int(status),
                        auth_prefix,
                        client_ip,
                        float(duration_ms),
                    ),
                )
                self._memory_conn.commit()
                return
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO owner_panel_audit "
                    "(ts_unix, method, path, status, auth_prefix, client_ip, duration_ms) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        ts_unix,
                        method,
                        path,
                        int(status),
                        auth_prefix,
                        client_ip,
                        float(duration_ms),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except sqlite3.Error:
            # Hot-path: молча игнорируем write-ошибки.
            pass

    def query_recent(self, limit: int = 100) -> list[dict[str, Any]]:
        """Возвращает последние ``limit`` записей по убыванию ts_unix."""
        limit = max(1, min(int(limit), 1000))
        try:
            if self._memory_conn is not None:
                conn: sqlite3.Connection = self._memory_conn
                rows = conn.execute(
                    "SELECT id, ts_unix, method, path, status, auth_prefix, "
                    "client_ip, duration_ms "
                    "FROM owner_panel_audit "
                    "ORDER BY ts_unix DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                file_conn = sqlite3.connect(str(self._db_path), timeout=2.0)
                try:
                    rows = file_conn.execute(
                        "SELECT id, ts_unix, method, path, status, auth_prefix, "
                        "client_ip, duration_ms "
                        "FROM owner_panel_audit "
                        "ORDER BY ts_unix DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
                finally:
                    file_conn.close()
        except sqlite3.Error:
            return []
        return [
            {
                "id": r[0],
                "ts_unix": r[1],
                "method": r[2],
                "path": r[3],
                "status": r[4],
                "auth_prefix": r[5],
                "client_ip": r[6],
                "duration_ms": r[7],
            }
            for r in rows
        ]


def _auth_prefix(request: Request) -> str | None:
    """Возвращает первые 4 символа аутентификационного ключа (или None).

    Источники (priority):
      1. ``Authorization`` header (Bearer prefix вырезан).
      2. ``X-Krab-Web-Key`` header.
      3. Query-параметр ``token``.
    Сам токен НЕ логируется — только 4-символьный префикс для корреляции.
    """
    auth = request.headers.get("Authorization", "").strip()
    if auth:
        token = auth.split(" ", 1)[1] if auth.lower().startswith("bearer ") else auth
        return token[:4] if token else None
    web_key = request.headers.get("X-Krab-Web-Key", "").strip()
    if web_key:
        return web_key[:4]
    token_q = request.query_params.get("token", "").strip()
    if token_q:
        return token_q[:4]
    return None


def _client_ip(request: Request) -> str | None:
    """Голый IP клиента — поддержка reverse-proxy (X-Forwarded-For)."""
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        ip = fwd.split(",", 1)[0].strip()
        if ip:
            return ip
    if request.client is not None and request.client.host:
        return request.client.host
    return None


class AuditLoggerMiddleware(BaseHTTPMiddleware):
    """ASGI-middleware: пишет в audit log все non-exempt API requests.

    Активна по умолчанию (``KRAB_OWNER_PANEL_AUDIT_ENABLED!=0``).
    EXEMPT_PATHS никогда не логируются.
    """

    def __init__(
        self,
        app: Any,
        storage: AuditStorage | None = None,
        exempt_paths: frozenset[str] | None = None,
        clock_fn: Callable[[], float] | None = None,
    ) -> None:
        super().__init__(app)
        self._storage = storage if storage is not None else get_default_storage()
        self._exempt = exempt_paths if exempt_paths is not None else EXEMPT_PATHS
        self._clock: Callable[[], float] = clock_fn or time.time
        self._monotonic: Callable[[], float] = time.monotonic

    @property
    def storage(self) -> AuditStorage:
        """Доступ к storage (для admin-endpoint'ов и тестов)."""
        return self._storage

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if path in self._exempt:
            return await call_next(request)
        started_mono = self._monotonic()
        try:
            response = await call_next(request)
        except Exception:
            # Записываем 500-сурогат, чтобы инцидент остался в audit log.
            duration_ms = (self._monotonic() - started_mono) * 1000.0
            self._storage.record(
                ts_unix=self._clock(),
                method=request.method,
                path=path,
                status=500,
                auth_prefix=_auth_prefix(request),
                client_ip=_client_ip(request),
                duration_ms=duration_ms,
            )
            record_request(method=request.method, path=path, status=500)
            raise
        duration_ms = (self._monotonic() - started_mono) * 1000.0
        self._storage.record(
            ts_unix=self._clock(),
            method=request.method,
            path=path,
            status=response.status_code,
            auth_prefix=_auth_prefix(request),
            client_ip=_client_ip(request),
            duration_ms=duration_ms,
        )
        record_request(method=request.method, path=path, status=response.status_code)
        return response


def is_audit_log_enabled() -> bool:
    """True если KRAB_OWNER_PANEL_AUDIT_ENABLED!=0 (default-ON).

    Чтобы отключить нужно явное ``=0`` в .env — иначе middleware
    активна по умолчанию.
    """
    raw = os.getenv("KRAB_OWNER_PANEL_AUDIT_ENABLED", "1").strip()
    return raw != "0"


# Singleton storage — переиспользуется между middleware и admin-endpoint'ом.
_DEFAULT_STORAGE: AuditStorage | None = None


def get_default_storage() -> AuditStorage:
    """Возвращает singleton AuditStorage (для admin-endpoint'а)."""
    global _DEFAULT_STORAGE
    if _DEFAULT_STORAGE is None:
        _DEFAULT_STORAGE = AuditStorage()
    return _DEFAULT_STORAGE


def reset_default_storage_for_tests() -> None:
    """Сбрасывает singleton — только для тестов."""
    global _DEFAULT_STORAGE
    _DEFAULT_STORAGE = None
