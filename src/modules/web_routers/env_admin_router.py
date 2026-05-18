# -*- coding: utf-8 -*-
"""
Env admin router — Wave 189 (Session 48).

Read-only dashboard со всеми KRAB_* (и связанными) env-переменными:
- описание, текущее значение, значение по умолчанию
- автомаскировка секретов (token/secret/key/password/api_key/hash/dsn)
- группировка по 8 категориям
- in-memory cache 30s (env меняется только на restart)

Lightweight by design:
- pure os.environ + один read .env файла
- никаких DB/httpx/Prometheus
- никаких POST endpoints (env mutations требуют рестарта)

Endpoints (READY):
- GET /api/admin/env/list   — JSON {categories: {name: [VarRecord, ...]}}
- GET /admin/env            — HTML grouped по категориям
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from src.core.logger import get_logger

from ._context import RouterContext

_logger = get_logger(__name__)

# Корень репозитория — для чтения .env (он лежит в repo root).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DOTENV_PATH = _REPO_ROOT / ".env"

# Regex для определения секретов (case-insensitive).
_SECRET_NAME_RE = re.compile(r"(?i)(token|secret|key|password|api[_-]?key|hash|dsn|credentials?)")

# Cache: env-значения читаются один раз / 30s.
_CACHE_TTL_SEC = 30.0
_cache: dict[str, Any] = {"ts": 0.0, "payload": None}


# ── Compile-time metadata: 8 категорий, 38 переменных ────────────────────────
#
# Schema: (KEY, category, description, default).
# default = строка "значение" или "" для unset-by-default.
# Категории нумеруются в порядке отображения на странице.

_ENV_METADATA: list[tuple[str, str, str, str]] = [
    # ── 1. AI / Models ─────────────────────────────────────────────────────
    (
        "KRAB_CODEX_CLI_FIRST_CHUNK_TIMEOUT_SEC",
        "ai_models",
        "Таймаут первого chunk-а от codex-cli (сек).",
        "600",
    ),
    (
        "KRAB_CODEX_CLI_FALLBACK_MODEL",
        "ai_models",
        "Fallback-модель если codex-cli таймаутит.",
        "google/gemini-3.1-pro-preview",
    ),
    (
        "KRAB_LLM_IDLE_TIMEOUT_SEC",
        "ai_models",
        "Idle-timeout: молчание без tool_calls → kill LLM call.",
        "180",
    ),
    (
        "KRAB_LLM_HEARTBEAT_INTERVAL_SEC",
        "ai_models",
        "Интервал heartbeat edit-а во время LLM call.",
        "60",
    ),
    (
        "KRAB_GOOGLE_DIRECT_BYPASS_ENABLED",
        "ai_models",
        "Прямой google.genai SDK bypass (Wave 18-B).",
        "1",
    ),
    (
        "GEMINI_PAID_KEY_ENABLED",
        "ai_models",
        "Активен ли paid Gemini API ключ.",
        "1",
    ),
    (
        "LM_STUDIO_NATIVE_REASONING_MODE",
        "ai_models",
        "Reasoning effort для LM Studio (low/medium/high).",
        "medium",
    ),
    (
        "KRAB_REASONING_LEVEL",
        "ai_models",
        "Глобальный reasoning-уровень Krab.",
        "medium",
    ),
    (
        "OPENCLAW_REASONING_EFFORT",
        "ai_models",
        "Reasoning effort gateway-а OpenClaw.",
        "medium",
    ),
    # ── 2. Telegram ────────────────────────────────────────────────────────
    (
        "TELEGRAM_API_ID",
        "telegram",
        "MTProto API ID (numeric, public).",
        "",
    ),
    (
        "TELEGRAM_API_HASH",
        "telegram",
        "MTProto API hash (SECRET, masked).",
        "",
    ),
    (
        "TELEGRAM_SESSION_NAME",
        "telegram",
        "Имя файла Pyrogram-сессии.",
        "krab",
    ),
    (
        "KRAB_OWNER_ID",
        "telegram",
        "Telegram user_id владельца (ACL anchor).",
        "",
    ),
    # ── 3. Memory ──────────────────────────────────────────────────────────
    (
        "KRAB_RAG_PHASE2_ENABLED",
        "memory",
        "Memory Phase 2: FTS5 + vec_chunks hybrid retrieval.",
        "1",
    ),
    (
        "KRAB_MEMORY_PRUNE_MAX_BATCH_SEC",
        "memory",
        "Максимальное время одного prune-батча (сек).",
        "30",
    ),
    (
        "KRAB_STATE_SNAPSHOT_INTERVAL_MINUTES",
        "memory",
        "Интервал снэпшотов critical-state файлов.",
        "60",
    ),
    (
        "KRAB_STATE_SNAPSHOT_KEEP_RECENT",
        "memory",
        "Сколько снэпшотов хранить (FIFO).",
        "24",
    ),
    # ── 4. Voice ───────────────────────────────────────────────────────────
    (
        "KRAB_TYPING_INDICATOR_ENABLED",
        "voice",
        "Показывать typing-индикатор в Telegram во время LLM.",
        "1",
    ),
    (
        "KRAB_TTS_VOICE",
        "voice",
        "Голос для TTS (orpheus/elevenlabs profile name).",
        "",
    ),
    (
        "VOICE_REPLY_BLOCKED_CHATS",
        "voice",
        "Per-chat voice opt-out (CSV chat_id-ов).",
        "",
    ),
    (
        "KRAB_EAR_HEALTH_PROBE_ENABLED",
        "voice",
        "Активна ли health-проба для Krab Ear.",
        "1",
    ),
    # ── 5. Sentry ──────────────────────────────────────────────────────────
    (
        "SENTRY_AUTH_TOKEN",
        "sentry",
        "Sentry API token для MCP/polling (SECRET).",
        "",
    ),
    (
        "SENTRY_DSN",
        "sentry",
        "Sentry DSN для in-app event-стрима (SECRET).",
        "",
    ),
    (
        "SENTRY_BASELINE_PATH",
        "sentry",
        "Путь к baseline-файлу для diff-отчётов.",
        "",
    ),
    # ── 6. Routing ─────────────────────────────────────────────────────────
    (
        "KRAB_IMPLICIT_TRIGGER_THRESHOLD",
        "routing",
        "Smart Routing: порог implicit-trigger LLM classifier.",
        "0.6",
    ),
    (
        "KRAB_STARTUP_CATCHUP_LIMIT",
        "routing",
        "Сколько сообщений catchup-ить per chat на старте.",
        "20",
    ),
    (
        "KRAB_STARTUP_CATCHUP_CHATS",
        "routing",
        "Дополнительные chat_id-ы для catchup (CSV).",
        "",
    ),
    (
        "KRAB_SWARM_GROUP_ID",
        "routing",
        "Forum-группа Krab Swarm (chat_id).",
        "-1003703978531",
    ),
    # ── 7. API keys (все маскируются) ──────────────────────────────────────
    (
        "GEMINI_API_KEY_PAID",
        "api_keys",
        "Paid Gemini API key (Vertex AI fallback).",
        "",
    ),
    (
        "GEMINI_API_KEY",
        "api_keys",
        "Free Gemini API key (fallback).",
        "",
    ),
    (
        "BRAVE_API_KEY",
        "api_keys",
        "Brave Search API key для !search.",
        "",
    ),
    (
        "OPENROUTER_API_KEY",
        "api_keys",
        "OpenRouter API key (multi-provider).",
        "",
    ),
    (
        "ANTHROPIC_API_KEY",
        "api_keys",
        "Anthropic Claude API key.",
        "",
    ),
    (
        "OPENAI_API_KEY",
        "api_keys",
        "OpenAI API key.",
        "",
    ),
    (
        "GITHUB_TOKEN",
        "api_keys",
        "GitHub PAT для MCP/agent tools.",
        "",
    ),
    # ── 8. Agent gates ─────────────────────────────────────────────────────
    (
        "KRAB_AGENT_ENGINE_DISPATCH_ENABLED",
        "agent_gates",
        "Hermes ACP dispatch (default OFF).",
        "0",
    ),
    (
        "KRAB_MODEL_FOOTER_ENABLED",
        "agent_gates",
        "📡 model footer per response (Wave 47-B).",
        "1",
    ),
    (
        "KRAB_MCP_APPLE_WRITE_ENABLED",
        "agent_gates",
        "Включает write-ops для Apple MCP (Reminders/Calendar/Notes).",
        "0",
    ),
    (
        "KRAB_AGENT_ENGINE_HEALTHCHECK_ENABLED",
        "agent_gates",
        "Активна ли health-проверка agent-engine resolver.",
        "1",
    ),
    # ── 9. Local LLM / Verifier (experimental opt-in, S57/S63) ─────────────
    # Cross-link: stats dashboard /api/admin/local-draft-verifier-stats
    (
        "KRAB_LOCAL_DRAFT_VERIFY_ENABLED",
        "local_llm",
        "S57: локальный verifier draft-ответов (experimental opt-in, default OFF).",
        "0",
    ),
    (
        "KRAB_LOCAL_DRAFT_VERIFY_SAMPLE_RATE",
        "local_llm",
        "Доля сэмплируемых draft-ов (0.0–1.0).",
        "0.2",
    ),
    (
        "KRAB_LOCAL_PRIMARY_BYPASS_ENABLED",
        "local_llm",
        "S53 P4: bypass primary cloud в local MLX (experimental opt-in).",
        "0",
    ),
    (
        "KRAB_LOCAL_VISION_ENABLED",
        "local_llm",
        "Локальное vision (LM Studio Gemma) вместо cloud для медиа.",
        "0",
    ),
    (
        "KRAB_LOCAL_TRANSLATOR_ENABLED",
        "local_llm",
        "Локальный переводчик (LM Studio) вместо cloud Gemini.",
        "0",
    ),
]


# Категории и их человеко-читаемые заголовки + emoji.
_CATEGORY_LABELS: dict[str, tuple[str, str]] = {
    "ai_models": ("🤖", "AI / Models"),
    "telegram": ("✈️", "Telegram"),
    "memory": ("🧠", "Memory"),
    "voice": ("🎙️", "Voice"),
    "sentry": ("🛡️", "Sentry"),
    "routing": ("🚦", "Routing"),
    "api_keys": ("🔑", "API keys"),
    "agent_gates": ("🚪", "Agent gates"),
    "local_llm": ("🧪", "Local LLM / Verifier (experimental)"),
}

# Порядок отображения категорий.
_CATEGORY_ORDER: list[str] = [
    "ai_models",
    "telegram",
    "memory",
    "voice",
    "sentry",
    "routing",
    "api_keys",
    "agent_gates",
    "local_llm",
]

# Категории, помеченные как experimental opt-in (UI badge).
_EXPERIMENTAL_CATEGORIES: frozenset[str] = frozenset({"local_llm"})

# Cross-link: dashboard URL для категории (отображается как кнопка).
_CATEGORY_LINKS: dict[str, tuple[str, str]] = {
    "local_llm": (
        "/api/admin/local-draft-verifier-stats",
        "verifier stats →",
    ),
}


def _is_secret_name(name: str) -> bool:
    """True если имя переменной похоже на секрет."""
    return bool(_SECRET_NAME_RE.search(name))


def _mask_value(value: str) -> str:
    """Маскирует значение секрета: ••••••••XXXX (последние 4 символа).

    Для значений ≤4 символов — полностью маскируем.
    """
    if not value:
        return ""
    if len(value) <= 4:
        return "•" * len(value)
    return "•" * 8 + value[-4:]


def _parse_dotenv(path: Path) -> dict[str, str]:
    """Парсит .env-файл в dict. Невалидные строки игнорируются.

    Возвращает пустой dict если файла нет / не читается.
    """
    result: dict[str, str] = {}
    try:
        if not path.is_file():
            return result
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        _logger.warning("env_admin.dotenv_read_failed", error=str(exc))
        return result

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # KEY=VALUE — допускаем surrounding quotes у value.
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        # Strip surrounding quotes.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        result[key] = value
    return result


def _build_env_snapshot() -> dict[str, Any]:
    """Собирает полный снимок env-переменных с метаданными.

    Возвращает структуру для JSON endpoint-а:
    {
      "ok": True,
      "generated_ts": float,
      "total_count": int,
      "set_count": int,
      "secret_count": int,
      "categories": {
          "<cat_key>": {
              "label": str, "emoji": str,
              "vars": [{key, value, masked, set, description, default}, ...]
          }, ...
      }
    }
    """
    dotenv_values = _parse_dotenv(_DOTENV_PATH)

    categories: dict[str, dict[str, Any]] = {}
    for cat_key in _CATEGORY_ORDER:
        emoji, label = _CATEGORY_LABELS[cat_key]
        cat_entry: dict[str, Any] = {
            "emoji": emoji,
            "label": label,
            "vars": [],
            "experimental": cat_key in _EXPERIMENTAL_CATEGORIES,
        }
        link = _CATEGORY_LINKS.get(cat_key)
        if link is not None:
            cat_entry["link_url"] = link[0]
            cat_entry["link_label"] = link[1]
        categories[cat_key] = cat_entry

    set_count = 0
    secret_count = 0
    for key, cat, description, default in _ENV_METADATA:
        # Приоритет: os.environ → .env → default.
        env_val = os.environ.get(key)
        if env_val is None:
            env_val = dotenv_values.get(key)
        is_set = env_val is not None and env_val != ""
        is_secret = _is_secret_name(key)
        if is_set:
            set_count += 1
        if is_secret:
            secret_count += 1

        # Маскировка только для секретов И только если значение установлено.
        if is_set and is_secret:
            display_value = _mask_value(env_val or "")
            masked = True
        elif is_set:
            display_value = env_val or ""
            masked = False
        else:
            display_value = ""
            masked = False

        record = {
            "key": key,
            "value": display_value,
            "masked": masked,
            "set": is_set,
            "description": description,
            "default": default,
            "secret": is_secret,
        }
        if cat not in categories:
            # Sanity: незнакомая категория — кладём в первую.
            categories[_CATEGORY_ORDER[0]]["vars"].append(record)
        else:
            categories[cat]["vars"].append(record)

    return {
        "ok": True,
        "generated_ts": time.time(),
        "total_count": len(_ENV_METADATA),
        "set_count": set_count,
        "secret_count": secret_count,
        "categories": categories,
    }


def _get_cached_snapshot() -> dict[str, Any]:
    """Возвращает кешированный snapshot, обновляя его не чаще раза в 30s."""
    now = time.time()
    payload = _cache.get("payload")
    if payload is not None and (now - float(_cache.get("ts", 0.0))) < _CACHE_TTL_SEC:
        return payload
    snapshot = _build_env_snapshot()
    _cache["payload"] = snapshot
    _cache["ts"] = now
    return snapshot


def _invalidate_cache() -> None:
    """Сброс кеша — используется в тестах."""
    _cache["payload"] = None
    _cache["ts"] = 0.0


def build_env_admin_router(ctx: RouterContext) -> APIRouter:  # noqa: ARG001
    """Factory: возвращает APIRouter с /admin/env page и JSON endpoint."""
    router = APIRouter(tags=["env-admin"])

    # ── GET /api/admin/env/list ─────────────────────────────────────────────

    @router.get("/api/admin/env/list")
    async def env_list() -> dict:
        """JSON-список всех зарегистрированных env-переменных."""
        return _get_cached_snapshot()

    # ── GET /admin/env — HTML страница ──────────────────────────────────────

    @router.get("/admin/env", response_class=HTMLResponse)
    async def env_admin_page() -> HTMLResponse:
        """HTML страница: фетчит JSON, рендерит таблицу по категориям."""
        return HTMLResponse(_ENV_ADMIN_PAGE_HTML)

    return router


# ── HTML страница /admin/env ─────────────────────────────────────────────────
# XSS-safe: client-side JS использует textContent/createElement,
# никаких innerHTML с user/env-данными.

_ENV_ADMIN_PAGE_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Krab · Admin Env</title>
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
            --secret: #f472b6;
        }
        * { box-sizing: border-box; }
        body {
            margin: 0;
            font-family: system-ui, -apple-system, BlinkMacSystemFont,
                "Segoe UI", Roboto, sans-serif;
            background-color: var(--bg);
            color: var(--text);
            line-height: 1.45;
        }
        .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, monospace; }
        header {
            display: flex; justify-content: space-between; align-items: center;
            padding: 12px 24px;
            background: #000; border-bottom: 1px solid var(--border);
        }
        header h1 { margin: 0; font-size: 1.4rem; }
        header .meta { color: var(--text-muted); font-size: 0.85rem; }
        main { padding: 20px 24px 40px; max-width: 1280px; margin: 0 auto; }
        .stats-row {
            display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap;
        }
        .stat-card {
            background: var(--card-bg); border: 1px solid var(--border);
            border-radius: 6px; padding: 10px 16px; min-width: 120px;
        }
        .stat-card .label {
            color: var(--text-muted); font-size: 0.75rem;
            text-transform: uppercase; letter-spacing: 0.05em;
        }
        .stat-card .value {
            font-size: 1.4rem; font-weight: 600; margin-top: 2px;
        }
        h2.cat-section {
            font-size: 1.05rem;
            color: var(--text);
            margin: 24px 0 10px;
            display: flex; align-items: center; gap: 8px;
        }
        h2.cat-section .cat-emoji { font-size: 1.3rem; }
        h2.cat-section .cat-count {
            color: var(--text-muted); font-size: 0.85rem; font-weight: normal;
        }
        table {
            width: 100%; border-collapse: collapse;
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 6px; overflow: hidden;
            font-size: 0.86rem;
        }
        th, td {
            padding: 8px 12px; text-align: left;
            border-bottom: 1px solid var(--border);
            vertical-align: top;
        }
        th {
            background: #1a1a1a; color: var(--text-muted);
            text-transform: uppercase; font-size: 0.7rem;
            letter-spacing: 0.05em;
        }
        tr:last-child td { border-bottom: none; }
        tr:hover { background: rgba(125, 211, 252, 0.04); }
        td.key-cell {
            width: 320px; color: var(--accent); font-weight: 500;
        }
        td.value-cell {
            color: var(--text); word-break: break-all;
            max-width: 320px;
        }
        td.value-cell.unset { color: var(--text-muted); font-style: italic; }
        td.value-cell.masked { color: var(--secret); }
        td.default-cell { color: var(--text-muted); font-size: 0.78rem; }
        td.desc-cell { color: var(--text-muted); font-size: 0.85rem; }
        .badge {
            display: inline-block; padding: 1px 6px;
            border-radius: 3px; font-size: 0.65rem; font-weight: 500;
            margin-left: 6px; vertical-align: middle;
        }
        .badge.secret { background: rgba(244,114,182,0.12); color: var(--secret); }
        .badge.unset { background: rgba(136,136,136,0.12); color: var(--text-muted); }
        .badge.experimental {
            background: rgba(250,204,21,0.14); color: var(--warn);
            font-size: 0.7rem; margin-left: 10px;
        }
        a.cat-link {
            margin-left: auto; color: var(--accent);
            text-decoration: none; font-size: 0.78rem;
            border: 1px solid var(--border); padding: 2px 8px;
            border-radius: 3px;
        }
        a.cat-link:hover { border-color: var(--accent); }
        .copy-btn {
            background: transparent; border: 1px solid var(--border);
            color: var(--text-muted); cursor: pointer;
            padding: 1px 6px; border-radius: 3px;
            font-size: 0.7rem; margin-left: 6px;
        }
        .copy-btn:hover { color: var(--accent); border-color: var(--accent); }
        .err-banner {
            color: #ef4444;
            background: rgba(239,68,68,0.08);
            padding: 12px;
            border-radius: 4px;
            margin-bottom: 14px;
        }
        footer {
            margin-top: 32px;
            padding-top: 16px;
            border-top: 1px solid var(--border);
            color: var(--text-muted);
            font-size: 0.8rem;
            text-align: center;
        }
        .ro-notice {
            background: rgba(250, 204, 21, 0.08);
            border-left: 3px solid var(--warn);
            padding: 10px 14px;
            margin-bottom: 20px;
            font-size: 0.88rem;
            color: var(--text);
            border-radius: 0 4px 4px 0;
        }
    </style>
</head>
<body>
    <header>
        <h1>⚙️ Krab · Admin Env</h1>
        <div class="meta">Read-only env dashboard · Wave 189</div>
    </header>
    <main>
        <div class="ro-notice">
            <strong>READ-ONLY.</strong> Изменения env-переменных требуют
            рестарта Krab (<span class="mono">new Stop Krab.command</span>
            → <span class="mono">new start_krab.command</span>).
            Секреты маскируются автоматически (••••••••XXXX).
        </div>

        <div class="stats-row" id="stats-row"></div>
        <div id="err-banner"></div>
        <div id="categories"></div>

        <footer>
            Krab Admin Console · Wave 189 · /admin/env
            · cache 30s
        </footer>
    </main>
    <script>
        function mkText(tag, text, cls) {
            const el = document.createElement(tag);
            if (cls) el.className = cls;
            if (text !== undefined && text !== null) el.textContent = text;
            return el;
        }
        function mkStat(label, value) {
            const card = mkText('div', null, 'stat-card');
            card.appendChild(mkText('div', label, 'label'));
            card.appendChild(mkText('div', String(value), 'value'));
            return card;
        }
        function mkCopyBtn(text) {
            const btn = document.createElement('button');
            btn.className = 'copy-btn mono';
            btn.type = 'button';
            btn.textContent = 'copy';
            btn.addEventListener('click', function() {
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(text).then(function() {
                        btn.textContent = 'ok';
                        setTimeout(function() { btn.textContent = 'copy'; }, 800);
                    }).catch(function() { btn.textContent = 'err'; });
                } else {
                    btn.textContent = 'n/a';
                }
            });
            return btn;
        }
        function mkValueCell(record) {
            const td = document.createElement('td');
            td.className = 'value-cell mono';
            if (!record.set) {
                td.classList.add('unset');
                td.textContent = '<not set>';
                return td;
            }
            if (record.masked) td.classList.add('masked');
            td.textContent = record.value;
            if (record.set) td.appendChild(mkCopyBtn(record.value));
            return td;
        }
        function mkKeyCell(record) {
            const td = document.createElement('td');
            td.className = 'key-cell mono';
            td.textContent = record.key;
            if (record.secret) {
                const b = mkText('span', 'SECRET', 'badge secret');
                td.appendChild(b);
            }
            if (!record.set) {
                const b = mkText('span', 'unset', 'badge unset');
                td.appendChild(b);
            }
            return td;
        }
        function renderCategory(catKey, catData) {
            const wrap = document.createElement('div');
            const h2 = document.createElement('h2');
            h2.className = 'cat-section';
            h2.appendChild(mkText('span', catData.emoji, 'cat-emoji'));
            h2.appendChild(mkText('span', catData.label));
            const cnt = (catData.vars || []).length;
            h2.appendChild(mkText('span',
                '(' + cnt + ' var' + (cnt !== 1 ? 's' : '') + ')',
                'cat-count'));
            if (catData.experimental) {
                h2.appendChild(mkText('span', 'EXPERIMENTAL OPT-IN',
                    'badge experimental'));
            }
            if (catData.link_url) {
                const a = document.createElement('a');
                a.className = 'cat-link';
                a.href = catData.link_url;
                a.target = '_blank';
                a.rel = 'noopener';
                a.textContent = catData.link_label || catData.link_url;
                h2.appendChild(a);
            }
            wrap.appendChild(h2);

            const table = document.createElement('table');
            const thead = document.createElement('thead');
            const headRow = document.createElement('tr');
            ['Key', 'Current value', 'Default', 'Description'].forEach(function(t) {
                headRow.appendChild(mkText('th', t));
            });
            thead.appendChild(headRow);
            table.appendChild(thead);

            const tbody = document.createElement('tbody');
            (catData.vars || []).forEach(function(rec) {
                const tr = document.createElement('tr');
                tr.appendChild(mkKeyCell(rec));
                tr.appendChild(mkValueCell(rec));
                tr.appendChild(mkText('td', rec.default || '—', 'default-cell mono'));
                tr.appendChild(mkText('td', rec.description || '', 'desc-cell'));
                tbody.appendChild(tr);
            });
            table.appendChild(tbody);
            wrap.appendChild(table);
            return wrap;
        }
        async function fetchEnv() {
            const errBanner = document.getElementById('err-banner');
            errBanner.textContent = '';
            try {
                const res = await fetch('/api/admin/env/list');
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const data = await res.json();

                const statsRow = document.getElementById('stats-row');
                while (statsRow.firstChild) statsRow.removeChild(statsRow.firstChild);
                statsRow.appendChild(mkStat('Total vars', data.total_count));
                statsRow.appendChild(mkStat('Set', data.set_count));
                statsRow.appendChild(mkStat('Secrets', data.secret_count));
                const ts = new Date((data.generated_ts || 0) * 1000);
                statsRow.appendChild(mkStat('Snapshot',
                    ts.toLocaleTimeString('ru-RU', { hour12: false })));

                const root = document.getElementById('categories');
                while (root.firstChild) root.removeChild(root.firstChild);
                const cats = data.categories || {};
                Object.keys(cats).forEach(function(catKey) {
                    root.appendChild(renderCategory(catKey, cats[catKey]));
                });
            } catch (e) {
                const banner = document.createElement('div');
                banner.className = 'err-banner';
                banner.textContent = 'Ошибка загрузки: ' + e.message;
                errBanner.appendChild(banner);
            }
        }
        fetchEnv();
    </script>
</body>
</html>
"""
