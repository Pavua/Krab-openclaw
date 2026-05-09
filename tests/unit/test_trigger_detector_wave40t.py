# -*- coding: utf-8 -*-
"""
Wave 40-T: smart routing trigger improvements — ironic + compound mentions.

Existing trigger_detector детектит generic AI questions ("кто знает", "подскажите"),
follow-up to Krab, и implicit questions в context window. Не покрывает:

1. Ironic mentions с явным упоминанием Краба:
   - "ну где же Краб?"
   - "куда пропал Краб?"
   - "что молчит Краб?"
   - "почему Краб не отвечает?"

2. Compound mentions — Краб упомянут рядом с @username:
   - "@yung_nagato Краб подключайся"
   - "Краб + @kraab"

Эти cases должны срабатывать с высоким score (>=0.65) чтобы пройти smart routing
threshold даже без explicit hard gate match.
"""

from __future__ import annotations

from src.core.trigger_detector import TriggerType, detect_implicit_mention

# ── ironic mentions: ironic question с упоминанием Краба ─────────────────────


def test_ironic_nu_gde_zhe_krab() -> None:
    """Wave 40-T: 'ну где же Краб?' → IMPLICIT_QUESTION с high score."""
    result = detect_implicit_mention("ну где же Краб?")
    assert result.trigger_type != TriggerType.NONE, f"Должен сработать: {result}"
    assert result.score >= 0.65, f"Ironic Krab mention должен быть >=0.65: {result.score}"


def test_ironic_kuda_propal_krab() -> None:
    """Wave 40-T: 'куда пропал Краб' (даже без ?) → triggers."""
    result = detect_implicit_mention("куда пропал Краб")
    assert result.trigger_type != TriggerType.NONE
    assert result.score >= 0.65


def test_ironic_chto_molchit_krab() -> None:
    """Wave 40-T: 'что молчит Краб?' → triggers."""
    result = detect_implicit_mention("что молчит Краб?")
    assert result.trigger_type != TriggerType.NONE
    assert result.score >= 0.65


def test_ironic_pochemu_krab_ne_otvechaet() -> None:
    """Wave 40-T: 'почему Краб не отвечает' → triggers."""
    result = detect_implicit_mention("почему Краб не отвечает?")
    assert result.trigger_type != TriggerType.NONE
    assert result.score >= 0.65


def test_ironic_gde_krab() -> None:
    """Wave 40-T: 'где Краб?' (короткая форма) → triggers."""
    result = detect_implicit_mention("где Краб?")
    assert result.trigger_type != TriggerType.NONE
    assert result.score >= 0.65


# ── compound mentions: @username рядом с Краб ────────────────────────────────


def test_compound_at_username_then_krab() -> None:
    """Wave 40-T: '@yung_nagato Краб подключайся' → triggers."""
    result = detect_implicit_mention("@yung_nagato Краб подключайся")
    assert result.trigger_type != TriggerType.NONE
    assert result.score >= 0.65


def test_compound_krab_then_at_username() -> None:
    """Wave 40-T: 'Краб + @kraab помоги' → triggers."""
    result = detect_implicit_mention("Краб и @kraab помоги")
    assert result.trigger_type != TriggerType.NONE
    assert result.score >= 0.65


# ── safety: false positive guards ─────────────────────────────────────────────


def test_krab_in_informational_context_no_trigger() -> None:
    """Wave 40-T: 'Например Краб такой бот' — informational, без вопроса/обращения,
    не должен срабатывать через ironic pattern."""
    result = detect_implicit_mention("Например Краб такой бот, который умеет отвечать")
    # Может сработать через generic_ai (бот + ? нету) или implicit question — но
    # ironic pattern не должен сработать. Если score < 0.65 — OK.
    assert result.score < 0.65, f"Не должен ironic-triggers'нуться: {result}"


def test_no_krab_no_trigger() -> None:
    """Wave 40-T: текст без Краба и без AI — не должен trigger через ironic patterns."""
    result = detect_implicit_mention("ну где же он?")
    # "ну где же" без Краба — не ironic Krab pattern, но и существующий
    # _IQ_PREFIX_PATTERNS имеет "ну а " не "ну где" → должен NONE
    assert result.score < 0.65


# ── existing patterns не сломаны ──────────────────────────────────────────────


def test_existing_implicit_question_still_works() -> None:
    """Wave 40-T: existing 'кто знает' все равно triggers."""
    result = detect_implicit_mention("кто знает как починить?")
    assert result.trigger_type == TriggerType.IMPLICIT_QUESTION
    assert result.score >= 0.4  # _IMPLICIT_QUESTION_SCORE


def test_existing_generic_ai_still_works() -> None:
    """Wave 40-T: existing 'бот ?' triggers."""
    result = detect_implicit_mention("бот, как дела?")
    assert result.trigger_type != TriggerType.NONE


def test_empty_text_returns_none() -> None:
    """Wave 40-T: пустой text — NONE (existing behavior)."""
    result = detect_implicit_mention("")
    assert result.trigger_type == TriggerType.NONE
