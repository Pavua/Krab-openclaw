# -*- coding: utf-8 -*-
"""Wave 46-D-nlu-tighten — swarm intent requires verb context.

Регрессионные тесты для _try_swarm: substring "команд" больше не должен
триггерить swarm dispatch без явного verb-pattern (запусти/позови/swarm и т.д.).

Production evidence: NLU выдавал `cmd=!swarm confidence=0.9` для текстов
типа "по командам: analysts: 53, coders: 33..." (inbox listing).
"""

from __future__ import annotations

import pytest

from src.core.command_intent_extractor import extract_command_intent


@pytest.mark.asyncio
async def test_swarm_dispatched_with_verb_phrase():
    """Явный verb + topic → high confidence dispatch."""
    intent = await extract_command_intent(
        "запусти команду traders на тему BTC анализ",
        is_owner=True,
    )
    assert intent is not None
    assert intent.command == "!swarm"
    assert intent.args.get("team") == "traders"
    assert intent.confidence >= 0.85


@pytest.mark.asyncio
async def test_swarm_dispatched_with_swarm_keyword():
    """Явное слово 'swarm' + team → dispatch."""
    intent = await extract_command_intent(
        "swarm coders сделайте рефакторинг auth модуля",
        is_owner=True,
    )
    assert intent is not None
    assert intent.command == "!swarm"
    assert intent.args.get("team") == "coders"


@pytest.mark.asyncio
async def test_no_dispatch_on_inbox_listing_substring():
    """Регрессия: inbox listing с упоминанием командам/team-имён не должен dispatch.

    Production case: NLU выдавал `cmd=!swarm traders loop 1 командам` для
    нейтральных listing-текстов. Substring "командам" + team-имя (analysts/etc.)
    были достаточны при старой логике.
    """
    text = (
        "Распределение по командам в инбоксе: analysts 53 пункта, "
        "coders 33, traders 21, creative 12. Бэклог растёт."
    )
    intent = await extract_command_intent(text, is_owner=True)
    # Не должно быть swarm dispatch — нет глагола запусти/позови/собери,
    # нет слова "swarm"/"team", только substring "командам".
    assert intent is None or intent.command != "!swarm"


@pytest.mark.asyncio
async def test_no_dispatch_without_verb():
    """Текст упоминает team-имя + 'командам' но без verb — не dispatch."""
    text = "Эти данные я отдам командам traders и coders попозже."
    intent = await extract_command_intent(text, is_owner=True)
    assert intent is None or intent.command != "!swarm"


@pytest.mark.asyncio
async def test_no_dispatch_legitimate_korr():
    """Переписка с упоминанием 'команды' но без swarm-намерения."""
    text = "Это наша переписка. У меня для команды есть вопрос про отпуск."
    intent = await extract_command_intent(text, is_owner=True)
    # "команды" встречается, но нет ни team-имени из _TEAMS, ни verb-pattern
    assert intent is None or intent.command != "!swarm"


@pytest.mark.asyncio
async def test_swarm_with_explicit_swarm_word():
    """Прямое 'swarm <team>' с topic → dispatch."""
    intent = await extract_command_intent(
        "swarm traders loop 2 BTC scalping",
        is_owner=True,
    )
    # Это идёт через _extract_explicit? Нет, нет '!' префикса. Через _try_swarm.
    assert intent is not None
    assert intent.command == "!swarm"
    assert intent.args.get("team") == "traders"


@pytest.mark.asyncio
async def test_existing_legitimate_call_pozovi_koderov():
    """Wave 44-O regression: 'позови кодеров' всё ещё работает."""
    intent = await extract_command_intent("позови кодеров", is_owner=True)
    assert intent is not None
    assert intent.command == "!swarm"
    assert intent.args.get("team") == "coders"


@pytest.mark.asyncio
async def test_existing_legitimate_call_zapusti_analitikov():
    """Wave 44-O regression: 'запусти аналитиков на тему BTC за 2 раунда'."""
    intent = await extract_command_intent(
        "запусти аналитиков на тему BTC за 2 раунда",
        is_owner=True,
    )
    assert intent is not None
    assert intent.command == "!swarm"
    assert intent.args.get("team") == "analysts"
    assert intent.args.get("count") == 2


@pytest.mark.asyncio
async def test_no_dispatch_just_word_komanda_no_team():
    """Просто 'у команды' без team-имени — не dispatch (нет team)."""
    intent = await extract_command_intent(
        "У команды сегодня выходной, давай обсудим завтра.",
        is_owner=True,
    )
    assert intent is None or intent.command != "!swarm"


@pytest.mark.asyncio
async def test_no_dispatch_komandam_substring_alone():
    """Регрессия: substring 'командам' без verb и с team-именем не должен срабатывать."""
    text = "Раздай командам coders и analysts по задаче когда время будет."
    intent = await extract_command_intent(text, is_owner=True)
    # "Раздай" не входит в verb-list — не dispatch
    assert intent is None or intent.command != "!swarm"
