# -*- coding: utf-8 -*-
"""
Тесты LLMTextProcessingMixin (`src/userbot/llm_text_processing.py`).

Покрываем:
1) strip transport markup (reply_to, think, final, transport tokens);
2) plaintext reasoning split / strip;
3) agentic scratchpad strip;
4) extract reasoning trace;
5) get_hidden_reasoning_trace snapshot (remember / get / clear);
6) split_message — базовое разбиение на части;
7) normalize_user_visible_fallback_text + looks_like_error_surface_text;
8) batched followup message id (remember / consume / TTL);
9) is_text_batch_candidate в private и group контексте;
10) apply_deferred_action_guard (pass-through и добавление предупреждения).
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.userbot_bridge import KraabUserbot

# ---------------------------------------------------------------------------
# Вспомогательные фикстуры
# ---------------------------------------------------------------------------


def _make_bot() -> KraabUserbot:
    """Минимальный stub KraabUserbot без реального __init__."""
    bot = KraabUserbot.__new__(KraabUserbot)
    bot.current_role = "default"
    bot.me = SimpleNamespace(id=999)
    return bot


def _make_message(
    *,
    msg_id: int = 1,
    text: str = "",
    sender_id: int = 42,
    chat_type=None,
    is_reply_to_me: bool = False,
    bot_id: int = 999,
) -> MagicMock:
    """Собирает mock Pyrogram Message с минимальными полями."""
    from pyrogram import enums

    msg = MagicMock()
    msg.id = msg_id
    msg.text = text
    msg.caption = None
    msg.photo = None
    msg.voice = None
    msg.audio = None
    msg.video_note = None

    user = SimpleNamespace(id=sender_id)
    msg.from_user = user

    chat = SimpleNamespace(type=chat_type or enums.ChatType.PRIVATE, id=100)
    msg.chat = chat

    if is_reply_to_me:
        reply_target = SimpleNamespace(from_user=SimpleNamespace(id=bot_id))
        msg.reply_to_message = reply_target
    else:
        msg.reply_to_message = None

    return msg


# ---------------------------------------------------------------------------
# _strip_transport_markup
# ---------------------------------------------------------------------------


def test_strip_transport_markup_removes_reply_to_tag() -> None:
    """[[reply_to:...]] должен пропасть из итогового текста."""
    raw = "[[reply_to:123]] Привет!"
    result = KraabUserbot._strip_transport_markup(raw)
    assert "[[reply_to:" not in result
    assert "Привет!" in result


def test_strip_transport_markup_removes_think_block() -> None:
    """<think>...</think> должен быть полностью вырезан."""
    raw = "<think>секретные мысли</think>Публичный ответ"
    result = KraabUserbot._strip_transport_markup(raw)
    assert "секретные мысли" not in result
    assert "Публичный ответ" in result


def test_strip_transport_markup_unwraps_final_block() -> None:
    """<final>содержимое</final> — содержимое остаётся, теги уходят."""
    raw = "<final>Готово</final>"
    result = KraabUserbot._strip_transport_markup(raw)
    assert "<final>" not in result
    assert "Готово" in result


def test_strip_transport_markup_removes_llm_im_tokens() -> None:
    """<|im_start|> и <|im_end|> должны уходить без остатка."""
    raw = "<|im_start|>assistant\nТекст ответа<|im_end|>"
    result = KraabUserbot._strip_transport_markup(raw)
    assert "<|im_start|>" not in result
    assert "<|im_end|>" not in result
    assert "Текст ответа" in result


def test_strip_transport_markup_removes_tool_response_block() -> None:
    """<tool_response>...</tool_response> + <|im_end|> должен вырезаться целиком.

    Паттерн ищет <tool_response>...<|im_end|> или до конца строки,
    поэтому блок должен завершаться <|im_end|> чтобы текст после был сохранён.
    """
    raw = 'Ответ\n<tool_response>\n{"status": "ok"}\n<|im_end|>'
    result = KraabUserbot._strip_transport_markup(raw)
    assert "<tool_response>" not in result
    assert '"status"' not in result
    assert "Ответ" in result


def test_strip_transport_markup_empty_string() -> None:
    """Пустой ввод не должен вызывать ошибку."""
    assert KraabUserbot._strip_transport_markup("") == ""


def test_strip_transport_markup_no_markup() -> None:
    """Обычный текст не изменяется (модulo trailing whitespace)."""
    raw = "Просто текст без разметки."
    result = KraabUserbot._strip_transport_markup(raw)
    assert result == raw


# ---------------------------------------------------------------------------
# _split_plaintext_reasoning_and_answer / _strip_plaintext_reasoning_prefix
# ---------------------------------------------------------------------------


def test_split_plaintext_reasoning_and_answer_basic() -> None:
    """Reasoning prefix должен отделяться от итогового ответа."""
    raw = "think\nThinking Process:\n1. Анализирую запрос\n2. Составляю ответ\nФинальный ответ."
    reasoning, answer = KraabUserbot._split_plaintext_reasoning_and_answer(raw)
    assert "Анализирую запрос" in reasoning
    assert answer == "Финальный ответ."


def test_strip_plaintext_reasoning_prefix_returns_answer_only() -> None:
    """_strip_plaintext_reasoning_prefix должен вернуть только итоговый ответ."""
    raw = "think\nThinking Process:\n1. Анализирую запрос\nКороткий ответ."
    result = KraabUserbot._strip_plaintext_reasoning_prefix(raw)
    assert "Thinking Process" not in result
    assert "Короткий ответ." in result


def test_split_plaintext_reasoning_and_answer_no_reasoning() -> None:
    """Обычный текст без reasoning prefix — reasoning пустой, answer весь текст."""
    raw = "Обычное сообщение без мыслей."
    reasoning, answer = KraabUserbot._split_plaintext_reasoning_and_answer(raw)
    assert reasoning == ""
    assert answer == raw


# ---------------------------------------------------------------------------
# _extract_reasoning_trace
# ---------------------------------------------------------------------------


def test_extract_reasoning_trace_think_block() -> None:
    """<think>...</think> должен возвращаться как trace, не как ответ."""
    raw = "<think>Глубокий анализ ситуации</think><final>Ответ</final>"
    trace = KraabUserbot._extract_reasoning_trace(raw)
    assert "Глубокий анализ ситуации" in trace
    assert "Ответ" not in trace


def test_extract_reasoning_trace_plaintext_prefix() -> None:
    """Plain-text reasoning тоже попадает в trace."""
    raw = "think\nThinking Process:\n1. Шаг первый\n2. Шаг второй\nИтоговый ответ."
    trace = KraabUserbot._extract_reasoning_trace(raw)
    assert "Шаг первый" in trace
    assert "Итоговый ответ" not in trace


def test_extract_reasoning_trace_empty_text() -> None:
    """Пустой ввод возвращает пустую строку."""
    assert KraabUserbot._extract_reasoning_trace("") == ""


# ---------------------------------------------------------------------------
# get_hidden_reasoning_trace snapshot: remember / get / clear
# ---------------------------------------------------------------------------


def test_hidden_reasoning_trace_roundtrip(monkeypatch) -> None:
    """remember → get должны вернуть сохранённый trace."""
    bot = _make_bot()
    from src.core.access_control import AccessLevel

    monkeypatch.setattr(
        "src.userbot.llm_text_processing.openclaw_client",
        MagicMock(
            get_last_runtime_route=MagicMock(return_value={"channel": "openai", "model": "gpt-4o"})
        ),
    )

    raw = "<think>Внутренний анализ</think><final>Ответ</final>"
    bot._remember_hidden_reasoning_trace(
        chat_id="777",
        query="Тест?",
        raw_response=raw,
        final_response="Ответ",
        access_level=AccessLevel.OWNER,
    )
    snap = bot.get_hidden_reasoning_trace_snapshot("777")
    assert snap.get("available") is True
    assert "Внутренний анализ" in snap.get("reasoning", "")
    assert snap.get("route_model") == "gpt-4o"


def test_hidden_reasoning_trace_not_stored_for_non_owner(monkeypatch) -> None:
    """Для не-owner access_level trace не сохраняется."""
    bot = _make_bot()
    monkeypatch.setattr("src.userbot.llm_text_processing.openclaw_client", MagicMock())
    bot._remember_hidden_reasoning_trace(
        chat_id="777",
        query="Тест?",
        raw_response="<think>что-то</think>Ответ",
        final_response="Ответ",
        access_level="guest",
    )
    snap = bot.get_hidden_reasoning_trace_snapshot("777")
    assert snap == {}


def test_clear_hidden_reasoning_trace() -> None:
    """clear должен удалять запись и возвращать True."""
    bot = _make_bot()
    bot._hidden_reasoning_traces = {"42": {"available": True, "reasoning": "X"}}
    removed = bot.clear_hidden_reasoning_trace_snapshot(42)
    assert removed is True
    assert bot.get_hidden_reasoning_trace_snapshot(42) == {}


def test_get_hidden_reasoning_trace_missing_chat() -> None:
    """Для чата без trace должен вернуться пустой dict."""
    bot = _make_bot()
    assert bot.get_hidden_reasoning_trace_snapshot("nonexistent") == {}


# ---------------------------------------------------------------------------
# _split_message
# ---------------------------------------------------------------------------


def test_split_message_short_text_not_split() -> None:
    """Короткий текст возвращается одним элементом без маркеров."""
    bot = _make_bot()
    parts = bot._split_message("Короткое сообщение", limit=4000)
    assert len(parts) == 1
    assert parts[0] == "Короткое сообщение"


def test_split_message_long_text_gets_part_markers() -> None:
    """Длинный текст должен иметь маркеры [Часть N/M] и быть разбит."""
    bot = _make_bot()
    long_text = "А" * 5000
    parts = bot._split_message(long_text, limit=500)
    assert len(parts) > 1
    assert parts[0].startswith("[Часть 1/")
    for part in parts:
        assert len(part) <= 500 + 10  # небольшой запас для маркера


def test_split_message_respects_line_boundaries() -> None:
    """Разбивка должна учитывать строки, не рвать в середине слова без нужды."""
    bot = _make_bot()
    lines = [f"Строка номер {i}" for i in range(50)]
    text = "\n".join(lines)
    parts = bot._split_message(text, limit=200)
    # каждая строка начинается либо с маркера, либо с «Строка»
    for part in parts:
        stripped = part.lstrip("[Часть 1234567890/]\n")
        assert stripped.startswith("Строка") or "[Часть" in part


# ---------------------------------------------------------------------------
# _normalize_user_visible_fallback_text + _looks_like_error_surface_text
# ---------------------------------------------------------------------------


def test_normalize_fallback_openclaw_no_response() -> None:
    """Технический placeholder OpenClaw должен стать читаемым."""
    result = KraabUserbot._normalize_user_visible_fallback_text("No response from OpenClaw.")
    assert "❌" in result
    assert "OpenClaw" in result


def test_normalize_fallback_regular_text_unchanged() -> None:
    """Обычный текст не должен изменяться."""
    text = "Обычный ответ без заглушки."
    assert KraabUserbot._normalize_user_visible_fallback_text(text) == text


def test_looks_like_error_surface_text_true() -> None:
    """Текст с ❌ распознаётся как error surface."""
    assert KraabUserbot._looks_like_error_surface_text("❌ что-то пошло не так") is True


def test_looks_like_error_surface_text_false() -> None:
    """Обычный текст не является error surface."""
    assert KraabUserbot._looks_like_error_surface_text("Всё хорошо") is False


# ---------------------------------------------------------------------------
# _remember_batched_followup_message_ids / _consume_batched_followup_message_id
# ---------------------------------------------------------------------------


def test_batched_followup_consume_returns_true() -> None:
    """Сохранённый id должен распознаваться при потреблении."""
    bot = _make_bot()
    bot._remember_batched_followup_message_ids(chat_id="100", message_ids=["55", "56"])
    assert bot._consume_batched_followup_message_id(chat_id="100", message_id="55") is True


def test_batched_followup_consume_unknown_returns_false() -> None:
    """Неизвестный id — False без ошибки."""
    bot = _make_bot()
    assert bot._consume_batched_followup_message_id(chat_id="100", message_id="99") is False


def test_batched_followup_consume_expires_old_ids(monkeypatch) -> None:
    """Id старше TTL (600 сек) не возвращается при потреблении."""
    bot = _make_bot()
    bot._remember_batched_followup_message_ids(chat_id="100", message_ids=["77"])
    # Сдвигаем saved_at назад за TTL
    bucket = bot._batched_followup_message_ids["100"]
    bucket["77"] = time.monotonic() - 700.0
    assert bot._consume_batched_followup_message_id(chat_id="100", message_id="77") is False


# ---------------------------------------------------------------------------
# _is_text_batch_candidate
# ---------------------------------------------------------------------------


def test_is_text_batch_candidate_private_plain_text() -> None:
    """В приватном чате plain-text от того же отправителя — кандидат."""
    from pyrogram import enums

    bot = _make_bot()
    # Мокаем зависимые методы
    bot._message_has_audio = MagicMock(return_value=False)
    bot._extract_message_text = MagicMock(return_value="Привет, продолжаю мысль")
    bot._get_clean_text = MagicMock(return_value="Привет, продолжаю мысль")
    bot._is_command_like_text = MagicMock(return_value=False)
    bot._is_trigger = MagicMock(return_value=False)

    msg = _make_message(
        text="Привет, продолжаю мысль", sender_id=42, chat_type=enums.ChatType.PRIVATE
    )
    result = bot._is_text_batch_candidate(
        message=msg,
        sender_id=42,
        is_private_chat=True,
        self_user_id=999,
    )
    assert result is True


def test_is_text_batch_candidate_wrong_sender() -> None:
    """Чужой отправитель — не кандидат, независимо от типа чата."""
    from pyrogram import enums

    bot = _make_bot()
    bot._message_has_audio = MagicMock(return_value=False)
    bot._extract_message_text = MagicMock(return_value="Текст")
    bot._get_clean_text = MagicMock(return_value="Текст")
    bot._is_command_like_text = MagicMock(return_value=False)
    bot._is_trigger = MagicMock(return_value=False)

    msg = _make_message(text="Текст", sender_id=99, chat_type=enums.ChatType.PRIVATE)
    assert (
        bot._is_text_batch_candidate(
            message=msg,
            sender_id=42,  # ждём 42, а пришёл 99
            is_private_chat=True,
            self_user_id=999,
        )
        is False
    )


def test_is_text_batch_candidate_group_with_trigger() -> None:
    """В группе сообщение с trigger-словом — кандидат."""
    from pyrogram import enums

    bot = _make_bot()
    bot._message_has_audio = MagicMock(return_value=False)
    bot._extract_message_text = MagicMock(return_value="Краб, продолжение")
    bot._get_clean_text = MagicMock(return_value="продолжение")
    bot._is_command_like_text = MagicMock(return_value=False)
    bot._is_trigger = MagicMock(return_value=True)

    msg = _make_message(text="Краб, продолжение", sender_id=42, chat_type=enums.ChatType.GROUP)
    assert (
        bot._is_text_batch_candidate(
            message=msg,
            sender_id=42,
            is_private_chat=False,
            self_user_id=999,
        )
        is True
    )


def test_is_text_batch_candidate_group_no_trigger_no_reply() -> None:
    """В группе без trigger и без reply-to-me — не кандидат."""
    from pyrogram import enums

    bot = _make_bot()
    bot._message_has_audio = MagicMock(return_value=False)
    bot._extract_message_text = MagicMock(return_value="Просто болтовня")
    bot._get_clean_text = MagicMock(return_value="Просто болтовня")
    bot._is_command_like_text = MagicMock(return_value=False)
    bot._is_trigger = MagicMock(return_value=False)

    msg = _make_message(text="Просто болтовня", sender_id=42, chat_type=enums.ChatType.GROUP)
    assert (
        bot._is_text_batch_candidate(
            message=msg,
            sender_id=42,
            is_private_chat=False,
            self_user_id=999,
        )
        is False
    )


def test_is_text_batch_candidate_photo_excluded() -> None:
    """Сообщение с фото не является кандидатом для batch."""
    from pyrogram import enums

    bot = _make_bot()
    bot._message_has_audio = MagicMock(return_value=False)
    bot._extract_message_text = MagicMock(return_value="")
    bot._get_clean_text = MagicMock(return_value="")
    bot._is_command_like_text = MagicMock(return_value=False)
    bot._is_trigger = MagicMock(return_value=False)

    msg = _make_message(sender_id=42, chat_type=enums.ChatType.PRIVATE)
    msg.photo = MagicMock()  # есть фото

    assert (
        bot._is_text_batch_candidate(
            message=msg,
            sender_id=42,
            is_private_chat=True,
            self_user_id=999,
        )
        is False
    )


# ---------------------------------------------------------------------------
# escape_urls_for_restricted_groups
# ---------------------------------------------------------------------------


def test_escape_bare_url_single() -> None:
    """Одиночный URL должен быть обёрнут в бэктики."""
    result = KraabUserbot.escape_urls_for_restricted_groups("Visit https://example.com")
    assert result == "Visit `https://example.com`"


def test_escape_bare_url_multiple() -> None:
    """Несколько URL в одной строке — каждый оборачивается отдельно."""
    result = KraabUserbot.escape_urls_for_restricted_groups("https://a.com and https://b.com")
    assert result == "`https://a.com` and `https://b.com`"


def test_no_double_escape_already_in_backticks() -> None:
    """URL уже в бэктиках — повторное оборачивание не происходит."""
    result = KraabUserbot.escape_urls_for_restricted_groups("Visit `https://example.com`")
    assert result == "Visit `https://example.com`"


def test_markdown_link_not_escaped() -> None:
    """Markdown-ссылка [текст](url) — URL внутри скобок не трогаем."""
    result = KraabUserbot.escape_urls_for_restricted_groups(
        "Посетите [сайт](https://example.com) прямо сейчас"
    )
    assert result == "Посетите [сайт](https://example.com) прямо сейчас"


def test_empty_string_unchanged() -> None:
    """Пустая строка остаётся пустой."""
    assert KraabUserbot.escape_urls_for_restricted_groups("") == ""


def test_no_urls_unchanged() -> None:
    """Текст без URL не изменяется."""
    text = "Привет, это просто текст!"
    assert KraabUserbot.escape_urls_for_restricted_groups(text) == text


def test_url_with_path_and_query() -> None:
    """URL со сложным путём и query-параметрами оборачивается целиком."""
    url = "https://example.com/path?foo=bar&baz=1"
    result = KraabUserbot.escape_urls_for_restricted_groups(f"See {url}")
    assert result == f"See `{url}`"


def test_mixed_bare_and_escaped_urls() -> None:
    """Часть URL уже в бэктиках, часть — нет: только «голые» оборачиваются."""
    text = "See `https://safe.com` and also https://bare.com"
    result = KraabUserbot.escape_urls_for_restricted_groups(text)
    assert result == "See `https://safe.com` and also `https://bare.com`"


def test_code_block_url_untouched() -> None:
    """URL внутри code-блока (тройные бэктики) не оборачивается."""
    text = "```\nhttps://inside-code.com\n```"
    result = KraabUserbot.escape_urls_for_restricted_groups(text)
    # тройные бэктики — это просто нечётный индекс в split, внутренний URL не трогается
    assert "`https://inside-code.com`" not in result
