# -*- coding: utf-8 -*-
"""
Тесты routing override для Telegram-related запросов.

Цель: убедиться, что запросы об истории переписки / первом сообщении /
поиске по чатам НЕ уходят на codex-cli (где Telegram MCP заблокирован),
а форсируются на cloud path.

Покрытие:
1. Позитивные — RU паттерны (история переписки, первое сообщение, etc.)
2. Позитивные — EN паттерны (chat history, first message, etc.)
3. Позитивные — @mention + контекстное слово
4. Негативные — обычные query НЕ детектируются
5. Edge cases — пустой query, None
6. Интеграционный stub: is_telegram_query() → force_cloud=True в routing chain
"""

from __future__ import annotations

from src.core.telegram_query_detector import is_telegram_query

# ── Позитивные: русские паттерны ────────────────────────────────────────────


class TestRussianPatterns:
    """Русскоязычные запросы о Telegram-истории должны детектироваться."""

    def test_pervoe_soobschenie(self):
        assert is_telegram_query("когда я написал первое сообщение @dodik_ggt?")

    def test_istoriya_perepiski(self):
        assert is_telegram_query("покажи историю переписки с Антоном")

    def test_istoriyu_perepiski(self):
        assert is_telegram_query("достань историю переписки из Telegram")

    def test_istoriyu_chata(self):
        assert is_telegram_query("пришли историю чата с мамой")

    def test_kogda_napisal(self):
        assert is_telegram_query("когда написал ей первый раз?")

    def test_kogda_ya_napisal(self):
        assert is_telegram_query("вспомни когда я написал в этот чат")

    def test_naydi_v_chate(self):
        assert is_telegram_query("найди в чате упоминание 'встреча'")

    def test_poishchi_v_chate(self):
        assert is_telegram_query("поищи в чате что писал про работу")

    def test_poisk_v_chate(self):
        assert is_telegram_query("поиск в чате за прошлую неделю")

    def test_naydi_v_dialoge(self):
        assert is_telegram_query("найди в диалоге с Петром слово 'проект'")

    def test_byl_li_razgovor(self):
        assert is_telegram_query("был ли разговор с ним о деньгах?")

    def test_chto_pisal(self):
        assert is_telegram_query("что писал ему вчера?")

    def test_chto_ya_pisal(self):
        assert is_telegram_query("что я писал в прошлом месяце?")

    def test_perepiski_mixed_case(self):
        assert is_telegram_query("Найди в Переписке с боссом")

    def test_posledniye_soobscheniya(self):
        assert is_telegram_query("покажи последние сообщения от него")

    def test_perepiski_s(self):
        assert is_telegram_query("переписка с @vasya_pupkin")

    def test_first_message_ru(self):
        assert is_telegram_query("первое сообщение от друга")

    def test_history_soobshcheniy(self):
        assert is_telegram_query("история сообщений за январь")


# ── Позитивные: английские паттерны ─────────────────────────────────────────


class TestEnglishPatterns:
    """Англоязычные запросы о Telegram-истории должны детектироваться."""

    def test_first_message(self):
        assert is_telegram_query("when was the first message from @dodik_ggt?")

    def test_chat_history(self):
        assert is_telegram_query("show me the chat history with Anton")

    def test_message_history(self):
        assert is_telegram_query("get message history from last week")

    def test_when_did_i(self):
        assert is_telegram_query("when did i first message her?")

    def test_when_i_first(self):
        assert is_telegram_query("when i first wrote to the group")

    def test_find_in_messages(self):
        assert is_telegram_query("find in messages the word 'meeting'")

    def test_find_in_chat(self):
        assert is_telegram_query("find in chat something about the project")

    def test_search_messages(self):
        assert is_telegram_query("search messages for keyword 'budget'")

    def test_search_chat(self):
        assert is_telegram_query("search chat for mentions of my name")

    def test_conversation_history(self):
        assert is_telegram_query("pull up the conversation history with mom")

    def test_history_with(self):
        assert is_telegram_query("history with @boss_man from last year")

    def test_read_the_chat(self):
        assert is_telegram_query("read the chat from last Tuesday")

    def test_what_did_i_write(self):
        assert is_telegram_query("what did i write to him about the deal?")


# ── Позитивные: @mention + контекст ─────────────────────────────────────────


