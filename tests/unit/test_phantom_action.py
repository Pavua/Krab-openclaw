# -*- coding: utf-8 -*-
"""
tests/unit/test_phantom_action.py

Тесты для fix phantom 'передал владельцу':
  A) is_phantom_forward_promise — детекция phantom фраз
  B) forward_request_to_owner — реальная отправка DM
  C) LLMTextProcessingMixin._apply_phantom_action_guard — post-processor
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.forward_to_owner import forward_request_to_owner, is_phantom_forward_promise
from src.userbot.llm_text_processing import LLMTextProcessingMixin

# ---------------------------------------------------------------------------
# A) Детекция phantom-фраз
# ---------------------------------------------------------------------------


class TestIsPhantomForwardPromise:
    def test_передал_владельцу(self):
        assert is_phantom_forward_promise("Я передал владельцу ваш запрос.") is True

    def test_передал_хозяину(self):
        assert is_phantom_forward_promise("Хорошо, я передал хозяину ваше сообщение.") is True

    def test_уведомил_владельца(self):
        assert is_phantom_forward_promise("Уведомил владельца об этом.") is True

    def test_forwarded_to_owner_en(self):
        assert is_phantom_forward_promise("I have forwarded to the owner your request.") is True

    def test_notified_owner_en(self):
        assert is_phantom_forward_promise("I notified the owner about this issue.") is True

    def test_no_phantom_regular_reply(self):
        assert is_phantom_forward_promise("Хорошо, могу помочь с этим вопросом.") is False

    def test_no_phantom_empty(self):
        assert is_phantom_forward_promise("") is False

    def test_no_phantom_none(self):
        assert is_phantom_forward_promise(None) is False  # type: ignore[arg-type]

    def test_no_phantom_partial_word(self):
        # 'передать' без 'владельцу' — не phantom
        assert is_phantom_forward_promise("Могу передать сообщение.") is False

    def test_передал_владельцу_case_insensitive(self):
        assert is_phantom_forward_promise("ПЕРЕДАЛ ВЛАДЕЛЬЦУ информацию.") is True

    def test_владельцу_уже_передал(self):
        assert is_phantom_forward_promise("Владельцу уже передал все детали.") is True

    # -- H1: precision reduction (composite scoring) ------------------------

    def test_phantom_detected_on_multiple_markers(self):
        """Отправил + fabricated messageId без tool_calls → phantom (weak≥2)."""
        text = "Отправил сообщение в telegram chat. messageId: 1455"
        assert is_phantom_forward_promise(text) is True

    def test_legit_confirmation_after_tool_call(self):
        """Тот же текст, но tool_calls=[telegram_send_message] → НЕ phantom."""
        text = "Отправил сообщение в telegram chat. messageId: 1455"
        assert is_phantom_forward_promise(text, tool_calls_made=["telegram_send_message"]) is False

    def test_legit_tech_discussion_single_marker(self):
        """Одиночный messageId в техническом обсуждении — не phantom."""
        assert is_phantom_forward_promise("Используй messageId 123 для reply в Bot API.") is False

    def test_legit_single_otpravil_no_phantom(self):
        """«Отправил сообщение в chat» без других маркеров — не phantom."""
        assert is_phantom_forward_promise("Отправил сообщение в chat позже.") is False

    def test_phantom_still_caught_triple(self):
        """«Доставка подтверждена» + chat NNN + messageId — composite ≥ 2 → phantom."""
        text = "Доставка подтверждена. chat 312, messageId 1455."
        assert is_phantom_forward_promise(text) is True

    def test_forward_tool_call_suppresses_strong(self):
        """Даже strong-фраза при реальном forward_request_to_owner → не phantom."""
        assert (
            is_phantom_forward_promise(
                "Я передал владельцу ваш запрос.",
                tool_calls_made=["forward_request_to_owner"],
            )
            is False
        )


# ---------------------------------------------------------------------------
# B) forward_request_to_owner — реальная отправка DM
# ---------------------------------------------------------------------------


class TestForwardRequestToOwner:
    @pytest.mark.asyncio
    async def test_sends_dm_to_self(self):
        """forward_request_to_owner должен вызвать send_message с me.id."""
        mock_client = MagicMock()
        mock_me = MagicMock()
        mock_me.id = 12345
        mock_client.get_me = AsyncMock(return_value=mock_me)
        mock_client.send_message = AsyncMock(return_value=MagicMock())

        mock_user = MagicMock()
        mock_user.first_name = "Иван"
        mock_user.last_name = "Петров"
        mock_user.username = "ivanp"
        mock_user.id = 9999

        result = await forward_request_to_owner(
            mock_client,
            from_user=mock_user,
            chat_id=-1001234567,
            chat_title="Тестовый чат",
            text="Привет, передайте владельцу что я хочу договориться.",
            category="request",
        )

        assert result is True
        mock_client.send_message.assert_called_once()
        call_args = mock_client.send_message.call_args
        assert call_args[0][0] == 12345  # self-DM
        msg_text = call_args[0][1]
        assert "Иван Петров" in msg_text or "ivanp" in msg_text
        assert "Тестовый чат" in msg_text

    @pytest.mark.asyncio
    async def test_returns_false_on_exception(self):
        """При ошибке отправки возвращает False (не бросает исключение)."""
        mock_client = MagicMock()
        mock_client.get_me = AsyncMock(side_effect=RuntimeError("network error"))

        result = await forward_request_to_owner(
            mock_client,
            from_user=None,
            chat_id=0,
            text="Тест",
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_handles_no_username(self):
        """Работает если у пользователя нет username."""
        mock_client = MagicMock()
        mock_me = MagicMock()
        mock_me.id = 1
        mock_client.get_me = AsyncMock(return_value=mock_me)
        mock_client.send_message = AsyncMock(return_value=MagicMock())

        mock_user = MagicMock()
        mock_user.first_name = "Аноним"
        mock_user.last_name = ""
        mock_user.username = ""
        mock_user.id = 777

        result = await forward_request_to_owner(
            mock_client,
            from_user=mock_user,
            text="Привет",
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_category_label_in_message(self):
        """Category 'question' формирует правильный label."""
        mock_client = MagicMock()
        mock_me = MagicMock()
        mock_me.id = 1
        mock_client.get_me = AsyncMock(return_value=mock_me)
        captured = []

        async def capture_send(chat_id, text):
            captured.append(text)

        mock_client.send_message = capture_send

        await forward_request_to_owner(
            mock_client,
            from_user=None,
            text="Вопрос",
            category="question",
        )
        assert captured
        assert "❓ Вопрос" in captured[0]


# ---------------------------------------------------------------------------
# C) LLMTextProcessingMixin._apply_phantom_action_guard
# ---------------------------------------------------------------------------


class ConcreteProcessor(LLMTextProcessingMixin):
    """Конкретная реализация для тестирования методов mixin."""

    # class-level паттерны (минимальные заглушки)
    _reply_to_tag_pattern = __import__("re").compile(r"\[\[reply_to:[^\]]*\]\]")
    _think_block_pattern = __import__("re").compile(r"<think>.*?</think>", __import__("re").DOTALL)
    _final_block_pattern = __import__("re").compile(
        r"<final>(.*?)</final>", __import__("re").DOTALL
    )
    _think_final_tag_pattern = __import__("re").compile(r"</?(?:think|final)>")
    _tool_response_block_pattern = __import__("re").compile(
        r"<tool_response>.*?</tool_response>", __import__("re").DOTALL
    )
    _llm_transport_tokens_pattern = __import__("re").compile(
        r"<\|im_start\|>.*?<\|im_end\|>", __import__("re").DOTALL
    )
    _plaintext_reasoning_intro_pattern = __import__("re").compile(
        r"^(think|thinking|thought):", __import__("re").IGNORECASE
    )
    _plaintext_reasoning_step_pattern = __import__("re").compile(r"^\d+\.")
    _plaintext_reasoning_meta_pattern = __import__("re").compile(
        r"^(step|note):", __import__("re").IGNORECASE
    )
    _agentic_scratchpad_line_pattern = __import__("re").compile(
        r"^(wait|ready|let's go)\b", __import__("re").IGNORECASE
    )
    _agentic_scratchpad_command_pattern = __import__("re").compile(r"^\$\s+\w+")
    _split_chunk_header_pattern = __import__("re").compile(r"^\[Часть \d+/\d+\]")
    _deferred_intent_pattern = __import__("re").compile(
        r"\b(через|завтра|позже)\b", __import__("re").IGNORECASE
    )
    _think_capture_pattern = __import__("re").compile(
        r"<think>(.*?)</think>", __import__("re").DOTALL
    )


class TestApplyPhantomActionGuard:
    def test_phantom_phrase_rewritten_when_no_tool(self):
        """Phantom фраза без tool_was_called=True переписывается честно."""
        processor = ConcreteProcessor()
        result = processor._apply_phantom_action_guard(
            "Хорошо, я передал владельцу ваш запрос.",
            tool_was_called=False,
        )
        assert "передал владельцу" not in result.lower()
        assert "@p0lrd" in result or "автоматического" in result

    def test_no_rewrite_when_tool_was_called(self):
        """Если tool_was_called=True — не трогаем текст."""
        processor = ConcreteProcessor()
        original = "Хорошо, я передал владельцу ваш запрос."
        result = processor._apply_phantom_action_guard(original, tool_was_called=True)
        assert result == original

    def test_no_rewrite_regular_text(self):
        """Обычный текст без phantom-фраз не меняется."""
        processor = ConcreteProcessor()
        original = "Я могу помочь с этим вопросом."
        result = processor._apply_phantom_action_guard(original, tool_was_called=False)
        assert result == original

    def test_empty_text_passthrough(self):
        """Пустая строка возвращается как есть."""
        processor = ConcreteProcessor()
        assert processor._apply_phantom_action_guard("", tool_was_called=False) == ""

    def test_phantom_english_rewritten(self):
        """English phantom 'forwarded to owner' тоже перехватывается."""
        processor = ConcreteProcessor()
        result = processor._apply_phantom_action_guard(
            "I have forwarded to the owner your request.",
            tool_was_called=False,
        )
        assert "forwarded to the owner" not in result.lower()
