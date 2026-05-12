# -*- coding: utf-8 -*-
"""
Logs admin router — Wave 169 (Session 48).

Owner-panel страница ``/admin/logs`` + JSON API для tail+grep+filter
поверх structlog лога ``~/.openclaw/krab_runtime_state/krab_main.log``
(см. ``src/core/logger.py:_resolve_log_file``).

Endpoints:
- GET  /api/admin/logs/tail?n=200&level=INFO&grep=...  — JSON last-N lines
- GET  /api/admin/logs/download?n=1000                 — text/plain attachment
- GET  /admin/logs                                       — HTML page (polling 5s)

Поведение:
- Файл может быть огромным (production: > 1 GB) → используем reverse-read
  с seek-to-end + чтение блоками назад. Жёсткий cap ``_MAX_SCAN_BYTES=5 MiB``
  предотвращает OOM на любых файлах.
- structlog (`ConsoleRenderer`) пишет ANSI escape sequences для уровня /
  ключей в TTY-стиле — мы strip-аем их перед сравнением/выдачей.
- Парсер допускает обе формы: classic ``ts [level    ] event ...``
  и формы с meta-маркерами (`==== Krab detached start ...`, `[launcher] ...`).
- Read-only endpoint — auth не требуется (owner-panel биндится на 127.0.0.1).

Match style of ``src/modules/web_routers/cron_admin_router.py``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, Response

from src.core.logger import get_logger

from ._context import RouterContext

_logger = get_logger(__name__)

# ── Конфигурация ────────────────────────────────────────────────────────────

# Жёсткий cap чтобы reverse-scan не вышел за пределы (предохранитель против
# гигантских файлов и патологически длинных строк).
_MAX_SCAN_BYTES = 5 * 1024 * 1024  # 5 MiB
_READ_CHUNK_SIZE = 64 * 1024  # 64 KiB

# Лимиты для query-параметров (защита от DoS).
_MAX_TAIL_LINES = 2000
_MAX_DOWNLOAD_LINES = 10000
_DEFAULT_TAIL_LINES = 200
_DEFAULT_DOWNLOAD_LINES = 1000

# Допустимые уровни (UI отправляет один из).
_VALID_LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "")

# ── Парсинг ─────────────────────────────────────────────────────────────────
# structlog ConsoleRenderer форматирует строки так:
#   "2026-05-13 01:25:14 [info     ] event_name      key=value module=..."
# ANSI escapes могут оборачивать level и ключи (если файл-handler писался без
# strip — structlog не убирает их). Сначала режем escapes, потом парсим.

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[mGKHF]")

# Поддерживаем уровни любого регистра, с trailing whitespace (`info    `).
_LOG_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})"
    r"\s+\["
    r"(?P<level>[A-Za-z]+)\s*"
    r"\]\s+"
    r"(?P<rest>.*)$"
)

# Из остатка вытаскиваем event_name (первое слово) + опциональный module=…
_MODULE_RE = re.compile(r"module=([\w.\-]+)")


def _strip_ansi(text: str) -> str:
    """Убирает ANSI escape sequences из строки лога."""
    return _ANSI_ESCAPE_RE.sub("", text)


def _parse_log_line(raw: str) -> dict[str, Any]:
    """Парсит одну строку лога в dict {ts, level, module, message, raw}.

    Графа structlog ConsoleRenderer: ``ts [level    ] event key=val ...``.
    Для не-парсящихся строк (meta-маркеры, launcher prefix) возвращаем
    ``level=""`` и весь текст в ``message`` — UI отрендерит их как plain.
    """
    clean = _strip_ansi(raw).rstrip("\n")
    match = _LOG_LINE_RE.match(clean)
    if not match:
        return {
            "ts": "",
            "level": "",
            "module": "",
            "message": clean,
            "raw": clean,
        }
    ts = match.group("ts")
    level = match.group("level").upper().strip()
    rest = match.group("rest").strip()
    module_match = _MODULE_RE.search(rest)
    module = module_match.group(1) if module_match else ""
    # event_name + аргументы — оставляем как message без module=… дубля.
    if module:
        message = _MODULE_RE.sub("", rest, count=1).strip()
    else:
        message = rest
    return {
        "ts": ts,
        "level": level,
        "module": module,
        "message": message,
        "raw": clean,
    }


# ── Поиск файла лога ─────────────────────────────────────────────────────────


def _resolve_log_path() -> Path | None:
    """Возвращает path к krab_main.log — re-uses логику из src.core.logger.

    Если KRAB_LOG_FILE отключён ("" или "none") — возвращаем None.
    """
    # Не импортируем _resolve_log_file напрямую — он private; повторяем
    # ту же логику, чтобы router был независим.
    raw = os.environ.get("KRAB_LOG_FILE")
    if raw is not None:
        if raw == "" or raw.lower() == "none":
            return None
        return Path(raw).expanduser()
    base = os.environ.get("KRAB_RUNTIME_STATE_DIR")
    base_dir = Path(base).expanduser() if base else Path.home() / ".openclaw" / "krab_runtime_state"
    return base_dir / "krab_main.log"


# ── Reverse-read бэкенд ──────────────────────────────────────────────────────


def _read_last_lines_reverse(
    path: Path,
    *,
    max_lines: int,
    max_scan_bytes: int = _MAX_SCAN_BYTES,
) -> tuple[list[str], bool, int]:
    """Читает последние ``max_lines`` строк из ``path`` reverse-сканом.

    Returns:
        (lines, truncated, scanned_bytes) — ``lines`` в порядке от старых к
        новым (как обычный tail), ``truncated`` = True если уперлись в cap
        раньше, чем набрали max_lines.
    """
    if not path.exists():
        return ([], False, 0)
    try:
        file_size = path.stat().st_size
    except OSError as exc:
        _logger.warning("logs_admin.stat_failed", path=str(path), error=str(exc))
        return ([], False, 0)
    if file_size == 0:
        return ([], False, 0)

    scan_limit = min(file_size, max_scan_bytes)
    truncated = False
    collected: list[bytes] = []  # обратном порядке (последняя строка первая)
    buffer = b""
    scanned = 0

    try:
        with open(path, "rb") as fp:
            pos = file_size
            while pos > 0 and scanned < scan_limit and len(collected) <= max_lines:
                read_size = min(_READ_CHUNK_SIZE, pos, scan_limit - scanned)
                pos -= read_size
                fp.seek(pos)
                chunk = fp.read(read_size)
                scanned += read_size
                buffer = chunk + buffer
                # Разбиваем по newline'ам, оставляя partial голову в buffer.
                lines_part = buffer.split(b"\n")
                # Голова (lines_part[0]) — недочитанная строка, кладём обратно.
                buffer = lines_part[0]
                for line in reversed(lines_part[1:]):
                    if line == b"":
                        continue
                    collected.append(line)
                    if len(collected) > max_lines:
                        break
                if scanned >= scan_limit and pos > 0:
                    truncated = True
                    break
            # Если дочитали до начала файла — последний buffer тоже строка.
            if pos == 0 and buffer:
                collected.append(buffer)
    except OSError as exc:
        _logger.warning("logs_admin.read_failed", path=str(path), error=str(exc))
        return ([], False, scanned)

    # collected содержит строки от новых к старым → реверсим.
    final = [b.decode("utf-8", errors="replace") for b in reversed(collected)]
    if len(final) > max_lines:
        final = final[-max_lines:]
    return (final, truncated, scanned)


# ── Фильтры ─────────────────────────────────────────────────────────────────

# Числовой ранг уровней (для "level=INFO → INFO и выше").
_LEVEL_RANK = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}


def _passes_level(entry_level: str, min_level: str) -> bool:
    """True если уровень записи >= min_level. Не-парсящиеся (level="")
    пропускаем чтобы meta-строки (launcher header) не пропадали."""
    if not min_level:
        return True
    if not entry_level:
        # Не-распарсенные строки показываем только при DEBUG (минимум).
        return min_level.upper() == "DEBUG"
    threshold = _LEVEL_RANK.get(min_level.upper(), 20)
    actual = _LEVEL_RANK.get(entry_level.upper(), 20)
    return actual >= threshold


def _passes_grep(entry_raw: str, grep: str) -> bool:
    """Case-insensitive substring match. Пустой grep → True."""
    if not grep:
        return True
    return grep.lower() in entry_raw.lower()


def _apply_filters(
    parsed: list[dict[str, Any]],
    *,
    level: str,
    grep: str,
) -> list[dict[str, Any]]:
    """Возвращает срез parsed, прошедший level+grep фильтры."""
    result = []
    for entry in parsed:
        if not _passes_level(entry["level"], level):
            continue
        if not _passes_grep(entry["raw"], grep):
            continue
        result.append(entry)
    return result


# ── HTTP factory ─────────────────────────────────────────────────────────────


def build_logs_admin_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с endpoints для tail+grep+download."""
    # ctx сейчас не используется — endpoints read-only. Сигнатура сохранена
    # для совместимости с remainder admin routers (pattern из cron/sentry).
    _ = ctx
    router = APIRouter(tags=["logs-admin"])

    # ── GET /api/admin/logs/tail ───────────────────────────────────────────

    @router.get("/api/admin/logs/tail")
    async def logs_tail(
        n: int = Query(default=_DEFAULT_TAIL_LINES, ge=1, le=_MAX_TAIL_LINES),
        level: str = Query(default=""),
        grep: str = Query(default=""),
    ) -> dict[str, Any]:
        """Возвращает последние N строк лога с применёнными фильтрами.

        Args:
            n: количество строк ДО фильтрации (cap _MAX_TAIL_LINES).
            level: минимальный уровень (DEBUG/INFO/WARNING/ERROR/CRITICAL).
            grep: case-insensitive substring filter (по raw line).

        Returns:
            {lines: [{ts, level, module, message, raw}], total_scanned, truncated}
        """
        level_upper = (level or "").upper().strip()
        if level_upper not in _VALID_LEVELS:
            raise HTTPException(
                status_code=400,
                detail=f"logs_invalid_level: {level} (allowed: {_VALID_LEVELS})",
            )
        path = _resolve_log_path()
        if path is None:
            return {
                "ok": True,
                "lines": [],
                "total_scanned": 0,
                "truncated": False,
                "path": None,
                "note": "log_file_disabled",
            }
        if not path.exists():
            return {
                "ok": True,
                "lines": [],
                "total_scanned": 0,
                "truncated": False,
                "path": str(path),
                "note": "log_file_missing",
            }

        raw_lines, truncated, scanned = _read_last_lines_reverse(path, max_lines=n)
        parsed = [_parse_log_line(line) for line in raw_lines]
        filtered = _apply_filters(parsed, level=level_upper, grep=grep or "")
        return {
            "ok": True,
            "lines": filtered,
            "total_scanned": len(parsed),
            "truncated": truncated,
            "path": str(path),
        }

    # ── GET /api/admin/logs/download ───────────────────────────────────────

    @router.get("/api/admin/logs/download")
    async def logs_download(
        n: int = Query(default=_DEFAULT_DOWNLOAD_LINES, ge=1, le=_MAX_DOWNLOAD_LINES),
    ) -> Response:
        """Скачивает последние N строк в plain text (без ANSI escapes)."""
        path = _resolve_log_path()
        if path is None or not path.exists():
            return Response(
                content="log_file_not_available\n",
                media_type="text/plain",
                status_code=200,
            )
        raw_lines, truncated, _scanned = _read_last_lines_reverse(path, max_lines=n)
        # Strip ANSI перед отдачей — owner просит plain text.
        cleaned = [_strip_ansi(line) for line in raw_lines]
        header = (
            f"# krab_main.log tail (last {len(cleaned)} lines"
            f"{', truncated at 5MiB scan cap' if truncated else ''})\n"
            f"# source: {path}\n"
            f"# request_n: {n}\n\n"
        )
        body = "\n".join(cleaned) + "\n"
        filename = f"krab_main_tail_{n}.log"
        return Response(
            content=header + body,
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )

    # ── GET /admin/logs — HTML ─────────────────────────────────────────────

    @router.get("/admin/logs", response_class=HTMLResponse)
    async def logs_admin_page() -> HTMLResponse:
        """HTML страница со списком логов (polling 5s)."""
        return HTMLResponse(_LOGS_ADMIN_PAGE_HTML)

    return router


