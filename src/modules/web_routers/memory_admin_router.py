# -*- coding: utf-8 -*-
"""
Memory admin router — Wave 184 (Session 48).

Owner-panel страница ``/admin/memory`` + JSON API для семантической
памяти Krab (Memory Phase 2 LIVE — hybrid retrieval с FTS5 + sqlite-vec
RRF + MMR diversity). Показывает row counts по archive.db, retrieval
метрики Prometheus, последние ``memory_retrieval_summary`` events и
позволяет owner'у вручную дёрнуть search через готовый Hybrid retriever
(``src.core.memory_adapter.search_archive``).

Endpoints (READY):
- GET  /api/admin/memory/stats   — таблицы (messages/chunks/peers) + метрики
- GET  /api/admin/memory/recent  — последние memory_retrieval_summary events
- POST /api/admin/memory/search  — search interface (text, chat_id, top_k)
- GET  /admin/memory             — HTML страница

Безопасность:
- /admin/memory bind only на 127.0.0.1 owner panel.
- Search endpoint — read-only, но всё равно gated через ``assert_write_access``
  чтобы не позволить случайному визитёру панели запускать поиск по личной
  переписке владельца.
- archive.db открывается ТОЛЬКО в read-only режиме (через ``sqlite3?mode=ro``).
- Match style of ``src/modules/web_routers/cron_admin_router.py`` /
  ``src/modules/web_routers/db_admin_router.py``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import HTMLResponse

from src.core.logger import get_logger

from ._context import RouterContext

_logger = get_logger(__name__)


# ── Конфигурация ────────────────────────────────────────────────────────────

# Путь к archive.db (single source of truth — re-uses memory_archive default).
_DEFAULT_ARCHIVE_DIR = Path("~/.openclaw/krab_memory").expanduser()
_DEFAULT_ARCHIVE_PATH = _DEFAULT_ARCHIVE_DIR / "archive.db"

# Путь к structlog логу — re-uses логику из logs_admin_router.
_DEFAULT_LOG_PATH = Path.home() / ".openclaw" / "krab_runtime_state" / "krab_main.log"

# Имена таблиц, чьи row counts особо интересны для UI (показываем cards).
_FEATURED_TABLES = ("messages", "chunks", "vec_chunks", "peers", "vec_chunks_meta")

# Дефолтный encoder model — может быть переопределён env.
_DEFAULT_ENCODER = "sentence-transformers/all-MiniLM-L6-v2"
_DEFAULT_ENCODER_DIM = 384

# Limit'ы на search interface.
_SEARCH_TOP_K_MAX = 50
_SEARCH_QUERY_MAX = 500
_SEARCH_TIMEOUT_SEC = 30

# Лимит recent events.
_RECENT_LIMIT_MAX = 200
# Сколько байт читать с хвоста krab_main.log (1 МБ хватает на 50 событий).
_RECENT_SCAN_BYTES = 1 * 1024 * 1024

# Регексп для chat_id (защита от инъекций в SQL/log scan).
_CHAT_ID_PATTERN = re.compile(r"^-?[0-9]{1,32}$")


# ── Helpers: archive.db stats ───────────────────────────────────────────────


def _archive_path() -> Path:
    """Возвращает путь к archive.db. Env override через ``KRAB_ARCHIVE_DB``."""
    raw = os.environ.get("KRAB_ARCHIVE_DB")
    if raw:
        return Path(raw).expanduser()
    return _DEFAULT_ARCHIVE_PATH


def _connect_archive_readonly(timeout: float = 5.0) -> sqlite3.Connection | None:
    """Открывает archive.db в read-only. Возвращает None если файла нет."""
    path = _archive_path()
    if not path.exists():
        return None
    try:
        uri = f"file:{path}?mode=ro"
        return sqlite3.connect(uri, uri=True, timeout=timeout)
    except sqlite3.Error as exc:
        _logger.warning("memory_admin.connect_failed", path=str(path), error=str(exc))
        return None


def _archive_table_counts_sync() -> dict[str, Any]:
    """Возвращает row counts по ключевым таблицам archive.db (sync).

    Безопасно: список таблиц enumerate'ится из sqlite_master, имена
    проверяются regex'ом перед подстановкой в COUNT.
    """
    path = _archive_path()
    out: dict[str, Any] = {
        "ok": False,
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": None,
        "tables": {},
        "featured": {},
        "error": None,
    }
    if not path.exists():
        out["error"] = "archive_db_not_found"
        return out
    try:
        out["size_bytes"] = path.stat().st_size
    except OSError:
        pass

    conn = _connect_archive_readonly()
    if conn is None:
        out["error"] = "connect_failed"
        return out

    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        table_names = [r[0] for r in cur.fetchall()]
        tables: dict[str, int | None] = {}
        for name in table_names:
            if not re.match(r"^[A-Za-z0-9_]+$", name):
                tables[name] = None
                continue
            try:
                cur.execute(f'SELECT COUNT(*) FROM "{name}"')
                row = cur.fetchone()
                tables[name] = int(row[0]) if row else 0
            except sqlite3.Error:
                tables[name] = None

        out["tables"] = tables
        out["featured"] = {name: tables.get(name) for name in _FEATURED_TABLES}
        out["ok"] = True
    except sqlite3.Error as exc:
        out["error"] = str(exc)
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass
    return out


def _vec_chunks_meta_health_sync() -> dict[str, Any]:
    """Проверяет здоровье vec_chunks_meta (Wave 23 desync detection).

    Сравниваем COUNT(vec_chunks) vs COUNT(vec_chunks_meta). Должны совпадать,
    иначе индекс рассинхронизирован и retrieval может выдавать orphan ids.
    """
    out: dict[str, Any] = {
        "ok": True,
        "vec_chunks_count": None,
        "vec_chunks_meta_count": None,
        "delta": None,
        "in_sync": None,
        "error": None,
    }
    conn = _connect_archive_readonly()
    if conn is None:
        out["ok"] = False
        out["error"] = "no_archive_db"
        return out
    try:
        cur = conn.cursor()
        try:
            cur.execute("SELECT COUNT(*) FROM vec_chunks")
            out["vec_chunks_count"] = int(cur.fetchone()[0])
        except sqlite3.Error:
            out["vec_chunks_count"] = None
        try:
            cur.execute("SELECT COUNT(*) FROM vec_chunks_meta")
            out["vec_chunks_meta_count"] = int(cur.fetchone()[0])
        except sqlite3.Error:
            out["vec_chunks_meta_count"] = None
        a = out["vec_chunks_count"]
        b = out["vec_chunks_meta_count"]
        if a is not None and b is not None:
            out["delta"] = a - b
            out["in_sync"] = a == b
        else:
            out["in_sync"] = None
    except sqlite3.Error as exc:
        out["ok"] = False
        out["error"] = str(exc)
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass
    return out


# ── Helpers: encoder info ───────────────────────────────────────────────────


def _encoder_info() -> dict[str, Any]:
    """Возвращает информацию о текущем encoder model.

    Читает env переменные (KRAB_RAG_ENCODER / KRAB_RAG_ENCODER_DIM) и
    пытается получить runtime-данные из HybridRetriever синглтона (если
    инициализирован).
    """
    info: dict[str, Any] = {
        "encoder_env": os.environ.get("KRAB_RAG_ENCODER", _DEFAULT_ENCODER),
        "embedding_dim": int(os.environ.get("KRAB_RAG_ENCODER_DIM", _DEFAULT_ENCODER_DIM)),
        "phase2_enabled": os.environ.get("KRAB_RAG_PHASE2_ENABLED", "0") == "1",
        "rrf_vector_weight": float(os.environ.get("KRAB_RAG_RRF_VECTOR_WEIGHT", "1.0")),
        "retriever_loaded": False,
        "retriever_model_name": None,
        "retriever_vec_available": None,
    }
    try:
        # Не вызываем _get_retriever() сами — это бы триггернуло lazy init;
        # читаем singleton как есть (None если ещё не инициализирован).
        from src.core import memory_adapter as _ma  # noqa: PLC0415

        singleton = getattr(_ma, "_retriever_singleton", None)
        if singleton is not None:
            info["retriever_loaded"] = True
            info["retriever_model_name"] = getattr(singleton, "_model_name", None)
            info["retriever_vec_available"] = getattr(singleton, "_vec_available", None)
    except Exception as exc:  # noqa: BLE001 — best-effort
        _logger.debug("memory_admin.retriever_info_failed", error=str(exc))
    return info


# ── Helpers: Prometheus metric snapshots ────────────────────────────────────


def _collect_metric_samples(metric: Any) -> list[dict[str, Any]]:
    """Best-effort: вычитывает label combinations и values из prom-метрики.

    Counter → list of {labels, value}.
    Histogram → list of {labels, count, sum}.
    Возвращает [] если метрика None / collect() сломалась.
    """
    if metric is None:
        return []
    samples: list[dict[str, Any]] = []
    try:
        for family in metric.collect():
            for s in family.samples:
                # s.name заканчивается на _total / _count / _sum / _bucket;
                # держим только totals/counts/sums для UI.
                name = s.name
                if name.endswith("_bucket"):
                    continue
                samples.append(
                    {
                        "metric": name,
                        "labels": dict(s.labels) if s.labels else {},
                        "value": float(s.value),
                    }
                )
    except Exception as exc:  # noqa: BLE001
        _logger.debug("memory_admin.metric_collect_failed", error=str(exc))
    return samples


def _retrieval_metrics_snapshot() -> dict[str, Any]:
    """Собирает snapshot retrieval-метрик. Fail-safe."""
    snapshot: dict[str, Any] = {
        "mode_total": [],
        "outcome_total": [],
        "duration_summary": {},
    }
    try:
        from src.core import prometheus_metrics as _pm  # noqa: PLC0415

        snapshot["mode_total"] = _collect_metric_samples(
            getattr(_pm, "_memory_retrieval_mode_total", None)
        )
        snapshot["outcome_total"] = _collect_metric_samples(
            getattr(_pm, "_memory_retrieval_total", None)
        )
        # Histograms — суммарно count/sum per phase.
        dur_metric = getattr(_pm, "_memory_retrieval_duration_seconds", None)
        if dur_metric is not None:
            per_phase: dict[str, dict[str, float]] = {}
            try:
                for family in dur_metric.collect():
                    for s in family.samples:
                        if not s.labels or "phase" not in s.labels:
                            continue
                        phase = s.labels["phase"]
                        bucket = per_phase.setdefault(phase, {"count": 0.0, "sum": 0.0})
                        if s.name.endswith("_count"):
                            bucket["count"] = float(s.value)
                        elif s.name.endswith("_sum"):
                            bucket["sum"] = float(s.value)
            except Exception as exc:  # noqa: BLE001
                _logger.debug("memory_admin.histogram_collect_failed", error=str(exc))
            # Считаем avg latency per phase.
            for phase, vals in per_phase.items():
                cnt = vals.get("count", 0.0)
                summ = vals.get("sum", 0.0)
                vals["avg_ms"] = (summ / cnt * 1000.0) if cnt > 0 else 0.0
            snapshot["duration_summary"] = per_phase
    except Exception as exc:  # noqa: BLE001
        _logger.warning("memory_admin.metrics_snapshot_failed", error=str(exc))
    return snapshot


# ── Helpers: recent retrieval events (structlog tail) ───────────────────────


def _log_path() -> Path:
    """Возвращает path к krab_main.log."""
    raw = os.environ.get("KRAB_LOG_FILE")
    if raw is not None:
        if raw == "" or raw.lower() == "none":
            return _DEFAULT_LOG_PATH
        return Path(raw).expanduser()
    base = os.environ.get("KRAB_RUNTIME_STATE_DIR")
    if base:
        return Path(base).expanduser() / "krab_main.log"
    return _DEFAULT_LOG_PATH


def _read_tail_bytes(path: Path, max_bytes: int) -> bytes:
    """Читает последние ``max_bytes`` файла. Возвращает b'' при отсутствии."""
    if not path.exists():
        return b""
    try:
        size = path.stat().st_size
    except OSError:
        return b""
    read_len = min(size, max_bytes)
    try:
        with open(path, "rb") as fp:
            fp.seek(max(0, size - read_len))
            return fp.read()
    except OSError as exc:
        _logger.warning("memory_admin.log_read_failed", path=str(path), error=str(exc))
        return b""


def _parse_retrieval_events_sync(*, limit: int) -> list[dict[str, Any]]:
    """Парсит хвост krab_main.log и возвращает ``memory_retrieval_summary`` events.

    Лог может быть в structlog JSON-формате или key=value. Берём оба
    варианта best-effort: ищем event=memory_retrieval_summary как substring,
    JSON-decode если строка похожа на {...}, иначе оставляем raw.
    """
    raw = _read_tail_bytes(_log_path(), _RECENT_SCAN_BYTES)
    if not raw:
        return []
    text = raw.decode("utf-8", errors="replace")
    events: list[dict[str, Any]] = []
    # Идём с конца чтобы быстрее набрать limit.
    for line in reversed(text.split("\n")):
        if "memory_retrieval_summary" not in line:
            continue
        entry: dict[str, Any] = {"raw": line[:1000]}
        # Попытка JSON parse (structlog json renderer).
        stripped = line.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, dict):
                    entry.update(parsed)
            except (ValueError, json.JSONDecodeError):
                pass
        else:
            # key=value renderer: вытащим знакомые поля по regex.
            for key in (
                "timestamp",
                "level",
                "mode",
                "total_ms",
                "vec_hits",
                "fts_hits",
                "merged_hits",
                "mmr_reranked",
                "query_len",
            ):
                m = re.search(rf"{key}=(['\"]?)([^\s'\"]+)\1", line)
                if m:
                    entry[key] = m.group(2)
        events.append(entry)
        if len(events) >= limit:
            break
    return events


# ── Helpers: HybridRetriever search ─────────────────────────────────────────


def _search_archive_sync(
    *,
    query: str,
    chat_id: str | None,
    top_k: int,
) -> dict[str, Any]:
    """Вызывает ``search_archive`` и сериализует результат в JSON-safe dict.

    Завернуто в sync функцию чтобы вызывающий код мог гонять через
    ``asyncio.to_thread`` с timeout.
    """
    started = time.perf_counter()
    try:
        from src.core.memory_adapter import search_archive  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"memory_adapter_import_failed: {exc}",
            "results": [],
            "elapsed_sec": 0.0,
        }

    try:
        raw_results = search_archive(
            query=query,
            chat_id=chat_id,
            top_k=top_k,
            with_context=1,
            decay_mode="auto",
            owner_only=True,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"search_failed: {exc}",
            "results": [],
            "elapsed_sec": round(time.perf_counter() - started, 3),
        }

    results: list[dict[str, Any]] = []
    for r in raw_results or []:
        # Поддерживаем dataclass (SearchResult) и dict.
        if is_dataclass(r):
            d = asdict(r)
        elif isinstance(r, dict):
            d = dict(r)
        else:
            d = {"repr": repr(r)[:500]}
        # Сериализуем datetime → iso str.
        ts = d.get("timestamp")
        if hasattr(ts, "isoformat"):
            try:
                d["timestamp"] = ts.isoformat()
            except Exception:  # noqa: BLE001
                d["timestamp"] = str(ts)
        # Усечём текст для UI чтобы не отдавать гигантские payloads.
        text = d.get("text_redacted") or ""
        if isinstance(text, str) and len(text) > 1000:
            d["text_redacted"] = text[:1000] + "…"
        results.append(d)

    return {
        "ok": True,
        "results": results,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "count": len(results),
    }


# ── Factory ─────────────────────────────────────────────────────────────────


def build_memory_admin_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter для /admin/memory + /api/admin/memory/*."""
    router = APIRouter(tags=["memory-admin"])

    # ── GET /api/admin/memory/stats ─────────────────────────────────────────

    @router.get("/api/admin/memory/stats")
    async def memory_stats() -> dict:
        """Возвращает агрегированный snapshot Memory Phase 2 подсистемы."""
        try:
            counts = await asyncio.to_thread(_archive_table_counts_sync)
            health = await asyncio.to_thread(_vec_chunks_meta_health_sync)
        except Exception as exc:  # noqa: BLE001
            _logger.error("memory_admin.stats_failed", error=str(exc))
            raise HTTPException(status_code=500, detail=f"memory_stats_failed: {exc}") from exc

        return {
            "ok": True,
            "archive": {
                "path": counts.get("path"),
                "exists": counts.get("exists"),
                "size_bytes": counts.get("size_bytes"),
                "featured_counts": counts.get("featured"),
                "tables": counts.get("tables"),
                "error": counts.get("error"),
            },
            "vec_meta_health": health,
            "encoder": _encoder_info(),
            "metrics": _retrieval_metrics_snapshot(),
        }

    # ── GET /api/admin/memory/recent ────────────────────────────────────────

    @router.get("/api/admin/memory/recent")
    async def memory_recent(limit: int = Query(default=50, ge=1, le=_RECENT_LIMIT_MAX)) -> dict:
        """Возвращает последние ``memory_retrieval_summary`` events из krab_main.log."""
        try:
            events = await asyncio.to_thread(_parse_retrieval_events_sync, limit=limit)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("memory_admin.recent_failed", error=str(exc))
            events = []
        return {
            "ok": True,
            "count": len(events),
            "events": events,
            "log_path": str(_log_path()),
        }

    # ── POST /api/admin/memory/search ───────────────────────────────────────

    @router.post("/api/admin/memory/search")
    async def memory_search(
        query: str = Query(..., min_length=1, max_length=_SEARCH_QUERY_MAX),
        chat_id: str = Query(default=""),
        top_k: int = Query(default=10, ge=1, le=_SEARCH_TOP_K_MAX),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Запускает hybrid search через ``memory_adapter.search_archive``.

        Owner-gated через assert_write_access чтобы random visitor панели не
        мог дёрнуть search по личному архиву.
        """
        ctx.assert_write_access_fn(x_krab_web_key, token)

        clean_query = (query or "").strip()
        if not clean_query:
            raise HTTPException(status_code=400, detail="empty_query")

        clean_chat_id: str | None = None
        if chat_id:
            chat_id = chat_id.strip()
            if not _CHAT_ID_PATTERN.match(chat_id):
                raise HTTPException(status_code=400, detail="invalid_chat_id")
            clean_chat_id = chat_id

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    _search_archive_sync,
                    query=clean_query,
                    chat_id=clean_chat_id,
                    top_k=top_k,
                ),
                timeout=_SEARCH_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError as exc:
            raise HTTPException(status_code=504, detail="memory_search_timeout") from exc

        _logger.info(
            "memory_admin.search",
            query_len=len(clean_query),
            chat_id=clean_chat_id,
            top_k=top_k,
            ok=result.get("ok"),
            elapsed=result.get("elapsed_sec"),
            count=result.get("count"),
        )
        return {
            "ok": result.get("ok", False),
            "query": clean_query,
            "chat_id": clean_chat_id,
            "top_k": top_k,
            "results": result.get("results", []),
            "count": result.get("count", 0),
            "elapsed_sec": result.get("elapsed_sec", 0.0),
            "error": result.get("error"),
        }

    # ── GET /admin/memory — HTML page ───────────────────────────────────────

    @router.get("/admin/memory", response_class=HTMLResponse)
    async def memory_admin_page() -> HTMLResponse:
        """HTML страница со stats cards + search form + recent events."""
        return HTMLResponse(_MEMORY_ADMIN_PAGE_HTML)

    return router


# ── HTML страница /admin/memory ─────────────────────────────────────────────
# Клиентский JS строит DOM через createElement/textContent — XSS-safe
# (никакого innerHTML с внешними строками).

_MEMORY_ADMIN_PAGE_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Krab · Memory Admin</title>
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
        main { padding: 16px 24px; max-width: 1200px; }
        .cards {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 12px;
            margin-bottom: 24px;
        }
        .card {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 14px;
        }
        .card .label {
            color: var(--text-muted);
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }
        .card .value {
            font-size: 1.4rem;
            font-weight: 600;
            margin-top: 4px;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, monospace;
        }
        .card .sub {
            font-size: 0.75rem;
            color: var(--text-muted);
            margin-top: 4px;
        }
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
        button {
            background: rgba(125,211,252,0.1);
            border: 1px solid var(--accent);
            color: var(--accent);
            padding: 6px 14px;
            font-size: 0.85rem;
            border-radius: 4px;
            cursor: pointer;
            font-family: inherit;
        }
        button:hover { background: rgba(125,211,252,0.2); }
        input[type="text"], input[type="number"] {
            background: #0a0a0a;
            border: 1px solid var(--border);
            color: var(--text);
            padding: 6px 10px;
            border-radius: 4px;
            font-family: inherit;
            font-size: 0.9rem;
        }
        input:focus { outline: 1px solid var(--accent); border-color: var(--accent); }
        .section-title {
            font-size: 1.1rem;
            margin: 32px 0 12px 0;
            color: var(--accent);
        }
        .form-row {
            display: flex; gap: 8px; flex-wrap: wrap; align-items: center;
            margin-bottom: 12px;
        }
        .form-row label { color: var(--text-muted); font-size: 0.85rem; }
        .form-row input[name="query"] { flex: 1; min-width: 240px; }
        .summary { color: var(--text-muted); margin-bottom: 12px; font-size: 0.85rem; }
        .err-banner {
            color: var(--err); padding: 12px;
            background: rgba(239,68,68,0.08);
            border-radius: 4px; margin-bottom: 12px;
        }
        .result-card {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 10px 14px;
            margin-bottom: 8px;
        }
        .result-card .meta {
            color: var(--text-muted);
            font-size: 0.75rem;
            margin-bottom: 4px;
        }
        .result-card .text {
            font-size: 0.9rem;
            white-space: pre-wrap;
            word-break: break-word;
        }
    </style>
</head>
<body>
    <header>
        <h1>🧠 Krab · Memory Admin</h1>
        <div class="meta">Memory Phase 2 · <span id="last-update">—</span></div>
    </header>
    <main>
        <div id="err-banner"></div>

        <div class="section-title">📊 Архив (archive.db)</div>
        <div id="counts-cards" class="cards"></div>

        <div class="section-title">⚙️ Encoder & Health</div>
        <div id="encoder-cards" class="cards"></div>

        <div class="section-title">📈 Retrieval metrics</div>
        <table id="metrics-table">
            <thead>
                <tr>
                    <th>Type</th>
                    <th>Label</th>
                    <th>Value</th>
                </tr>
            </thead>
            <tbody id="metrics-body"></tbody>
        </table>

        <div class="section-title">🔍 Search archive</div>
        <form id="search-form" class="form-row" onsubmit="return false;">
            <label>query:</label>
            <input type="text" name="query" placeholder="введите запрос..." maxlength="500" required>
            <label>chat_id:</label>
            <input type="text" name="chat_id" placeholder="(optional)" maxlength="32">
            <label>top_k:</label>
            <input type="number" name="top_k" min="1" max="50" value="10">
            <button id="search-btn" type="submit">▶ Search</button>
        </form>
        <div id="search-summary" class="summary"></div>
        <div id="search-results"></div>

        <div class="section-title">🕒 Recent retrievals</div>
        <div id="recent-summary" class="summary">Загружаем…</div>
        <table id="recent-table">
            <thead>
                <tr>
                    <th>Time</th>
                    <th>Mode</th>
                    <th>Total ms</th>
                    <th>FTS / Vec / Merged / MMR</th>
                </tr>
            </thead>
            <tbody id="recent-body"></tbody>
        </table>
    </main>
    <script>
        function mkBadge(text, cls) {
            const span = document.createElement('span');
            span.className = 'badge ' + cls;
            span.textContent = text;
            return span;
        }
        function mkCard(label, value, sub) {
            const div = document.createElement('div');
            div.className = 'card';
            const l = document.createElement('div');
            l.className = 'label';
            l.textContent = label;
            div.appendChild(l);
            const v = document.createElement('div');
            v.className = 'value';
            v.textContent = value;
            div.appendChild(v);
            if (sub !== undefined && sub !== null) {
                const s = document.createElement('div');
                s.className = 'sub';
                s.textContent = sub;
                div.appendChild(s);
            }
            return div;
        }
        function fmtBytes(n) {
            if (n === null || n === undefined) return '—';
            if (n < 1024) return n + ' B';
            if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
            if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB';
            return (n / 1024 / 1024 / 1024).toFixed(2) + ' GB';
        }
        function fmtCount(n) {
            if (n === null || n === undefined) return '—';
            return Number(n).toLocaleString('en-US');
        }
        function setErr(msg) {
            const banner = document.getElementById('err-banner');
            while (banner.firstChild) banner.removeChild(banner.firstChild);
            if (msg) {
                const div = document.createElement('div');
                div.className = 'err-banner';
                div.textContent = 'Ошибка: ' + msg;
                banner.appendChild(div);
            }
        }
        async function fetchStats() {
            try {
                const res = await fetch('/api/admin/memory/stats');
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const data = await res.json();
                renderStats(data);
                document.getElementById('last-update').textContent =
                    new Date().toLocaleTimeString('ru-RU', { hour12: false });
            } catch (e) {
                setErr('stats: ' + e.message);
            }
        }
        function renderStats(data) {
            // Counts cards.
            const cc = document.getElementById('counts-cards');
            while (cc.firstChild) cc.removeChild(cc.firstChild);
            const arch = data.archive || {};
            const feat = arch.featured_counts || {};
            cc.appendChild(mkCard('Size', fmtBytes(arch.size_bytes), arch.exists ? 'archive.db' : 'NOT FOUND'));
            cc.appendChild(mkCard('messages', fmtCount(feat.messages), 'архив сообщений'));
            cc.appendChild(mkCard('chunks', fmtCount(feat.chunks), 'индексные чанки'));
            cc.appendChild(mkCard('vec_chunks', fmtCount(feat.vec_chunks), 'эмбеддинги'));
            cc.appendChild(mkCard('peers', fmtCount(feat.peers), 'известные peers'));

            // Encoder cards.
            const ec = document.getElementById('encoder-cards');
            while (ec.firstChild) ec.removeChild(ec.firstChild);
            const enc = data.encoder || {};
            ec.appendChild(mkCard('Encoder', enc.encoder_env || '—', 'KRAB_RAG_ENCODER'));
            ec.appendChild(mkCard('Embedding dim', String(enc.embedding_dim || '—'), 'размер вектора'));
            ec.appendChild(mkCard('Phase 2', enc.phase2_enabled ? 'ON' : 'OFF', 'KRAB_RAG_PHASE2_ENABLED'));
            ec.appendChild(mkCard('RRF vec weight', String(enc.rrf_vector_weight || '1.0'), 'KRAB_RAG_RRF_VECTOR_WEIGHT'));
            ec.appendChild(mkCard('Retriever loaded', enc.retriever_loaded ? 'yes' : 'no',
                enc.retriever_loaded ? (enc.retriever_vec_available ? 'vec ok' : 'fts-only') : 'lazy init'));
            const vh = data.vec_meta_health || {};
            const inSync = vh.in_sync;
            const syncLabel = inSync === true ? 'in sync' : (inSync === false ? 'DESYNC ' + vh.delta : 'unknown');
            ec.appendChild(mkCard('vec_chunks_meta', syncLabel,
                'vec=' + fmtCount(vh.vec_chunks_count) + ' meta=' + fmtCount(vh.vec_chunks_meta_count)));

            // Metrics.
            const mb = document.getElementById('metrics-body');
            while (mb.firstChild) mb.removeChild(mb.firstChild);
            const mt = data.metrics || {};
            (mt.mode_total || []).forEach(s => {
                const tr = document.createElement('tr');
                tr.appendChild(_td('mode_total'));
                tr.appendChild(_td('mode=' + (s.labels.mode || '?')));
                tr.appendChild(_td(String(s.value), true));
                mb.appendChild(tr);
            });
            (mt.outcome_total || []).forEach(s => {
                const tr = document.createElement('tr');
                tr.appendChild(_td('outcome_total'));
                tr.appendChild(_td('outcome=' + (s.labels.outcome || '?')));
                tr.appendChild(_td(String(s.value), true));
                mb.appendChild(tr);
            });
            const ds = mt.duration_summary || {};
            Object.keys(ds).forEach(phase => {
                const v = ds[phase] || {};
                const tr = document.createElement('tr');
                tr.appendChild(_td('latency (avg)'));
                tr.appendChild(_td('phase=' + phase));
                const avg = (v.avg_ms || 0).toFixed(2) + ' ms · n=' + (v.count || 0);
                tr.appendChild(_td(avg, true));
                mb.appendChild(tr);
            });
            if (!mb.firstChild) {
                const tr = document.createElement('tr');
                const td = document.createElement('td');
                td.colSpan = 3;
                td.textContent = '— нет данных (prometheus_client недоступен или нет вызовов retrieval)';
                td.style.color = 'var(--text-muted)';
                tr.appendChild(td);
                mb.appendChild(tr);
            }
        }
        function _td(text, mono) {
            const td = document.createElement('td');
            if (mono) td.className = 'mono';
            td.textContent = text;
            return td;
        }
        async function fetchRecent() {
            try {
                const res = await fetch('/api/admin/memory/recent?limit=50');
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const data = await res.json();
                const events = data.events || [];
                const tbody = document.getElementById('recent-body');
                while (tbody.firstChild) tbody.removeChild(tbody.firstChild);
                for (const ev of events) {
                    const tr = document.createElement('tr');
                    tr.appendChild(_td(String(ev.timestamp || '—'), true));
                    tr.appendChild(_td(String(ev.mode || '—')));
                    tr.appendChild(_td(String(ev.total_ms || '—'), true));
                    const detail = 'fts=' + (ev.fts_hits || 0) +
                        ' vec=' + (ev.vec_hits || 0) +
                        ' merged=' + (ev.merged_hits || 0) +
                        ' mmr=' + (ev.mmr_reranked || 0);
                    tr.appendChild(_td(detail, true));
                    tbody.appendChild(tr);
                }
                const sd = document.getElementById('recent-summary');
                sd.textContent = 'События memory_retrieval_summary: ' + events.length +
                    ' · лог: ' + (data.log_path || '?');
            } catch (e) {
                document.getElementById('recent-summary').textContent = 'Ошибка: ' + e.message;
            }
        }
        async function runSearch() {
            const form = document.getElementById('search-form');
            const query = form.query.value.trim();
            const chatId = form.chat_id.value.trim();
            const topK = Math.max(1, Math.min(50, parseInt(form.top_k.value || '10', 10)));
            if (!query) return;
            const params = new URLSearchParams();
            params.set('query', query);
            if (chatId) params.set('chat_id', chatId);
            params.set('top_k', String(topK));
            const btn = document.getElementById('search-btn');
            btn.disabled = true;
            btn.textContent = '⏳ ...';
            const summary = document.getElementById('search-summary');
            const out = document.getElementById('search-results');
            summary.textContent = 'Поиск...';
            while (out.firstChild) out.removeChild(out.firstChild);
            try {
                const res = await fetch('/api/admin/memory/search?' + params.toString(),
                    { method: 'POST' });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(data.detail || ('HTTP ' + res.status));
                summary.textContent = 'Найдено: ' + (data.count || 0) +
                    ' · elapsed: ' + (data.elapsed_sec || 0) + 's' +
                    (data.error ? ' · error: ' + data.error : '');
                for (const r of (data.results || [])) {
                    const card = document.createElement('div');
                    card.className = 'result-card';
                    const meta = document.createElement('div');
                    meta.className = 'meta mono';
                    meta.textContent = 'score=' + (r.score !== undefined ? Number(r.score).toFixed(4) : '?') +
                        ' · chat=' + (r.chat_id || '?') +
                        ' · msg_id=' + (r.message_id || '?') +
                        ' · ts=' + (r.timestamp || '?');
                    card.appendChild(meta);
                    const text = document.createElement('div');
                    text.className = 'text';
                    text.textContent = r.text_redacted || r.repr || '(empty)';
                    card.appendChild(text);
                    out.appendChild(card);
                }
            } catch (e) {
                summary.textContent = 'Ошибка: ' + e.message;
            } finally {
                btn.disabled = false;
                btn.textContent = '▶ Search';
            }
        }
        document.getElementById('search-form').addEventListener('submit', function(e) {
            e.preventDefault();
            runSearch();
        });
        fetchStats();
        fetchRecent();
        setInterval(fetchStats, 60000);
        setInterval(fetchRecent, 60000);
    </script>
</body>
</html>
"""