class TestAtMentionContext:
    """@username + Telegram-контекстное слово → True."""

    def test_at_mention_with_napisal(self):
        assert is_telegram_query("@dodik_ggt мне написал что-то важное — найди")

    def test_at_mention_with_perepiski(self):
        assert is_telegram_query("переписка с @vasya_pupkin когда началась?")

    def test_at_mention_with_historia(self):
        assert is_telegram_query("@boss история чата с ним за год")

    def test_at_mention_with_chat_en(self):
        assert is_telegram_query("@some_user chat history please")

    def test_at_mention_with_wrote(self):
        assert is_telegram_query("when @friend wrote about the party?")

    def test_at_mention_with_history_en(self):
        assert is_telegram_query("@user123 message history from January")

    def test_at_mention_with_skazal(self):
        assert is_telegram_query("что @kolya сказал про это?")

    def test_at_mention_with_dialog(self):
        assert is_telegram_query("диалог с @vanya — первое сообщение")


# ── Негативные: НЕ Telegram-related ─────────────────────────────────────────


class TestNegativePatterns:
    """Обычные запросы НЕ должны детектироваться как Telegram-related."""

    def test_code_question(self):
        assert not is_telegram_query("что нового в коде этого репозитория?")

    def test_weather(self):
        assert not is_telegram_query("какая погода в Москве завтра?")

    def test_translation(self):
        assert not is_telegram_query("переведи этот текст на английский")

    def test_ai_question(self):
        assert not is_telegram_query("объясни как работает GPT-4?")

    def test_at_mention_no_context(self):
        # @mention без Telegram-контекстных слов → False
        assert not is_telegram_query("@dodik_ggt посоветовал мне хороший ресторан")

    def test_general_search(self):
        assert not is_telegram_query("поищи информацию про Python 3.12")

    def test_coding_task(self):
        assert not is_telegram_query("напиши функцию сортировки списка")

    def test_math(self):
        assert not is_telegram_query("посчитай 15% от 2400 рублей")

    def test_summary(self):
        assert not is_telegram_query("сделай сводку по файлу report.pdf")

    def test_empty_string(self):
        assert not is_telegram_query("")

    def test_whitespace_only(self):
        assert not is_telegram_query("   ")

    def test_short_word(self):
        # Одиночное слово без контекста
        assert not is_telegram_query("привет")

    def test_python_chat_word(self):
        # "чат" в Python-контексте (нет совпадения с полным паттерном "найди в чате")
        assert not is_telegram_query("в Python есть чат-библиотека")

    def test_at_mention_advice(self):
        # @mention + совет/рекомендация → False
        assert not is_telegram_query("@dodik_ggt советует использовать Redis")


# ── Edge cases ───────────────────────────────────────────────────────────────


class TestEdgeCases:
    """Граничные случаи."""

    def test_cyrillic_mixed_caps(self):
        # Детектор должен работать с любым регистром
        assert is_telegram_query("ПЕРВОЕ СООБЩЕНИЕ от него?")

    def test_long_query_with_pattern(self):
        long_q = "а" * 200 + " история переписки с ним " + "б" * 200
        assert is_telegram_query(long_q)

    def test_multiline_query(self):
        assert is_telegram_query("расскажи что происходило\nнайди в чате\nинфо о встрече")

    def test_english_pattern_in_ru_sentence(self):
        # Смешанный текст — паттерн на EN в RU предложении
        assert is_telegram_query("нужна chat history с Антоном за январь")


# ── Интеграционный: force_cloud wire-up ─────────────────────────────────────


class TestRoutingIntegration:
    """Имитируем routing chain: is_telegram_query() → force_cloud=True."""

    def _simulate_routing(self, query: str, config_enabled: bool = True) -> bool:
        """Минимальная имитация логики из userbot_bridge.py."""
        force_cloud = False  # исходное состояние (FORCE_CLOUD=0)
        if config_enabled and is_telegram_query(query):
            force_cloud = True
        return force_cloud

    def test_telegram_query_forces_cloud(self):
        """Telegram-related query → force_cloud=True."""
        result = self._simulate_routing("найди первое сообщение @dodik_ggt")
        assert result is True

    def test_normal_query_no_cloud(self):
        """Обычный query → force_cloud=False (codex-cli может обрабатывать)."""
        result = self._simulate_routing("что нового в коде?")
        assert result is False

    def test_feature_flag_disabled(self):
        """При KRAB_TELEGRAM_QUERY_FORCE_CLOUD=0 override выключен."""
        result = self._simulate_routing(
            "найди первое сообщение @dodik_ggt", config_enabled=False
        )
        assert result is False

    def test_history_query_with_username(self):
        """Запрос с @username + история → force_cloud=True."""
        result = self._simulate_routing("@vasya_pupkin история переписки за 2025 год")
        assert result is True

    def test_code_review_query_not_forced(self):
        """Code review вопрос → NOT forced to cloud."""
        result = self._simulate_routing("сделай code review для этого класса")
        assert result is False

    def test_en_first_message_query_forces_cloud(self):
        """EN: first message query → force_cloud=True."""
        result = self._simulate_routing("when was the first message with @kate?")
        assert result is True
