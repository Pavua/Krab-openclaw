# -*- coding: utf-8 -*-
"""Тесты для StickerRepliesEngine (Idea 2 — sticker replies decision engine)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.sticker_replies import StickerRepliesEngine


@pytest.fixture
def engine(tmp_path: Path) -> StickerRepliesEngine:
    """Чистый engine с временным JSON-store."""
    storage = tmp_path / "sticker_replies_chats.json"
    return StickerRepliesEngine(storage_path=storage)


def test_pattern_match_returns_sticker_id_when_configured(engine: StickerRepliesEngine) -> None:
    """Если чат opt-in и слот сконфигурён — возвращаем sticker_id."""
    chat_id = "12345"
    engine.enable_for_chat(chat_id)
    engine.set_sticker_id("ack_ok", "CAACAgIAAxkBAAEABCD_ok_sticker")

    result = engine.should_reply_with_sticker("ок", chat_id=chat_id)

    assert result == "CAACAgIAAxkBAAEABCD_ok_sticker"


def test_no_match_returns_none(engine: StickerRepliesEngine) -> None:
    """Длинный/непаттерновый ответ → None даже если чат opt-in."""
    chat_id = "12345"
    engine.enable_for_chat(chat_id)
    engine.set_sticker_id("ack_ok", "CAACAgIAAxkBAAEABCD_ok_sticker")

    # Длинный ответ — стикером не заменишь.
    long_text = "Это длинный развёрнутый ответ от Краба, " * 3
    assert engine.should_reply_with_sticker(long_text, chat_id=chat_id) is None
    # Совсем другой текст — None.
    assert engine.should_reply_with_sticker("какая сегодня погода", chat_id=chat_id) is None
    # Пустой текст — None.
    assert engine.should_reply_with_sticker("", chat_id=chat_id) is None


def test_chat_not_opted_in_returns_none(engine: StickerRepliesEngine) -> None:
    """Если чат не opt-in — никогда не возвращаем sticker, даже на полный match."""
    chat_id = "12345"
    engine.set_sticker_id("ack_ok", "CAACAgIAAxkBAAEABCD_ok_sticker")
    # Намеренно НЕ enable_for_chat
    assert engine.should_reply_with_sticker("ок", chat_id=chat_id) is None

    # Включаем — теперь да.
    engine.enable_for_chat(chat_id)
    assert engine.should_reply_with_sticker("ок", chat_id=chat_id) is not None


def test_unconfigured_slot_returns_none(engine: StickerRepliesEngine) -> None:
    """Match есть, но sticker_id не подставлен (placeholder) → None.

    Это страховка: если owner забыл сконфигурить слот, мы не отправим
    plaintext placeholder вместо настоящего стикера — лучше fallback на текст.
    """
    chat_id = "12345"
    engine.enable_for_chat(chat_id)
    # set_sticker_id для ack_ok не вызываем — слот в placeholder-состоянии.
    assert engine.should_reply_with_sticker("ок", chat_id=chat_id) is None


def test_persistence_round_trip(tmp_path: Path) -> None:
    """opt-in список и configured sticker_ids переживают рестарт engine."""
    storage = tmp_path / "sticker_replies_chats.json"
    engine1 = StickerRepliesEngine(storage_path=storage)
    engine1.enable_for_chat("11111")
    engine1.enable_for_chat("22222")
    engine1.set_sticker_id("ack_thanks", "CAACAgIAAxkBAAEABCD_thanks_sticker")

    # Файл должен содержать оба чата и configured sticker.
    raw = json.loads(storage.read_text(encoding="utf-8"))
    assert sorted(raw["chats_enabled"]) == ["11111", "22222"]
    assert raw["sticker_ids"]["ack_thanks"] == "CAACAgIAAxkBAAEABCD_thanks_sticker"

    # Новый engine с тем же путём — должен подхватить состояние.
    engine2 = StickerRepliesEngine(storage_path=storage)
    assert engine2.is_enabled_for_chat("11111")
    assert engine2.is_enabled_for_chat("22222")
    assert not engine2.is_enabled_for_chat("99999")
    assert engine2.should_reply_with_sticker("спасибо", chat_id="11111") == (
        "CAACAgIAAxkBAAEABCD_thanks_sticker"
    )


def test_multi_pattern_first_match_wins(engine: StickerRepliesEngine) -> None:
    """Несколько слотов сконфигурены — побеждает первый по порядку шаблон.

    DEFAULT_PATTERNS перечислены в порядке от узких к общим. Тут проверяем,
    что разные тексты попадают в разные слоты, а не все в один.
    """
    chat_id = "12345"
    engine.enable_for_chat(chat_id)
    engine.set_sticker_id("ack_ok", "STICKER_OK")
    engine.set_sticker_id("ack_thanks", "STICKER_THANKS")
    engine.set_sticker_id("emoji_fire", "STICKER_FIRE")
    engine.set_sticker_id("emoji_thumbs", "STICKER_THUMBS")
    engine.set_sticker_id("crab_greeting", "STICKER_CRAB")

    assert engine.should_reply_with_sticker("ок", chat_id=chat_id) == "STICKER_OK"
    assert engine.should_reply_with_sticker("спасибо", chat_id=chat_id) == "STICKER_THANKS"
    assert engine.should_reply_with_sticker("🔥🔥🔥", chat_id=chat_id) == "STICKER_FIRE"
    assert engine.should_reply_with_sticker("👍", chat_id=chat_id) == "STICKER_THUMBS"
    assert engine.should_reply_with_sticker("Привет!", chat_id=chat_id) == "STICKER_CRAB"


def test_disable_for_chat_stops_replies(engine: StickerRepliesEngine) -> None:
    """После disable_for_chat — стикер больше не возвращается."""
    chat_id = "777"
    engine.enable_for_chat(chat_id)
    engine.set_sticker_id("ack_ok", "STICKER_OK")
    assert engine.should_reply_with_sticker("ок", chat_id=chat_id) == "STICKER_OK"

    changed = engine.disable_for_chat(chat_id)
    assert changed is True
    assert engine.should_reply_with_sticker("ок", chat_id=chat_id) is None
    # Повторный disable — no-op.
    assert engine.disable_for_chat(chat_id) is False


def test_add_custom_pattern(engine: StickerRepliesEngine) -> None:
    """Owner может добавить кастомный шаблон, и он будет работать."""
    chat_id = "555"
    engine.enable_for_chat(chat_id)

    added = engine.add_pattern("custom_lol", r"^(лол|лолз|кек)[\.\!]*$", "русский смех")
    assert added is True
    engine.set_sticker_id("custom_lol", "STICKER_LOL")

    assert engine.should_reply_with_sticker("лол", chat_id=chat_id) == "STICKER_LOL"
    assert engine.should_reply_with_sticker("КЕК!", chat_id=chat_id) == "STICKER_LOL"

    # Битый regex → False, без падений.
    assert engine.add_pattern("bad", r"[unclosed", "битый") is False


def test_list_slots_reports_configured_status(engine: StickerRepliesEngine) -> None:
    """list_slots показывает какие слоты уже сконфигурены, какие нет."""
    engine.set_sticker_id("ack_ok", "STICKER_OK")
    slots = engine.list_slots()
    by_slot = {s["slot"]: s for s in slots}

    assert by_slot["ack_ok"]["configured"] is True
    assert by_slot["ack_ok"]["sticker_id"] == "STICKER_OK"
    assert by_slot["ack_thanks"]["configured"] is False
    assert by_slot["ack_thanks"]["sticker_id"].startswith("PLACEHOLDER_STICKER:")


def test_normalize_chat_id_accepts_int_and_str(engine: StickerRepliesEngine) -> None:
    """chat_id может прийти как int (Pyrogram) или str — оба работают одинаково."""
    engine.enable_for_chat(12345)  # int
    engine.set_sticker_id("ack_ok", "STICKER_OK")

    # Тот же чат, но как str — должен распознаться.
    assert engine.should_reply_with_sticker("ок", chat_id="12345") == "STICKER_OK"
    assert engine.should_reply_with_sticker("ок", chat_id=12345) == "STICKER_OK"
    # Пустой/None — None без падений.
    assert engine.should_reply_with_sticker("ок", chat_id=None) is None
    assert engine.should_reply_with_sticker("ок", chat_id="") is None
