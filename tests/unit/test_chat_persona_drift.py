# -*- coding: utf-8 -*-
"""
Тесты Feature C — Per-Chat Persona Drift.

Покрывают:
- profile creation (build_profile_from_messages → store get/list)
- suffix formatting (наличие ключевых маркеров)
- empty chat → graceful "" suffix
- tone detection: technical / casual / family
- prompt без chat_id не содержит suffix
- idempotence: повторный build перезаписывает profile, suffix остаётся валидным
- mat-фильтрация common_words
- TTL: просроченный profile → suffix пустой
- access_control hook: _append_runtime_constraints с chat_id добавляет блок
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.core.chat_persona_profile import (
    PROFILE_TTL_HOURS,
    ChatPersonaStore,
    analyze_messages,
    build_profile_from_messages,
    format_persona_suffix,
)

# --- Fixtures ---------------------------------------------------------------


@pytest.fixture
def tmp_store(tmp_path: Path) -> ChatPersonaStore:
    """Свежий store с tmp-файлом, без bootstrap из дефолтного пути."""
    return ChatPersonaStore(storage_path=tmp_path / "chat_persona_profile.json")


def _config_mock(*, scheduler_enabled: bool = False, drift_enabled: bool = True) -> MagicMock:
    cfg = MagicMock()
    cfg.SCHEDULER_ENABLED = scheduler_enabled
    cfg.KRAB_PERSONA_DRIFT_ENABLED = drift_enabled
    return cfg


# --- 1. profile creation ----------------------------------------------------


class TestProfileCreation:
    def test_build_profile_persists_to_store(self, tmp_store: ChatPersonaStore) -> None:
        messages = [
            "обсуждаем новую LLM модель и промпт инженерию",
            "залил коммит, тесты проходят",
            "запушил docker и проверил endpoint",
            "давай посмотрим логи python и регистр ошибок",
            "json парсится корректно, async работает",
            "всё чисто, ci прошёл",
        ]
        profile = build_profile_from_messages(
            "-1001587432709",
            messages,
            title_hint="ЧАТ How2AI",
            store=tmp_store,
        )
        assert profile is not None
        assert profile["tone"] == "technical"
        assert profile["title_hint"] == "ЧАТ How2AI"
        assert profile["message_count"] == len(messages)
        assert "last_updated_at" in profile

        # Persist round-trip: новый store с тем же путём — данные читаются.
        reborn = ChatPersonaStore(storage_path=tmp_store._storage_path)
        assert reborn.get_profile("-1001587432709") is not None

    def test_build_profile_too_few_messages_returns_none(self, tmp_store: ChatPersonaStore) -> None:
        result = build_profile_from_messages("chat_x", ["только два", "сообщения"], store=tmp_store)
        assert result is None
        assert tmp_store.get_profile("chat_x") is None


# --- 2. suffix formatting ---------------------------------------------------


class TestSuffixFormatting:
    def test_suffix_contains_key_markers(self, tmp_store: ChatPersonaStore) -> None:
        build_profile_from_messages(
            "chat_tech",
            [
                "обсуждаем модель LLM и промпты",
                "git commit прошёл, deploy на сервер ок",
                "regex и json в API endpoint",
                "python async корутины правильно работают",
                "stack trace показал error в module",
                "конфиг env починили",
            ],
            title_hint="ЧАТ How2AI",
            store=tmp_store,
        )
        with patch("src.core.chat_persona_profile.chat_persona_store", tmp_store):
            suffix = format_persona_suffix("chat_tech")
        assert "Контекст этого чата" in suffix
        assert "ЧАТ How2AI" in suffix
        assert "технический" in suffix
        assert "длина ответа" in suffix


# --- 3. empty chat graceful -------------------------------------------------


class TestEmptyChatGraceful:
    def test_no_profile_returns_empty(self, tmp_store: ChatPersonaStore) -> None:
        with patch("src.core.chat_persona_profile.chat_persona_store", tmp_store):
            assert format_persona_suffix("nonexistent_chat") == ""

    def test_empty_messages_analyze(self) -> None:
        profile = analyze_messages([])
        assert profile["message_count"] == 0
        assert profile["common_words"] == []

    def test_no_chat_id_returns_empty(self, tmp_store: ChatPersonaStore) -> None:
        with patch("src.core.chat_persona_profile.chat_persona_store", tmp_store):
            assert format_persona_suffix(None) == ""
            assert format_persona_suffix("") == ""


# --- 4. tone detection ------------------------------------------------------


class TestToneDetection:
    def test_technical_tone(self) -> None:
        msgs = [
            "обсуждаем python код и git commit",
            "deploy на server прошёл, регистр logs чистый",
            "endpoint API возвращает json",
            "regex и async корутины",
            "test упал, fix нужен",
        ] * 3  # density >3%
        profile = analyze_messages(msgs)
        assert profile["tone"] == "technical"

    def test_family_tone(self) -> None:
        msgs = [
            "мама приехала, обед готовлю",
            "сын позвонил из магазина",
            "ужин будет в семь, бабушка тоже придёт",
            "малыш уснул, родители рады",
            "брат и сестра приедут на выходные",
        ] * 3
        profile = analyze_messages(msgs)
        assert profile["tone"] == "family"

    def test_casual_default(self) -> None:
        msgs = [
            "привет как дела",
            "норм всё, отдыхаю",
            "посмотрел кино новое, классное",
            "погода радует сегодня",
            "куда пойдём вечером",
        ]
        profile = analyze_messages(msgs)
        assert profile["tone"] == "casual"


# --- 5. prompt без chat_id --------------------------------------------------


class TestAppendRuntimeConstraintsNoChatId:
    def test_no_chat_id_no_suffix(self) -> None:
        from src.userbot.access_control import AccessControlMixin

        with patch("src.config.config", _config_mock()):
            out = AccessControlMixin._append_runtime_constraints("BASE PROMPT")
        assert "Контекст этого чата" not in out
        # Базовые правила сохранены.
        assert "паразитных хвостов" in out
        assert "Reply-first правило" in out


# --- 6. access_control hook прокидывает suffix ------------------------------


class TestAppendRuntimeConstraintsWithChatId:
    def test_chat_id_with_fresh_profile_adds_suffix(self, tmp_store: ChatPersonaStore) -> None:
        from src.userbot.access_control import AccessControlMixin

        build_profile_from_messages(
            "chat_demo",
            [
                "git commit и python deploy",
                "endpoint API json regex",
                "async корутины test fix",
                "logs server stack trace",
                "config env module import",
                "build ok ci прошёл",
            ],
            title_hint="DEMO chat",
            store=tmp_store,
        )
        with (
            patch("src.config.config", _config_mock()),
            patch("src.core.chat_persona_profile.chat_persona_store", tmp_store),
        ):
            out = AccessControlMixin._append_runtime_constraints("BASE", chat_id="chat_demo")
        assert "Контекст этого чата" in out
        assert "DEMO chat" in out
        # Базовые блоки сохранены.
        assert "паразитных хвостов" in out

    def test_chat_id_without_profile_no_suffix(self, tmp_store: ChatPersonaStore) -> None:
        from src.userbot.access_control import AccessControlMixin

        with (
            patch("src.config.config", _config_mock()),
            patch("src.core.chat_persona_profile.chat_persona_store", tmp_store),
        ):
            out = AccessControlMixin._append_runtime_constraints("BASE", chat_id="unknown_chat")
        assert "Контекст этого чата" not in out

    def test_feature_disabled_no_suffix(self, tmp_store: ChatPersonaStore) -> None:
        from src.userbot.access_control import AccessControlMixin

        build_profile_from_messages(
            "chat_demo",
            ["сообщение раз", "сообщение два", "три", "четыре", "пять", "шесть"],
            store=tmp_store,
        )
        with (
            patch("src.config.config", _config_mock(drift_enabled=False)),
            patch("src.core.chat_persona_profile.chat_persona_store", tmp_store),
        ):
            out = AccessControlMixin._append_runtime_constraints("BASE", chat_id="chat_demo")
        assert "Контекст этого чата" not in out


# --- 7. idempotence ---------------------------------------------------------


class TestIdempotence:
    def test_rebuild_overwrites_profile(self, tmp_store: ChatPersonaStore) -> None:
        # Первый build — technical.
        build_profile_from_messages(
            "chat_z",
            [
                "python код и git commit",
                "deploy на server",
                "endpoint json regex",
                "async корутины test",
                "logs stack trace error",
                "ci build ok",
            ],
            store=tmp_store,
        )
        first = tmp_store.get_profile("chat_z")
        assert first is not None
        assert first["tone"] == "technical"

        # Второй build — family.
        build_profile_from_messages(
            "chat_z",
            [
                "мама приехала на обед",
                "сын позвонил",
                "ужин в семь, бабушка тоже",
                "малыш уснул, родители рады",
                "брат с сестрой завтра",
            ]
            * 3,
            store=tmp_store,
        )
        second = tmp_store.get_profile("chat_z")
        assert second is not None
        assert second["tone"] == "family"
        assert second["last_updated_at"] >= first["last_updated_at"]


# --- 8. mat filtering -------------------------------------------------------


class TestMatFiltering:
    def test_banned_words_excluded_from_common(self) -> None:
        msgs = [
            "блять опять баг",
            "хуёво всё работает",
            "пиздец конечно",
            "нормально кодим дальше",
            "fix готов наконец",
            "deploy прошёл",
        ]
        profile = analyze_messages(msgs)
        for word in profile["common_words"]:
            # Ни одно из mat-stems не должно попасть в common_words.
            assert "бля" not in word
            assert "хуй" not in word and "хуё" not in word
            assert "пизд" not in word


# --- 9. TTL -----------------------------------------------------------------


class TestTTL:
    def test_stale_profile_returns_empty_suffix(self, tmp_path: Path) -> None:
        # Mutable now-fn чтобы прыгнуть на 7 часов вперёд.
        clock = [datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc)]
        store = ChatPersonaStore(
            storage_path=tmp_path / "p.json",
            now_fn=lambda: clock[0],
        )
        build_profile_from_messages(
            "chat_old",
            [
                "обычное сообщение раз",
                "обычное сообщение два",
                "обычное сообщение три",
                "обычное сообщение четыре",
                "обычное сообщение пять",
                "обычное сообщение шесть",
            ],
            store=store,
        )
        # Сразу после save — fresh.
        assert store.is_fresh("chat_old") is True
        # Прыжок далеко за TTL.
        clock[0] = clock[0] + timedelta(hours=PROFILE_TTL_HOURS + 1)
        assert store.is_fresh("chat_old") is False
        with patch("src.core.chat_persona_profile.chat_persona_store", store):
            assert format_persona_suffix("chat_old") == ""
