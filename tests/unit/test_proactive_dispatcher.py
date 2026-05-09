# -*- coding: utf-8 -*-
"""
tests/unit/test_proactive_dispatcher.py — Wave 39-B-1: ProactiveDispatcher.

RED phase: все тесты написаны до реализации модуля.
Покрытие:
  - Глобальный gate (KRAB_PROACTIVE_ENABLED=0)
  - Gate existing trigger (не-none)
  - Определение типов событий (join / media / ai_alias / none)
  - Тест ложных срабатываний ai_alias
  - Gate SILENT mode (policy)
  - Per-chat opt-in: proactive_joins / media / ai_alias
  - Квота (дневной лимит)
  - Burst cooldown (5 мин)
  - Backoff при 3+ dismiss реакциях за 24h
  - record_response инкрементирует счётчик
  - Ежедневный сброс счётчиков (midnight reset)
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub-объекты — не импортируем реальные зависимости, чтобы не мешать другим
# ---------------------------------------------------------------------------


@dataclass
class _FakeChatPolicy:
    """Минимальная заглушка ChatResponsePolicy."""

    mode: str = "normal"
    proactive_joins: bool = True
    proactive_media: bool = True
    proactive_ai: bool = True


class _FakePolicyStore:
    """Заглушка ChatResponsePolicyStore."""

    def __init__(
        self, *, mode: str = "normal", joins: bool = True, media: bool = True, ai: bool = True
    ):
        self._mode = mode
        self._joins = joins
        self._media = media
        self._ai = ai

    def get_policy(self, chat_id: str | int) -> _FakeChatPolicy:
        return _FakeChatPolicy(
            mode=self._mode,
            proactive_joins=self._joins,
            proactive_media=self._media,
            proactive_ai=self._ai,
        )


class _FakeFeedbackTracker:
    """Заглушка FeedbackTracker — не содержит dismiss-реакций по умолчанию."""

    def __init__(self, *, consecutive_dismisses: int = 0):
        self._dismisses = consecutive_dismisses

    def get_consecutive_dismisses(self, chat_id: str | int) -> int:  # noqa: ARG002
        return self._dismisses


# ---------------------------------------------------------------------------
# Хелперы для создания fake Pyrogram Message
# ---------------------------------------------------------------------------


def _make_message(
    *,
    text: str | None = None,
    service: bool = False,
    new_chat_members=None,
    photo=None,
    video=None,
    voice=None,
    sticker=None,
    caption: str | None = None,
) -> MagicMock:
    """Создаёт MagicMock под pyrogram.types.Message с нужными атрибутами."""
    msg = MagicMock()
    msg.text = text
    msg.caption = caption
    msg.service = service
    msg.new_chat_members = new_chat_members
    msg.photo = photo
    msg.video = video
    msg.voice = voice
    msg.sticker = sticker
    return msg


# ---------------------------------------------------------------------------
# Импорт тестируемого модуля (RED: ещё не существует)
# ---------------------------------------------------------------------------
from src.core.proactive_dispatcher import ProactiveDecision, ProactiveDispatcher  # noqa: E402

# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


@pytest.fixture
def dispatcher():
    """Dispatcher с дефолтными заглушками (all opts-in, no dismisses)."""
    return ProactiveDispatcher(
        policy_store=_FakePolicyStore(),
        feedback_tracker=_FakeFeedbackTracker(),
    )


@pytest.fixture(autouse=True)
def _unset_env(monkeypatch):
    """По умолчанию KRAB_PROACTIVE_ENABLED не выставлен (=0)."""
    monkeypatch.delenv("KRAB_PROACTIVE_ENABLED", raising=False)


# ===========================================================================
# 1. Глобальный gate — KRAB_PROACTIVE_ENABLED=0 (default off)
# ===========================================================================


def test_global_gate_disabled_by_default(dispatcher):
    """Без KRAB_PROACTIVE_ENABLED=1 все сообщения → DISABLED."""
    msg = _make_message(new_chat_members=[MagicMock()])
    result = dispatcher.dispatch_sync(msg, chat_id="-100", existing_trigger_decision_was_none=True)
    assert result.should_respond is False
    assert result.reason == "global_disabled"


def test_global_gate_enabled(monkeypatch, dispatcher):
    """С KRAB_PROACTIVE_ENABLED=1 — gate открывается."""
    monkeypatch.setenv("KRAB_PROACTIVE_ENABLED", "1")
    msg = _make_message(new_chat_members=[MagicMock()])
    result = dispatcher.dispatch_sync(msg, chat_id="-100", existing_trigger_decision_was_none=True)
    # join event → should_respond может быть True (quota чиста, policy нормальная)
    assert result.event_type == "join"


# ===========================================================================
# 2. Gate existing trigger — не-None → SKIP
# ===========================================================================


def test_existing_trigger_not_none_skips(monkeypatch, dispatcher):
    """Если existing trigger уже был (decision_was_none=False) — proactive не нужен."""
    monkeypatch.setenv("KRAB_PROACTIVE_ENABLED", "1")
    msg = _make_message(text="кто-нибудь шарит?")
    result = dispatcher.dispatch_sync(msg, chat_id="-100", existing_trigger_decision_was_none=False)
    assert result.should_respond is False
    assert result.reason == "existing_trigger"


# ===========================================================================
# 3. Определение типов событий
# ===========================================================================


class TestDetectJoin:
    def test_new_chat_members(self, monkeypatch, dispatcher):
        monkeypatch.setenv("KRAB_PROACTIVE_ENABLED", "1")
        msg = _make_message(new_chat_members=[MagicMock()])
        result = dispatcher.dispatch_sync(
            msg, chat_id="-100", existing_trigger_decision_was_none=True
        )
        assert result.event_type == "join"

    def test_service_message(self, monkeypatch, dispatcher):
        monkeypatch.setenv("KRAB_PROACTIVE_ENABLED", "1")
        msg = _make_message(service=True)
        # service=True но без new_chat_members → может быть join или none
        # Тестируем вместе с new_chat_members
        msg.new_chat_members = [MagicMock()]
        result = dispatcher.dispatch_sync(
            msg, chat_id="-100", existing_trigger_decision_was_none=True
        )
        assert result.event_type == "join"

    def test_no_join_without_members(self, monkeypatch, dispatcher):
        monkeypatch.setenv("KRAB_PROACTIVE_ENABLED", "1")
        msg = _make_message(service=False, new_chat_members=None, text="привет всем")
        result = dispatcher.dispatch_sync(
            msg, chat_id="-100", existing_trigger_decision_was_none=True
        )
        assert result.event_type != "join"


class TestDetectMedia:
    def test_photo_without_caption(self, monkeypatch, dispatcher):
        monkeypatch.setenv("KRAB_PROACTIVE_ENABLED", "1")
        msg = _make_message(photo=MagicMock(), caption=None)
        result = dispatcher.dispatch_sync(
            msg, chat_id="-100", existing_trigger_decision_was_none=True
        )
        assert result.event_type == "media"

    def test_video_without_caption(self, monkeypatch, dispatcher):
        monkeypatch.setenv("KRAB_PROACTIVE_ENABLED", "1")
        msg = _make_message(video=MagicMock(), caption=None)
        result = dispatcher.dispatch_sync(
            msg, chat_id="-100", existing_trigger_decision_was_none=True
        )
        assert result.event_type == "media"

    def test_voice_without_caption(self, monkeypatch, dispatcher):
        monkeypatch.setenv("KRAB_PROACTIVE_ENABLED", "1")
        msg = _make_message(voice=MagicMock(), caption=None)
        result = dispatcher.dispatch_sync(
            msg, chat_id="-100", existing_trigger_decision_was_none=True
        )
        assert result.event_type == "media"

    def test_sticker_without_caption(self, monkeypatch, dispatcher):
        monkeypatch.setenv("KRAB_PROACTIVE_ENABLED", "1")
        msg = _make_message(sticker=MagicMock(), caption=None)
        result = dispatcher.dispatch_sync(
            msg, chat_id="-100", existing_trigger_decision_was_none=True
        )
        assert result.event_type == "media"

    def test_photo_with_caption_not_media_event(self, monkeypatch, dispatcher):
        """Фото С подписью — не media-proactive (есть контекст)."""
        monkeypatch.setenv("KRAB_PROACTIVE_ENABLED", "1")
        msg = _make_message(photo=MagicMock(), caption="смотрите какой закат")
        result = dispatcher.dispatch_sync(
            msg, chat_id="-100", existing_trigger_decision_was_none=True
        )
        assert result.event_type != "media"

    def test_empty_caption_string_treated_as_no_caption(self, monkeypatch, dispatcher):
        """Пустая строка caption трактуется как отсутствие caption."""
        monkeypatch.setenv("KRAB_PROACTIVE_ENABLED", "1")
        msg = _make_message(photo=MagicMock(), caption="")
        result = dispatcher.dispatch_sync(
            msg, chat_id="-100", existing_trigger_decision_was_none=True
        )
        assert result.event_type == "media"


class TestDetectAiAlias:
    @pytest.mark.parametrize(
        "text",
        [
            "кто-нибудь шарит по этой теме?",
            "кто нибудь разбирается в питоне?",
            "кто-нибудь подскажет как сделать?",
            "может бот ответит?",
            "может ии подскажет?",
            "умеет бот помочь с этим?",
            "ии может?",
            "ии может объяснить?",
            "боты знают как это работает?",
            "бота шарит в этом?",
        ],
    )
    def test_ai_alias_matches(self, monkeypatch, dispatcher, text):
        monkeypatch.setenv("KRAB_PROACTIVE_ENABLED", "1")
        msg = _make_message(text=text)
        result = dispatcher.dispatch_sync(
            msg, chat_id="-100", existing_trigger_decision_was_none=True
        )
        assert result.event_type == "ai_alias", f"Должен матчить: {text!r}"

    @pytest.mark.parametrize(
        "text",
        [
            "у меня есть бот для напоминалок",
            "я написал бот для автоматизации",
            "мой бот работает нормально",
            "купил новый телефон",
            "хороший день сегодня",
            "у нас в проекте есть ai интеграция",
            "посмотрите на этот ии-инструмент",
        ],
    )
    def test_ai_alias_false_positives_suppressed(self, monkeypatch, dispatcher, text):
        """Информационные фразы про ботов/ИИ — НЕ триггер."""
        monkeypatch.setenv("KRAB_PROACTIVE_ENABLED", "1")
        msg = _make_message(text=text)
        result = dispatcher.dispatch_sync(
            msg, chat_id="-100", existing_trigger_decision_was_none=True
        )
        assert result.event_type != "ai_alias", f"Ложное срабатывание: {text!r}"


# ===========================================================================
# 4. Gate SILENT policy
# ===========================================================================


def test_silent_policy_skips_all(monkeypatch):
    monkeypatch.setenv("KRAB_PROACTIVE_ENABLED", "1")
    d = ProactiveDispatcher(
        policy_store=_FakePolicyStore(mode="silent"),
        feedback_tracker=_FakeFeedbackTracker(),
    )
    msg = _make_message(new_chat_members=[MagicMock()])
    result = d.dispatch_sync(msg, chat_id="-100", existing_trigger_decision_was_none=True)
    assert result.should_respond is False
    assert result.reason == "silent_mode"


# ===========================================================================
# 5. Per-chat opt-in gates
# ===========================================================================


def test_per_chat_join_opt_out(monkeypatch):
    monkeypatch.setenv("KRAB_PROACTIVE_ENABLED", "1")
    d = ProactiveDispatcher(
        policy_store=_FakePolicyStore(joins=False),
        feedback_tracker=_FakeFeedbackTracker(),
    )
    msg = _make_message(new_chat_members=[MagicMock()])
    result = d.dispatch_sync(msg, chat_id="-100", existing_trigger_decision_was_none=True)
    assert result.should_respond is False
    assert result.reason == "opt_out_join"


def test_per_chat_media_opt_out(monkeypatch):
    monkeypatch.setenv("KRAB_PROACTIVE_ENABLED", "1")
    d = ProactiveDispatcher(
        policy_store=_FakePolicyStore(media=False),
        feedback_tracker=_FakeFeedbackTracker(),
    )
    msg = _make_message(photo=MagicMock(), caption=None)
    result = d.dispatch_sync(msg, chat_id="-100", existing_trigger_decision_was_none=True)
    assert result.should_respond is False
    assert result.reason == "opt_out_media"


def test_per_chat_ai_alias_opt_out(monkeypatch):
    monkeypatch.setenv("KRAB_PROACTIVE_ENABLED", "1")
    d = ProactiveDispatcher(
        policy_store=_FakePolicyStore(ai=False),
        feedback_tracker=_FakeFeedbackTracker(),
    )
    msg = _make_message(text="ии может подсказать?")
    result = d.dispatch_sync(msg, chat_id="-100", existing_trigger_decision_was_none=True)
    assert result.should_respond is False
    assert result.reason == "opt_out_ai_alias"


# ===========================================================================
# 6. Квота (дневные лимиты)
# ===========================================================================


class TestQuota:
    def _make_d(self) -> ProactiveDispatcher:
        return ProactiveDispatcher(
            policy_store=_FakePolicyStore(),
            feedback_tracker=_FakeFeedbackTracker(),
        )

    def test_join_quota_1_per_day(self, monkeypatch):
        monkeypatch.setenv("KRAB_PROACTIVE_ENABLED", "1")
        d = self._make_d()
        chat_id = "-101"
        msg = _make_message(new_chat_members=[MagicMock()])
        # Первый — OK
        r1 = d.dispatch_sync(msg, chat_id=chat_id, existing_trigger_decision_was_none=True)
        assert r1.should_respond is True
        # Записываем с ts в прошлом — чтобы burst cooldown не мешал quota-тесту
        d.record_response(chat_id, "join", ts=time.time() - 400)
        # Второй — quota exceeded (join limit=1)
        r2 = d.dispatch_sync(msg, chat_id=chat_id, existing_trigger_decision_was_none=True)
        assert r2.should_respond is False
        assert r2.reason == "quota_exhausted"

    def test_media_quota_5_per_day(self, monkeypatch):
        monkeypatch.setenv("KRAB_PROACTIVE_ENABLED", "1")
        d = self._make_d()
        chat_id = "-102"
        msg = _make_message(photo=MagicMock(), caption=None)
        # Записываем ответы с ts в прошлом — burst cooldown не мешает
        past_base = time.time() - 4000
        for i in range(5):
            r = d.dispatch_sync(msg, chat_id=chat_id, existing_trigger_decision_was_none=True)
            assert r.should_respond is True, f"Iteration {i}: должен быть True"
            d.record_response(chat_id, "media", ts=past_base - i * 400)
        # 6-й — quota exhausted
        r6 = d.dispatch_sync(msg, chat_id=chat_id, existing_trigger_decision_was_none=True)
        assert r6.should_respond is False
        assert r6.reason == "quota_exhausted"

    def test_ai_alias_quota_3_per_day(self, monkeypatch):
        monkeypatch.setenv("KRAB_PROACTIVE_ENABLED", "1")
        d = self._make_d()
        chat_id = "-103"
        msg = _make_message(text="ии может?")
        past_base = time.time() - 4000
        for i in range(3):
            r = d.dispatch_sync(msg, chat_id=chat_id, existing_trigger_decision_was_none=True)
            assert r.should_respond is True, f"Iteration {i}: должен быть True"
            d.record_response(chat_id, "ai_alias", ts=past_base - i * 400)
        # 4-й — quota exhausted
        r4 = d.dispatch_sync(msg, chat_id=chat_id, existing_trigger_decision_was_none=True)
        assert r4.should_respond is False
        assert r4.reason == "quota_exhausted"

    def test_quotas_are_per_chat(self, monkeypatch):
        """Квота одного чата не влияет на другой."""
        monkeypatch.setenv("KRAB_PROACTIVE_ENABLED", "1")
        d = self._make_d()
        msg = _make_message(new_chat_members=[MagicMock()])
        # Исчерпываем join-квоту чата A
        d.dispatch_sync(msg, chat_id="-200", existing_trigger_decision_was_none=True)
        d.record_response("-200", "join")
        # Чат B — квота чистая
        r = d.dispatch_sync(msg, chat_id="-201", existing_trigger_decision_was_none=True)
        assert r.should_respond is True


# ===========================================================================
# 7. Burst cooldown (5 минут между proactive в одном чате)
# ===========================================================================


class TestBurstCooldown:
    def test_burst_cooldown_blocks_immediate_second(self, monkeypatch):
        monkeypatch.setenv("KRAB_PROACTIVE_ENABLED", "1")
        d = ProactiveDispatcher(
            policy_store=_FakePolicyStore(),
            feedback_tracker=_FakeFeedbackTracker(),
        )
        chat_id = "-300"
        msg_join = _make_message(new_chat_members=[MagicMock()])
        r1 = d.dispatch_sync(msg_join, chat_id=chat_id, existing_trigger_decision_was_none=True)
        assert r1.should_respond is True
        d.record_response(chat_id, "join")

        # Сразу второй (тип media — другой тип, но cooldown глобальный по чату)
        msg_media = _make_message(photo=MagicMock(), caption=None)
        r2 = d.dispatch_sync(msg_media, chat_id=chat_id, existing_trigger_decision_was_none=True)
        assert r2.should_respond is False
        assert r2.reason == "burst_cooldown"

    def test_burst_cooldown_clears_after_5_min(self, monkeypatch):
        monkeypatch.setenv("KRAB_PROACTIVE_ENABLED", "1")
        d = ProactiveDispatcher(
            policy_store=_FakePolicyStore(),
            feedback_tracker=_FakeFeedbackTracker(),
        )
        chat_id = "-301"
        # Записываем ответ с прошедшим временем > 5 минут назад
        past = time.time() - 310  # 5 мин 10 сек назад
        d.record_response(chat_id, "media", ts=past)

        msg = _make_message(photo=MagicMock(), caption=None)
        r = d.dispatch_sync(msg, chat_id=chat_id, existing_trigger_decision_was_none=True)
        assert r.should_respond is True


# ===========================================================================
# 8. Dismiss backoff (3+ подряд за 24h → пропуск)
# ===========================================================================


class TestDismissBackoff:
    def test_3_consecutive_dismisses_block(self, monkeypatch):
        monkeypatch.setenv("KRAB_PROACTIVE_ENABLED", "1")
        d = ProactiveDispatcher(
            policy_store=_FakePolicyStore(),
            feedback_tracker=_FakeFeedbackTracker(consecutive_dismisses=3),
        )
        msg = _make_message(new_chat_members=[MagicMock()])
        r = d.dispatch_sync(msg, chat_id="-400", existing_trigger_decision_was_none=True)
        assert r.should_respond is False
        assert r.reason == "dismiss_backoff"

    def test_2_consecutive_dismisses_ok(self, monkeypatch):
        monkeypatch.setenv("KRAB_PROACTIVE_ENABLED", "1")
        d = ProactiveDispatcher(
            policy_store=_FakePolicyStore(),
            feedback_tracker=_FakeFeedbackTracker(consecutive_dismisses=2),
        )
        msg = _make_message(new_chat_members=[MagicMock()])
        r = d.dispatch_sync(msg, chat_id="-401", existing_trigger_decision_was_none=True)
        assert r.should_respond is True

    def test_record_dismiss_reaction_increments(self, monkeypatch):
        """record_dismiss_reaction инкрементирует внутренний счётчик."""
        monkeypatch.setenv("KRAB_PROACTIVE_ENABLED", "1")
        d = ProactiveDispatcher(
            policy_store=_FakePolicyStore(),
            feedback_tracker=_FakeFeedbackTracker(consecutive_dismisses=0),
        )
        chat_id = "-402"
        d.record_dismiss_reaction(chat_id)
        d.record_dismiss_reaction(chat_id)
        d.record_dismiss_reaction(chat_id)
        msg = _make_message(new_chat_members=[MagicMock()])
        r = d.dispatch_sync(msg, chat_id=chat_id, existing_trigger_decision_was_none=True)
        assert r.should_respond is False
        assert r.reason == "dismiss_backoff"


# ===========================================================================
# 9. record_response инкрементирует счётчик
# ===========================================================================


def test_record_response_increments_counter(monkeypatch):
    monkeypatch.setenv("KRAB_PROACTIVE_ENABLED", "1")
    d = ProactiveDispatcher(
        policy_store=_FakePolicyStore(),
        feedback_tracker=_FakeFeedbackTracker(),
    )
    chat_id = "-500"
    d.record_response(chat_id, "media")
    d.record_response(chat_id, "media")
    stats = d.get_chat_stats(chat_id)
    assert stats["media_today"] == 2


# ===========================================================================
# 10. Daily reset (crossing midnight UTC)
# ===========================================================================


def test_daily_reset_on_midnight_crossing(monkeypatch):
    monkeypatch.setenv("KRAB_PROACTIVE_ENABLED", "1")
    d = ProactiveDispatcher(
        policy_store=_FakePolicyStore(),
        feedback_tracker=_FakeFeedbackTracker(),
    )
    chat_id = "-600"
    # Записываем media 5 раз (макс quota), с ts в прошлом — burst не мешает
    past_base = time.time() - 4000
    for i in range(5):
        d.record_response(chat_id, "media", ts=past_base - i * 400)

    # Проверяем — quota exhausted
    msg = _make_message(photo=MagicMock(), caption=None)
    r_before = d.dispatch_sync(msg, chat_id=chat_id, existing_trigger_decision_was_none=True)
    assert r_before.should_respond is False
    assert r_before.reason == "quota_exhausted"

    # Симулируем смену дня: вручную ставим last_reset в прошлое (вчера)
    yesterday = time.time() - 86401
    d._set_last_reset_for_test(chat_id, yesterday)

    r_after = d.dispatch_sync(msg, chat_id=chat_id, existing_trigger_decision_was_none=True)
    assert r_after.should_respond is True


# ===========================================================================
# 11. Структура ProactiveDecision
# ===========================================================================


def test_decision_dataclass_fields():
    dec = ProactiveDecision(
        should_respond=True,
        event_type="join",
        reason="match_join",
        suggested_prompt_hint="Новый user @foo вступил",
    )
    assert dec.should_respond is True
    assert dec.event_type == "join"
    assert dec.reason == "match_join"
    assert "foo" in dec.suggested_prompt_hint


# ===========================================================================
# 12. Hint text populated for join event
# ===========================================================================


def test_join_event_populates_hint(monkeypatch):
    monkeypatch.setenv("KRAB_PROACTIVE_ENABLED", "1")
    d = ProactiveDispatcher(
        policy_store=_FakePolicyStore(),
        feedback_tracker=_FakeFeedbackTracker(),
    )
    new_member = MagicMock()
    new_member.username = "testuser"
    new_member.first_name = "Test"
    msg = _make_message(new_chat_members=[new_member])
    r = d.dispatch_sync(msg, chat_id="-700", existing_trigger_decision_was_none=True)
    assert r.should_respond is True
    assert r.suggested_prompt_hint  # непустой hint
    assert r.event_type == "join"


# ===========================================================================
# 13. No event → event_type "none"
# ===========================================================================


def test_no_event_type_none(monkeypatch, dispatcher):
    monkeypatch.setenv("KRAB_PROACTIVE_ENABLED", "1")
    msg = _make_message(text="привет как дела")
    r = dispatcher.dispatch_sync(msg, chat_id="-800", existing_trigger_decision_was_none=True)
    assert r.event_type == "none"
    assert r.should_respond is False
