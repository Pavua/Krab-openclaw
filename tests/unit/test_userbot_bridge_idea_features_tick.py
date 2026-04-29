# -*- coding: utf-8 -*-
"""
Wire-up tests: периодический tick для idea-features.

Проверяем, что _idea_features_tick_loop корректно:
- 1. Вынимает due-jobs из reply_scheduler и шлёт их через client.send_message
- 2. Отправляет daily_brief в self-DM в 08:00 local при KRAB_DAILY_BRIEF_ENABLED=1
- 3. Отправляет channel_digest в KRAB_CHANNEL_DIGEST_CHAT_ID в 09:00
- 4. Fail-open: ошибка одной фичи не валит петлю и не маскирует другие.

Тесты вызывают одну итерацию loop через monkey-patch asyncio.sleep,
который выбрасывает CancelledError после первого тика.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _stub_bot():
    """Stub KraabUserbot достаточный для _idea_features_tick_loop."""
    from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

    bot = KraabUserbot.__new__(KraabUserbot)
    bot.client = MagicMock()
    bot.client.send_message = AsyncMock()
    bot.me = SimpleNamespace(id=12345, username="krab")
    bot._idea_tick_state = {}
    bot._idea_features_task = None
    return bot


async def _run_one_tick(bot):
    """Запускает _idea_features_tick_loop и обрывает после первой итерации."""
    call_count = {"n": 0}

    async def fake_sleep(_seconds):
        call_count["n"] += 1
        if call_count["n"] >= 2:
            # Прерываем второй виток (после первого полного тика)
            raise asyncio.CancelledError()
        return None

    with patch("src.userbot_bridge.asyncio.sleep", side_effect=fake_sleep):
        with pytest.raises(asyncio.CancelledError):
            await bot._idea_features_tick_loop()


def test_reply_scheduler_dispatches_due_jobs():
    """pop_due возвращает 2 job-а → client.send_message вызывается дважды."""
    bot = _stub_bot()

    job1 = SimpleNamespace(
        job_id="abc",
        chat_id=111,
        text="привет",
        metadata={},
    )
    job2 = SimpleNamespace(
        job_id="def",
        chat_id=222,
        text="ответ",
        metadata={"reply_to_message_id": "999"},
    )

    fake_scheduler = MagicMock()
    fake_scheduler.pop_due.return_value = [job1, job2]
    fake_pattern = MagicMock()
    fake_pattern.detect_patterns.return_value = []

    with (
        patch.dict(
            os.environ, {"KRAB_DAILY_BRIEF_ENABLED": "0", "KRAB_CHANNEL_DIGEST_CHAT_ID": ""}
        ),
        patch("src.core.reply_scheduler.reply_scheduler", fake_scheduler),
        patch("src.core.proactive_suggestions.pattern_detector", fake_pattern),
    ):
        asyncio.run(_run_one_tick(bot))

    assert bot.client.send_message.await_count == 2
    # Первый — без reply_to
    args0, kwargs0 = bot.client.send_message.await_args_list[0]
    assert args0 == (111, "привет")
    assert "reply_to_message_id" not in kwargs0
    # Второй — с reply_to (cast в int)
    args1, kwargs1 = bot.client.send_message.await_args_list[1]
    assert args1 == (222, "ответ")
    assert kwargs1.get("reply_to_message_id") == 999


def test_daily_brief_sent_at_scheduled_hour():
    """В 08:00 local + KRAB_DAILY_BRIEF_ENABLED=1 → brief шлётся в self-DM."""
    bot = _stub_bot()

    fake_scheduler = MagicMock()
    fake_scheduler.pop_due.return_value = []
    fake_pattern = MagicMock()
    fake_pattern.detect_patterns.return_value = []

    fake_builder = MagicMock()
    fake_builder.build_brief = AsyncMock(return_value="# 🦀 Daily Brief\n\nfoo")

    # datetime.now().astimezone() даст 08:xx local — патчим datetime в bridge
    fixed_now = datetime(2026, 4, 29, 8, 5, 0, tzinfo=timezone.utc)

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            # astimezone() будет вызван caller'ом; вернём UTC dt с tzinfo
            if tz is None:
                # naive — но caller сразу делает .astimezone()
                return fixed_now.replace(tzinfo=None)
            return fixed_now

    # Чтобы now_local.hour == 8, мокаем так, чтобы astimezone() дал hour=8.
    # Простейший путь — патчить datetime.now ВНУТРИ bridge на функцию,
    # возвращающую aware-объект с hour=8 в local zone.
    fake_local_now = MagicMock()
    fake_local_now.hour = 8

    with (
        patch.dict(
            os.environ,
            {"KRAB_DAILY_BRIEF_ENABLED": "1", "KRAB_CHANNEL_DIGEST_CHAT_ID": ""},
        ),
        patch("src.core.reply_scheduler.reply_scheduler", fake_scheduler),
        patch("src.core.proactive_suggestions.pattern_detector", fake_pattern),
        patch("src.core.daily_brief.DailyBriefBuilder", return_value=fake_builder),
        patch("src.userbot_bridge.datetime") as mock_dt,
    ):
        mock_dt.now.return_value.astimezone.return_value = fake_local_now
        asyncio.run(_run_one_tick(bot))

    # Brief отправлен в self-DM (id 12345)
    fake_builder.build_brief.assert_awaited_once()
    bot.client.send_message.assert_any_await(12345, "# 🦀 Daily Brief\n\nfoo")
    assert "daily_brief" in bot._idea_tick_state


def test_channel_digest_sent_with_configured_chat():
    """KRAB_CHANNEL_DIGEST_CHAT_ID + 09:00 → digest шлётся в этот чат."""
    bot = _stub_bot()

    fake_scheduler = MagicMock()
    fake_scheduler.pop_due.return_value = []
    fake_pattern = MagicMock()
    fake_pattern.detect_patterns.return_value = []

    fake_digest_builder = MagicMock()
    fake_digest_builder.build_digest.return_value = "# Channel Digest\n\nbar"

    fake_local_now = MagicMock()
    fake_local_now.hour = 9

    with (
        patch.dict(
            os.environ,
            {
                "KRAB_DAILY_BRIEF_ENABLED": "0",
                "KRAB_CHANNEL_DIGEST_CHAT_ID": "-1001234567890",
            },
        ),
        patch("src.core.reply_scheduler.reply_scheduler", fake_scheduler),
        patch("src.core.proactive_suggestions.pattern_detector", fake_pattern),
        patch(
            "src.core.channel_digest.channel_digest_builder",
            fake_digest_builder,
        ),
        patch("src.userbot_bridge.datetime") as mock_dt,
    ):
        mock_dt.now.return_value.astimezone.return_value = fake_local_now
        asyncio.run(_run_one_tick(bot))

    fake_digest_builder.build_digest.assert_called_once()
    bot.client.send_message.assert_any_await(-1001234567890, "# Channel Digest\n\nbar")
    assert "channel_digest" in bot._idea_tick_state


def test_fail_open_per_feature():
    """Исключение в reply_scheduler не должно валить остальные фичи."""
    bot = _stub_bot()

    fake_scheduler = MagicMock()
    fake_scheduler.pop_due.side_effect = RuntimeError("scheduler boom")
    fake_pattern = MagicMock()
    fake_pattern.detect_patterns.return_value = ["s1", "s2", "s3"]

    fake_local_now = MagicMock()
    fake_local_now.hour = 14  # не 08 и не 09 → daily/channel skip

    with (
        patch.dict(
            os.environ,
            {"KRAB_DAILY_BRIEF_ENABLED": "0", "KRAB_CHANNEL_DIGEST_CHAT_ID": ""},
        ),
        patch("src.core.reply_scheduler.reply_scheduler", fake_scheduler),
        patch("src.core.proactive_suggestions.pattern_detector", fake_pattern),
        patch("src.userbot_bridge.datetime") as mock_dt,
    ):
        mock_dt.now.return_value.astimezone.return_value = fake_local_now
        # Не должно бросить наружу — внутри логируется и идём дальше
        asyncio.run(_run_one_tick(bot))

    # pattern_detector всё равно отработал, несмотря на падение reply_scheduler
    fake_pattern.detect_patterns.assert_called_once()
    assert "pattern_detector" in bot._idea_tick_state
