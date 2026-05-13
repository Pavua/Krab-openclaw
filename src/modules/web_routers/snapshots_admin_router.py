# -*- coding: utf-8 -*-
"""
Snapshots admin router — Wave 226 (Session 50+).

Owner-panel страница ``/admin/snapshots`` + JSON API для browser'а
Wave 49-F state snapshots (``StateSnapshotManager``).

Snapshots живут в ``~/.openclaw/krab_runtime_state/snapshots/<timestamp>/<file>.bak``
и создаются периодически (default 60 мин, retention 24 keep / 7d age).

Endpoints (READY):
- GET  /api/admin/snapshots/list                    — JSON list со всеми snapshots
- GET  /api/admin/snapshots/{name}/preview          — preview одного .bak файла
- POST /api/admin/snapshots/trigger                 — write-access, manual snapshot
- GET  /api/admin/snapshots/{name}/download         — file response (tar.gz по содержимому каталога)
- POST /api/admin/snapshots/{name}/restore          — write-access, 501 Not Implemented (v1 placeholder)
- GET  /admin/snapshots                              — HTML страница

Безопасность:
- Owner panel биндится на 127.0.0.1, read-only без auth.
- Write-actions (``trigger`` / ``restore``) требуют ``ctx.assert_write_access``.
- ``name`` валидируется regex ``^[A-Za-z0-9._-]{1,80}$`` — защита от path
  traversal. После regex дополнительно проверяем что resolved path остался
  внутри snapshot_root.
- ``restore`` пока возвращает 501 — нужна архитектурная проработка (рестарт
  компонентов, локи, atomic apply).

Match style of ``src/modules/web_routers/db_admin_router.py`` (Wave 176).
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from src.core.logger import get_logger
from src.core.state_snapshots import (
    DEFAULT_INTERVAL_MINUTES,
    StateSnapshotManager,
    state_snapshot_manager,
)

from ._context import RouterContext

_logger = get_logger(__name__)

# ── Конфигурация ────────────────────────────────────────────────────────────

# Regex для безопасного snapshot-имени (path-traversal protection).
# Имя — это таймстамп каталога, например ``20260509T225605Z`` или
# ``_pre_restore_20260509T235959Z``.
_SNAPSHOT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,80}$")

# Лимит preview (первые N байт читаем, дальше — truncated).
_PREVIEW_MAX_BYTES = 4096
_PREVIEW_PRETTY_CHARS = 1000

# Cap на download tar.gz (если каталог огромный — fail-safe).
_DOWNLOAD_MAX_BYTES = 50 * 1024 * 1024  # 50 MB


# ── Helpers ─────────────────────────────────────────────────────────────────


def _get_manager(ctx: RouterContext) -> StateSnapshotManager:
    """Возвращает manager. Если в ctx.deps есть кастомный — берём его (для
    тестов), иначе module-level singleton.
    """
    custom = ctx.deps.get("state_snapshot_manager") if ctx and ctx.deps else None
    if isinstance(custom, StateSnapshotManager):
        return custom
    return state_snapshot_manager


def _validate_snapshot_name(name: str) -> str:
    """Валидирует snapshot-имя. Raises HTTPException(400) при невалидном."""
    name = (name or "").strip()
    if not name or not _SNAPSHOT_NAME_PATTERN.match(name):
        raise HTTPException(status_code=400, detail="snapshot_invalid_name")
    if ".." in name:
        raise HTTPException(status_code=400, detail="snapshot_traversal_blocked")
    return name


def _resolve_snapshot_dir(manager: StateSnapshotManager, name: str) -> Path:
    """Резолвит snapshot name → Path внутри manager.snapshot_root.

    Raises HTTPException(404) если не существует, (400) если выходит за
    пределы корня (defence-in-depth поверх regex).
    """
    name = _validate_snapshot_name(name)
    root = manager.snapshot_root.resolve()
    candidate = (manager.snapshot_root / name).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="snapshot_outside_root") from exc
    if not candidate.exists() or not candidate.is_dir():
        raise HTTPException(status_code=404, detail=f"snapshot_not_found: {name}")
    return candidate


def _humanize_bytes(n: int | None) -> str:
    """'1.2 MB' / '518.0 MB' / '1.4 GB' — match db_admin_router style."""
    if n is None:
        return "—"
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"


def _parse_timestamp_to_iso(ts: str) -> str | None:
    """Парсит ``20260509T225605Z`` → ``2026-05-09T22:56:05Z`` для UI.

    ``_pre_restore_`` префикс убираем перед парсингом. Возвращает None
    если не получилось распарсить — UI покажет raw имя.
    """
    raw = ts
    if raw.startswith("_pre_restore_"):
        raw = raw[len("_pre_restore_") :]
    try:
        dt = datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def _group_by_date(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Группирует snapshots по дате (YYYY-MM-DD UTC). Последние 7 дней
    отдаются в первую очередь; остальные — в "earlier".
    """
    cutoff = time.time() - 7 * 24 * 3600
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        mtime = row.get("mtime") or 0.0
        if mtime >= cutoff:
            day = time.strftime("%Y-%m-%d", time.gmtime(mtime))
        else:
            day = "earlier"
        groups.setdefault(day, []).append(row)
    return groups


