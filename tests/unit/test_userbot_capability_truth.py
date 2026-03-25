# -*- coding: utf-8 -*-
"""
Тесты truthful capability-ответа в userbot_bridge.

Проверяем:
1) capability-вопрос распознаётся отдельной эвристикой;
2) runtime-summary отражает реальные owner-only возможности;
3) `_process_message` отвечает fast-path'ом без вызова LLM-стрима.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pyrogram import enums

import src.userbot_bridge as userbot_bridge_module
from src.userbot_bridge import KraabUserbot


def _make_bot_stub() -> KraabUserbot:
    """Создаёт минимальный bot stub без запуска Pyrogram client."""
    bot = KraabUserbot.__new__(KraabUserbot)
    bot.me = SimpleNamespace(id=777, username="owner")
    bot.current_role = "default"
    bot.voice_mode = False
    bot._known_commands = set()
    bot._disclosure_sent_for_chat_ids = set()
    bot._is_trigger = Mock(return_value=True)
    bot._get_clean_text = Mock(side_effect=lambda text: text)
    bot._get_chat_context = AsyncMock(return_value="")
    bot._apply_optional_disclosure = Mock(side_effect=lambda **kwargs: kwargs["text"])
    bot._safe_edit = AsyncMock(side_effect=lambda msg, text: msg)
    bot.client = SimpleNamespace(
        send_chat_action=AsyncMock(),
        send_voice=AsyncMock(),
        send_message=AsyncMock(),
        download_media=AsyncMock(),
    )
    return bot


def test_looks_like_capability_status_question_detects_runtime_prompt() -> None:
    """Capability-вопросы должны выделяться в отдельный truthful fast-path."""
    assert KraabUserbot._looks_like_capability_status_question(
        "Что ты уже умеешь, а что еще нет?"
    ) is True
    assert KraabUserbot._looks_like_capability_status_question(
        "Какие у тебя возможности сейчас?"
    ) is True
    assert KraabUserbot._looks_like_capability_status_question(
        "Просто расскажи анекдот"
    ) is False


def test_looks_like_commands_question_detects_help_intent() -> None:
    """Вопросы про команды должны уходить в truthful commands fast-path."""
    assert KraabUserbot._looks_like_commands_question("Какие у тебя команды?") is True
    assert KraabUserbot._looks_like_commands_question("Покажи список команд") is True
    assert KraabUserbot._looks_like_commands_question("Расскажи сказку") is False


def test_looks_like_integrations_question_detects_runtime_tools() -> None:
    """Вопросы про инструменты и интеграции должны распознаваться отдельно."""
    assert KraabUserbot._looks_like_integrations_question("Какие у тебя интеграции?") is True
    assert KraabUserbot._looks_like_integrations_question("Что у тебя подключено?") is True
    assert KraabUserbot._looks_like_integrations_question("Как дела?") is False


def test_looks_like_runtime_truth_question_detects_self_check_intent() -> None:
    """Fast-path отключён по просьбе пользователя — функция всегда возвращает False."""
    # NOTE: _looks_like_runtime_truth_question disabled ("все вопросы уходят в LLM")
    assert KraabUserbot._looks_like_runtime_truth_question("Проверка связи") is False
    assert KraabUserbot._looks_like_runtime_truth_question("Что работает, а что нет?") is False
    assert KraabUserbot._looks_like_runtime_truth_question("Есть ли у тебя доступ к браузеру?") is False
    assert KraabUserbot._looks_like_runtime_truth_question("Расскажи шутку") is False


def test_looks_like_runtime_truth_question_detects_full_diagnostics_intent() -> None:
    """Fast-path отключён — диагностические вопросы тоже уходят в LLM."""
    # NOTE: _looks_like_runtime_truth_question disabled ("все вопросы уходят в LLM")
    assert KraabUserbot._looks_like_runtime_truth_question("Cron у тебя уже работает? Проведи полную диагностику") is False
    assert KraabUserbot._looks_like_runtime_truth_question(
        "Проведи полную диагностику рантайма и скажи текущую модель"
    ) is False


def test_build_runtime_capability_status_owner_includes_real_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """Для доверенного контура summary должен отражать реальные owner-инструменты."""
    bot = _make_bot_stub()

    monkeypatch.setattr(userbot_bridge_module.model_manager, "get_current_model", lambda: "nvidia/nemotron-3-nano")
    monkeypatch.setattr(
        userbot_bridge_module.openclaw_client,
        "get_last_runtime_route",
        lambda: {"channel": "local_direct", "model": "nvidia/nemotron-3-nano"},
    )
    monkeypatch.setattr(userbot_bridge_module.config, "SCHEDULER_ENABLED", True, raising=False)

    text = bot._build_runtime_capability_status(is_allowed_sender=True)

    assert "Что я уже умею сейчас" in text
    assert "`!search`" in text
    assert "`!remember`" in text
    assert "`!ls`, `!read`, `!write`" in text
    assert "`nvidia/nemotron-3-nano`" in text
    assert "Не запоминаю всю переписку навсегда автоматически" in text


def test_build_runtime_capability_status_guest_hides_owner_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """Для гостевого контура не должны светиться owner-only команды."""
    bot = _make_bot_stub()

    monkeypatch.setattr(userbot_bridge_module.model_manager, "get_current_model", lambda: "")
    monkeypatch.setattr(
        userbot_bridge_module.openclaw_client,
        "get_last_runtime_route",
        lambda: {},
    )

    text = bot._build_runtime_capability_status(is_allowed_sender=False)

    assert "`!write`" not in text
    assert "`!web`" not in text
    assert "доступны только доверенному контуру владельца" in text


def test_build_runtime_commands_status_owner_includes_live_command_groups() -> None:
    """Owner-summary должен показывать реальные группы доступных команд."""
    bot = _make_bot_stub()

    text = bot._build_runtime_commands_status(is_allowed_sender=True, access_level="owner")

    assert "Команды, которые реально доступны сейчас" in text
    assert "`!model local`" in text
    assert "`!search <запрос>`" in text
    assert "`!acl ...`" in text
    assert "`!web`" in text


def test_build_runtime_commands_status_guest_hides_owner_commands() -> None:
    """Гостю нельзя раскрывать owner-only командный слой."""
    bot = _make_bot_stub()

    text = bot._build_runtime_commands_status(is_allowed_sender=False)

    assert "`!write`" not in text
    assert "`!panel`" not in text
    assert "служебные команды владельца" in text.lower()


@pytest.mark.asyncio
async def test_build_runtime_integrations_status_owner_uses_runtime_truth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Owner-summary должен отражать реальные интеграции и их configured-state."""
    bot = _make_bot_stub()

    monkeypatch.setattr(userbot_bridge_module.model_manager, "get_current_model", lambda: "nvidia/nemotron-3-nano")
    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "health_check", AsyncMock(return_value=True))
    monkeypatch.setattr(userbot_bridge_module.config, "SCHEDULER_ENABLED", True, raising=False)

    def _fake_launch(name: str):
        if name == "firecrawl":
            return {"missing_env": ["FIRECRAWL_API_KEY"]}
        return {"missing_env": []}

    monkeypatch.setattr(userbot_bridge_module, "resolve_managed_server_launch", _fake_launch)

    text = await bot._build_runtime_integrations_status(is_allowed_sender=True)

    assert "OpenClaw Gateway: ON" in text
    assert "LM Studio local: ON (`nvidia/nemotron-3-nano`)" in text
    assert "Web search (Brave): configured" in text
    assert "Context7 docs: configured" in text
    assert "Firecrawl: missing key / credits" in text


