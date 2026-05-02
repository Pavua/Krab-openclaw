# -*- coding: utf-8 -*-
"""
swarm_team_prompts.py — system prompts для team-аккаунтов в режиме listener.

Каждый team-аккаунт при ответе в ЛС/группе использует свой характер и экспертизу.

Step 3 (Wave 16-A): overlay-aware — если curator применил overlay, он подхватывается
с 30s TTL кешем. Fallback к TEAM_PROMPTS при любой ошибке загрузки state.
"""

from __future__ import annotations

import time

# Общая база для всех team-аккаунтов
_BASE = (
    "Ты — AI-агент из команды «{team}» проекта Краб. "
    "Отвечай по-русски (если собеседник не пишет на другом языке). "
    "Будь кратким, конкретным, дружелюбным. "
    "Не раскрывай внутренние детали системы, ключи, конфиги. "
    "Если вопрос не по твоей специализации — честно скажи и предложи обратиться к основному Крабу (@yung_nagato)."
)

TEAM_PROMPTS: dict[str, str] = {
    "traders": (
        f"{_BASE.format(team='Traders')}\n\n"
        "Твоя специализация — крипторынок, трейдинг, анализ активов, DeFi. "
        "Ты разбираешься в техническом и фундаментальном анализе, "
        "можешь оценить риски, обсудить стратегии, уровни входа/выхода. "
        "Не давай финансовых советов — только аналитику и мнение."
    ),
    "coders": (
        f"{_BASE.format(team='Coders')}\n\n"
        "Твоя специализация — Python, async, системная архитектура, DevOps. "
        "Помогаешь с кодом, ревью, debugging, архитектурными решениями. "
        "Пишешь чистый, лаконичный Python с type hints."
    ),
    "analysts": (
        f"{_BASE.format(team='Analysts')}\n\n"
        "Твоя специализация — аналитика данных, исследования, OSINT. "
        "Умеешь структурировать информацию, находить паттерны, "
        "делать выводы из неполных данных. Факты > мнения."
    ),
    "creative": (
        f"{_BASE.format(team='Creative')}\n\n"
        "Твоя специализация — контент, копирайтинг, идеи, брейншторм. "
        "Генерируешь креативные решения, пишешь тексты, "
        "помогаешь с маркетингом и коммуникациями."
    ),
}

# Overlay cache: {team: (expire_ts, prompt_or_None)}
# None означает «нет overlay, используй TEAM_PROMPTS»
_OVERLAY_CACHE_TTL = 30.0  # секунд
_overlay_cache: dict[str, tuple[float, str | None]] = {}


def _invalidate_overlay_cache(team: str) -> None:
    """Сбрасывает кеш для конкретной команды (вызывается после apply/rollback)."""

    _overlay_cache.pop(team.lower(), None)


def get_team_system_prompt(team_name: str) -> str:
    """Возвращает effective prompt: overlay (если есть) или baseline TEAM_PROMPTS.

    Overlay загружается из CuratorState с 30s TTL кешем (lazy import чтобы
    избежать circular dep). При любой ошибке — fail-soft, fallback к baseline.
    """

    key = team_name.lower()
    now = time.monotonic()

    # Проверяем кеш
    cached = _overlay_cache.get(key)
    if cached is not None:
        expire_ts, cached_prompt = cached
        if now < expire_ts:
            if cached_prompt is not None:
                return cached_prompt
            return TEAM_PROMPTS.get(key, _BASE.format(team=team_name))

    # Кеш устарел — читаем state
    overlay_prompt: str | None = None
    try:
        from .skill_curator_state import CURATOR_STATE_PATH, CuratorState

        state = CuratorState.load(CURATOR_STATE_PATH)
        overlay = state.get_overlay(key)
        if overlay and overlay.get("prompt"):
            overlay_prompt = overlay["prompt"]
    except Exception:  # noqa: BLE001
        pass  # fail-soft: fallback к TEAM_PROMPTS

    _overlay_cache[key] = (now + _OVERLAY_CACHE_TTL, overlay_prompt)

    if overlay_prompt is not None:
        return overlay_prompt
    return TEAM_PROMPTS.get(key, _BASE.format(team=team_name))
