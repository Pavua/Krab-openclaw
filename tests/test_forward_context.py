# -*- coding: utf-8 -*-
"""Тесты формирования reply/forward контекста для автоответчика."""

from __future__ import annotations

from types import SimpleNamespace

from src.handlers.ai import (
    _build_author_context,
    _build_forward_context,
    _build_reply_context,
    _drop_service_busy_context_items,
    _drop_service_busy_phrases,
    _filter_context_for_group_author,
    _is_service_busy_artifact_text,
    _looks_incomplete_response,
    _should_emit_stream_edit,
)


class _Msg(SimpleNamespace):
    """Минимальная заглушка Telegram Message для unit-тестов."""


def test_build_forward_context_marks_as_foreign_content() -> None:
    """Форвард должен явно помечаться как чужой контент для анализа."""
    msg = _Msg(
        forward_from=None,
        forward_sender_name="Другой пользователь",
        forward_from_chat=None,
        forward_date="2026-02-16 14:00:00",
        is_automatic_forward=False,
    )

    context = _build_forward_context(msg, enabled=True)

    assert "пересланный материал" in context
    assert "Другой пользователь" in context
    assert "Автофорвард: False" in context


def test_build_reply_context_includes_original_message() -> None:
    """Reply-контекст должен включать текст исходного сообщения."""
    reply_to = _Msg(
        text="Исходный текст для ответа",
        caption=None,
        voice=None,
        audio=None,
        video=None,
        animation=None,
        photo=None,
        sticker=None,
        document=None,
        from_user=SimpleNamespace(username="origin_user", first_name="Origin"),
    )
    msg = _Msg(reply_to_message=reply_to)

    context = _build_reply_context(msg)

    assert "REPLY CONTEXT" in context
    assert "@origin_user" in context
    assert "Исходный текст" in context


def test_build_author_context_marks_participant_in_group() -> None:
    """В группах контекст автора должен явно отмечать participant, а не owner."""
    msg = _Msg(
        chat=SimpleNamespace(type=SimpleNamespace(name="GROUP")),
        from_user=SimpleNamespace(id=777, username="guest_user", first_name="Guest"),
    )
    context = _build_author_context(msg, is_owner_sender=False)

    assert "author=@guest_user" in context
    assert "author_role=participant" in context
    assert "chat_type=group" in context
    assert "цитатой/материалом для анализа" in context


def test_drop_service_busy_phrases_removes_queue_artifacts() -> None:
    """Служебные строки очереди должны отфильтровываться из пользовательского ответа."""
    payload = (
        "Обрабатываю предыдущий запрос. Отправь следующее сообщение через пару секунд.\n"
        "Вот итоговый анализ:\n"
        "Все в порядке."
    )
    cleaned, removed = _drop_service_busy_phrases(payload)
    assert removed is True
    assert "обрабатываю предыдущий запрос" not in cleaned.lower()
    assert "отправь следующее сообщение" not in cleaned.lower()
    assert "итоговый анализ" in cleaned.lower()


def test_service_busy_artifact_detector_matches_known_markers() -> None:
    assert _is_service_busy_artifact_text("⏳ Обрабатываю предыдущий запрос.") is True
    assert _is_service_busy_artifact_text("Обычный пользовательский текст без техфраз.") is False


def test_drop_service_busy_context_items_filters_queue_lines() -> None:
    context = [
        {"role": "assistant", "text": "⏳ Обрабатываю предыдущий запрос. Отправь следующее сообщение через пару секунд."},
        {"role": "assistant", "text": "Нормальный ответ по задаче."},
        {"role": "user", "text": "Подожди пару секунд и повтори."},
    ]
    cleaned, dropped = _drop_service_busy_context_items(context)
    assert dropped == 2
    assert len(cleaned) == 1
    assert cleaned[0]["text"] == "Нормальный ответ по задаче."


def test_filter_context_for_group_author_keeps_only_current_user_prompts() -> None:
    """В группе для participant должны сохраняться user-реплики только текущего автора."""
    context = [
        {"role": "system", "text": "system note"},
        {"role": "assistant", "text": "assistant answer"},
        {"role": "user", "text": "[AUTHOR CONTEXT]:\nauthor_id=111\n...\nвопрос 1"},
        {"role": "user", "text": "[AUTHOR CONTEXT]:\nauthor_id=222\n...\nчужой вопрос"},
        {"role": "user", "text": "старый формат без author_id"},
    ]
    filtered, trimmed = _filter_context_for_group_author(
        context=context,
        current_author_id=111,
        is_private=False,
        is_owner_sender=False,
        enabled=True,
    )
    assert trimmed is True
    joined = "\n".join(str(item.get("text", "")) for item in filtered)
    assert "author_id=111" in joined
    assert "author_id=222" not in joined
    assert "старый формат без author_id" not in joined


def test_filter_context_for_group_author_prefers_explicit_author_id_field() -> None:
    """Если в контексте есть author_id в payload, фильтрация должна идти по нему."""
    context = [
        {"role": "user", "text": "вопрос текущего", "author_id": 111},
        {"role": "user", "text": "чужой вопрос", "author_id": 222},
        {"role": "assistant", "text": "ответ ассистента"},
    ]
    filtered, trimmed = _filter_context_for_group_author(
        context=context,
        current_author_id=111,
        is_private=False,
        is_owner_sender=False,
        enabled=True,
    )
    assert trimmed is True
    assert len(filtered) == 2
    assert filtered[0]["text"] == "вопрос текущего"
    assert filtered[1]["role"] == "assistant"


def test_filter_context_for_group_author_skips_filter_for_owner_or_private() -> None:
    """Для owner или приватного чата фильтрация не должна применяться."""
    context = [
        {"role": "user", "text": "[AUTHOR CONTEXT]:\nauthor_id=111\n..."},
        {"role": "user", "text": "[AUTHOR CONTEXT]:\nauthor_id=222\n..."},
    ]
    same_private, trimmed_private = _filter_context_for_group_author(
        context=context,
        current_author_id=111,
        is_private=True,
        is_owner_sender=False,
        enabled=True,
    )
    assert trimmed_private is False
    assert same_private == context

    same_owner, trimmed_owner = _filter_context_for_group_author(
        context=context,
        current_author_id=111,
        is_private=False,
        is_owner_sender=True,
        enabled=True,
    )
    assert trimmed_owner is False
    assert same_owner == context


def test_should_emit_stream_edit_respects_delta_threshold() -> None:
    """Редактирование стрима не должно триггериться на микроскопические изменения."""
    assert _should_emit_stream_edit("", "abc", min_delta_chars=60) is True
    assert _should_emit_stream_edit("abcd", "abcd", min_delta_chars=60) is False
    assert _should_emit_stream_edit("a" * 100, "b" * 120, min_delta_chars=60) is False
    assert _should_emit_stream_edit("a" * 100, "b" * 170, min_delta_chars=60) is True


def test_looks_incomplete_response_detects_plan_intro_without_steps() -> None:
    payload = (
        "Отличный вопрос! Спасение на острове — это классическая задача выживания. "
        "Вот пошаговый план, как спастись на обычном острове:"
    )
    assert _looks_incomplete_response(payload) is True


def test_looks_incomplete_response_ignores_finished_answer() -> None:
    payload = (
        "Отличный вопрос. Вот короткий план:\n"
        "1. Проверь воду.\n"
        "2. Найди укрытие.\n"
        "3. Подай сигнал SOS.\n"
        "Удачи!"
    )
    assert _looks_incomplete_response(payload) is False
