# -*- coding: utf-8 -*-
"""
Spawn admin router — Wave 234 (Session 48+).

Owner-side панель для быстрого single-shot спавна задач к Krab без
захода в Telegram. Полезно для quick debugging, batch ops, test
prompts, или прогона нового системного промпта против fixed input.

Поддерживает 4 backend-режима:
  * ``primary``           — активный primary через openclaw_client
                            (default routing, с tools если backend supports).
  * ``mlx-local-kv4``     — локальный MLX через openclaw_client с
                            ``preferred_model="mlx-local-kv4/gemma-4-26b"``
                            (Wave 221/225 helpers применяют MLX-specific
                            payload mutations).
  * ``openclaw-cloud``    — cloud routing через openclaw_client
                            с ``force_cloud=True``.
  * ``direct-8088``       — bypass openclaw_client целиком, raw httpx
                            POST на ``http://127.0.0.1:8088/v1/chat/completions``
                            (используется для тестирования OpenAI-совместимого
                            эндпоинта без всех middleware).

История последних 10 задач сохраняется в
``~/.openclaw/krab_runtime_state/spawn_history.jsonl`` (append-only,
обрезается до 10 при чтении). Промпты длиннее 500 символов
маскируются (head 500 + "…") чтобы не хранить sensitive content.

Endpoints (READY):
- POST /api/admin/spawn/run     — write-access; body
                                  ``{prompt, backend, tools_enabled?}``
                                  → ``{ok, response_text, latency_ms,
                                  tokens_used, history_id, backend, error?}``.
- GET  /api/admin/spawn/history — last 10 spawn entries (timestamps + masked
                                  prompt + backend + latency).
- GET  /admin/spawn             — HTML form + history.

Контракт безопасности: ``/api/admin/spawn/run`` требует write-access
(X-Krab-Web-Key или ``token``-query). Rate-limit: max 5 spawns / 60s,
state shared через ``ctx.rate_state``.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from src.core.logger import get_logger

from ._context import RouterContext

_logger = get_logger(__name__)

# Канонический файл истории — append-only, обрезается до _HISTORY_LIMIT при чтении.
_HISTORY_PATH = Path.home() / ".openclaw" / "krab_runtime_state" / "spawn_history.jsonl"
_HISTORY_LIMIT = 10

# Маскировка промптов длиннее этого порога (избегаем хранения sensitive content).
_PROMPT_MASK_LENGTH = 500

# Известные backend-режимы.
_KNOWN_BACKENDS = frozenset(
    {
        "primary",
        "mlx-local-kv4",
        "openclaw-cloud",
        "direct-8088",
    }
)

# Direct backend (раздельный port 8088, OpenAI-compat endpoint).
_DIRECT_8088_URL = "http://127.0.0.1:8088/v1/chat/completions"

# Rate-limit ключи в ctx.rate_state.
_RATE_KEY = "spawn_admin_recent_runs"
_RATE_WINDOW_SEC = 60.0
_RATE_LIMIT = 5

# MLX local model ID — Wave 221/225 distinct mlx-target.
_MLX_LOCAL_MODEL = "mlx-local-kv4/gemma-4-26b"

# Максимум секунд на один spawn-запрос (не дать LLM зависнуть).
_SPAWN_TIMEOUT_SEC = 120.0


class SpawnRunRequest(BaseModel):
    """Payload для ``POST /api/admin/spawn/run``."""

    prompt: str = Field(..., min_length=1, max_length=20000)
    backend: str = Field(default="primary")
    tools_enabled: bool = Field(default=False)


def _mask_prompt(prompt: str) -> str:
    """Маскирует длинные промпты для хранения в истории."""
    if not isinstance(prompt, str):
        return ""
    if len(prompt) <= _PROMPT_MASK_LENGTH:
        return prompt
    return prompt[:_PROMPT_MASK_LENGTH] + "…"


def _ensure_history_dir() -> None:
    """Создаёт parent-директорию для истории если нужно."""
    try:
        _HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _logger.warning("spawn_admin.history_dir_create_failed", error=str(exc))


def _append_history(entry: dict[str, Any]) -> None:
    """Append одной записи в spawn_history.jsonl (silent на error)."""
    _ensure_history_dir()
    try:
        with open(_HISTORY_PATH, "a", encoding="utf-8") as fp:
            fp.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        _logger.warning("spawn_admin.history_append_failed", error=str(exc))


def _read_history(limit: int = _HISTORY_LIMIT) -> list[dict[str, Any]]:
    """Читает последние ``limit`` записей из jsonl (с конца файла).

    Корректно обрабатывает повреждённые строки (skip silently).
    """
    if not _HISTORY_PATH.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with open(_HISTORY_PATH, "r", encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    rows.append(obj)
    except OSError as exc:
        _logger.warning("spawn_admin.history_read_failed", error=str(exc))
        return []
    # Last N — file append-ordered; reverse чтобы свежее сверху.
    return list(reversed(rows[-limit:]))


def _rate_check_and_record(rate_state: dict[str, Any]) -> None:
    """Проверка rate-limit. Raises HTTPException(429) при превышении.

    Состояние хранится как list[float] timestamps в ``rate_state[_RATE_KEY]``,
    обрезается по skipping window перед каждой проверкой.
    """
    now = time.time()
    recent_raw = rate_state.get(_RATE_KEY) or []
    if not isinstance(recent_raw, list):
        recent_raw = []
    # Окно: drop старше _RATE_WINDOW_SEC.
    cutoff = now - _RATE_WINDOW_SEC
    recent = [ts for ts in recent_raw if isinstance(ts, (int, float)) and ts >= cutoff]
    if len(recent) >= _RATE_LIMIT:
        # Сохраняем урезанный список (без новой записи).
        rate_state[_RATE_KEY] = recent
        raise HTTPException(
            status_code=429,
            detail=f"spawn_rate_limit: max {_RATE_LIMIT}/{int(_RATE_WINDOW_SEC)}s",
        )
    recent.append(now)
    rate_state[_RATE_KEY] = recent


def _normalize_backend(backend: str) -> str:
    """Sanitize backend-ID, default → primary."""
    backend = (backend or "").strip().lower()
    if backend not in _KNOWN_BACKENDS:
        return "primary"
    return backend


def _estimate_tokens(text: str) -> int:
    """Очень грубая оценка tokens (≈ 4 chars/token) для UI."""
    if not isinstance(text, str) or not text:
        return 0
    return max(1, len(text) // 4)


async def _spawn_via_openclaw(
    *,
    openclaw_client: Any,
    prompt: str,
    backend: str,
    tools_enabled: bool,
) -> tuple[str, dict[str, Any]]:
    """Спавнит задачу через openclaw_client.send_message_stream.

    Возвращает ``(response_text, meta)``. Meta включает применённые routing
    параметры (model/force_cloud) — для отображения в истории.
    """
    if openclaw_client is None or not hasattr(openclaw_client, "send_message_stream"):
        raise HTTPException(status_code=503, detail="openclaw_client_unavailable")

    # Уникальный chat_id для каждого spawn — чтобы не загрязнять реальные
    # сессии (openclaw_client кэширует историю per chat_id).
    chat_id = f"spawn-admin-{uuid.uuid4().hex[:12]}"

    preferred_model: str | None = None
    force_cloud = False
    if backend == "mlx-local-kv4":
        preferred_model = _MLX_LOCAL_MODEL
    elif backend == "openclaw-cloud":
        force_cloud = True
    # "primary" — без overrides; routing решает openclaw.

    chunks: list[str] = []

    async def _collect() -> None:
        async for chunk in openclaw_client.send_message_stream(
            message=prompt,
            chat_id=chat_id,
            system_prompt=None,
            images=None,
            force_cloud=force_cloud,
            preferred_model=preferred_model,
            max_output_tokens=None,
            disable_tools=not tools_enabled,
        ):
            if isinstance(chunk, str):
                chunks.append(chunk)

    try:
        await asyncio.wait_for(_collect(), timeout=_SPAWN_TIMEOUT_SEC)
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail=f"spawn_timeout: {_SPAWN_TIMEOUT_SEC}s",
        ) from exc

    response_text = "".join(chunks).strip()
    meta = {
        "model": preferred_model or "auto",
        "force_cloud": force_cloud,
        "tools_enabled": tools_enabled,
        "chat_id": chat_id,
    }
    return response_text, meta


async def _spawn_via_direct_8088(prompt: str) -> tuple[str, dict[str, Any]]:
    """Bypass openclaw_client целиком, raw POST на :8088 chat/completions.

    Используется для проверки чистого LM Studio endpoint без всей цепочки
    (tools/routing/recovery). Возвращает ``(response_text, meta)``.
    """
    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "temperature": 0.7,
    }
    try:
        async with httpx.AsyncClient(timeout=_SPAWN_TIMEOUT_SEC) as client:
            resp = await client.post(_DIRECT_8088_URL, json=payload)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"direct_8088_unreachable: {exc}",
        ) from exc

    if resp.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"direct_8088_status_{resp.status_code}",
        )

    try:
        data = resp.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail="direct_8088_bad_json") from exc

    # OpenAI-совместимый формат: choices[0].message.content
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        content = ""

    meta = {
        "model": data.get("model") or "direct-8088",
        "force_cloud": False,
        "tools_enabled": False,
        "endpoint": _DIRECT_8088_URL,
    }
    return str(content or "").strip(), meta


def build_spawn_admin_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter для /admin/spawn + API."""
    router = APIRouter(tags=["spawn-admin"])

    # ── POST /api/admin/spawn/run ─────────────────────────────────────────

    @router.post("/api/admin/spawn/run")
    async def spawn_run(
        body: SpawnRunRequest,
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Single-shot спавн через выбранный backend.

        Body:
          ``prompt``        — текст запроса (1..20000 chars).
          ``backend``       — primary | mlx-local-kv4 | openclaw-cloud | direct-8088.
          ``tools_enabled`` — пускать ли tools (только для backend через openclaw).

        Возвращает ``{ok, response_text, latency_ms, tokens_used,
        history_id, backend}``. На ошибке — HTTP 4xx/5xx с detail.
        """
        ctx.assert_write_access_fn(x_krab_web_key, token)
        _rate_check_and_record(ctx.rate_state)

        backend = _normalize_backend(body.backend)
        # tools_enabled значим только для openclaw-backed путей.
        tools_enabled = bool(body.tools_enabled) if backend != "direct-8088" else False

        prompt = body.prompt.strip()
        if not prompt:
            raise HTTPException(status_code=400, detail="spawn_empty_prompt")

        history_id = uuid.uuid4().hex[:16]
        started_at = time.time()
        error_text: str | None = None
        response_text = ""
        meta: dict[str, Any] = {}

        try:
            if backend == "direct-8088":
                response_text, meta = await _spawn_via_direct_8088(prompt)
            else:
                openclaw_client = ctx.get_dep("openclaw_client")
                response_text, meta = await _spawn_via_openclaw(
                    openclaw_client=openclaw_client,
                    prompt=prompt,
                    backend=backend,
                    tools_enabled=tools_enabled,
                )
        except HTTPException as exc:
            # Зафиксируем error-entry в истории для visibility, потом re-raise.
            error_text = str(exc.detail)
            _append_history(
                {
                    "history_id": history_id,
                    "ts": started_at,
                    "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started_at)),
                    "prompt": _mask_prompt(prompt),
                    "prompt_chars": len(prompt),
                    "backend": backend,
                    "tools_enabled": tools_enabled,
                    "latency_ms": int((time.time() - started_at) * 1000),
                    "response_chars": 0,
                    "tokens_used": 0,
                    "ok": False,
                    "error": error_text,
                }
            )
            raise
        except Exception as exc:  # noqa: BLE001 — широкая защита по контуру admin.
            error_text = f"spawn_unhandled: {exc}"
            _logger.error("spawn_admin.run_failed", error=str(exc), backend=backend)
            _append_history(
                {
                    "history_id": history_id,
                    "ts": started_at,
                    "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started_at)),
                    "prompt": _mask_prompt(prompt),
                    "prompt_chars": len(prompt),
                    "backend": backend,
                    "tools_enabled": tools_enabled,
                    "latency_ms": int((time.time() - started_at) * 1000),
                    "response_chars": 0,
                    "tokens_used": 0,
                    "ok": False,
                    "error": error_text,
                }
            )
            raise HTTPException(status_code=500, detail=error_text) from exc

        latency_ms = int((time.time() - started_at) * 1000)
        tokens_used = _estimate_tokens(response_text)

        _append_history(
            {
                "history_id": history_id,
                "ts": started_at,
                "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started_at)),
                "prompt": _mask_prompt(prompt),
                "prompt_chars": len(prompt),
                "backend": backend,
                "tools_enabled": tools_enabled,
                "latency_ms": latency_ms,
                "response_chars": len(response_text),
                "tokens_used": tokens_used,
                "ok": True,
                "meta": meta,
            }
        )

        _logger.info(
            "spawn_admin.run_ok",
            history_id=history_id,
            backend=backend,
            latency_ms=latency_ms,
            response_chars=len(response_text),
        )

        return {
            "ok": True,
            "history_id": history_id,
            "backend": backend,
            "response_text": response_text,
            "latency_ms": latency_ms,
            "tokens_used": tokens_used,
            "meta": meta,
        }

    # ── GET /api/admin/spawn/history ──────────────────────────────────────

    @router.get("/api/admin/spawn/history")
    async def spawn_history() -> dict:
        """Возвращает последние ``_HISTORY_LIMIT`` записей (свежее сверху)."""
        rows = _read_history(_HISTORY_LIMIT)
        return {"ok": True, "count": len(rows), "history": rows}

    # ── GET /admin/spawn — HTML ───────────────────────────────────────────

    @router.get("/admin/spawn", response_class=HTMLResponse)
    async def spawn_admin_page() -> HTMLResponse:
        """HTML страница с формой спавна и блоком истории."""
        return HTMLResponse(_SPAWN_ADMIN_PAGE_HTML)

    return router


# ── HTML страница /admin/spawn ───────────────────────────────────────────────
# Клиент строит DOM через createElement/textContent (XSS-safe).
# Response пользователя/история тоже через textContent — не innerHTML.

_SPAWN_ADMIN_PAGE_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Krab · Spawn Admin</title>
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
            background: var(--bg);
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
        main { padding: 16px 24px; display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
        @media (max-width: 900px) { main { grid-template-columns: 1fr; } }
        .panel {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 12px 16px;
        }
        .panel h2 { margin: 0 0 10px 0; font-size: 1rem; color: var(--text-muted);
            text-transform: uppercase; letter-spacing: 0.04em; font-weight: 600; }
        label { display: block; font-size: 0.85rem; color: var(--text-muted);
            margin-bottom: 4px; }
        textarea, select {
            width: 100%;
            background: #0a0a0a;
            color: var(--text);
            border: 1px solid var(--border);
            border-radius: 4px;
            padding: 8px;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, monospace;
            font-size: 0.85rem;
            margin-bottom: 12px;
        }
        textarea { min-height: 140px; resize: vertical; }
        select { height: 32px; }
        .row { display: flex; gap: 12px; align-items: center; margin-bottom: 12px; }
        .row label { display: flex; align-items: center; gap: 6px; margin: 0; }
        input[type="checkbox"] { accent-color: var(--accent); }
        button {
            background: var(--accent);
            border: none;
            color: #000;
            padding: 8px 16px;
            font-size: 0.9rem;
            border-radius: 4px;
            cursor: pointer;
            font-weight: 600;
            font-family: inherit;
        }
        button:hover:not(:disabled) { background: #38bdf8; }
        button:disabled { opacity: 0.5; cursor: not-allowed; }
        .badge {
            display: inline-block; padding: 2px 8px;
            border-radius: 4px; font-size: 0.72rem; font-weight: 500;
        }
        .badge-ok { background: rgba(34,197,94,0.15); color: var(--ok); }
        .badge-warn { background: rgba(250,204,21,0.15); color: var(--warn); }
        .badge-err { background: rgba(239,68,68,0.15); color: var(--err); }
        .badge-muted { background: rgba(255,255,255,0.06); color: var(--text-muted); }
        #response {
            white-space: pre-wrap;
            word-break: break-word;
            background: #0a0a0a;
            border: 1px solid var(--border);
            border-radius: 4px;
            padding: 10px;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, monospace;
            font-size: 0.82rem;
            min-height: 80px;
            max-height: 480px;
            overflow-y: auto;
        }
        .stats { font-size: 0.78rem; color: var(--text-muted); margin-bottom: 8px; }
        ul.history { list-style: none; margin: 0; padding: 0; }
        ul.history li {
            padding: 8px 0;
            border-bottom: 1px solid var(--border);
            font-size: 0.82rem;
        }
        ul.history li:last-child { border-bottom: none; }
        ul.history .prompt-preview {
            color: var(--text-muted);
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, monospace;
            display: block;
            margin-top: 2px;
            word-break: break-word;
        }
        .err-banner {
            color: var(--err); padding: 10px;
            background: rgba(239,68,68,0.08); border-radius: 4px;
            margin-bottom: 8px; font-size: 0.85rem;
        }
    </style>
</head>
<body>
    <header>
        <h1>🚀 Krab · Spawn Admin</h1>
        <div class="meta">Quick single-shot · max 5/мин · история: 10</div>
    </header>
    <main>
        <section class="panel">
            <h2>Spawn</h2>
            <div id="err-banner"></div>
            <label for="prompt">Prompt</label>
            <textarea id="prompt" placeholder="Введите prompt…"></textarea>
            <label for="backend">Backend</label>
            <select id="backend">
                <option value="primary">Active primary (default routing)</option>
                <option value="mlx-local-kv4">mlx-local-kv4/gemma-4-26b (via OpenClaw)</option>
                <option value="openclaw-cloud">openclaw/main (force_cloud)</option>
                <option value="direct-8088">Direct :8088 (no tools, no routing)</option>
            </select>
            <div class="row">
                <label><input type="checkbox" id="tools_enabled"> tools enabled</label>
                <button id="send-btn">Send</button>
            </div>
            <div class="stats" id="stats">—</div>
            <h2 style="margin-top: 12px;">Response</h2>
            <div id="response">—</div>
        </section>
        <section class="panel">
            <h2>History (last 10)</h2>
            <button id="refresh-btn" style="margin-bottom: 10px;">Refresh</button>
            <ul class="history" id="history-list"></ul>
        </section>
    </main>
    <script>
        function showError(msg) {
            const banner = document.getElementById('err-banner');
            while (banner.firstChild) banner.removeChild(banner.firstChild);
            if (!msg) return;
            const div = document.createElement('div');
            div.className = 'err-banner';
            div.textContent = msg;
            banner.appendChild(div);
        }
        function setResponse(text) {
            const el = document.getElementById('response');
            el.textContent = text || '—';
        }
        function setStats(latency, tokens, backend, ok) {
            const el = document.getElementById('stats');
            while (el.firstChild) el.removeChild(el.firstChild);
            if (ok === undefined) { el.textContent = '—'; return; }
            const badge = document.createElement('span');
            badge.className = 'badge ' + (ok ? 'badge-ok' : 'badge-err');
            badge.textContent = ok ? 'ok' : 'fail';
            el.appendChild(badge);
            const span = document.createElement('span');
            span.textContent = ' · ' + backend + ' · ' + latency + 'ms · ~' + tokens + ' tokens';
            el.appendChild(span);
        }
        async function spawnSend() {
            showError('');
            const prompt = document.getElementById('prompt').value;
            const backend = document.getElementById('backend').value;
            const toolsEnabled = document.getElementById('tools_enabled').checked;
            if (!prompt.trim()) { showError('Введите prompt'); return; }
            const btn = document.getElementById('send-btn');
            btn.disabled = true; btn.textContent = 'Sending…';
            setResponse('…');
            setStats();
            try {
                const res = await fetch('/api/admin/spawn/run', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        prompt: prompt,
                        backend: backend,
                        tools_enabled: toolsEnabled
                    })
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    showError('Ошибка ' + res.status + ': ' + (data.detail || 'unknown'));
                    setResponse('—');
                    return;
                }
                setResponse(data.response_text || '(empty response)');
                setStats(data.latency_ms, data.tokens_used, data.backend, data.ok);
                fetchHistory();
            } catch (e) {
                showError('Сетевая ошибка: ' + e.message);
                setResponse('—');
            } finally {
                btn.disabled = false; btn.textContent = 'Send';
            }
        }
        function fmtAge(iso) {
            if (!iso) return '';
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
            const s = document.createElement('span');
            s.className = 'badge ' + cls;
            s.textContent = text;
            return s;
        }
        async function fetchHistory() {
            try {
                const res = await fetch('/api/admin/spawn/history');
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const data = await res.json();
                const list = document.getElementById('history-list');
                while (list.firstChild) list.removeChild(list.firstChild);
                if (!data.history || !data.history.length) {
                    const li = document.createElement('li');
                    li.textContent = 'История пуста.';
                    list.appendChild(li);
                    return;
                }
                for (const row of data.history) {
                    const li = document.createElement('li');
                    li.appendChild(mkBadge(row.ok ? 'ok' : 'fail',
                        row.ok ? 'badge-ok' : 'badge-err'));
                    li.appendChild(document.createTextNode(' '));
                    const head = document.createElement('span');
                    head.className = 'mono';
                    head.textContent = row.backend + ' · '
                        + (row.latency_ms || 0) + 'ms · '
                        + (row.tokens_used || 0) + ' tok · '
                        + fmtAge(row.ts_iso);
                    li.appendChild(head);
                    if (row.error) {
                        li.appendChild(document.createTextNode(' '));
                        li.appendChild(mkBadge(row.error.slice(0, 40), 'badge-err'));
                    }
                    const pre = document.createElement('span');
                    pre.className = 'prompt-preview';
                    pre.textContent = row.prompt || '';
                    li.appendChild(pre);
                    list.appendChild(li);
                }
            } catch (e) {
                const list = document.getElementById('history-list');
                while (list.firstChild) list.removeChild(list.firstChild);
                const li = document.createElement('li');
                li.textContent = 'Ошибка загрузки истории: ' + e.message;
                list.appendChild(li);
            }
        }
        document.getElementById('send-btn').addEventListener('click', spawnSend);
        document.getElementById('refresh-btn').addEventListener('click', fetchHistory);
        document.getElementById('prompt').addEventListener('keydown', (e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') spawnSend();
        });
        fetchHistory();
    </script>
</body>
</html>
"""
