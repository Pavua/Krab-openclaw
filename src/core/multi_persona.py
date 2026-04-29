# -*- coding: utf-8 -*-
"""Idea 31 — Multi-persona switcher.

Краб переключает persona-режимы (technical / casual / family / business /
playful) в зависимости от профиля чата (см. `chat_persona_profile`,
Feature C). Этот модуль — чистая надстройка над уже накопленным анализом
чата: он не модифицирует store, не лезет в access_control напрямую,
и используется как helper для system-prompt suffix.

Use-case:
    >>> from src.core.multi_persona import persona_suffix_for_prompt
    >>> tail = persona_suffix_for_prompt(chat_id)
    >>> system_prompt = base_prompt + ("\\n\\n" + tail if tail else "")

Ключевые функции:
    - `pick_persona_for_chat(chat_id)` — возвращает PersonaProfile
    - `persona_suffix_for_prompt(chat_id)` — собирает финальный suffix
    - `register_persona(profile)` — добавить кастомный preset

Default-safe: при любой ошибке возвращаем neutral-результат и пустой
suffix, чтобы не сломать пайплайн system-prompt'а.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from .chat_persona_profile import (
    ChatPersonaStore,
    chat_persona_store,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Datatypes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PersonaProfile:
    """Описание persona-режима.

    - `name`: канонический id (`technical`, `casual`, ...)
    - `system_prompt_suffix`: текст, дописываемый в системный промпт
    - `tone_keywords`: маркеры тона (для диагностики/UI)
    - `examples`: короткие примеры реплик в этой persona
    """

    name: str
    system_prompt_suffix: str
    tone_keywords: tuple[str, ...] = field(default_factory=tuple)
    examples: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Built-in persona presets
# ---------------------------------------------------------------------------


_TECHNICAL = PersonaProfile(
    name="technical",
    system_prompt_suffix=(
        "Persona: technical. Чат про разработку/AI/инфраструктуру. "
        "Будь точным и предметным: код, команды, имена файлов и опции — "
        "пиши прямо, без лишних метафор. Можно термины и аббревиатуры. "
        "Не извиняйся за технический стиль."
    ),
    tone_keywords=("точно", "по делу", "термины"),
    examples=(
        "Перезапусти gateway: `openclaw gateway`.",
        "Падает на pyrofork 2.3.69 — посмотри traceback в logs/krab.log.",
    ),
)

_CASUAL = PersonaProfile(
    name="casual",
    system_prompt_suffix=(
        "Persona: casual. Повседневная переписка. Лёгкий разговорный тон, "
        "короткие фразы, можно сленг и эмоджи в меру. Не формализуй ответ — "
        "это не отчёт."
    ),
    tone_keywords=("разговорно", "коротко", "лайтово"),
    examples=(
        "ок, посмотрю позже",
        "ага, понял, давай так",
    ),
)

_FAMILY = PersonaProfile(
    name="family",
    system_prompt_suffix=(
        "Persona: family. Семейный чат с близкими. Тёплый и заботливый "
        "тон, обращайся по-доброму, избегай технического жаргона и резких "
        "формулировок. Помни про общий контекст семьи."
    ),
    tone_keywords=("тепло", "забота", "просто"),
    examples=(
        "Понял, заеду вечером",
        "Не переживай, всё хорошо",
    ),
)

_BUSINESS = PersonaProfile(
    name="business",
    system_prompt_suffix=(
        "Persona: business. Деловая переписка. Выдержанный и формальный "
        "тон, чёткая структура, никаких эмоджи и сленга. Уточняй сроки и "
        "ответственных, формулируй договорённости явно."
    ),
    tone_keywords=("формально", "структурно", "по делу"),
    examples=(
        "Подтверждаю получение, отвечу до конца дня.",
        "Предлагаю синхронизироваться завтра в 11:00.",
    ),
)

_PLAYFUL = PersonaProfile(
    name="playful",
    system_prompt_suffix=(
        "Persona: playful. Дружеский чат с шутками. Лёгкий юмор и игривые "
        "формулировки уместны, но без сарказма в адрес собеседника. Не "
        "превращай каждый ответ в шутку — баланс важнее."
    ),
    tone_keywords=("игриво", "юмор", "лайтово"),
    examples=(
        "ну ты и затейник 😄",
        "ладно-ладно, уговорил",
    ),
)


_BUILTIN_PROFILES: dict[str, PersonaProfile] = {
    p.name: p for p in (_TECHNICAL, _CASUAL, _FAMILY, _BUSINESS, _PLAYFUL)
}


# Изменяемый реестр (built-ins + custom через register_persona).
_REGISTRY: dict[str, PersonaProfile] = dict(_BUILTIN_PROFILES)


# ---------------------------------------------------------------------------
# Mapping rules
# ---------------------------------------------------------------------------


# Маппинг tone (из chat_persona_profile.analyze_messages) → persona name.
_TONE_TO_PERSONA: dict[str, str] = {
    "technical": "technical",
    "casual": "casual",
    "family": "family",
    "formal": "business",
    "neutral": "casual",
}


def _formality_override(formality: str, base: str) -> str:
    """Корректирует выбор по formality: formal-чат → business, даже если
    tone был casual; casual-formality в technical-чате остаётся technical.
    """
    if formality == "formal" and base in ("casual", "family"):
        return "business"
    return base


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_personas() -> list[PersonaProfile]:
    """Snapshot всех зарегистрированных persona (built-ins + custom)."""
    return list(_REGISTRY.values())


def get_persona(name: str) -> PersonaProfile | None:
    """Достаёт persona по имени или None."""
    if not name:
        return None
    return _REGISTRY.get(name.strip().lower())


def register_persona(profile: PersonaProfile) -> None:
    """Регистрирует кастомную persona. Перезаписывает существующую с тем же name."""
    if not isinstance(profile, PersonaProfile):
        raise TypeError("profile must be PersonaProfile")
    if not profile.name:
        raise ValueError("profile.name must be non-empty")
    _REGISTRY[profile.name.strip().lower()] = profile
    logger.info("multi_persona_registered", name=profile.name)


def reset_registry() -> None:
    """Сбрасывает registry к built-ins. Используется в тестах."""
    _REGISTRY.clear()
    _REGISTRY.update(_BUILTIN_PROFILES)


def pick_persona_for_chat(
    chat_id: Any,
    *,
    fallback: str = "casual",
    store: ChatPersonaStore | None = None,
) -> PersonaProfile:
    """Подбирает PersonaProfile под чат, опираясь на chat_persona_profile.

    Default-safe: при отсутствии данных или ошибке возвращает fallback
    persona (по умолчанию `casual`). Если fallback тоже не найден —
    отдаём пустую neutral-persona без suffix.
    """
    fallback_profile = _REGISTRY.get(fallback.strip().lower()) or PersonaProfile(
        name="neutral",
        system_prompt_suffix="",
    )
    if not chat_id:
        return fallback_profile

    store = store or chat_persona_store
    try:
        if not store.is_fresh(chat_id):
            return fallback_profile
        profile_data = store.get_profile(chat_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "multi_persona_pick_failed",
            chat_id=str(chat_id),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return fallback_profile

    if not profile_data:
        return fallback_profile

    tone = str(profile_data.get("tone") or "neutral").lower()
    formality = str(profile_data.get("formality") or "casual").lower()

    base = _TONE_TO_PERSONA.get(tone, fallback)
    name = _formality_override(formality, base)
    return _REGISTRY.get(name) or fallback_profile


def persona_suffix_for_prompt(
    chat_id: Any,
    *,
    fallback: str = "casual",
    store: ChatPersonaStore | None = None,
) -> str:
    """Возвращает persona-suffix для system prompt или "".

    Используется как «второй слой» поверх Feature C suffix:
        full_suffix = chat_persona_profile.format_persona_suffix(chat_id)
        full_suffix += "\\n\\n" + multi_persona.persona_suffix_for_prompt(chat_id)

    Default-safe: при ошибке возвращает "".
    """
    try:
        persona = pick_persona_for_chat(chat_id, fallback=fallback, store=store)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "multi_persona_suffix_failed",
            chat_id=str(chat_id),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return ""
    return persona.system_prompt_suffix or ""
