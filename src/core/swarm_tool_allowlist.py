# -*- coding: utf-8 -*-
"""
src/core/swarm_tool_allowlist.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Per-team tool allowlist для свёрма.

В отличие от `swarm_tool_scope.py` (который влияет только на prompt-hint),
этот модуль фильтрует РЕАЛЬНЫЙ OpenAI-совместимый tools manifest перед отправкой
в OpenClaw → LLM. Таким образом LLM не видит запрещённые команде инструменты
и не может их вызвать (kроме галлюцинации).

Hook-точка: ContextVar `_swarm_team_ctx` выставляется в `_AgentRoomRouterAdapter`
и читается в `openclaw_client._openclaw_completion_once` сразу после получения
manifest'а от `mcp_manager.get_tool_manifest()`.

Backward-compat: пустой/неизвестный team → manifest возвращается как есть.
"""

from __future__ import annotations

import contextvars
from typing import Any

from .logger import get_logger
from .swarm_bus import resolve_team_name

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# ContextVar: активная команда свёрма для текущего LLM-запроса.
# ---------------------------------------------------------------------------
# Выставляется в `_AgentRoomRouterAdapter.route_query` перед `send_message_stream`
# и сбрасывается в finally. Читается в `_openclaw_completion_once` и в
# `mcp_client.call_tool_unified` для silent-strip guard'а.

_swarm_team_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "swarm_team_ctx",
    default=None,
)


def get_current_team() -> str | None:
    """Текущая активная команда свёрма (или None вне свёрм-запроса)."""
    return _swarm_team_ctx.get()


def set_current_team(team: str | None) -> contextvars.Token:
    """Выставить команду; сохранить token для сброса в finally."""
    return _swarm_team_ctx.set(team)


def reset_current_team(token: contextvars.Token) -> None:
    """Сбросить ContextVar по token'у."""
    try:
        _swarm_team_ctx.reset(token)
    except (ValueError, LookupError):
        # Token уже сброшен или принадлежит другому контексту — не критично.
        pass


# ---------------------------------------------------------------------------
# Per-team whitelist.
# ---------------------------------------------------------------------------
# Ключи — канонические имена команд из TEAM_REGISTRY (swarm_bus.py).
# Значения — frozenset базовых имён tools (без `{server}__` префикса).
# Lookup — O(1) на каждый tool; иммутабельно.
#
# Базовый набор (_BASE_ALLOWLIST) добавляется к каждой команде автоматически —
# минимум нужный для работы свёрма.

_BASE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "web_search",
        "krab_memory_search",
    }
)

TEAM_TOOL_ALLOWLIST: dict[str, frozenset[str]] = {
    "traders": frozenset(
        {
            "web_search",
            "krab_memory_search",
            "krab_memory_stats",
            "tor_fetch",
        }
    ),
    "analysts": frozenset(
        {
            "web_search",
            "tor_fetch",
            "krab_memory_search",
            "krab_memory_stats",
            "telegram_search",
            "telegram_get_chat_history",
            "peekaboo",
            # fs/db/http — MCP commit aa7cf30: analyst может читать код и SQL.
            "fs_read_file",
            "fs_search",
            "db_query",
            "http_fetch",
        }
    ),
    "coders": frozenset(
        {
            "krab_run_tests",
            "krab_tail_logs",
            "krab_status",
            "krab_restart_gateway",
            "krab_memory_search",
            "web_search",
            "claude_cli",
            "codex",
            "gemini",
            # fs/git/system — MCP commit aa7cf30: coder читает код и git state.
            "fs_read_file",
            "fs_search",
            "fs_list_dir",
            "git_status",
            "git_log",
            "git_diff",
            "system_info",
            "http_fetch",
            "time_now",
            "time_parse",
            "db_query",
        }
    ),
    "creative": frozenset(
        {
            "web_search",
            "krab_memory_search",
            "telegram_send_message",
            "telegram_edit_message",
        }
    ),
}


def _base_tool_name(full_name: str) -> str:
    """Извлекает базовое имя tool'а из `{server}__{tool}` или возвращает как есть."""
    if "__" in full_name:
        return full_name.split("__", 1)[1]
    return full_name


def is_tool_allowed(tool_name: str, team: str | None) -> bool:
    """
    True если tool разрешён команде (или team не задана / не в allowlist).

    Используется в mcp_client.call_tool_unified как silent-strip guard.
    """
    if not team:
        return True
    canonical = resolve_team_name(team) or team
    allow = TEAM_TOOL_ALLOWLIST.get(canonical)
    if allow is None:
        # Незнакомая команда → backward-compat: разрешаем всё.
        return True
    base = _base_tool_name(tool_name)
    return base in allow or base in _BASE_ALLOWLIST


def filter_tools_for_team(
    manifest: list[dict[str, Any]],
    team: str | None,
) -> list[dict[str, Any]]:
    """
    Фильтрует OpenAI-совместимый tools manifest под allowlist команды.

    Поведение:
    - team пустая / None → manifest возвращается без изменений;
    - team не в TEAM_TOOL_ALLOWLIST (после resolve_team_name) → без изменений;
    - иначе — оставляем только tools, base-name которых в allowlist команды
      или в _BASE_ALLOWLIST.

    Args:
        manifest: список dict'ов формата `{"type": "function", "function": {"name": ..., ...}}`.
        team: имя команды (канон или alias) или None.

    Returns:
        Новый список (исходный не модифицируется).
    """
    if not manifest or not team:
        return manifest

    canonical = resolve_team_name(team) or team
    allow = TEAM_TOOL_ALLOWLIST.get(canonical)
    if allow is None:
        # Backward-compat: незнакомая команда → full manifest.
        return manifest

    effective = allow | _BASE_ALLOWLIST
    filtered: list[dict[str, Any]] = []
    dropped: list[str] = []
    for tool in manifest:
        try:
            name = str(tool.get("function", {}).get("name", ""))
        except (AttributeError, TypeError):
            # Не trust'аем формат — пропускаем подозрительные записи.
            continue
        if not name:
            continue
        base = _base_tool_name(name)
        if base in effective:
            filtered.append(tool)
        else:
            dropped.append(name)

    if dropped:
        logger.info(
            "swarm_tool_manifest_filtered",
            team=canonical,
            kept=len(filtered),
            dropped=len(dropped),
            dropped_sample=dropped[:5],
        )
    return filtered


# ---------------------------------------------------------------------------
# Prometheus-метрика для blocked tools (silent strip в call_tool_unified).
# ---------------------------------------------------------------------------
# Хранится локально как dict[(team, tool)] -> int; читается prometheus_metrics.py.

_BLOCKED_TOOL_COUNTER: dict[tuple[str, str], int] = {}


def record_blocked_tool(team: str, tool: str) -> None:
    """Инкрементирует счётчик заблокированных tool-calls."""
    key = (team, tool)
    _BLOCKED_TOOL_COUNTER[key] = _BLOCKED_TOOL_COUNTER.get(key, 0) + 1


def get_blocked_tool_stats() -> dict[tuple[str, str], int]:
    """Snapshot счётчика (read-only copy)."""
    return dict(_BLOCKED_TOOL_COUNTER)