@pytest.mark.asyncio
async def test_build_runtime_integrations_status_guest_hides_owner_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Гостевому контуру не показываем owner-only MCP слой."""
    bot = _make_bot_stub()

    monkeypatch.setattr(userbot_bridge_module.model_manager, "get_current_model", lambda: "")
    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "health_check", AsyncMock(return_value=True))

    text = await bot._build_runtime_integrations_status(is_allowed_sender=False)

    assert "OpenClaw Gateway: ON" in text
    assert "LM Studio local: IDLE" in text
    assert "MCP" not in text
    assert "скрыты в этом чате" in text


@pytest.mark.asyncio
async def test_process_message_capability_question_uses_fast_path_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Capability-вопрос должен обходить LLM-генерацию и сразу отдавать truthful summary.
    Это защищает от устаревших "не умею" и экономит токены.
    """
    bot = _make_bot_stub()
    bot._is_allowed_sender = Mock(return_value=True)

    incoming = SimpleNamespace(
        from_user=SimpleNamespace(id=42, username="tester", is_bot=False),
        text="Что ты уже умеешь, а что еще нет?",
        caption=None,
        photo=None,
        voice=None,
        chat=SimpleNamespace(id=123, type=enums.ChatType.PRIVATE),
        reply_to_message=None,
        reply=AsyncMock(return_value=SimpleNamespace(chat=SimpleNamespace(id=123), text="", caption="")),
    )

    send_stream_mock = Mock()
    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "send_message_stream", send_stream_mock)
    monkeypatch.setattr(userbot_bridge_module.model_manager, "get_current_model", lambda: "nvidia/nemotron-3-nano")
    monkeypatch.setattr(
        userbot_bridge_module.openclaw_client,
        "get_last_runtime_route",
        lambda: {"channel": "local_direct", "model": "nvidia/nemotron-3-nano"},
    )

    await bot._process_message(incoming)

    send_stream_mock.assert_not_called()
    assert bot._safe_edit.await_count >= 1
    delivered_text = bot._safe_edit.await_args_list[-1].args[1]
    assert "Что я уже умею сейчас" in delivered_text
    assert "`!search`" in delivered_text
    assert "доверенному контуру владельца" not in delivered_text


