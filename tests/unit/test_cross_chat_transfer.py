# -*- coding: utf-8 -*-
"""
Тесты Feature I — Cross-Chat Learning Transfer.

Покрывают:
- find_similar_chat_for_profile: похожий чат найден >= threshold
- find_similar_chat_for_profile: нет похожих → (None, score)
- suggest_template: borrowed=True + borrowed_from
- suggest_template: missing source graceful
- bootstrap + format_persona_suffix integration: borrowed header
- idempotence: повторный вызов даёт тот же результат, source не меняется
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.chat_persona_profile import (
    ChatPersonaStore,
    build_profile_from_messages,
    format_persona_suffix,
)
from src.core.cross_chat_transfer import (
    bootstrap_borrowed_profile,
    find_similar_chat_for_profile,
    suggest_template,
)


@pytest.fixture
def tmp_store(tmp_path: Path) -> ChatPersonaStore:
    return ChatPersonaStore(storage_path=tmp_path / "chat_persona_profile.json")


@pytest.fixture
def populated_store(tmp_store: ChatPersonaStore) -> ChatPersonaStore:
    """Несколько profile с разными tone/formality."""
    # Технический чат — много кода/git/python.
    tech_msgs = [
        "Запушил commit, надо deploy на сервер",
        "Python венв сломался, fix через pip install",
        "API endpoint падает на регрессе, смотри log",
        "Регулярка regex зашла, тест прошёл в CI",
        "Модель LLM выдаёт мусор, prompt надо переписать",
        "Docker контейнер не стартует, смотри stack trace",
    ]
    build_profile_from_messages("tech_chat_1", tech_msgs, store=tmp_store)

    # Семейный чат.
    fam_msgs = [
        "Мам, я приехал, ужин будет в семь",
        "Папа звонил, обещал зайти в магазин",
        "Сестра приехала с детьми, обед готовлю",
        "Бабушка передаёт привет, сын её обнял",
        "Купил продукты в магазине, готовлю обед",
        "Малыш уснул, приедем позже на ужин",
    ]
    build_profile_from_messages("family_chat_1", fam_msgs, store=tmp_store)

    return tmp_store


def test_find_similar_chat_returns_tech_match(populated_store: ChatPersonaStore) -> None:
    """Новый технический чат должен матчиться с tech_chat_1."""
    new_msgs = [
        "Python код упал, надо fix",
        "Регресс на API, смотри log",
        "Deploy не прошёл в CI, баг в prompt",
        "Модель LLM ругается, перепиши config",
        "Git commit не уходит, regex поломан",
    ]
    from src.core.chat_persona_profile import analyze_messages

    partial = analyze_messages(new_msgs)
    source_id, score = find_similar_chat_for_profile(
        partial, "new_chat_99", store=populated_store, threshold=0.5
    )
    assert source_id == "tech_chat_1", f"expected tech match, got {source_id} score={score}"
    assert score >= 0.5


def test_find_similar_chat_no_match(populated_store: ChatPersonaStore) -> None:
    """Очень короткий пустой target_profile → нет матча."""
    partial = {"tone": "neutral", "formality": "casual", "common_words": []}
    source_id, score = find_similar_chat_for_profile(
        partial, "new_chat_99", store=populated_store, threshold=0.95
    )
    assert source_id is None
    # score может быть >0 за счёт formality, но < threshold.
    assert score < 0.95


def test_suggest_template_marks_borrowed(populated_store: ChatPersonaStore) -> None:
    """suggest_template копирует profile и помечает borrowed=True."""
    template = suggest_template("tech_chat_1", "new_chat_99", store=populated_store)
    assert template is not None
    assert template["borrowed"] is True
    assert template["borrowed_from"] == "tech_chat_1"
    assert template["target_chat_id"] == "new_chat_99"
    assert template.get("tone") == "technical"

    # Read-only от source: оригинал не помечен borrowed.
    source = populated_store.get_profile("tech_chat_1")
    assert source is not None
    assert "borrowed" not in source
    assert "borrowed_from" not in source


def test_suggest_template_missing_source_graceful(populated_store: ChatPersonaStore) -> None:
    """Несуществующий source → None без exception."""
    template = suggest_template("ghost_chat", "new_chat_99", store=populated_store)
    assert template is None


def test_bootstrap_idempotent_and_formats_suffix(populated_store: ChatPersonaStore) -> None:
    """Повторный bootstrap → тот же source. format_persona_suffix отмечает заимствование."""
    new_msgs = [
        "Python код упал, надо fix",
        "Регресс на API, смотри log",
        "Deploy не прошёл в CI, баг в prompt",
        "Модель LLM ругается, перепиши config",
        "Git commit не уходит, regex поломан",
    ]
    from src.core.chat_persona_profile import analyze_messages

    partial = analyze_messages(new_msgs)

    template_a = bootstrap_borrowed_profile(
        "new_chat_99",
        partial_target_profile=partial,
        store=populated_store,
        threshold=0.5,
    )
    template_b = bootstrap_borrowed_profile(
        "new_chat_99",
        partial_target_profile=partial,
        store=populated_store,
        threshold=0.5,
    )
    assert template_a is not None
    assert template_b is not None
    assert template_a["borrowed_from"] == template_b["borrowed_from"] == "tech_chat_1"
    assert template_a["similarity_score"] == template_b["similarity_score"]

    # Source profile не должен быть переписан после двух bootstrap.
    source = populated_store.get_profile("tech_chat_1")
    assert source is not None
    assert "borrowed" not in source

    # У target свой profile тоже не должен появиться (read-only).
    assert populated_store.get_profile("new_chat_99") is None

    # format_persona_suffix с borrowed_template — должен содержать «заимствовано».
    suffix = format_persona_suffix(
        "new_chat_99",
        store=populated_store,
        borrowed_template=template_a,
    )
    assert suffix
    assert "заимствовано" in suffix.lower()
    assert "tech_chat_1" in suffix
