# -*- coding: utf-8 -*-
"""
Тесты Wave 37-C: tech-metaphors restraint в system_prompt.

Issue 2 из handoff: Krab перегружал ответы IT-аналогиями (SSH, OAuth, ports,
kernel) в casual чатах. Добавляем guidance в `_append_runtime_constraints`.

Тест: после прохождения через _append_runtime_constraints в prompt должен
появиться блок про сдержанность с техническими метафорами.
"""

from __future__ import annotations

import pytest

from src.userbot.access_control import AccessControlMixin

# ── basic: guidance должен быть добавлен ──────────────────────────────────────


def test_runtime_constraints_adds_tech_metaphors_guidance() -> None:
    """Wave 37-C: после _append_runtime_constraints в prompt появляется
    блок про tech-metaphors restraint."""
    base = "Ты — Краб."
    result = AccessControlMixin._append_runtime_constraints(base)

    # Должны быть ключевые слова из guidance
    assert "технических" in result.lower() or "tech-метафор" in result.lower(), (
        f"Должен быть указан tech metaphors restraint в prompt, got:\n{result}"
    )
    # Конкретные примеры из handoff
    assert "ssh" in result.lower(), (
        "Guidance должен явно перечислять примеры избегаемых метафор (SSH)"
    )


def test_runtime_constraints_keeps_other_guidance() -> None:
    """Wave 37-C: добавление tech-metaphors не ломает другие constraints
    (anti-parasite, reply-first)."""
    base = "Ты — Краб."
    result = AccessControlMixin._append_runtime_constraints(base)

    assert "паразитн" in result.lower(), "anti-parasite должен остаться"
    assert "reply-first" in result.lower(), "reply-first должен остаться"


def test_runtime_constraints_idempotent_for_tech_metaphors() -> None:
    """Wave 37-C: повторный вызов не дублирует guidance."""
    base = "Ты — Краб."
    result1 = AccessControlMixin._append_runtime_constraints(base)
    result2 = AccessControlMixin._append_runtime_constraints(result1)

    # Считаем уникальное вхождение marker'а
    marker = "ssh"
    count1 = result1.lower().count(marker)
    count2 = result2.lower().count(marker)
    assert count1 == count2, (
        f"Idempotency: повторный append не должен дублировать tech-metaphors "
        f"(count1={count1}, count2={count2})"
    )