@pytest.mark.asyncio
async def test_process_message_commands_question_uses_fast_path_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Вопрос про команды должен обходить LLM и отдавать deterministic help."""
    bot = _make_bot_stub()
    bot._is_allowed_sender = Mock(return_value=True)

    incoming = SimpleNamespace(
        from_user=SimpleNamespace(id=42, username="tester", is_bot=False),
        text="Какие у тебя команды?",
        caption=None,
        photo=None,
        voice=None,
        chat=SimpleNamespace(id=123, type=enums.ChatType.PRIVATE),
        reply_to_message=None,
        reply=AsyncMock(return_value=SimpleNamespace(chat=SimpleNamespace(id=123), text="", caption="")),
    )

    send_stream_mock = Mock()
    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "send_message_stream", send_stream_mock)

    await bot._process_message(incoming)

    send_stream_mock.assert_not_called()
    delivered_text = bot._safe_edit.await_args_list[-1].args[1]
    assert "Команды, которые реально доступны сейчас" in delivered_text
    assert "`!model local`" in delivered_text
    assert "`!search <запрос>`" in delivered_text


@pytest.mark.asyncio
async def test_process_message_integrations_question_uses_fast_path_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Вопрос про интеграции должен обходить LLM и опираться на runtime truth."""
    bot = _make_bot_stub()
    bot._is_allowed_sender = Mock(return_value=True)

    incoming = SimpleNamespace(
        from_user=SimpleNamespace(id=42, username="tester", is_bot=False),
        text="Что у тебя подключено?",
        caption=None,
        photo=None,
        voice=None,
        chat=SimpleNamespace(id=123, type=enums.ChatType.PRIVATE),
        reply_to_message=None,
        reply=AsyncMock(return_value=SimpleNamespace(chat=SimpleNamespace(id=123), text="", caption="")),
    )

    send_stream_mock = Mock()
    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "send_message_stream", send_stream_mock)
    monkeypatch.setattr(userbot_bridge_module.model_manager, "get_current_model", lambda: "nvidia/nemotron-3-nano")
    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "health_check", AsyncMock(return_value=True))
    monkeypatch.setattr(
        userbot_bridge_module,
        "resolve_managed_server_launch",
        lambda name: {"missing_env": []},
    )

    await bot._process_message(incoming)

    send_stream_mock.assert_not_called()
    delivered_text = bot._safe_edit.await_args_list[-1].args[1]
    assert "Реальные интеграции и инструменты runtime" in delivered_text
    assert "OpenClaw Gateway: ON" in delivered_text
    assert "LM Studio local: ON (`nvidia/nemotron-3-nano`)" in delivered_text


