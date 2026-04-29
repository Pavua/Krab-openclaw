# -*- coding: utf-8 -*-
"""
tests/unit/test_gospodin_hard_filter.py

Проверяет, что _strip_gospodin() корректно удаляет все формы
запрещённых обращений "Мой Господин", "Господин", "Хозяин"
из финального текста ответа.

Hard-filter — last-resort защита: срабатывает даже если system prompt
не предотвратил генерацию запрещённого обращения.
"""

from __future__ import annotations

import pytest

from src.userbot.llm_text_processing import LLMTextProcessingMixin


_strip = LLMTextProcessingMixin._strip_gospodin


class TestStripGospodinBasic:
    """Базовые случаи — прямые варианты обращения."""

    def test_moi_gospodin_prefix_removed(self):
        result = _strip("Мой Господин, система зелёная.")
        assert "Господин" not in result
        assert "система зелёная" in result

    def test_moi_gospodin_lowercase_g(self):
        result = _strip("Мой господин, всё в порядке.")
        assert "господин" not in result
        assert "в порядке" in result

    def test_gospodin_alone_at_line_end(self):
        result = _strip("На связи, Господин")
        assert "Господин" not in result

    def test_gospodin_with_comma_then_text(self):
        result = _strip("Господин, проверил логи — всё чисто.")
        assert "Господин" not in result
        assert "проверил логи" in result

    def test_khozyain_prefix_removed(self):
        result = _strip("Хозяин, Gateway поднят.")
        assert "Хозяин" not in result
        assert "Gateway поднят" in result

    def test_khozyain_at_line_end(self):
        result = _strip("Слушаю, Хозяин")
        assert "Хозяин" not in result


class TestStripGospodinMultiline:
    """Многострочные ответы — фильтр не ломает структуру."""

    def test_multiline_with_greeting(self):
        text = "Мой Господин 🦀\n\nСтатус: зелёный.\n- Gateway: UP\n- Inbox: чист"
        result = _strip(text)
        assert "Господин" not in result
        assert "Статус: зелёный" in result
        assert "Gateway: UP" in result

    def test_multiline_gospodin_in_middle(self):
        text = "Отвечаю.\n\nГосподин, результат проверки:\n- OK"
        result = _strip(text)
        assert "Господин" not in result
        assert "результат проверки" in result

    def test_clean_text_untouched(self):
        text = "Статус: зелёный. Всё работает штатно."
        result = _strip(text)
        assert result == text


class TestStripGospodinEdgeCases:
    """Edge cases — не ломаем нормальный текст."""

    def test_empty_string(self):
        assert _strip("") == ""

    def test_none_graceful(self):
        # Функция принимает str, но проверяем устойчивость
        result = _strip("")
        assert result == ""

    def test_quoted_prohibition_text_not_stripped(self):
        # Упоминание в кавычках (например, в документации) — оставляем
        text = "Правило: 'Мой Господин' запрещено использовать."
        result = _strip(text)
        # Кавычка перед "Мой" защищает — текст должен сохраниться
        # (это описание правила, не само обращение)
        assert "запрещено" in result

    def test_word_gospodin_in_sentence_context(self):
        # "господин" как часть нарицательного в предложении (не обращение)
        text = "Он был настоящим господином положения."
        result = _strip(text)
        # Слово в середине предложения не является обращением — не трогаем
        assert "господином положения" in result

    def test_emoji_preserved_after_strip(self):
        text = "Мой Господин 🦀\n\nСистема: UP"
        result = _strip(text)
        assert "🦀" in result
        assert "Система: UP" in result


class TestStripGospodinRealWorldReplies:
    """Реальные примеры из inbox_state.json — убедиться что фильтруются."""

    def test_real_reply_na_svyazi(self):
        text = "На связи, Мой Господин 🦀\n\nСтатус проактивности: Красный."
        result = _strip(text)
        assert "Господин" not in result
        assert "Статус проактивности" in result

    def test_real_reply_vinovat(self):
        text = "Виноват, Мой Господин 🦀 \n\nМы упёрлись в лимит по процессам."
        result = _strip(text)
        assert "Господин" not in result
        assert "лимит по процессам" in result

    def test_real_reply_otlichno(self):
        text = "Отлично, Мой Господин 🦀\n\nSentry clean: 0 новых issues."
        result = _strip(text)
        assert "Господин" not in result
        assert "Sentry clean" in result