def _list_snapshots_enriched(manager: StateSnapshotManager) -> list[dict[str, Any]]:
    """Дополняет manager.list_snapshots() метаданными для UI."""
    rows = manager.list_snapshots()
    now = time.time()
    enriched: list[dict[str, Any]] = []
    for row in rows:
        mtime = row.get("mtime") or 0.0
        age_sec = int(now - mtime) if mtime else None
        enriched.append(
            {
                **row,
                "size_human": _humanize_bytes(row.get("total_bytes")),
                "mtime_iso": (
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mtime)) if mtime else None
                ),
                "timestamp_iso": _parse_timestamp_to_iso(str(row.get("timestamp") or "")),
                "age_sec": age_sec,
                "is_pre_restore": str(row.get("timestamp") or "").startswith("_pre_restore_"),
            }
        )
    return enriched


def _read_preview_sync(file_path: Path) -> dict[str, Any]:
    """Читает первые байты файла, парсит JSON если возможно — sync."""
    try:
        size = file_path.stat().st_size
    except OSError as exc:
        return {"ok": False, "error": f"stat_failed: {exc}"}

    try:
        with file_path.open("rb") as f:
            chunk = f.read(_PREVIEW_MAX_BYTES + 1)
    except OSError as exc:
        return {"ok": False, "error": f"read_failed: {exc}"}

    truncated = len(chunk) > _PREVIEW_MAX_BYTES
    raw = chunk[:_PREVIEW_MAX_BYTES]
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        text = repr(raw)

    # Попытка pretty-print JSON для .json.bak / .jsonl.bak.
    pretty: str | None = None
    suffix_lower = file_path.name.lower()
    is_json_like = suffix_lower.endswith(".json.bak") or suffix_lower.endswith(".json")
    is_jsonl_like = suffix_lower.endswith(".jsonl.bak") or suffix_lower.endswith(".jsonl")
    if is_json_like and not truncated:
        try:
            parsed = json.loads(text)
            pretty = json.dumps(parsed, indent=2, ensure_ascii=False)[:_PREVIEW_PRETTY_CHARS]
        except (json.JSONDecodeError, ValueError):
            pretty = None
    elif is_jsonl_like:
        # Парсим первые несколько строк отдельно.
        lines_pretty: list[str] = []
        for line in text.splitlines()[:10]:
            line = line.strip()
            if not line:
                continue
            try:
                lines_pretty.append(json.dumps(json.loads(line), ensure_ascii=False))
            except (json.JSONDecodeError, ValueError):
                lines_pretty.append(line)
        pretty = "\n".join(lines_pretty)[:_PREVIEW_PRETTY_CHARS]

    return {
        "ok": True,
        "size": size,
        "truncated": truncated,
        "text": text[:_PREVIEW_PRETTY_CHARS],
        "pretty": pretty,
        "is_json": is_json_like,
        "is_jsonl": is_jsonl_like,
    }