# ── HTML страница ────────────────────────────────────────────────────────────
# XSS-safe: создаём элементы через createElement/textContent.
# Стиль скопирован с cron_admin_router для единства.

_LOGS_ADMIN_PAGE_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Krab · Logs Admin</title>
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
            --crit: #f87171;
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
        .controls {
            display: flex; gap: 12px; flex-wrap: wrap;
            margin-bottom: 12px;
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 10px 14px;
        }
        .controls label {
            display: flex; flex-direction: column; gap: 4px;
            font-size: 0.75rem; color: var(--text-muted);
            text-transform: uppercase; letter-spacing: 0.05em;
        }
        .controls input, .controls select {
            background: #0a0a0a;
            border: 1px solid var(--border);
            color: var(--text);
            padding: 4px 8px;
            border-radius: 4px;
            font-family: inherit;
            font-size: 0.85rem;
            min-width: 100px;
        }
        .controls input:focus, .controls select:focus {
            outline: none; border-color: var(--accent);
        }
        .controls .grep-input { min-width: 240px; }
        .actions { display: flex; gap: 8px; align-items: flex-end; }
        button {
            background: rgba(125,211,252,0.1);
            border: 1px solid var(--accent);
            color: var(--accent);
            padding: 5px 12px;
            font-size: 0.85rem;
            border-radius: 4px;
            cursor: pointer;
            font-family: inherit;
        }
        button:hover { background: rgba(125,211,252,0.2); }
        button.secondary {
            border-color: var(--text-muted); color: var(--text-muted);
            background: rgba(255,255,255,0.04);
        }
        button.secondary:hover { background: rgba(255,255,255,0.1); }
        table {
            width: 100%; border-collapse: collapse;
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 6px; overflow: hidden;
            font-size: 0.82rem;
        }
        th, td {
            padding: 6px 10px; text-align: left;
            border-bottom: 1px solid var(--border);
            vertical-align: top;
        }
        th {
            background: #1a1a1a; color: var(--text-muted);
            text-transform: uppercase; font-size: 0.7rem;
            letter-spacing: 0.04em; position: sticky; top: 0;
        }
        tr:last-child td { border-bottom: none; }
        td.ts { white-space: nowrap; color: var(--text-muted); }
        td.module { white-space: nowrap; color: var(--accent); }
        td.message { word-break: break-word; }
        tr.level-WARNING td.message { color: var(--warn); }
        tr.level-WARNING td.lvl { color: var(--warn); font-weight: 600; }
        tr.level-ERROR td.message { color: var(--err); }
        tr.level-ERROR td.lvl { color: var(--err); font-weight: 600; }
        tr.level-CRITICAL td.message { color: var(--crit); font-weight: 600; }
        tr.level-CRITICAL td.lvl { color: var(--crit); font-weight: 700; }
        tr.level-CRITICAL td { background: rgba(248,113,113,0.05); }
        .summary { color: var(--text-muted); margin-bottom: 12px; font-size: 0.85rem; }
        .err-banner { color: var(--err); padding: 12px;
            background: rgba(239,68,68,0.08); border-radius: 4px; margin-bottom: 12px; }
        .truncated-note {
            color: var(--warn); font-size: 0.8rem; margin-top: 8px;
            padding: 8px 12px; background: rgba(250,204,21,0.08);
            border-left: 3px solid var(--warn); border-radius: 4px;
        }
    </style>
