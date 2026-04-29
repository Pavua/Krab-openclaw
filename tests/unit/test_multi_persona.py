# -*- coding: utf-8 -*-
"""Тесты Idea 31 — Multi-persona switcher.

Покрывают:
- наличие всех 5 built-in профилей и непустой suffix у каждого
- маппинг chat_persona_profile (tone+formality) → persona name
- fallback graceful когда профиль не свежий / отсутствует
- persona_suffix_for_prompt формирует строку с маркером "Persona:"
- register_persona добавляет custom preset, reset_registry откатывает
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.core.chat_persona_profile import ChatPersonaStore
from src.core.multi_persona import (
    PersonaProfile,
    get_persona,
    list_personas,
    persona_suffix_for_prompt,
    pick_persona_for_chat,
    register_persona,
    reset_registry,
)

# --- Fixtures ---------------------------------------------------------------


@pytest.fixture
def fresh_store(tmp_path: Path) -> ChatPersonaStore:
    """Свежий store с управляемыми timestamp'ами (always fresh)."""
    return ChatPersonaStore(
        storage_path=tmp_path / "persona.json",
        now_fn=lambda: datetime.now(timezone.utc),
    )


@pytest.fixture(autouse=True)
def _reset() -> None:
    """Откатываем registry между тестами, чтобы custom не утекали."""
    reset_registry()
    yield
    reset_registry()


# --- Built-ins --------------------------------------------------------------


def test_all_five_builtin_personas_have_nonempty_suffix() -> None:
    """Все 5 встроенных profile должны быть зарегистрированы и иметь suffix."""
    expected = {"technical", "casual", "family", "business", "playful"}
    names = {p.name for p in list_personas()}
    assert expected <= names

    for name in expected:
        profile = get_persona(name)
        assert profile is not None
        assert profile.system_prompt_suffix.strip(), f"{name} suffix is empty"
        assert "Persona:" in profile.system_prompt_suffix


# --- Mapping ----------------------------------------------------------------


@pytest.mark.parametrize(
    "tone,formality,expected_name",
    [
        ("technical", "casual_pro", "technical"),
        ("casual", "casual", "casual"),
        ("family", "casual", "family"),
        ("formal", "formal", "business"),
        # formality=formal перекрывает casual/family → business
        ("casual", "formal", "business"),
        ("family", "formal", "business"),
        # neutral tone → fallback к casual
        ("neutral", "casual", "casual"),
    ],
)
def test_mapping_from_chat_profile_to_persona(
    fresh_store: ChatPersonaStore,
    tone: str,
    formality: str,
    expected_name: str,
) -> None:
    """tone+formality из chat_persona_profile → правильная persona."""
    chat_id = "-100123"
    fresh_store.save_profile(
        chat_id,
        {
            "tone": tone,
            "formality": formality,
            "preferred_reply_length": "medium",
            "message_count": 50,
        },
    )
    persona = pick_persona_for_chat(chat_id, store=fresh_store)
    assert persona.name == expected_name


# --- Fallback ---------------------------------------------------------------


def test_pick_persona_no_chat_id_returns_fallback() -> None:
    """Без chat_id → fallback по умолчанию (casual)."""
    persona = pick_persona_for_chat(None)
    assert persona.name == "casual"

    persona = pick_persona_for_chat("", fallback="business")
    assert persona.name == "business"


def test_pick_persona_no_profile_returns_fallback(
    fresh_store: ChatPersonaStore,
) -> None:
    """Чат без сохранённого profile → fallback graceful."""
    persona = pick_persona_for_chat("-100999", store=fresh_store, fallback="playful")
    assert persona.name == "playful"


def test_persona_suffix_for_prompt_empty_for_unknown_chat(
    fresh_store: ChatPersonaStore,
) -> None:
    """Для чата без profile suffix всё равно возвращается (от fallback persona)."""
    suffix = persona_suffix_for_prompt("-100888", store=fresh_store, fallback="casual")
    assert "Persona: casual" in suffix


def test_persona_suffix_for_known_chat_contains_marker(
    fresh_store: ChatPersonaStore,
) -> None:
    """Suffix содержит явный маркер persona-режима."""
    chat_id = "-100777"
    fresh_store.save_profile(
        chat_id,
        {
            "tone": "technical",
            "formality": "casual_pro",
            "preferred_reply_length": "medium",
            "message_count": 100,
        },
    )
    suffix = persona_suffix_for_prompt(chat_id, store=fresh_store)
    assert "Persona: technical" in suffix
    assert "разработк" in suffix.lower() or "ai" in suffix.lower()


# --- Custom registration ----------------------------------------------------


def test_register_custom_persona_and_lookup() -> None:
    """register_persona добавляет custom profile, get_persona достаёт его."""
    custom = PersonaProfile(
        name="lawyer",
        system_prompt_suffix="Persona: lawyer. Юридический точный стиль.",
        tone_keywords=("точно", "формально"),
    )
    register_persona(custom)

    fetched = get_persona("lawyer")
    assert fetched is custom
    assert fetched.system_prompt_suffix.startswith("Persona: lawyer")

    # reset_registry убирает кастомные профили
    reset_registry()
    assert get_persona("lawyer") is None


def test_register_persona_validation() -> None:
    """register_persona отвергает мусорные входы."""
    with pytest.raises(TypeError):
        register_persona("not a profile")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        register_persona(PersonaProfile(name="", system_prompt_suffix="x"))
