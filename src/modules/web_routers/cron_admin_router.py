# -*- coding: utf-8 -*-
"""
Cron admin router — Wave 165 (Session 48).

Owner-side панель для всех launchd cron-агентов Krab. Эндпоинты ниже
дают status enumeration (listing) и controlling (trigger/pause/resume)
поверх ``launchctl`` CLI. Расписания (StartCalendarInterval /
StartInterval) парсятся из plist-файлов в ``scripts/launchagents/``
или ``~/Library/LaunchAgents/``. last_run/exit_code/skip_reason
читаются из ``~/.openclaw/krab_runtime_state/health_watcher.json``
(Wave 75) — graceful fallback если файл отсутствует.

Endpoints (READY):
- GET  /api/admin/cron/list           — enumeration всех LaunchAgent статусов.
- POST /api/admin/cron/{label}/trigger — launchctl kickstart -k (write).
- POST /api/admin/cron/{label}/pause   — launchctl bootout (write).
- POST /api/admin/cron/{label}/resume  — launchctl bootstrap (write).
- GET  /admin/cron                      — HTML page с polling каждые 30s.

Контракт безопасности: все write эндпоинты идут через
``ctx.assert_write_access`` (X-Krab-Web-Key / token), label
сверяется с allow-list, плюс sanitize через regex.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import HTMLResponse

from src.core.logger import get_logger
from src.core.subprocess_env import clean_subprocess_env

from ._context import RouterContext

_logger = get_logger(__name__)

# Wave 75: health_watcher state файл.
_HEALTH_WATCHER_STATE = Path.home() / ".openclaw" / "krab_runtime_state" / "health_watcher.json"

# Каталог plist-файлов в репозитории (источник правды для schedule).
_REPO_LAUNCHAGENTS_DIR = Path(__file__).resolve().parents[3] / "scripts" / "launchagents"
# Каталог plist-файлов, реально загруженных в launchd.
_USER_LAUNCHAGENTS_DIR = Path.home() / "Library" / "LaunchAgents"

# Безопасное regex для label — только разрешённые символы.
_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")

# Whitelist префиксов label (защита от ../../ и unrelated labels).
_ALLOWED_LABEL_PREFIXES = ("ai.krab.", "ai.openclaw.", "com.krab.")


def _validate_label(label: str) -> str:
    """Sanitize/validate label перед использованием в launchctl."""
    label = (label or "").strip()
    if not label or not _LABEL_PATTERN.match(label):
        raise HTTPException(status_code=400, detail="cron_invalid_label")
    if not any(label.startswith(prefix) for prefix in _ALLOWED_LABEL_PREFIXES):
        raise HTTPException(status_code=403, detail="cron_label_not_allowed")
    return label


def _run_launchctl(args: list[str], *, timeout: float = 10.0) -> dict[str, Any]:
    """Запуск launchctl с чистым env, без shell."""
    cmd = ["/bin/launchctl", *args]
    try:
        proc = subprocess.run(
            cmd,
            env=clean_subprocess_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": "launchctl_timeout",
        }
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
    }


def _launchctl_list_parse(stdout: str) -> dict[str, dict[str, Any]]:
    """Парсинг табличного вывода `launchctl list` → {label: {pid, exit_code}}.

    Колонки: PID\tStatus\tLabel — PID/Status может быть '-' для остановленных.
    """
    result: dict[str, dict[str, Any]] = {}
    for line in stdout.splitlines():
        line = line.rstrip()
        if not line or line.startswith("PID"):
            continue
        parts = line.split("\t") if "\t" in line else line.split()
        if len(parts) < 3:
            continue
        pid_raw, status_raw, label = parts[0], parts[1], parts[-1]
        if not any(label.startswith(p) for p in _ALLOWED_LABEL_PREFIXES):
            continue
        try:
            pid: int | None = int(pid_raw)
        except (TypeError, ValueError):
            pid = None
        try:
            exit_code: int | None = int(status_raw)
        except (TypeError, ValueError):
            exit_code = None
        result[label] = {"pid": pid, "exit_code": exit_code}
    return result


def _find_plist_path(label: str) -> Path | None:
    """Найти plist-файл для label: сначала смотрим в загруженных
    (~/Library/LaunchAgents), затем в repo-копии."""
    candidates = [
        _USER_LAUNCHAGENTS_DIR / f"{label}.plist",
        _REPO_LAUNCHAGENTS_DIR / f"{label}.plist",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def _format_schedule(plist_data: dict[str, Any]) -> str:
    """Преобразует StartCalendarInterval или StartInterval в читаемую строку."""
    sci = plist_data.get("StartCalendarInterval")
    if isinstance(sci, dict):
        parts: list[str] = []
        if "Hour" in sci or "Minute" in sci:
            hour = sci.get("Hour", "*")
            minute = sci.get("Minute", "0")
            if isinstance(hour, int) and isinstance(minute, int):
                parts.append(f"{hour:02d}:{minute:02d}")
            else:
                parts.append(f"{hour}:{minute}")
        if "Weekday" in sci:
            parts.append(f"weekday={sci['Weekday']}")
        if "Day" in sci:
            parts.append(f"day={sci['Day']}")
        return " ".join(parts) if parts else "calendar"
    if isinstance(sci, list):
        return f"calendar x {len(sci)}"
    si = plist_data.get("StartInterval")
    if isinstance(si, int):
        if si >= 3600 and si % 3600 == 0:
            return f"every {si // 3600}h"
        if si >= 60 and si % 60 == 0:
            return f"every {si // 60}m"
        return f"every {si}s"
    if plist_data.get("KeepAlive"):
        return "keep-alive"
    if plist_data.get("RunAtLoad"):
        return "at-load"
    return "unknown"


def _load_health_watcher_state() -> dict[str, Any]:
    """Возвращает Wave 75 health_watcher.json или пустой dict.

    Поля: panel_down_count, gateway_down_count, last_check_utc,
    last_checks{}, last_actions[].
    """
    try:
        if _HEALTH_WATCHER_STATE.exists():
            return json.loads(_HEALTH_WATCHER_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("cron_admin.health_watcher_read_failed", error=str(exc))
    return {}


def _mtime_or_none(path: Path | None) -> float | None:
    """Возвращает mtime если путь существует."""
    if path is None or not path.exists():
        return None
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _interval_seconds(plist_data: dict[str, Any]) -> int | None:
    """Возвращает интервал в секундах для StartInterval (для overdue detection)."""
    si = plist_data.get("StartInterval")
    if isinstance(si, int) and si > 0:
        return si
    return None


def _is_overdue(last_run_ts: float | None, interval_sec: int | None) -> bool:
    """Считаем overdue если для StartInterval-cron прошло >2× интервала."""
    if last_run_ts is None or interval_sec is None:
        return False
    return (time.time() - last_run_ts) > (interval_sec * 2)


def _read_log_file_mtime(plist_data: dict[str, Any]) -> tuple[float | None, str | None]:
    """Достаём mtime лог-файла как proxy для last_run + статус последнего exit."""
    stdout_path = plist_data.get("StandardOutPath") or plist_data.get("StandardErrorPath")
    if not isinstance(stdout_path, str):
        return None, None
    return _mtime_or_none(Path(stdout_path)), stdout_path


def _enumerate_agents() -> list[dict[str, Any]]:
    """Главная функция: enumerate все LaunchAgents Krab+OpenClaw."""
    listing = _run_launchctl(["list"])
    state_table = _launchctl_list_parse(listing.get("stdout", "")) if listing.get("ok") else {}

    # Соберём union из launchctl и из plist-файлов на диске
    # (есть unloaded plists — мы покажем их как "stopped").
    labels: set[str] = set(state_table.keys())
    for directory in (_REPO_LAUNCHAGENTS_DIR, _USER_LAUNCHAGENTS_DIR):
        if not directory.exists():
            continue
        for plist_file in directory.glob("ai.krab.*.plist"):
            labels.add(plist_file.stem)
        for plist_file in directory.glob("ai.openclaw.*.plist"):
            labels.add(plist_file.stem)
        for plist_file in directory.glob("com.krab.*.plist"):
            labels.add(plist_file.stem)

    health_state = _load_health_watcher_state()
    health_last_actions = health_state.get("last_actions") or []
    skip_reason_by_label: dict[str, str] = {}
    if isinstance(health_last_actions, list):
        for entry in health_last_actions:
            if not isinstance(entry, dict):
                continue
            target = str(entry.get("label") or entry.get("target") or "").strip()
            if target:
                skip_reason_by_label[target] = str(entry.get("reason") or "")

    agents: list[dict[str, Any]] = []
    for label in sorted(labels):
        plist_path = _find_plist_path(label)
        plist_data: dict[str, Any] = {}
        if plist_path:
            try:
                with open(plist_path, "rb") as fp:
                    plist_data = plistlib.load(fp)
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "cron_admin.plist_read_failed",
                    label=label,
                    path=str(plist_path),
                    error=str(exc),
                )

        schedule_str = _format_schedule(plist_data)
        interval_sec = _interval_seconds(plist_data)
        log_mtime, log_path = _read_log_file_mtime(plist_data)

        state = state_table.get(label, {})
        pid = state.get("pid")
        exit_code = state.get("exit_code")

        # Если процесс запущен (pid != None и != -) — last_run ~= now
        if pid:
            last_run_ts: float | None = time.time()
            last_run_source = "running"
        else:
            last_run_ts = log_mtime
            last_run_source = "log_mtime" if log_mtime else "unknown"

        agents.append(
            {
                "label": label,
                "schedule": schedule_str,
                "interval_sec": interval_sec,
                "loaded": label in state_table,
                "pid": pid,
                "exit_code": exit_code,
                "last_run_ts": last_run_ts,
                "last_run_source": last_run_source,
                "last_run_iso": (
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(last_run_ts))
                    if last_run_ts
                    else None
                ),
                "last_skip_reason": skip_reason_by_label.get(label, ""),
                "is_overdue": _is_overdue(last_run_ts, interval_sec),
                "log_path": log_path,
                "plist_path": str(plist_path) if plist_path else None,
            }
        )

    return agents


def build_cron_admin_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с endpoints управления launchd-cron."""
    router = APIRouter(tags=["cron-admin"])

    # ── GET /api/admin/cron/list ────────────────────────────────────────────

    @router.get("/api/admin/cron/list")
    async def cron_list() -> dict:
        """Возвращает enumeration всех launchd-агентов Krab+OpenClaw.

        Источники данных:
          • `launchctl list` — runtime status (pid, last exit_code).
          • plist-файлы из ``scripts/launchagents/`` и
            ``~/Library/LaunchAgents/`` — schedule, log paths.
          • ``~/.openclaw/krab_runtime_state/health_watcher.json`` —
            last_skip_reason (Wave 75).
        """
        try:
            agents = _enumerate_agents()
        except Exception as exc:  # noqa: BLE001
            _logger.error("cron_admin.list_failed", error=str(exc))
            raise HTTPException(status_code=500, detail=f"cron_list_failed: {exc}") from exc

        health_state = _load_health_watcher_state()
        return {
            "ok": True,
            "count": len(agents),
            "agents": agents,
            "health_watcher": {
                "last_check_utc": health_state.get("last_check_utc"),
                "panel_down_count": health_state.get("panel_down_count", 0),
                "gateway_down_count": health_state.get("gateway_down_count", 0),
            },
        }

    # ── POST /api/admin/cron/{label}/trigger ────────────────────────────────

    @router.post("/api/admin/cron/{label}/trigger")
    async def cron_trigger(
        label: str,
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Принудительный запуск cron-job через `launchctl kickstart -k`."""
        ctx.assert_write_access_fn(x_krab_web_key, token)
        label = _validate_label(label)
        uid = os.getuid()
        result = _run_launchctl(["kickstart", "-k", f"gui/{uid}/{label}"])
        _logger.info(
            "cron_admin.trigger",
            label=label,
            returncode=result.get("returncode"),
        )
        if not result.get("ok"):
            raise HTTPException(
                status_code=500,
                detail=f"cron_trigger_failed: {result.get('stderr') or 'unknown'}",
            )
        return {"ok": True, "label": label, "result": result}

    # ── POST /api/admin/cron/{label}/pause ──────────────────────────────────

    @router.post("/api/admin/cron/{label}/pause")
    async def cron_pause(
        label: str,
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Останавливает (unload) cron-job через `launchctl bootout`."""
        ctx.assert_write_access_fn(x_krab_web_key, token)
        label = _validate_label(label)
        uid = os.getuid()
        result = _run_launchctl(["bootout", f"gui/{uid}/{label}"])
        _logger.info(
            "cron_admin.pause",
            label=label,
            returncode=result.get("returncode"),
        )
        # bootout returncode != 0 если сервис уже unloaded — это OK.
        return {
            "ok": True,
            "label": label,
            "result": result,
            "warning": "" if result.get("ok") else "already_unloaded_or_failed",
        }

    # ── POST /api/admin/cron/{label}/resume ─────────────────────────────────

    @router.post("/api/admin/cron/{label}/resume")
    async def cron_resume(
        label: str,
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Загружает (bootstrap) cron-job обратно из plist."""
        ctx.assert_write_access_fn(x_krab_web_key, token)
        label = _validate_label(label)
        plist_path = _find_plist_path(label)
        if plist_path is None:
            raise HTTPException(
                status_code=404,
                detail=f"cron_resume_plist_not_found: {label}",
            )
        uid = os.getuid()
        result = _run_launchctl(["bootstrap", f"gui/{uid}", str(plist_path)])
        _logger.info(
            "cron_admin.resume",
            label=label,
            plist=str(plist_path),
            returncode=result.get("returncode"),
        )
        if not result.get("ok"):
            raise HTTPException(
                status_code=500,
                detail=f"cron_resume_failed: {result.get('stderr') or 'unknown'}",
            )
        return {
            "ok": True,
            "label": label,
            "plist": str(plist_path),
            "result": result,
        }

    # ── GET /admin/cron — HTML page ─────────────────────────────────────────

    @router.get("/admin/cron", response_class=HTMLResponse)
    async def cron_admin_page() -> HTMLResponse:
        """HTML страница со списком cron-агентов (polling 30s)."""
        return HTMLResponse(_CRON_ADMIN_PAGE_HTML)

    return router


# ── HTML страница /admin/cron ────────────────────────────────────────────────
# Клиентский JS строит DOM через createElement/textContent чтобы не
# полагаться на innerHTML с внешними строками (XSS-safe).

_CRON_ADMIN_PAGE_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Krab · Cron Admin</title>
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
        }
        th, td {
            padding: 8px 10px; text-align: left;
            border-bottom: 1px solid var(--border);
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
            padding: 4px 10px;
            font-size: 0.75rem;
            border-radius: 4px;
            cursor: pointer;
            margin-right: 4px;
            font-family: inherit;
        }
        button:hover { background: rgba(125,211,252,0.2); }
        button.danger { border-color: var(--err); color: var(--err); background: rgba(239,68,68,0.08); }
        button.danger:hover { background: rgba(239,68,68,0.18); }
        .summary { color: var(--text-muted); margin-bottom: 12px; font-size: 0.85rem; }
        .err-banner { color: var(--err); padding: 12px;
            background: rgba(239,68,68,0.08); border-radius: 4px; margin-bottom: 12px; }
    </style>
</head>
<body>
    <header>
        <h1>🦀 Krab · Cron Admin</h1>
        <div class="meta">Polling каждые 30 сек · <span id="last-update">—</span></div>
    </header>
    <main>
        <div id="summary" class="summary">Загружаем агентов…</div>
        <div id="err-banner"></div>
        <table id="cron-table">
            <thead>
                <tr>
                    <th>Label</th>
                    <th>Schedule</th>
                    <th>Loaded</th>
                    <th>PID</th>
                    <th>Exit</th>
                    <th>Last Run</th>
                    <th>Skip Reason</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody id="cron-body"></tbody>
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
        async function trigger(label) {
            if (!confirm('Запустить ' + label + ' сейчас?')) return;
            await callAdmin('POST', '/api/admin/cron/' + encodeURIComponent(label) + '/trigger');
            fetchAgents();
        }
        async function pause(label) {
            if (!confirm('Pause (bootout) ' + label + '?')) return;
            await callAdmin('POST', '/api/admin/cron/' + encodeURIComponent(label) + '/pause');
            fetchAgents();
        }
        async function resume(label) {
            if (!confirm('Resume (bootstrap) ' + label + '?')) return;
            await callAdmin('POST', '/api/admin/cron/' + encodeURIComponent(label) + '/resume');
            fetchAgents();
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
        function mkCell(content) {
            const td = document.createElement('td');
            if (typeof content === 'string') td.textContent = content;
            else if (content instanceof Node) td.appendChild(content);
            return td;
        }
        function mkMonoCell(text) {
            const td = document.createElement('td');
            td.className = 'mono';
            td.textContent = text;
            return td;
        }
        function renderLoaded(loaded) {
            return mkBadge(loaded ? 'loaded' : 'stopped', loaded ? 'badge-ok' : 'badge-muted');
        }
        function renderExit(code) {
            if (code === null || code === undefined) return mkBadge('—', 'badge-muted');
            if (code === 0) return mkBadge('0', 'badge-ok');
            return mkBadge(String(code), 'badge-err');
        }
        function renderLabelCell(agent) {
            const td = document.createElement('td');
            td.className = 'mono';
            td.appendChild(document.createTextNode(agent.label));
            if (agent.is_overdue) {
                td.appendChild(document.createTextNode(' '));
                td.appendChild(mkBadge('overdue', 'badge-warn'));
            }
            return td;
        }
        function renderLastRunCell(agent) {
            const td = document.createElement('td');
            const age = fmtAge(agent.last_run_iso);
            if (age) {
                const span = document.createElement('span');
                span.title = agent.last_run_iso || '';
                span.textContent = age;
                td.appendChild(span);
            } else {
                td.appendChild(mkBadge('—', 'badge-muted'));
            }
            return td;
        }
        function renderActionsCell(label) {
            const td = document.createElement('td');
            td.appendChild(mkButton('▶ trigger', () => trigger(label)));
            td.appendChild(mkButton('⏸ pause', () => pause(label), 'danger'));
            td.appendChild(mkButton('↻ resume', () => resume(label)));
            return td;
        }
        async function fetchAgents() {
            const errBanner = document.getElementById('err-banner');
            errBanner.textContent = '';
            try {
                const res = await fetch('/api/admin/cron/list');
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const data = await res.json();
                const agents = data.agents || [];
                const tbody = document.getElementById('cron-body');
                while (tbody.firstChild) tbody.removeChild(tbody.firstChild);
                let overdueCount = 0;
                for (const a of agents) {
                    if (a.is_overdue) overdueCount++;
                    const tr = document.createElement('tr');
                    tr.appendChild(renderLabelCell(a));
                    tr.appendChild(mkMonoCell(a.schedule || ''));
                    const loadedTd = document.createElement('td');
                    loadedTd.appendChild(renderLoaded(a.loaded));
                    tr.appendChild(loadedTd);
                    tr.appendChild(mkMonoCell(a.pid !== null && a.pid !== undefined ? String(a.pid) : '—'));
                    const exitTd = document.createElement('td');
                    exitTd.appendChild(renderExit(a.exit_code));
                    tr.appendChild(exitTd);
                    tr.appendChild(renderLastRunCell(a));
                    tr.appendChild(mkCell(a.last_skip_reason || '—'));
                    tr.appendChild(renderActionsCell(a.label));
                    tbody.appendChild(tr);
                }
                const summary = document.getElementById('summary');
                while (summary.firstChild) summary.removeChild(summary.firstChild);
                summary.appendChild(document.createTextNode(
                    'Всего агентов: ' + agents.length +
                    ' · overdue: ' + overdueCount +
                    ' · last health check: '
                ));
                const lastChk = document.createElement('span');
                lastChk.className = 'mono';
                lastChk.textContent = (data.health_watcher && data.health_watcher.last_check_utc) || '—';
                summary.appendChild(lastChk);
                document.getElementById('last-update').textContent =
                    new Date().toLocaleTimeString('ru-RU', { hour12: false });
            } catch (e) {
                const banner = document.createElement('div');
                banner.className = 'err-banner';
                banner.textContent = 'Ошибка загрузки: ' + e.message;
                errBanner.appendChild(banner);
            }
        }
        fetchAgents();
        setInterval(fetchAgents, 30000);
    </script>
</body>
</html>
"""
