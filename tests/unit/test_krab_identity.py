# -*- coding: utf-8 -*-
"""
Тесты для src/core/krab_identity.py

Проверяют:
- mention detection (Краб, Krab, 🦀, @yung_nagato)
- различение owner vs self
- корректность identity system prompt
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# is_krab_mentioned — mention detection
# ---------------------------------------------------------------------------


def test_mention_russian_explicit():
    from src.core.krab_identity import is_krab_mentioned

    assert is_krab_mentioned("Краб, привет!")
    assert is_krab_mentioned("что скажет Краб про это?")
    assert is_krab_mentioned("краб")  # lower case
    assert is_krab_mentioned("КРАБ")  # upper case


def test_mention_russian_word_boundary():
    """'крабы' — множественное число, не должно матчить \bкраб\b"""
    from src.core.krab_identity import is_krab_mentioned

    assert not is_krab_mentioned("крабы вкусные")
    assert not is_krab_mentioned("краба съел")  # родительный падеж


def test_mention_english():
    from src.core.krab_identity import is_krab_mentioned

    assert is_krab_mentioned("hey Krab")
    assert is_krab_mentioned("KRAB respond")
    assert is_krab_mentioned("krab please help")


def test_mention_english_word_boundary():
    """'krabs' — не совпадает с \bkrab\b"""
    from src.core.krab_identity import is_krab_mentioned

    assert not is_krab_mentioned("Mr. Krabs from SpongeBob")


def test_mention_emoji():
    from src.core.krab_identity import is_krab_mentioned

    assert is_krab_mentioned("🦀 ?")
    assert is_krab_mentioned("привет 🦀 как дела?")
    assert is_krab_mentioned("🦀")


def test_mention_username():
    from src.core.krab_identity import is_krab_mentioned

    assert is_krab_mentioned("@yung_nagato")
    assert is_krab_mentioned("эй @yung_nagato что думаешь?")
    assert is_krab_mentioned("@YUNG_NAGATO")  # case-insensitive


def test_no_mention():
    from src.core.krab_identity import is_krab_mentioned

    assert not is_krab_mentioned("просто сообщение")
    assert not is_krab_mentioned("")
    assert not is_krab_mentioned("привет всем")
    assert not is_krab_mentioned("@p0lrd как дела?")  # owner mention — не триггер


def test_mention_mid_sentence():
    """Краб упомянут в середине предложения — должен реагировать."""
    from src.core.krab_identity import is_krab_mentioned

    assert is_krab_mentioned("ребята, Краб знает ответ?")
    # "Краба" — родительный падеж, не матчит \bкраб\b (word boundary)
    # Но @yung_nagato / 🦀 всё равно дают match
    assert is_krab_mentioned("спроси у @yung_nagato")
    assert is_krab_mentioned("спроси у 🦀")


# ---------------------------------------------------------------------------
# is_message_from_owner / is_message_from_self
# ---------------------------------------------------------------------------


def test_owner_detection():
    from src.core.krab_identity import is_message_from_owner

    assert is_message_from_owner(312322764)  # p0lrd
    assert not is_message_from_owner(6435872621)  # yung_nagato (self)
    assert not is_message_from_owner(0)
    assert not is_message_from_owner(999999999)


def test_self_detection():
    from src.core.krab_identity import is_message_from_self

    assert is_message_from_self(6435872621)  # yung_nagato
    assert not is_message_from_self(312322764)  # p0lrd (owner)
    assert not is_message_from_self(0)


def test_owner_and_self_are_different():
    """Owner и Krab — разные user_id, никогда не совпадают."""
    from src.core.krab_identity import KRAB_USER_ID, OWNER_USER_ID

    assert KRAB_USER_ID != OWNER_USER_ID


# ---------------------------------------------------------------------------
# get_identity_system_prompt
# ---------------------------------------------------------------------------


def test_system_prompt_contains_krab_name():
    from src.core.krab_identity import get_identity_system_prompt

    p = get_identity_system_prompt()
    assert "Краб" in p or "Krab" in p


def test_system_prompt_contains_krab_username():
    from src.core.krab_identity import get_identity_system_prompt

    p = get_identity_system_prompt()
    assert "yung_nagato" in p


def test_system_prompt_contains_owner_username():
    from src.core.krab_identity import get_identity_system_prompt

    p = get_identity_system_prompt()
    assert "p0lrd" in p


def test_system_prompt_has_identity_separation():
    """Промпт должен явно указывать что Краб ≠ owner."""
    from src.core.krab_identity import get_identity_system_prompt

    p = get_identity_system_prompt().lower()
    # "не путай" или "не является" или "отдельн"
    assert "не путай" in p or "не является" in p or "отдельн" in p


def test_system_prompt_contains_user_ids():
    from src.core.krab_identity import KRAB_USER_ID, OWNER_USER_ID, get_identity_system_prompt

    p = get_identity_system_prompt()
    assert str(KRAB_USER_ID) in p
    assert str(OWNER_USER_ID) in p


def test_system_prompt_nonempty():
    from src.core.krab_identity import get_identity_system_prompt

    p = get_identity_system_prompt()
    assert len(p.strip()) > 50  # содержательный блок