def _build_targz_sync(snapshot_dir: Path) -> bytes:
    """Архивирует snapshot dir в tar.gz (in-memory). Cap = 50MB."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for entry in sorted(snapshot_dir.iterdir()):
            if not entry.is_file():
                continue
            tar.add(entry, arcname=f"{snapshot_dir.name}/{entry.name}")
            if buf.tell() > _DOWNLOAD_MAX_BYTES:
                raise ValueError("snapshot_download_too_large")
    return buf.getvalue()


# ── Factory ─────────────────────────────────────────────────────────────────


def build_snapshots_admin_router(ctx: RouterContext) -> APIRouter:
    """Factory: APIRouter для /admin/snapshots и /api/admin/snapshots/*."""
    router = APIRouter(tags=["snapshots-admin"])

    # ── GET /api/admin/snapshots/list ───────────────────────────────────────

    @router.get("/api/admin/snapshots/list")
    async def snapshots_list() -> dict:
        """Возвращает snapshots + grouped-by-date payload."""
        manager = _get_manager(ctx)
        try:
            rows = await asyncio.to_thread(_list_snapshots_enriched, manager)
        except Exception as exc:  # noqa: BLE001
            _logger.error("snapshots_admin.list_failed", error=str(exc))
            raise HTTPException(status_code=500, detail=f"snapshots_list_failed: {exc}") from exc

        groups = _group_by_date(rows)
        total_bytes = sum(int(r.get("total_bytes") or 0) for r in rows)

        return {
            "ok": True,
            "count": len(rows),
            "total_bytes": total_bytes,
            "total_size_human": _humanize_bytes(total_bytes),
            "snapshots": rows,
            "groups": groups,
            "snapshot_root": str(manager.snapshot_root),
            "interval_minutes": manager.interval_minutes,
            "default_interval_minutes": DEFAULT_INTERVAL_MINUTES,
        }

    # ── GET /api/admin/snapshots/{name}/preview ─────────────────────────────

    @router.get("/api/admin/snapshots/{name}/preview")
    async def snapshots_preview(
        name: str,
        file: str = Query(default=""),
    ) -> dict:
        """Возвращает preview одного файла внутри snapshot dir.

        Query ``file=<filename>`` — конкретный .bak файл. Если не указан,
        берём первый файл из каталога.
        """
        manager = _get_manager(ctx)
        snap_dir = _resolve_snapshot_dir(manager, name)

        # Pick target file.
        file_clean = (file or "").strip()
        if file_clean:
            # Защита от traversal в file-параметре.
            if not re.match(r"^[A-Za-z0-9._-]{1,120}$", file_clean):
                raise HTTPException(status_code=400, detail="file_invalid_name")
            target = (snap_dir / file_clean).resolve()
            try:
                target.relative_to(snap_dir.resolve())
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="file_outside_snapshot") from exc
            if not target.exists() or not target.is_file():
                raise HTTPException(status_code=404, detail=f"file_not_found: {file_clean}")
        else:
            files = sorted(p for p in snap_dir.iterdir() if p.is_file())
            if not files:
                raise HTTPException(status_code=404, detail="snapshot_empty")
            target = files[0]

        result = await asyncio.to_thread(_read_preview_sync, target)
        return {
            "ok": result.get("ok", False),
            "snapshot": name,
            "file": target.name,
            "path": str(target),
            **result,
        }

    # ── POST /api/admin/snapshots/trigger ───────────────────────────────────

    @router.post("/api/admin/snapshots/trigger")
    async def snapshots_trigger(
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Создаёт snapshot прямо сейчас (manual trigger)."""
        ctx.assert_write_access_fn(x_krab_web_key, token)
        manager = _get_manager(ctx)

        started = time.time()
        try:
            result = await asyncio.to_thread(manager.snapshot_now, "manual")
        except Exception as exc:  # noqa: BLE001
            _logger.error("snapshots_admin.trigger_failed", error=str(exc))
            raise HTTPException(status_code=500, detail=f"snapshot_trigger_failed: {exc}") from exc

        elapsed = time.time() - started
        _logger.info(
            "snapshots_admin.triggered",
            timestamp=result.get("timestamp"),
            copied=len(result.get("copied") or []),
            skipped=len(result.get("skipped") or []),
            elapsed_sec=round(elapsed, 2),
        )
        return {
            "ok": True,
            "elapsed_sec": round(elapsed, 2),
            **result,
        }

    # ── GET /api/admin/snapshots/{name}/download ────────────────────────────

    @router.get("/api/admin/snapshots/{name}/download")
    async def snapshots_download(name: str) -> Response:
        """Download snapshot dir как tar.gz."""
        manager = _get_manager(ctx)
        snap_dir = _resolve_snapshot_dir(manager, name)

        try:
            data = await asyncio.to_thread(_build_targz_sync, snap_dir)
        except ValueError as exc:
            raise HTTPException(status_code=413, detail=f"snapshot_download_failed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            _logger.error("snapshots_admin.download_failed", name=name, error=str(exc))
            raise HTTPException(status_code=500, detail=f"snapshot_download_failed: {exc}") from exc

        filename = f"{name}.tar.gz"
        return Response(
            content=data,
            media_type="application/gzip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # ── POST /api/admin/snapshots/{name}/restore ────────────────────────────

    @router.post("/api/admin/snapshots/{name}/restore")
    async def snapshots_restore(
        name: str,
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> Response:
        """Restore из snapshot — v1 placeholder. 501 Not Implemented.

        Опасная операция: требует архитектурной проработки (рестарт
        компонентов, atomic apply, lock-coordination). В v1 endpoint
        существует, но возвращает 501 — UI кнопка показывает alert.
        """
        ctx.assert_write_access_fn(x_krab_web_key, token)
        _validate_snapshot_name(name)
        _logger.warning("snapshots_admin.restore_attempted_v1_placeholder", name=name)
        return PlainTextResponse(
            "snapshot_restore_not_implemented_v1: dangerous operation requires "
            "architectural review (component restart, locks, atomic apply). "
            "Use scripts/openclaw_runtime_repair.py for manual recovery.",
            status_code=501,
        )

    # ── GET /admin/snapshots ────────────────────────────────────────────────

    @router.get("/admin/snapshots", response_class=HTMLResponse)
    async def snapshots_admin_page() -> HTMLResponse:
        """HTML страница со списком snapshots и actions."""
        return HTMLResponse(_SNAPSHOTS_ADMIN_PAGE_HTML)

    return router


# ── HTML страница /admin/snapshots ──────────────────────────────────────────
# Клиентский JS строит DOM через createElement/textContent — XSS-safe.

_SNAPSHOTS_ADMIN_PAGE_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Krab · Snapshots Admin</title>
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
        .summary { color: var(--text-muted); margin-bottom: 12px; font-size: 0.85rem; }
        .toolbar { margin-bottom: 16px; }
        button {
            background: rgba(125,211,252,0.1);
            border: 1px solid var(--accent);
            color: var(--accent);
            padding: 6px 14px;
            font-size: 0.85rem;
            border-radius: 4px;
            cursor: pointer;
            margin-right: 6px;
            font-family: inherit;
        }
        button:hover { background: rgba(125,211,252,0.2); }
        button.small { padding: 3px 8px; font-size: 0.75rem; }
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
        .group-title {
            font-size: 0.85rem;
            margin: 24px 0 8px 0;
            color: var(--accent);
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        table {
            width: 100%; border-collapse: collapse;
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 6px; overflow: hidden;
            font-size: 0.88rem;
            margin-bottom: 16px;
        }
        th, td {
            padding: 8px 10px; text-align: left;
            border-bottom: 1px solid var(--border);
            vertical-align: top;
        }
        th {
            background: #1a1a1a; color: var(--text-muted);
            text-transform: uppercase; font-size: 0.72rem;
            letter-spacing: 0.04em;
        }
        tr:last-child td { border-bottom: none; }
        tr:hover { background: rgba(125,211,252,0.04); }
        .badge {
            display: inline-block; padding: 2px 8px;
            border-radius: 4px; font-size: 0.72rem; font-weight: 500;
        }
        .badge-ok { background: rgba(34,197,94,0.15); color: var(--ok); }
        .badge-warn { background: rgba(250,204,21,0.15); color: var(--warn); }
        .badge-muted { background: rgba(255,255,255,0.06); color: var(--text-muted); }
        .badge-pre { background: rgba(239,68,68,0.10); color: var(--err); }
        .err-banner {
            color: var(--err); padding: 12px;
            background: rgba(239,68,68,0.08);
            border-radius: 4px; margin-bottom: 12px;
        }
        #preview-modal {
            display: none;
            position: fixed; top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.85);
            z-index: 100;
            padding: 32px;
            overflow-y: auto;
        }
        #preview-modal.open { display: block; }
        .modal-inner {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 20px;
            max-width: 900px;
            margin: 0 auto;
        }
        .modal-header {
            display: flex; justify-content: space-between; align-items: center;
            margin-bottom: 16px;
        }
        pre.preview {
            background: #000;
            border: 1px solid var(--border);
            padding: 12px;
            border-radius: 4px;
            font-size: 0.8rem;
            white-space: pre-wrap;
            word-break: break-word;
            max-height: 60vh;
            overflow-y: auto;
        }
        .small { font-size: 0.75rem; color: var(--text-muted); }
        .files-list {
            display: flex; flex-wrap: wrap; gap: 4px;
        }
    </style>
</head>
<body>
    <header>
        <h1>🦀 Krab · Snapshots Admin</h1>
        <div class="meta">Polling каждые 60 сек · <span id="last-update">—</span></div>
    </header>
    <main>
        <div id="summary" class="summary">Загружаем snapshots…</div>
        <div id="err-banner"></div>
        <div class="toolbar">
            <button id="btn-trigger">📸 Создать snapshot сейчас</button>
            <button id="btn-refresh" class="small">🔄 Обновить</button>
        </div>
        <div id="groups-container"></div>
    </main>

    <div id="preview-modal">
        <div class="modal-inner">
            <div class="modal-header">
                <div>
                    <strong id="preview-title">—</strong>
                    <div class="small" id="preview-meta"></div>
                </div>
                <button id="btn-close-preview">✕ Закрыть</button>
            </div>
            <pre class="preview" id="preview-body">…</pre>
        </div>
    </div>

    <script>
        async function callApi(method, url) {
            const res = await fetch(url, { method: method });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || ('HTTP ' + res.status));
            return data;
        }
        function fmtAge(sec) {
            if (sec === null || sec === undefined) return '—';
            if (sec < 60) return sec + 's ago';
            if (sec < 3600) return Math.floor(sec / 60) + 'm ago';
            if (sec < 86400) return Math.floor(sec / 3600) + 'h ago';
            return Math.floor(sec / 86400) + 'd ago';
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
            btn.className = 'small ' + (cls || '');
            btn.addEventListener('click', onClick);
            return btn;
        }
        function mkTh(text) {
            const th = document.createElement('th');
            th.textContent = text;
            return th;
        }
        async function showPreview(snapName, fileName) {
            const titleEl = document.getElementById('preview-title');
            const metaEl = document.getElementById('preview-meta');
            const bodyEl = document.getElementById('preview-body');
            titleEl.textContent = snapName + ' / ' + (fileName || '(default)');
            metaEl.textContent = 'Загружаем…';
            bodyEl.textContent = '…';
            document.getElementById('preview-modal').classList.add('open');
            try {
                const url = '/api/admin/snapshots/' + encodeURIComponent(snapName) +
                    '/preview' + (fileName ? '?file=' + encodeURIComponent(fileName) : '');
                const data = await callApi('GET', url);
                const sizeText = data.size + ' bytes' + (data.truncated ? ' (truncated)' : '');
                metaEl.textContent = data.file + ' · ' + sizeText;
                bodyEl.textContent = data.pretty || data.text || '(empty)';
            } catch (e) {
                metaEl.textContent = '';
                bodyEl.textContent = 'Ошибка: ' + e.message;
            }
        }
        function closePreview() {
            document.getElementById('preview-modal').classList.remove('open');
        }
        async function triggerSnapshot() {
            if (!confirm('Создать snapshot всех state-файлов сейчас?')) return;
            try {
                const data = await callApi('POST', '/api/admin/snapshots/trigger');
                alert('Snapshot создан ✅\\nTimestamp: ' + data.timestamp +
                    '\\nСкопировано: ' + (data.copied || []).length +
                    '\\nПропущено: ' + (data.skipped || []).length +
                    '\\nЗатрачено: ' + data.elapsed_sec + 's');
                fetchSnapshots();
            } catch (e) {
                alert('Ошибка: ' + e.message);
            }
        }
        async function restoreSnapshot(snapName) {
            if (!confirm('⚠️ ВОССТАНОВИТЬ из ' + snapName + '?\\n\\nЭто перезапишет текущие state-файлы. Опасная операция.')) return;
            try {
                const res = await fetch('/api/admin/snapshots/' + encodeURIComponent(snapName) + '/restore', {
                    method: 'POST'
                });
                const text = await res.text();
                alert('Restore: HTTP ' + res.status + '\\n' + text);
            } catch (e) {
                alert('Ошибка: ' + e.message);
            }
        }
        function downloadSnapshot(snapName) {
            window.location.href = '/api/admin/snapshots/' + encodeURIComponent(snapName) + '/download';
        }
        function renderSnapshotRow(row) {
            const tr = document.createElement('tr');

            // Timestamp / name cell.
            const tsTd = document.createElement('td');
            tsTd.className = 'mono';
            const strong = document.createElement('strong');
            strong.textContent = row.timestamp;
            tsTd.appendChild(strong);
            if (row.is_pre_restore) {
                tsTd.appendChild(document.createTextNode(' '));
                tsTd.appendChild(mkBadge('pre-restore', 'badge-pre'));
            }
            if (row.timestamp_iso) {
                const sub = document.createElement('div');
                sub.className = 'small';
                sub.textContent = row.timestamp_iso;
                tsTd.appendChild(sub);
            }
            tr.appendChild(tsTd);

            // Files cell.
            const filesTd = document.createElement('td');
            filesTd.className = 'small mono';
            const files = row.files || [];
            const fl = document.createElement('div');
            fl.className = 'files-list';
            for (const f of files.slice(0, 8)) {
                const btn = mkButton('👁 ' + f, () => showPreview(row.timestamp, f));
                fl.appendChild(btn);
            }
            if (files.length > 8) {
                const more = document.createElement('span');
                more.className = 'small';
                more.textContent = ' +' + (files.length - 8) + ' more';
                fl.appendChild(more);
            }
            filesTd.appendChild(fl);
            tr.appendChild(filesTd);

            // Size cell.
            const sizeTd = document.createElement('td');
            sizeTd.className = 'mono';
            sizeTd.textContent = row.size_human || '—';
            tr.appendChild(sizeTd);

            // Age cell.
            const ageTd = document.createElement('td');
            ageTd.className = 'small';
            ageTd.textContent = fmtAge(row.age_sec);
            tr.appendChild(ageTd);

            // Actions cell.
            const actTd = document.createElement('td');
            actTd.appendChild(mkButton('⬇ download', () => downloadSnapshot(row.timestamp)));
            actTd.appendChild(mkButton('♻ restore', () => restoreSnapshot(row.timestamp), 'danger'));
            tr.appendChild(actTd);

            return tr;
        }
        function renderGroup(title, rows) {
            const wrap = document.createElement('div');
            const h = document.createElement('div');
            h.className = 'group-title';
            h.textContent = title + ' · ' + rows.length + ' snapshot(s)';
            wrap.appendChild(h);

            const table = document.createElement('table');
            const thead = document.createElement('thead');
            const headRow = document.createElement('tr');
            headRow.appendChild(mkTh('Timestamp'));
            headRow.appendChild(mkTh('Files (click to preview)'));
            headRow.appendChild(mkTh('Size'));
            headRow.appendChild(mkTh('Age'));
            headRow.appendChild(mkTh('Actions'));
            thead.appendChild(headRow);
            table.appendChild(thead);
            const tbody = document.createElement('tbody');
            for (const r of rows) tbody.appendChild(renderSnapshotRow(r));
            table.appendChild(tbody);
            wrap.appendChild(table);
            return wrap;
        }
        async function fetchSnapshots() {
            const errBanner = document.getElementById('err-banner');
            errBanner.textContent = '';
            try {
                const data = await callApi('GET', '/api/admin/snapshots/list');
                const groups = data.groups || {};
                const container = document.getElementById('groups-container');
                while (container.firstChild) container.removeChild(container.firstChild);

                // Сортируем ключи: сначала свежие YYYY-MM-DD desc, потом "earlier".
                const keys = Object.keys(groups).filter(k => k !== 'earlier').sort().reverse();
                for (const k of keys) {
                    container.appendChild(renderGroup(k, groups[k]));
                }
                if (groups.earlier && groups.earlier.length) {
                    container.appendChild(renderGroup('Старше 7 дней', groups.earlier));
                }

                const summary = document.getElementById('summary');
                summary.textContent = 'Всего snapshots: ' + data.count +
                    ' · Общий размер: ' + data.total_size_human +
                    ' · Интервал: ' + data.interval_minutes + ' мин · ' +
                    'Root: ' + data.snapshot_root;
                document.getElementById('last-update').textContent =
                    new Date().toLocaleTimeString('ru-RU', { hour12: false });
            } catch (e) {
                const banner = document.createElement('div');
                banner.className = 'err-banner';
                banner.textContent = 'Ошибка загрузки: ' + e.message;
                errBanner.appendChild(banner);
            }
        }
        document.getElementById('btn-trigger').addEventListener('click', triggerSnapshot);
        document.getElementById('btn-refresh').addEventListener('click', fetchSnapshots);
        document.getElementById('btn-close-preview').addEventListener('click', closePreview);
        document.getElementById('preview-modal').addEventListener('click', (e) => {
            if (e.target.id === 'preview-modal') closePreview();
        });
        fetchSnapshots();
        setInterval(fetchSnapshots, 60000);
    </script>
</body>
</html>
"""
