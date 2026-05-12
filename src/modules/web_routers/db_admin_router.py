# -*- coding: utf-8 -*-
"""
DB admin router — Wave 176 (Session 48).

Owner-panel страница ``/admin/db`` + JSON API для здоровья всех SQLite
БД Krab (``archive.db`` главная память + runtime state + workspace
DBs). Показывает размер, mtime, integrity_check status, table-level row
counts; даёт write-actions: ``integrity check``, ``WAL checkpoint``,
``VACUUM``.

Endpoints (READY):
- GET  /api/admin/db/list                          — все .db в ~/.openclaw
- GET  /api/admin/db/{db_name}/tables              — row counts per table
- POST /api/admin/db/{db_name}/integrity           — PRAGMA integrity_check
- POST /api/admin/db/{db_name}/checkpoint          — PRAGMA wal_checkpoint(TRUNCATE)
- POST /api/admin/db/{db_name}/vacuum              — VACUUM (требует confirm)
- GET  /admin/db                                    — HTML страница

Поведение:
- Все sqlite3-connection'ы открываются ``mode=ro`` URI чтобы не мешать
  основным писателям (особенно archive.db). Для write-actions
  (checkpoint/vacuum/integrity) — нормальная rw connection с
  ``timeout=5`` секунд.
- Все блокирующие sqlite-вызовы запускаются через ``asyncio.to_thread``
  — никаких блоков event loop.
- WAL/SHM файлы не считаются как самостоятельные БД, но размер их
  показывается рядом с основной .db (через ``wal_size`` / ``shm_size``).
- Кэширование integrity_check: 5 минут (или ?refresh=1).
- Match style of ``src/modules/web_routers/cron_admin_router.py``.

Безопасность:
- Owner panel биндится на 127.0.0.1, read-only endpoint без auth.
- Write-actions требуют ``ctx.assert_write_access`` (WEB_API_KEY).
- ``db_name`` — sanitize через whitelist (только known names из
  enumerated списка) — защита от path traversal.
- Vacuum требует ``?confirm=yes`` query-параметр (UI отдельно
  подтверждает).
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import HTMLResponse

from src.core.logger import get_logger

from ._context import RouterContext

_logger = get_logger(__name__)

# ── Конфигурация ────────────────────────────────────────────────────────────

# Корневой каталог поиска SQLite БД.
_OPENCLAW_ROOT = Path.home() / ".openclaw"

# Главная "memory" — у неё table-level breakdown.
_MAIN_DB_NAME = "archive.db"

# Whitelist подкаталогов для сканирования (защита от Chrome browser
# каталогов и др. — там тоже .db, но это не наши).
_SCAN_SUBDIRS = (
    "krab_memory",
    "krab_runtime_state",
    "workspace",
    "workspace-main-messaging",
    "memory",
)

# Также сканируем .db в самом корне ~/.openclaw (openclaw.db).
_SCAN_ROOT_FILES = True

# Регекс для безопасного db_name (path-traversal protection).
# Допускаем буквы, цифры, точки, тире, подчёркивания, slash.
_DB_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._/\-]+$")

# Cache TTL для integrity_check результатов.
_INTEGRITY_CACHE_TTL_SEC = 300  # 5 минут

# Лимиты безопасности.
_INTEGRITY_TIMEOUT_SEC = 60  # integrity_check на 500MB может занять время
_CHECKPOINT_TIMEOUT_SEC = 30
_VACUUM_TIMEOUT_SEC = 600  # vacuum на 500MB может занять минуты
_TABLE_COUNT_TIMEOUT_SEC = 30

# In-memory cache: {db_path: {"ts": float, "result": str}}.
_INTEGRITY_CACHE: dict[str, dict[str, Any]] = {}


# ── Helpers: enumeration / file system ──────────────────────────────────────


def _safe_stat(path: Path) -> tuple[int, float] | tuple[None, None]:
    """Возвращает (size, mtime) или (None, None) при ошибке."""
    try:
        st = path.stat()
        return st.st_size, st.st_mtime
    except OSError:
        return None, None


def _collect_db_files() -> list[Path]:
    """Сканирует ~/.openclaw для .db файлов.

    Допускаются:
      • файлы в подкаталогах из ``_SCAN_SUBDIRS``;
      • .db файлы в самом корне ~/.openclaw (например, openclaw.db).

    Исключаются:
      • chrome-debug-profile/* (служебные браузерные кэши);
      • backups/* (backup-копии — для read-only показа смысла мало);
      • .db-wal, .db-shm (это не самостоятельные БД).
    """
    if not _OPENCLAW_ROOT.exists():
        return []

    found: list[Path] = []

    # 1) Файлы в корне (~/.openclaw/*.db).
    if _SCAN_ROOT_FILES:
        for entry in _OPENCLAW_ROOT.glob("*.db"):
            if entry.is_file():
                found.append(entry)

    # 2) Подкаталоги.
    for subdir_name in _SCAN_SUBDIRS:
        subdir = _OPENCLAW_ROOT / subdir_name
        if not subdir.exists() or not subdir.is_dir():
            continue
        for entry in subdir.glob("*.db"):
            if entry.is_file():
                found.append(entry)

    found.sort(key=lambda p: p.relative_to(_OPENCLAW_ROOT).as_posix())
    return found


def _db_name_from_path(path: Path) -> str:
    """Возвращает relative-path как имя БД (для URL-параметра).

    Например, ``/Users/pablito/.openclaw/krab_memory/archive.db`` →
    ``krab_memory/archive.db``. Используется в endpoint path.
    """
    try:
        return path.relative_to(_OPENCLAW_ROOT).as_posix()
    except ValueError:
        return path.name


def _resolve_db_path(db_name: str) -> Path:
    """Резолвит db_name (URL-параметр) в Path и валидирует.

    Raises HTTPException(400/404) при невалидном имени или отсутствии файла.
    """
    db_name = (db_name or "").strip()
    if not db_name or not _DB_NAME_PATTERN.match(db_name):
        raise HTTPException(status_code=400, detail="db_invalid_name")
    # Path-traversal protection: запрещаем "..".
    if ".." in db_name.split("/"):
        raise HTTPException(status_code=400, detail="db_traversal_blocked")

    candidate = (_OPENCLAW_ROOT / db_name).resolve()
    try:
        candidate.relative_to(_OPENCLAW_ROOT.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="db_outside_root") from exc

    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail=f"db_not_found: {db_name}")

    # Дополнительная проверка: должен быть в whitelist каталогов.
    rel = candidate.relative_to(_OPENCLAW_ROOT)
    parts = rel.parts
    if len(parts) == 1:
        # Файл в корне ~/.openclaw — допускаем только если включено.
        if not _SCAN_ROOT_FILES:
            raise HTTPException(status_code=403, detail="db_root_files_disabled")
    else:
        if parts[0] not in _SCAN_SUBDIRS:
            raise HTTPException(status_code=403, detail="db_subdir_not_allowed")

    return candidate


# ── Helpers: SQLite I/O (synchronous blocking calls) ────────────────────────


def _connect_readonly(db_path: Path, *, timeout: float = 5.0) -> sqlite3.Connection:
    """Открывает SQLite БД в read-only режиме через URI.

    ``mode=ro`` — не блокирует пишущие транзакции (важно для archive.db).
    """
    uri = f"file:{db_path}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=timeout)


def _connect_rw(db_path: Path, *, timeout: float = 5.0) -> sqlite3.Connection:
    """Открывает SQLite БД в read-write режиме (для checkpoint/vacuum).

    Используется только в write-actions. ``timeout`` мал чтобы не зависнуть.
    """
    return sqlite3.connect(str(db_path), timeout=timeout)


def _quick_integrity_check_sync(db_path: Path) -> dict[str, Any]:
    """PRAGMA integrity_check; возвращает результат (sync).

    Если БД здорова — вернёт ``[("ok",)]``.
    """
    try:
        conn = _connect_readonly(db_path, timeout=5.0)
    except sqlite3.Error as exc:
        return {"ok": False, "result": f"connection_failed: {exc}"}

    try:
        cur = conn.cursor()
        # Используем quick_check (быстрее full integrity_check на больших БД).
        cur.execute("PRAGMA quick_check;")
        rows = cur.fetchall()
        result = "; ".join(str(r[0]) for r in rows) if rows else "empty"
        return {
            "ok": result.strip().lower() == "ok",
            "result": result,
        }
    except sqlite3.Error as exc:
        return {"ok": False, "result": f"check_failed: {exc}"}
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def _full_integrity_check_sync(db_path: Path) -> dict[str, Any]:
    """PRAGMA integrity_check (full) — медленнее quick, но полная проверка."""
    try:
        conn = _connect_readonly(db_path, timeout=5.0)
    except sqlite3.Error as exc:
        return {"ok": False, "result": f"connection_failed: {exc}"}

    try:
        cur = conn.cursor()
        cur.execute("PRAGMA integrity_check;")
        rows = cur.fetchall()
        result = "; ".join(str(r[0]) for r in rows) if rows else "empty"
        return {
            "ok": result.strip().lower() == "ok",
            "result": result,
        }
    except sqlite3.Error as exc:
        return {"ok": False, "result": f"check_failed: {exc}"}
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def _table_counts_sync(db_path: Path) -> dict[str, Any]:
    """Возвращает row counts по каждой таблице (sync)."""
    try:
        conn = _connect_readonly(db_path, timeout=5.0)
    except sqlite3.Error as exc:
        return {"ok": False, "error": f"connection_failed: {exc}", "tables": []}

    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
        table_names = [r[0] for r in cur.fetchall()]

        tables: list[dict[str, Any]] = []
        for name in table_names:
            # Защитный квот: name validated through sqlite_master, но
            # на всякий случай отгораживаем backticks вокруг.
            if not re.match(r"^[A-Za-z0-9_]+$", name):
                tables.append({"name": name, "row_count": None, "error": "unsafe_name"})
                continue
            try:
                cur.execute(f'SELECT COUNT(*) FROM "{name}"')
                count = cur.fetchone()[0]
                tables.append({"name": name, "row_count": int(count)})
            except sqlite3.Error as exc:
                tables.append({"name": name, "row_count": None, "error": str(exc)})

        return {"ok": True, "tables": tables}
    except sqlite3.Error as exc:
        return {"ok": False, "error": str(exc), "tables": []}
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def _wal_checkpoint_sync(db_path: Path) -> dict[str, Any]:
    """PRAGMA wal_checkpoint(TRUNCATE) — обрезает WAL после checkpoint."""
    try:
        conn = _connect_rw(db_path, timeout=5.0)
    except sqlite3.Error as exc:
        return {"ok": False, "error": f"connection_failed: {exc}"}

    try:
        cur = conn.cursor()
        cur.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        row = cur.fetchone()
        # Контракт: (busy, log, checkpointed). busy=0 — успех.
        if row is None:
            return {"ok": True, "busy": None, "log": None, "checkpointed": None}
        busy, log_frames, checkpointed = row[0], row[1], row[2]
        return {
            "ok": int(busy) == 0,
            "busy": int(busy),
            "log": int(log_frames),
            "checkpointed": int(checkpointed),
        }
    except sqlite3.Error as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def _vacuum_sync(db_path: Path) -> dict[str, Any]:
    """VACUUM — пересборка БД (освобождение места, дефраг)."""
    # Замерим size_before / size_after для отчёта.
    size_before, _ = _safe_stat(db_path)
    try:
        conn = _connect_rw(db_path, timeout=5.0)
    except sqlite3.Error as exc:
        return {"ok": False, "error": f"connection_failed: {exc}"}

    try:
        cur = conn.cursor()
        cur.execute("VACUUM;")
        conn.commit()
    except sqlite3.Error as exc:
        return {"ok": False, "error": str(exc), "size_before": size_before}
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass

    size_after, _ = _safe_stat(db_path)
    return {
        "ok": True,
        "size_before": size_before,
        "size_after": size_after,
        "reclaimed_bytes": ((size_before - size_after) if size_before and size_after else None),
    }


# ── Helpers: enumeration с метаданными ──────────────────────────────────────


def _enumerate_dbs(*, refresh: bool = False) -> list[dict[str, Any]]:
    """Главная enumeration — возвращает список dict-ов.

    Каждая запись содержит:
      - ``name`` — относительный путь от ~/.openclaw (URL identifier);
      - ``path`` — абсолютный путь (для отображения);
      - ``size`` — размер .db (bytes);
      - ``size_human`` — человекочитаемо (MB, GB);
      - ``mtime`` — unix timestamp;
      - ``mtime_iso`` — ISO формат UTC;
      - ``wal_size`` — размер WAL (bytes) или None;
      - ``shm_size`` — размер SHM (bytes) или None;
      - ``integrity_status`` — "ok" / "fail" / "unknown" / "cached_ok";
      - ``integrity_result`` — текстовый результат проверки;
      - ``integrity_checked_at`` — unix ts последней проверки или None;
      - ``is_main`` — True для archive.db;
    """
    paths = _collect_db_files()
    out: list[dict[str, Any]] = []
    for p in paths:
        size, mtime = _safe_stat(p)
        if size is None:
            continue
        wal_size, _ = _safe_stat(p.with_suffix(p.suffix + "-wal"))
        shm_size, _ = _safe_stat(p.with_suffix(p.suffix + "-shm"))

        # Cache lookup для integrity.
        cache_entry = _INTEGRITY_CACHE.get(str(p))
        now = time.time()
        if (
            cache_entry
            and not refresh
            and (now - cache_entry.get("ts", 0)) < _INTEGRITY_CACHE_TTL_SEC
        ):
            integrity_status = "ok" if cache_entry.get("ok") else "fail"
            integrity_result = cache_entry.get("result", "")
            integrity_checked_at = cache_entry.get("ts")
        else:
            integrity_status = "unknown"
            integrity_result = ""
            integrity_checked_at = None

        rel_name = _db_name_from_path(p)
        out.append(
            {
                "name": rel_name,
                "path": str(p),
                "size": size,
                "size_human": _humanize_bytes(size),
                "mtime": mtime,
                "mtime_iso": (
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mtime)) if mtime else None
                ),
                "wal_size": wal_size,
                "shm_size": shm_size,
                "integrity_status": integrity_status,
                "integrity_result": integrity_result,
                "integrity_checked_at": integrity_checked_at,
                "is_main": p.name == _MAIN_DB_NAME,
            }
        )
    return out


def _humanize_bytes(n: int | None) -> str:
    """Превращает байты в '1.2 MB' / '518.0 MB' / '1.4 GB'."""
    if n is None:
        return "—"
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"


# ── Factory ─────────────────────────────────────────────────────────────────


def build_db_admin_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter для /admin/db и /api/admin/db/*."""
    router = APIRouter(tags=["db-admin"])

    # ── GET /api/admin/db/list ──────────────────────────────────────────────

    @router.get("/api/admin/db/list")
    async def db_list(refresh: int = Query(default=0)) -> dict:
        """Возвращает список всех SQLite БД Krab с метаданными.

        Query params:
          - ``refresh=1`` — игнорирует кэш integrity_check, заставляет
            повторно прочитать статус (но НЕ запускает проверку — для
            запуска используется POST endpoint).
        """
        try:
            dbs = await asyncio.to_thread(_enumerate_dbs, refresh=bool(refresh))
        except Exception as exc:  # noqa: BLE001
            _logger.error("db_admin.list_failed", error=str(exc))
            raise HTTPException(status_code=500, detail=f"db_list_failed: {exc}") from exc

        return {
            "ok": True,
            "count": len(dbs),
            "dbs": dbs,
            "openclaw_root": str(_OPENCLAW_ROOT),
            "cache_ttl_sec": _INTEGRITY_CACHE_TTL_SEC,
        }

    # ── GET /api/admin/db/{db_name}/tables ──────────────────────────────────

    @router.get("/api/admin/db/{db_name:path}/tables")
    async def db_tables(db_name: str) -> dict:
        """Возвращает row counts по каждой таблице БД."""
        db_path = _resolve_db_path(db_name)
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(_table_counts_sync, db_path),
                timeout=_TABLE_COUNT_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError as exc:
            raise HTTPException(
                status_code=504,
                detail="db_tables_timeout",
            ) from exc

        return {
            "ok": result.get("ok", False),
            "db": db_name,
            "path": str(db_path),
            "tables": result.get("tables", []),
            "error": result.get("error"),
        }

    # ── POST /api/admin/db/{db_name}/integrity ──────────────────────────────

    @router.post("/api/admin/db/{db_name:path}/integrity")
    async def db_integrity(
        db_name: str,
        full: int = Query(default=0),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Запускает PRAGMA integrity_check на БД.

        Query params:
          - ``full=1`` — полная проверка (медленнее);
          - ``full=0`` (default) — quick_check (быстрее).
        """
        ctx.assert_write_access_fn(x_krab_web_key, token)
        db_path = _resolve_db_path(db_name)

        check_fn = _full_integrity_check_sync if full else _quick_integrity_check_sync
        started = time.time()
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(check_fn, db_path),
                timeout=_INTEGRITY_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError as exc:
            raise HTTPException(
                status_code=504,
                detail="db_integrity_timeout",
            ) from exc

        elapsed = time.time() - started
        _INTEGRITY_CACHE[str(db_path)] = {
            "ts": time.time(),
            "ok": result.get("ok", False),
            "result": result.get("result", ""),
        }

        _logger.info(
            "db_admin.integrity_check",
            db=db_name,
            ok=result.get("ok"),
            full=bool(full),
            elapsed_sec=round(elapsed, 2),
        )

        return {
            "ok": result.get("ok", False),
            "db": db_name,
            "result": result.get("result", ""),
            "elapsed_sec": round(elapsed, 2),
            "full": bool(full),
        }

    # ── POST /api/admin/db/{db_name}/checkpoint ─────────────────────────────

    @router.post("/api/admin/db/{db_name:path}/checkpoint")
    async def db_checkpoint(
        db_name: str,
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """PRAGMA wal_checkpoint(TRUNCATE) — обрезает WAL после checkpoint."""
        ctx.assert_write_access_fn(x_krab_web_key, token)
        db_path = _resolve_db_path(db_name)

        started = time.time()
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(_wal_checkpoint_sync, db_path),
                timeout=_CHECKPOINT_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError as exc:
            raise HTTPException(
                status_code=504,
                detail="db_checkpoint_timeout",
            ) from exc

        elapsed = time.time() - started
        _logger.info(
            "db_admin.checkpoint",
            db=db_name,
            ok=result.get("ok"),
            busy=result.get("busy"),
            checkpointed=result.get("checkpointed"),
            elapsed_sec=round(elapsed, 2),
        )

        if not result.get("ok") and result.get("error"):
            raise HTTPException(
                status_code=500,
                detail=f"db_checkpoint_failed: {result.get('error')}",
            )

        return {
            "ok": result.get("ok", False),
            "db": db_name,
            "busy": result.get("busy"),
            "log": result.get("log"),
            "checkpointed": result.get("checkpointed"),
            "elapsed_sec": round(elapsed, 2),
        }

    # ── POST /api/admin/db/{db_name}/vacuum ─────────────────────────────────

    @router.post("/api/admin/db/{db_name:path}/vacuum")
    async def db_vacuum(
        db_name: str,
        confirm: str = Query(default=""),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """VACUUM — пересборка БД (освобождение места).

        Требует ``?confirm=yes`` query-параметр (защита от случайных
        запусков на больших БД — может занять минуты).
        """
        ctx.assert_write_access_fn(x_krab_web_key, token)
        if (confirm or "").strip().lower() not in {"yes", "true", "1"}:
            raise HTTPException(
                status_code=400,
                detail="db_vacuum_requires_confirm: pass ?confirm=yes",
            )
        db_path = _resolve_db_path(db_name)

        started = time.time()
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(_vacuum_sync, db_path),
                timeout=_VACUUM_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError as exc:
            raise HTTPException(
                status_code=504,
                detail="db_vacuum_timeout",
            ) from exc

        elapsed = time.time() - started
        _logger.info(
            "db_admin.vacuum",
            db=db_name,
            ok=result.get("ok"),
            size_before=result.get("size_before"),
            size_after=result.get("size_after"),
            reclaimed=result.get("reclaimed_bytes"),
            elapsed_sec=round(elapsed, 2),
        )

        if not result.get("ok"):
            raise HTTPException(
                status_code=500,
                detail=f"db_vacuum_failed: {result.get('error', 'unknown')}",
            )

        return {
            "ok": True,
            "db": db_name,
            "size_before": result.get("size_before"),
            "size_after": result.get("size_after"),
            "reclaimed_bytes": result.get("reclaimed_bytes"),
            "elapsed_sec": round(elapsed, 2),
        }

    # ── GET /admin/db — HTML page ───────────────────────────────────────────

    @router.get("/admin/db", response_class=HTMLResponse)
    async def db_admin_page() -> HTMLResponse:
        """HTML страница со списком БД, кнопками и polling."""
        return HTMLResponse(_DB_ADMIN_PAGE_HTML)

    return router


# ── HTML страница /admin/db ─────────────────────────────────────────────────
# Клиентский JS строит DOM через createElement/textContent — XSS-safe
# (никакого innerHTML с внешними строками).

_DB_ADMIN_PAGE_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Krab · DB Admin</title>
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
        main { padding: 16px 24px; }
        table {
            width: 100%; border-collapse: collapse;
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 6px; overflow: hidden;
            font-size: 0.9rem;
            margin-bottom: 24px;
        }
        th, td {
            padding: 8px 10px; text-align: left;
            border-bottom: 1px solid var(--border);
            vertical-align: top;
        }
        th {
            background: #1a1a1a; color: var(--text-muted);
            text-transform: uppercase; font-size: 0.75rem;
            letter-spacing: 0.04em;
        }
        tr:last-child td { border-bottom: none; }
        tr:hover { background: rgba(125, 211, 252, 0.04); }
        .badge {
            display: inline-block; padding: 2px 8px;
            border-radius: 4px; font-size: 0.75rem; font-weight: 500;
        }
        .badge-ok { background: rgba(34,197,94,0.15); color: var(--ok); }
        .badge-warn { background: rgba(250,204,21,0.15); color: var(--warn); }
        .badge-err { background: rgba(239,68,68,0.15); color: var(--err); }
        .badge-muted { background: rgba(255,255,255,0.06); color: var(--text-muted); }
        .badge-main { background: rgba(125,211,252,0.15); color: var(--accent); }
        button {
            background: rgba(125,211,252,0.1);
            border: 1px solid var(--accent);
            color: var(--accent);
            padding: 4px 10px;
            font-size: 0.75rem;
            border-radius: 4px;
            cursor: pointer;
            margin-right: 4px;
            margin-bottom: 4px;
            font-family: inherit;
        }
        button:hover { background: rgba(125,211,252,0.2); }
        button.danger {
            border-color: var(--err);
            color: var(--err);
            background: rgba(239,68,68,0.08);
        }
        button.danger:hover { background: rgba(239,68,68,0.18); }
        button.warn {
            border-color: var(--warn);
            color: var(--warn);
            background: rgba(250,204,21,0.08);
        }
        button.warn:hover { background: rgba(250,204,21,0.18); }
        .summary { color: var(--text-muted); margin-bottom: 12px; font-size: 0.85rem; }
        .err-banner {
            color: var(--err); padding: 12px;
            background: rgba(239,68,68,0.08);
            border-radius: 4px; margin-bottom: 12px;
        }
        .section-title {
            font-size: 1.1rem;
            margin: 32px 0 12px 0;
            color: var(--accent);
        }
        .table-stats {
            font-size: 0.85rem;
        }
        .table-stats td { padding: 4px 10px; }
        details summary {
            cursor: pointer;
            font-size: 0.8rem;
            color: var(--accent);
            margin-top: 4px;
        }
        .small {
            font-size: 0.75rem;
            color: var(--text-muted);
        }
        .path-cell {
            max-width: 360px;
            word-break: break-all;
        }
    </style>
</head>
<body>
    <header>
        <h1>🦀 Krab · DB Admin</h1>
        <div class="meta">Polling каждые 60 сек · <span id="last-update">—</span></div>
    </header>
    <main>
        <div id="summary" class="summary">Загружаем БД…</div>
        <div id="err-banner"></div>
        <table id="db-table">
            <thead>
                <tr>
                    <th>Имя</th>
                    <th>Размер</th>
                    <th>WAL / SHM</th>
                    <th>Modified</th>
                    <th>Integrity</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody id="db-body"></tbody>
        </table>

        <div class="section-title">📊 Таблицы archive.db</div>
        <div id="archive-tables-summary" class="summary">Загружаем…</div>
        <table id="tables-table" class="table-stats">
            <thead>
                <tr>
                    <th>Таблица</th>
                    <th>Row Count</th>
                </tr>
            </thead>
            <tbody id="tables-body"></tbody>
        </table>
    </main>
    <script>
        async function callAdmin(method, url) {
            try {
                const res = await fetch(url, { method: method });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(data.detail || ('HTTP ' + res.status));
                return data;
            } catch (e) {
                alert('Ошибка: ' + e.message);
                throw e;
            }
        }
        async function runIntegrity(dbName, full) {
            const label = full ? 'full integrity_check' : 'quick_check';
            if (!confirm('Запустить ' + label + ' на ' + dbName + '? (может занять время)')) return;
            const url = '/api/admin/db/' + dbName + '/integrity' + (full ? '?full=1' : '');
            const data = await callAdmin('POST', url);
            alert('Integrity: ' + (data.ok ? '✅ OK' : '❌ FAIL') +
                  '\\nResult: ' + data.result +
                  '\\nElapsed: ' + data.elapsed_sec + 's');
            fetchDbs();
        }
        async function runCheckpoint(dbName) {
            if (!confirm('Запустить WAL checkpoint на ' + dbName + '?')) return;
            const data = await callAdmin('POST', '/api/admin/db/' + dbName + '/checkpoint');
            alert('Checkpoint: ' + (data.ok ? '✅ OK' : '⚠️ BUSY') +
                  '\\nLog frames: ' + data.log +
                  '\\nCheckpointed: ' + data.checkpointed +
                  '\\nElapsed: ' + data.elapsed_sec + 's');
            fetchDbs();
        }
        async function runVacuum(dbName) {
            if (!confirm('⚠️ VACUUM ' + dbName + '?\\n\\nЭто пересборка БД, может занять МИНУТЫ на большой БД (500MB+).\\nКрабу нельзя будет писать в БД пока вакуум идёт.\\n\\nПродолжить?')) return;
            if (!confirm('Подтверждаю VACUUM ' + dbName + ' — повторное подтверждение.')) return;
            const data = await callAdmin('POST', '/api/admin/db/' + dbName + '/vacuum?confirm=yes');
            const reclaimedMB = data.reclaimed_bytes ? (data.reclaimed_bytes / 1024 / 1024).toFixed(1) : '?';
            alert('VACUUM завершён ✅\\nReclaimed: ' + reclaimedMB + ' MB\\nElapsed: ' + data.elapsed_sec + 's');
            fetchDbs();
        }
        function fmtAge(iso) {
            if (!iso) return null;
            try {
                const d = new Date(iso);
                const ageSec = Math.floor((Date.now() - d.getTime()) / 1000);
                if (ageSec < 60) return ageSec + 's ago';
                if (ageSec < 3600) return Math.floor(ageSec / 60) + 'm ago';
                if (ageSec < 86400) return Math.floor(ageSec / 3600) + 'h ago';
                return Math.floor(ageSec / 86400) + 'd ago';
            } catch (e) { return iso; }
        }
        function mkBadge(text, cls) {
            const span = document.createElement('span');
            span.className = 'badge ' + cls;
            span.textContent = text;
            return span;
        }
        function mkButton(text, onClick, cls) {
            const btn = document.createElement('button');
            btn.textContent = text;
            if (cls) btn.className = cls;
            btn.addEventListener('click', onClick);
            return btn;
        }
        function mkCell(content, mono) {
            const td = document.createElement('td');
            if (mono) td.className = 'mono';
            if (typeof content === 'string') td.textContent = content;
            else if (content instanceof Node) td.appendChild(content);
            return td;
        }
        function renderIntegrity(db) {
            const span = document.createElement('span');
            if (db.integrity_status === 'ok') {
                span.appendChild(mkBadge('ok', 'badge-ok'));
            } else if (db.integrity_status === 'fail') {
                span.appendChild(mkBadge('FAIL', 'badge-err'));
                if (db.integrity_result) {
                    const det = document.createElement('details');
                    const sum = document.createElement('summary');
                    sum.textContent = 'show';
                    det.appendChild(sum);
                    const pre = document.createElement('div');
                    pre.className = 'small mono';
                    pre.textContent = db.integrity_result;
                    det.appendChild(pre);
                    span.appendChild(document.createElement('br'));
                    span.appendChild(det);
                }
            } else {
                span.appendChild(mkBadge('unknown', 'badge-muted'));
            }
            if (db.integrity_checked_at) {
                const small = document.createElement('div');
                small.className = 'small';
                const d = new Date(db.integrity_checked_at * 1000);
                small.textContent = 'checked ' + d.toLocaleString('ru-RU', { hour12: false });
                span.appendChild(small);
            }
            return span;
        }
        function renderActions(db) {
            const td = document.createElement('td');
            td.appendChild(mkButton('🔍 quick', () => runIntegrity(db.name, false)));
            td.appendChild(mkButton('🔬 full', () => runIntegrity(db.name, true)));
            td.appendChild(mkButton('💾 checkpoint', () => runCheckpoint(db.name), 'warn'));
            td.appendChild(mkButton('🗜 vacuum', () => runVacuum(db.name), 'danger'));
            return td;
        }
        function renderNameCell(db) {
            const td = document.createElement('td');
            td.className = 'mono path-cell';
            const strong = document.createElement('strong');
            strong.textContent = db.name;
            td.appendChild(strong);
            if (db.is_main) {
                td.appendChild(document.createTextNode(' '));
                td.appendChild(mkBadge('MAIN', 'badge-main'));
            }
            const sub = document.createElement('div');
            sub.className = 'small mono';
            sub.textContent = db.path;
            td.appendChild(sub);
            return td;
        }
        function fmtBytes(n) {
            if (n === null || n === undefined) return '—';
            if (n === 0) return '0';
            if (n < 1024) return n + ' B';
            if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
            if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB';
            return (n / 1024 / 1024 / 1024).toFixed(2) + ' GB';
        }
        function renderWalShm(db) {
            const td = document.createElement('td');
            td.className = 'mono small';
            const wal = db.wal_size !== null && db.wal_size !== undefined ? fmtBytes(db.wal_size) : '—';
            const shm = db.shm_size !== null && db.shm_size !== undefined ? fmtBytes(db.shm_size) : '—';
            td.textContent = 'wal: ' + wal + ' / shm: ' + shm;
            return td;
        }
        async function fetchDbs() {
            const errBanner = document.getElementById('err-banner');
            errBanner.textContent = '';
            try {
                const res = await fetch('/api/admin/db/list');
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const data = await res.json();
                const dbs = data.dbs || [];
                const tbody = document.getElementById('db-body');
                while (tbody.firstChild) tbody.removeChild(tbody.firstChild);
                let totalSize = 0;
                let failCount = 0;
                for (const db of dbs) {
                    totalSize += (db.size || 0);
                    if (db.integrity_status === 'fail') failCount++;
                    const tr = document.createElement('tr');
                    tr.appendChild(renderNameCell(db));
                    tr.appendChild(mkCell(db.size_human, true));
                    tr.appendChild(renderWalShm(db));
                    const mtCell = document.createElement('td');
                    const age = fmtAge(db.mtime_iso);
                    if (age) {
                        const span = document.createElement('span');
                        span.title = db.mtime_iso || '';
                        span.textContent = age;
                        mtCell.appendChild(span);
                    } else {
                        mtCell.appendChild(mkBadge('—', 'badge-muted'));
                    }
                    tr.appendChild(mtCell);
                    const intCell = document.createElement('td');
                    intCell.appendChild(renderIntegrity(db));
                    tr.appendChild(intCell);
                    tr.appendChild(renderActions(db));
                    tbody.appendChild(tr);
                }
                const summary = document.getElementById('summary');
                while (summary.firstChild) summary.removeChild(summary.firstChild);
                summary.appendChild(document.createTextNode(
                    'Всего БД: ' + dbs.length +
                    ' · Общий размер: ' + fmtBytes(totalSize) +
                    ' · Integrity FAIL: ' + failCount
                ));
                document.getElementById('last-update').textContent =
                    new Date().toLocaleTimeString('ru-RU', { hour12: false });
                fetchArchiveTables();
            } catch (e) {
                const banner = document.createElement('div');
                banner.className = 'err-banner';
                banner.textContent = 'Ошибка загрузки: ' + e.message;
                errBanner.appendChild(banner);
            }
        }
        async function fetchArchiveTables() {
            const sumDiv = document.getElementById('archive-tables-summary');
            const tbody = document.getElementById('tables-body');
            while (tbody.firstChild) tbody.removeChild(tbody.firstChild);
            try {
                const res = await fetch('/api/admin/db/krab_memory/archive.db/tables');
                if (!res.ok) {
                    sumDiv.textContent = 'archive.db tables: HTTP ' + res.status;
                    return;
                }
                const data = await res.json();
                if (!data.ok || !data.tables) {
                    sumDiv.textContent = 'archive.db: ' + (data.error || 'no tables');
                    return;
                }
                let total = 0;
                for (const t of data.tables) {
                    if (typeof t.row_count === 'number') total += t.row_count;
                    const tr = document.createElement('tr');
                    const nameTd = document.createElement('td');
                    nameTd.className = 'mono';
                    nameTd.textContent = t.name;
                    tr.appendChild(nameTd);
                    const countTd = document.createElement('td');
                    countTd.className = 'mono';
                    if (t.row_count === null || t.row_count === undefined) {
                        countTd.appendChild(mkBadge(t.error || 'err', 'badge-err'));
                    } else {
                        countTd.textContent = t.row_count.toLocaleString('en-US');
                    }
                    tr.appendChild(countTd);
                    tbody.appendChild(tr);
                }
                sumDiv.textContent = 'Таблиц: ' + data.tables.length +
                    ' · всего строк: ' + total.toLocaleString('en-US');
            } catch (e) {
                sumDiv.textContent = 'Ошибка: ' + e.message;
            }
        }
        fetchDbs();
        setInterval(fetchDbs, 60000);
    </script>
</body>
</html>
"""
