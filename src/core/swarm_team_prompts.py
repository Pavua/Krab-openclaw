# -*- coding: utf-8 -*-
"""
swarm_team_prompts.py — system prompts для team-аккаунтов в режиме listener.

Каждый team-аккаунт при ответе в ЛС/группе использует свой характер и экспертизу.
"""

from __future__ import annotations

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


# Wave 16-D: in-memory overlay для A/B-тестирования промптов.
# set_team_prompt_overlay() записывает, get_team_system_prompt() читает overlay
# с приоритетом над TEAM_PROMPTS. Сброс — set_team_prompt_overlay(team, None).
_PROMPT_OVERLAYS: dict[str, str] = {}


def get_team_system_prompt(team_name: str) -> str:
    """Возвращает system prompt для team-аккаунта.

    Сначала проверяет overlay (A/B candidate), затем TEAM_PROMPTS, затем базовый шаблон.
    """
    key = team_name.lower()
    # Overlay имеет наивысший приоритет — позволяет A/B применить кандидатный промпт
    if key in _PROMPT_OVERLAYS and _PROMPT_OVERLAYS[key]:
        return _PROMPT_OVERLAYS[key]
    return TEAM_PROMPTS.get(key, _BASE.format(team=team_name))


def set_team_prompt_overlay(team_name: str, prompt: str | None) -> None:
    """Устанавливает (или снимает при prompt=None) overlay-промпт для команды.

    Используется SkillCurator при apply кандидатного промпта после A/B-теста.
    Изменение вступает в силу немедленно (in-memory, не персистируется).
    """
    key = team_name.lower()
    if prompt:
        _PROMPT_OVERLAYS[key] = prompt
    else:
        _PROMPT_OVERLAYS.pop(key, None)