</head>
<body>
    <header>
        <h1>📋 Krab · Logs Admin</h1>
        <div class="meta">Polling каждые 5 сек · <span id="last-update">—</span></div>
    </header>
    <main>
        <div class="controls">
            <label>Tail lines
                <input type="number" id="ctrl-n" value="200" min="1" max="2000">
            </label>
            <label>Level
                <select id="ctrl-level">
                    <option value="">(all)</option>
                    <option value="DEBUG">DEBUG+</option>
                    <option value="INFO" selected>INFO+</option>
                    <option value="WARNING">WARNING+</option>
                    <option value="ERROR">ERROR+</option>
                    <option value="CRITICAL">CRITICAL</option>
                </select>
            </label>
            <label>Grep (case-insensitive)
                <input type="text" id="ctrl-grep" class="grep-input" placeholder="substring filter">
            </label>
            <label>Auto-refresh
                <select id="ctrl-auto">
                    <option value="5000" selected>5s</option>
                    <option value="15000">15s</option>
                    <option value="60000">60s</option>
                    <option value="0">off</option>
                </select>
            </label>
            <div class="actions">
                <button id="btn-refresh">Refresh</button>
                <button id="btn-download" class="secondary">Download 1000</button>
            </div>
        </div>
        <div id="summary" class="summary">Загружаем логи…</div>
        <div id="err-banner"></div>
        <div id="truncated-note"></div>
        <table id="logs-table">
            <thead>
                <tr>
                    <th style="width: 150px;">Time</th>
                    <th style="width: 80px;">Level</th>
                    <th style="width: 180px;">Module</th>
                    <th>Message</th>
                </tr>
            </thead>
            <tbody id="logs-body"></tbody>
        </table>
    </main>
    <script>
        // Безопасное создание DOM-узлов (без innerHTML с пользовательскими данными).
        function mkCell(text, cls) {
            const td = document.createElement('td');
            if (cls) td.className = cls;
            td.textContent = text || '';
            return td;
        }
        function clearChildren(el) {
            while (el.firstChild) el.removeChild(el.firstChild);
        }

        let timerHandle = null;

        function buildTailUrl() {
            const n = parseInt(document.getElementById('ctrl-n').value, 10) || 200;
            const level = document.getElementById('ctrl-level').value || '';
            const grep = document.getElementById('ctrl-grep').value || '';
            const params = new URLSearchParams();
            params.set('n', String(Math.min(2000, Math.max(1, n))));
            if (level) params.set('level', level);
            if (grep) params.set('grep', grep);
            return '/api/admin/logs/tail?' + params.toString();
        }

        function buildDownloadUrl() {
            return '/api/admin/logs/download?n=1000';
        }

        async function fetchLogs() {
            const errBanner = document.getElementById('err-banner');
            clearChildren(errBanner);
            try {
                const res = await fetch(buildTailUrl());
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const data = await res.json();
                const lines = data.lines || [];
                const tbody = document.getElementById('logs-body');
                clearChildren(tbody);
                for (const e of lines) {
                    const tr = document.createElement('tr');
                    const lvl = (e.level || '').toUpperCase();
                    if (lvl) tr.className = 'level-' + lvl;
                    tr.appendChild(mkCell(e.ts, 'ts mono'));
                    tr.appendChild(mkCell(lvl || '—', 'lvl mono'));
                    tr.appendChild(mkCell(e.module, 'module mono'));
                    tr.appendChild(mkCell(e.message, 'message mono'));
                    tbody.appendChild(tr);
                }
                const summary = document.getElementById('summary');
                clearChildren(summary);
                summary.appendChild(document.createTextNode(
                    'Показано: ' + lines.length +
                    ' · просканировано: ' + (data.total_scanned || 0) +
                    ' · источник: '
                ));
                const src = document.createElement('span');
                src.className = 'mono';
                src.textContent = data.path || '—';
                summary.appendChild(src);
                const note = document.getElementById('truncated-note');
                clearChildren(note);
                if (data.truncated) {
                    const div = document.createElement('div');
                    div.className = 'truncated-note';
                    div.textContent = '⚠ scan capped at 5 MiB — increase tail.n или используй grep чтобы сузить.';
                    note.appendChild(div);
                }
                if (data.note) {
                    const div = document.createElement('div');
                    div.className = 'truncated-note';
                    div.textContent = 'note: ' + data.note;
                    note.appendChild(div);
                }
                document.getElementById('last-update').textContent =
                    new Date().toLocaleTimeString('ru-RU', { hour12: false });
            } catch (e) {
                const banner = document.createElement('div');
                banner.className = 'err-banner';
                banner.textContent = 'Ошибка загрузки: ' + e.message;
                errBanner.appendChild(banner);
            }
        }

        function setupAutoRefresh() {
            if (timerHandle) { clearInterval(timerHandle); timerHandle = null; }
            const ms = parseInt(document.getElementById('ctrl-auto').value, 10);
            if (ms > 0) timerHandle = setInterval(fetchLogs, ms);
        }

        document.getElementById('btn-refresh').addEventListener('click', fetchLogs);
        document.getElementById('btn-download').addEventListener('click', () => {
            window.location.href = buildDownloadUrl();
        });
        document.getElementById('ctrl-auto').addEventListener('change', setupAutoRefresh);
        for (const id of ['ctrl-n', 'ctrl-level', 'ctrl-grep']) {
            document.getElementById(id).addEventListener('change', fetchLogs);
        }
        // Initial load + auto-refresh.
        fetchLogs();
        setupAutoRefresh();
    </script>
</body>
</html>
"""
