# -*- coding: utf-8 -*-
"""
Wave 192: compound + anaphora + imperative-softener NLU patterns.

Дополняет Wave 40-T (ironic mentions) тремя классами обращений к Крабу:

1. Compound mention + вопрос:
   - "Краб, а ты что думаешь?"
   - "Крабушка, что скажешь?"
   - "Краб, как считаешь?"

2. Imperative softener:
   - "Краб, можешь подсказать?"
   - "Крабушка, плиз помоги"
   - "Краб, помоги"
   - "Крабушка, глянь"

3. Anaphora bridge внутри implicit-question window:
   - "Он же только что писал" (после Krab сообщения в чате)
   - "Его ответ был странный"

Все должны срабатывать через `detect_implicit_mention` с score >= 0.6.
"""

from __future__ import annotations

import pytest

from src.core.trigger_detector import (
    TriggerType,
    detect_implicit_mention,
    last_krab_msg,
)


@pytest.fixture(autouse=True)
def _clear_last_krab():
    """Сбросить store перед каждым тестом для детерминизма."""
    last_krab_msg._store.clear()
    yield
    last_krab_msg._store.clear()


# ── Compound mention + вопрос: "Краб, а ты что думаешь?" ─────────────────────


def test_compound_krab_chto_dumaesh() -> None:
    """Wave 192: 'Краб, а ты что думаешь?' → high score."""
    result = detect_implicit_mention("Краб, а ты что думаешь?")
    assert result.trigger_type != TriggerType.NONE, f"Должен сработать: {result}"
    assert result.score >= 0.6


def test_compound_krabushka_chto_skazhesh() -> None:
    """Wave 192: 'Крабушка, что скажешь по этому поводу?' → triggers."""
    result = detect_implicit_mention("Крабушка, что скажешь по этому поводу?")
    assert result.trigger_type != TriggerType.NONE
    assert result.score >= 0.6


def test_compound_krab_kak_schitaesh() -> None:
    """Wave 192: 'Краб как считаешь?' → triggers (без запятой)."""
    result = detect_implicit_mention("Краб как считаешь, стоит делать?")
    assert result.trigger_type != TriggerType.NONE
    assert result.score >= 0.6


# ── Imperative softener: "Краб, можешь..." / "Крабушка, помоги" ──────────────


def test_softener_krab_mozhesh_podskazat() -> None:
    """Wave 192: 'Краб, можешь подсказать?' → triggers."""
    result = detect_implicit_mention("Краб, можешь подсказать как настроить?")
    assert result.trigger_type != TriggerType.NONE
    assert result.score >= 0.6


def test_softener_krabushka_pomogi() -> None:
    """Wave 192: 'Крабушка, помоги' — прямая просьба → triggers."""
    result = detect_implicit_mention("Крабушка, помоги разобраться")
    assert result.trigger_type != TriggerType.NONE
    assert result.score >= 0.6


def test_softener_krabushka_pliz() -> None:
    """Wave 192: 'Крабушка, плиз' — аффективная просьба → triggers."""
    result = detect_implicit_mention("Крабушка, плиз")
    assert result.trigger_type != TriggerType.NONE
    assert result.score >= 0.6


def test_softener_krab_glyan() -> None:
    """Wave 192: 'Краб, глянь-ка' → triggers."""
    result = detect_implicit_mention("Краб, глянь-ка что тут происходит")
    assert result.trigger_type != TriggerType.NONE
    assert result.score >= 0.6


def test_softener_krab_ne_mozhesh_obyasnit() -> None:
    """Wave 192: 'Краб, не можешь объяснить?' (с отрицанием) → triggers."""
    result = detect_implicit_mention("Краб, не можешь объяснить что это значит?")
    assert result.trigger_type != TriggerType.NONE
    assert result.score >= 0.6


# ── Anaphora bridge внутри окна ──────────────────────────────────────────────


def test_anaphora_on_zhe_within_window() -> None:
    """Wave 192: 'Он же только что писал' после Krab-сообщения → triggers."""
    # Симулируем что Krab только что писал в чат.
    last_krab_msg.record("100500")
    result = detect_implicit_mention("Он же только что писал про это", chat_id="100500")
    assert result.trigger_type != TriggerType.NONE
    assert result.score >= 0.6


def test_anaphora_ego_otvet_within_window() -> None:
    """Wave 192: 'Его ответ был странный' после Krab → triggers."""
    last_krab_msg.record("200600")
    result = detect_implicit_mention("Его ответ был странный", chat_id="200600")
    assert result.trigger_type != TriggerType.NONE
    assert result.score >= 0.6


def test_anaphora_outside_window_no_trigger() -> None:
    """Wave 192: 'Он же только что писал' БЕЗ window — НЕ triggers."""
    # last_krab_msg НЕ записан → нет window.
    result = detect_implicit_mention("Он же только что писал", chat_id="999999")
    # Может быть NONE или generic low-score — главное < 0.6.
    assert result.score < 0.6


# ── Safety: false positives guards ──────────────────────────────────────────


def test_krab_in_url_no_compound_trigger() -> None:
    """Wave 192: 'крабовые палочки' — informational, не должен compound-triggers'нуться."""
    result = detect_implicit_mention("Купил крабовые палочки в магазине")
    # Не должно быть IMPLICIT_QUESTION от compound (нет вопроса/просьбы).
    # Может быть NONE или другой тип с low score.
    assert "compound_question" not in (result.matched or "")


def test_compound_without_krab_no_trigger() -> None:
    """Wave 192: 'а ты что думаешь?' без Krab — НЕ compound trigger."""
    result = detect_implicit_mention("а ты что думаешь об этом фильме?")
    # Может trigger через IQ prefix ("а ") + window, но не compound_question.
    assert "compound_question" not in (result.matched or "")


def test_anaphora_random_pronoun_outside_window() -> None:
    """Wave 192: 'Ему всё равно' без recent Krab activity — НЕ trigger."""
    result = detect_implicit_mention("Ему всё равно на это", chat_id="555")
    assert result.score < 0.6
