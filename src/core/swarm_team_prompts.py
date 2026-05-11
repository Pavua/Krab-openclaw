# -*- coding: utf-8 -*-
"""
swarm_team_prompts.py — system prompts для team-аккаунтов в режиме listener.

Каждый team-аккаунт при ответе в ЛС/группе использует свой характер и экспертизу.

Step 3 (Wave 16-A): overlay-aware — если curator применил overlay, он подхватывается
с 30s TTL кешем. Fallback к TEAM_PROMPTS при любой ошибке загрузки state.

Wave 65-C / AGE-16: sender-aware — при передаче sender, в prompt инжектируется
identity-блок (Owner context для Pavel / Sender context для остальных).
Защищает от paranoid security default: «Я обрабатываю каждый запрос как новый,
без привязки к личности собеседника» — owner теперь распознаётся.
"""

from __future__ import annotations

import time
from typing import Any

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


def _build_sender_identity_block(sender: Any) -> str:
    """
    Wave 65-C / AGE-16: строит identity-блок отправителя для system prompt.

    - Owner (Pavel @p0lrd) → Owner context: доверенный, без security disclaimers,
      можно ссылаться на prior context, упоминать имя.
    - Не-owner → Sender context: обращаться по имени, но guarded по умолчанию.
    - sender=None или невалидный → пустая строка (backwards compat).

    Защищает от paranoid security default ответа bot'a:
    «Я обрабатываю каждый запрос как новый, без привязки к личности собеседника».
    """
    if sender is None:
        return ""

    sender_id = getattr(sender, "id", None)
    if not sender_id:
        return ""

    # Lazy import чтобы избежать circular dep + дать тестам patch'ить.
    try:
        from .access_control import is_owner_user_id
    except Exception:  # noqa: BLE001
        return ""

    try:
        if is_owner_user_id(sender_id):
            return (
                "\n\n## Owner context\n"
                "Это Pavel (@p0lrd, user_id=312322764), владелец Krab и main maintainer. "
                "Доверенный пользователь — можешь упомянуть имя, ссылаться на prior context, "
                "понимать что он Krab developer. Не нужно security disclaimers вроде "
                "«я обрабатываю каждый запрос как новый, без привязки к личности» — "
                "это default paranoid behavior, не подходящий для owner."
            )
    except Exception:  # noqa: BLE001
        # fail-soft: если ACL чтение упало — обращаемся как к не-owner.
        pass

    # Не-owner: обращение по имени, но guarded.
    first_name = (getattr(sender, "first_name", "") or "").strip()
    username = (getattr(sender, "username", "") or "").strip()
    sender_name = first_name or username or "Unknown"
    return (
        f"\n\n## Sender context\n"
        f"Собеседник: {sender_name} (user_id={sender_id}). "
        "Не доверенный по умолчанию, но можешь обращаться по имени. "
        "Применяй обычные security boundaries: не раскрывай внутренние детали, ключи, конфиги."
    )


def get_team_system_prompt(team_name: str, *, sender: Any = None) -> str:
    """Возвращает effective prompt: overlay (если есть) или baseline TEAM_PROMPTS.

    Overlay загружается из CuratorState с 30s TTL кешем (lazy import чтобы
    избежать circular dep). При любой ошибке — fail-soft, fallback к baseline.

    Wave 65-C: при передаче `sender` (pyrogram User-like), в prompt добавляется
    identity-блок (Owner context для Pavel / Sender context для остальных).
    Identity всегда инжектируется ПОСЛЕ overlay/baseline prompt — sender-зависимая
    часть не кэшируется, baseline кэшируется как раньше.
    """

    key = team_name.lower()
    now = time.monotonic()

    # Проверяем кеш baseline/overlay prompt (без identity)
    base_prompt: str | None = None
    cached = _overlay_cache.get(key)
    if cached is not None:
        expire_ts, cached_prompt = cached
        if now < expire_ts:
            if cached_prompt is not None:
                base_prompt = cached_prompt
            else:
                base_prompt = TEAM_PROMPTS.get(key, _BASE.format(team=team_name))

    if base_prompt is None:
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
            base_prompt = overlay_prompt
        else:
            base_prompt = TEAM_PROMPTS.get(key, _BASE.format(team=team_name))

    # Wave 65-C: добавляем sender identity block если sender передан
    identity_block = _build_sender_identity_block(sender)
    if identity_block:
        return base_prompt + identity_block
    return base_prompt
