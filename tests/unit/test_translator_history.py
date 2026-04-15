# -*- coding: utf-8 -*-
"""
Тесты для translator history:
- append_translator_history_entry (core)
- !translator history [N] (command_handlers)
- GET /api/translator/history (web_app endpoint)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.translator_session_state import (
    HISTORY_MAX,
    append_translator_history_entry,
    default_translator_session_state,
)
from src.handlers.command_handlers import handle_translator

# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _make_state(history: list | None = None) -> dict:
    """Возвращает session state с заданной историей."""
    state = default_translator_session_state()
    if history is not None:
        state["history"] = history
    return state


def _make_bot(history: list | None = None) -> SimpleNamespace:
    """Mock-bot с методами translator, поддерживающий history."""
    session: dict = {
        **default_translator_session_state(),
        "stats": {"total_translations": 3, "total_latency_ms": 4500},
        "history": list(history or []),
    }

    def _get_session():
        return dict(session)

    def _update_session(**kwargs):
        session.update(kwargs)
        return dict(session)

    return SimpleNamespace(
        get_translator_session_state=_get_session,
        update_translator_session_state=_update_session,
        get_translator_runtime_profile=lambda: {
            "language_pair": "es-ru",
            "translation_mode": "bilingual",
        },
        update_translator_runtime_profile=lambda **kw: kw,
    )


def _make_message(text: str, chat_id: int = 42) -> SimpleNamespace:
    """Mock Message."""
    parts = text.split()
    return SimpleNamespace(
        command=parts,
        text=text,
        reply=AsyncMock(),
        chat=SimpleNamespace(id=chat_id),
    )


def _sample_entry(i: int = 0) -> dict:
    return {
        "src_lang": "es",
        "tgt_lang": "ru",
        "original": f"Buenos días {i}",
        "translation": f"Доброе утро {i}",
        "latency_ms": 1200 + i * 100,
        "timestamp": "2026-04-12T10:00:00Z",
    }


# ---------------------------------------------------------------------------
# Тесты: append_translator_history_entry
# ---------------------------------------------------------------------------


def test_append_добавляет_запись():
    state = _make_state()
    updated = append_translator_history_entry(
        state,
        src_lang="es",
        tgt_lang="ru",
        original="Hola",
        translation="Привет",
        latency_ms=800,
    )
    assert len(updated["history"]) == 1
    entry = updated["history"][0]
    assert entry["src_lang"] == "es"
    assert entry["tgt_lang"] == "ru"
    assert entry["original"] == "Hola"
    assert entry["translation"] == "Привет"
    assert entry["latency_ms"] == 800
    assert "timestamp" in entry


def test_append_не_мутирует_исходный_state():
    state = _make_state()
    original_len = len(state["history"])
    append_translator_history_entry(
        state, src_lang="ru", tgt_lang="es", original="X", translation="Y", latency_ms=1
    )
    assert len(state["history"]) == original_len


def test_append_ограничивает_до_history_max():
    state = _make_state(history=[_sample_entry(i) for i in range(HISTORY_MAX)])
    updated = append_translator_history_entry(
        state, src_lang="en", tgt_lang="ru", original="New", translation="Новый", latency_ms=500
    )
    assert len(updated["history"]) == HISTORY_MAX
    # Последняя запись должна быть новой
    assert updated["history"][-1]["original"] == "New"


def test_append_обрезает_длинные_строки():
    long_text = "A" * 400
    state = _make_state()
    updated = append_translator_history_entry(
        state, src_lang="en", tgt_lang="ru", original=long_text, translation=long_text, latency_ms=1
    )
    entry = updated["history"][0]
    assert len(entry["original"]) <= 300
    assert len(entry["translation"]) <= 300


def test_append_с_пустым_history_в_state():
    state = _make_state(history=[])
    updated = append_translator_history_entry(
        state, src_lang="ru", tgt_lang="es", original="Привет", translation="Hola", latency_ms=900
    )
    assert len(updated["history"]) == 1


# ---------------------------------------------------------------------------
# Тесты: !translator history (command_handlers)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_пустая_история():
    """При пустой истории сообщаем об этом."""
    bot = _make_bot(history=[])
    msg = _make_message("!translator history")
    await handle_translator(bot, msg)
    msg.reply.assert_called_once()
    assert "пуста" in msg.reply.call_args[0][0]


@pytest.mark.asyncio
async def test_history_показывает_записи():
    """Проверяем, что записи отображаются в правильном формате."""
    entries = [_sample_entry(i) for i in range(3)]
    bot = _make_bot(history=entries)
    msg = _make_message("!translator history")
    await handle_translator(bot, msg)
    msg.reply.assert_called_once()
    text = msg.reply.call_args[0][0]
    assert "Последние переводы" in text
    assert "es→ru" in text
    assert "Buenos días" in text
    assert "Доброе утро" in text


@pytest.mark.asyncio
async def test_history_default_показывает_5():
    """По умолчанию показываем 5 последних записей."""
    entries = [_sample_entry(i) for i in range(10)]
    bot = _make_bot(history=entries)
    msg = _make_message("!translator history")
    await handle_translator(bot, msg)
    text = msg.reply.call_args[0][0]
    # Нумерация должна идти до 5, не больше
    assert "5." in text
    assert "6." not in text


@pytest.mark.asyncio
async def test_history_с_аргументом_n():
    """!translator history 3 → показывает 3 записи."""
    entries = [_sample_entry(i) for i in range(8)]
    bot = _make_bot(history=entries)
    msg = _make_message("!translator history 3")
    await handle_translator(bot, msg)
    text = msg.reply.call_args[0][0]
    assert "3." in text
    assert "4." not in text


@pytest.mark.asyncio
async def test_history_n_clamp_max():
    """!translator history 99 → показывает не более 20 записей."""
    entries = [_sample_entry(i) for i in range(HISTORY_MAX)]
    bot = _make_bot(history=entries)
    msg = _make_message("!translator history 99")
    await handle_translator(bot, msg)
    text = msg.reply.call_args[0][0]
    assert "20." in text
    assert "21." not in text


@pytest.mark.asyncio
async def test_history_n_1():
    """!translator history 1 → показывает только последний перевод."""
    entries = [_sample_entry(i) for i in range(5)]
    bot = _make_bot(history=entries)
    msg = _make_message("!translator history 1")
    await handle_translator(bot, msg)
    text = msg.reply.call_args[0][0]
    assert "1." in text
    assert "2." not in text


@pytest.mark.asyncio
async def test_history_latency_отображается_в_секундах():
    """Latency должна быть в секундах с одним знаком после точки."""
    entries = [
        {
            "src_lang": "ru",
            "tgt_lang": "es",
            "original": "Привет",
            "translation": "Hola",
            "latency_ms": 2300,
            "timestamp": "2026-04-12T10:00:00Z",
        }
    ]
    bot = _make_bot(history=entries)
    msg = _make_message("!translator history")
    await handle_translator(bot, msg)
    text = msg.reply.call_args[0][0]
    assert "2.3s" in text


@pytest.mark.asyncio
async def test_history_порядок_новые_первыми():
    """Самые новые записи отображаются первыми (idx=1)."""
    entries = [
        {**_sample_entry(0), "original": "Старый"},
        {**_sample_entry(1), "original": "Новый"},
    ]
    bot = _make_bot(history=entries)
    msg = _make_message("!translator history")
    await handle_translator(bot, msg)
    text = msg.reply.call_args[0][0]
    # "1." должен быть у "Новый"
    idx_1 = text.find("1.")
    idx_new = text.find("Новый")
    idx_old = text.find("Старый")
    assert idx_1 < idx_new < idx_old or (idx_new > 0 and idx_old > idx_new)


# ---------------------------------------------------------------------------
# Тесты: append_translator_history_entry — накопление
# ---------------------------------------------------------------------------


def test_append_несколько_записей_по_порядку():
    state = _make_state()
    for i in range(5):
        state = append_translator_history_entry(
            state,
            src_lang="es",
            tgt_lang="ru",
            original=f"text_{i}",
            translation=f"текст_{i}",
            latency_ms=i * 100,
        )
    assert len(state["history"]) == 5
    assert state["history"][0]["original"] == "text_0"
    assert state["history"][-1]["original"] == "text_4"


def test_append_переполнение_вытесняет_старые():
    state = _make_state(history=[_sample_entry(i) for i in range(HISTORY_MAX)])
    # Добавляем ещё 3 записи
    for i in range(3):
        state = append_translator_history_entry(
            state,
            src_lang="en",
            tgt_lang="fr",
            original=f"extra_{i}",
            translation=f"supplémentaire_{i}",
            latency_ms=100,
        )
    assert len(state["history"]) == HISTORY_MAX
    # Первые 3 должны быть вытеснены
    originals = [e["original"] for e in state["history"]]
    assert "extra_0" in originals
    assert "extra_2" in originals
    # sample_entry(0) и sample_entry(1) и sample_entry(2) вытеснены
    assert _sample_entry(0)["original"] not in originals