@pytest.mark.skip(reason="runtime truth fast-path disabled per user request — все вопросы уходят в LLM")
@pytest.mark.asyncio
async def test_process_message_runtime_truth_question_uses_fast_path_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Self-check вопрос должен отвечать factual runtime summary без вызова LLM."""
    bot = _make_bot_stub()
    bot._is_allowed_sender = Mock(return_value=True)

    incoming = SimpleNamespace(
        from_user=SimpleNamespace(id=42, username="tester", is_bot=False),
        text="Проверка связи, что работает, а что нет?",
        caption=None,
        photo=None,
        voice=None,
        chat=SimpleNamespace(id=123, type=enums.ChatType.PRIVATE),
        reply_to_message=None,
        reply=AsyncMock(return_value=SimpleNamespace(chat=SimpleNamespace(id=123), text="", caption="")),
    )

    send_stream_mock = Mock()
    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "send_message_stream", send_stream_mock)
    monkeypatch.setattr(userbot_bridge_module.model_manager, "get_current_model", lambda: "nvidia/nemotron-3-nano")
    monkeypatch.setattr(
        userbot_bridge_module.openclaw_client,
        "get_last_runtime_route",
        lambda: {"channel": "local_direct", "model": "nvidia/nemotron-3-nano", "provider": "lmstudio"},
    )
    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "health_check", AsyncMock(return_value=True))
    monkeypatch.setattr(userbot_bridge_module.config, "SCHEDULER_ENABLED", True, raising=False)
    monkeypatch.setattr(userbot_bridge_module.krab_scheduler, "_started", True, raising=False)
    monkeypatch.setattr(
        userbot_bridge_module,
        "resolve_managed_server_launch",
        lambda name: {"missing_env": []},
    )

    await bot._process_message(incoming)

    send_stream_mock.assert_not_called()
    delivered_text = bot._safe_edit.await_args_list[-1].args[1]
    assert "Фактический runtime self-check" in delivered_text
    assert "Gateway / transport: ON" in delivered_text
    assert "Текущий канал: Python Telegram userbot (primary transport)" in delivered_text
    assert "Последняя модель: `nvidia/nemotron-3-nano`" in delivered_text
    assert "Scheduler / reminders: включён и подтверждён runtime-стартом" in delivered_text
    assert "Cron / heartbeat: scheduler активен, transport живой." in delivered_text


@pytest.mark.skip(reason="runtime truth fast-path disabled per user request — все вопросы уходят в LLM")
@pytest.mark.asyncio
async def test_process_message_full_diagnostics_question_uses_runtime_truth_fast_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Полный диагностический вопрос владельца должен обходить LLM.

    Это защищает owner-чат от ложных "контекст потерян" / "не помню" ответов
    на запросы про cron/runtime, которые по смыслу должны отвечаться из live truth.
    """
    bot = _make_bot_stub()
    bot._is_allowed_sender = Mock(return_value=True)

    incoming = SimpleNamespace(
        from_user=SimpleNamespace(id=42, username="tester", is_bot=False),
        text="Cron у тебя уже работает? Проведи полную диагностику",
        caption=None,
        photo=None,
        voice=None,
        chat=SimpleNamespace(id=123, type=enums.ChatType.PRIVATE),
        reply_to_message=None,
        reply=AsyncMock(return_value=SimpleNamespace(chat=SimpleNamespace(id=123), text="", caption="")),
    )

    send_stream_mock = Mock()
    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "send_message_stream", send_stream_mock)
    monkeypatch.setattr(userbot_bridge_module.model_manager, "get_current_model", lambda: "openai-codex/gpt-5.4")
    monkeypatch.setattr(
        userbot_bridge_module.openclaw_client,
        "get_last_runtime_route",
        lambda: {
            "channel": "openclaw_cloud",
            "model": "openai-codex/gpt-5.4",
            "provider": "openai-codex",
        },
    )
    monkeypatch.setattr(userbot_bridge_module.openclaw_client, "health_check", AsyncMock(return_value=True))
    monkeypatch.setattr(userbot_bridge_module.config, "SCHEDULER_ENABLED", True, raising=False)
    monkeypatch.setattr(userbot_bridge_module.krab_scheduler, "_started", True, raising=False)
    monkeypatch.setattr(
        userbot_bridge_module,
        "resolve_managed_server_launch",
        lambda name: {"missing_env": []},
    )

    await bot._process_message(incoming)

    send_stream_mock.assert_not_called()
    delivered_text = bot._safe_edit.await_args_list[-1].args[1]
    assert "Фактический runtime self-check" in delivered_text
    assert "Последний маршрут: `openclaw_cloud`" in delivered_text
    assert "Последняя модель: `openai-codex/gpt-5.4`" in delivered_text
    assert "Cron / heartbeat: scheduler активен, transport живой." in delivered_text
