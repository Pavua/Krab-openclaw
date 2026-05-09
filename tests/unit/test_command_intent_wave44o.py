# -*- coding: utf-8 -*-
"""Tests for Wave 44-O-nlu — CommandIntentExtractor."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.core.command_intent_extractor import (
    CommandIntent,
    extract_command_intent,
)


@pytest.mark.asyncio
async def test_explicit_command_passthrough():
    intent = await extract_command_intent("!swarm analysts loop 2 BTC", is_owner=True)
    assert intent is not None
    assert intent.command == "!swarm"
    assert intent.confidence == 1.0
    assert "analysts" in intent.rendered.lower()


@pytest.mark.asyncio
async def test_swarm_natural_ru_analysts_btc():
    intent = await extract_command_intent(
        "запусти аналитиков на тему BTC за 2 раунда",
        is_owner=True,
    )
    assert intent is not None, "должен распознать swarm intent"
    assert intent.command == "!swarm"
    assert intent.args.get("team") == "analysts"
    assert intent.args.get("count") == 2
    assert "BTC" in (intent.args.get("topic") or "")
    assert intent.confidence >= 0.85


@pytest.mark.asyncio
async def test_status_natural():
    intent = await extract_command_intent("проверь статус", is_owner=True)
    assert intent is not None
    assert intent.command == "!status"
    assert intent.confidence >= 0.8


@pytest.mark.asyncio
async def test_destructive_guard_caps_confidence():
    intent = await extract_command_intent(
        "удали все задачи свёрма",
        is_owner=True,
    )
    # Either None (no template fired with conf>=0.4) or destructive cap < 0.8.
    if intent is not None:
        assert intent.confidence < 0.8
        assert intent.destructive is True


@pytest.mark.asyncio
async def test_chitchat_returns_none():
    intent = await extract_command_intent("как дела", is_owner=True)
    assert intent is None


@pytest.mark.asyncio
async def test_non_owner_gated():
    intent = await extract_command_intent(
        "проверь статус",
        owner_only=True,
        is_owner=False,
    )
    assert intent is None


@pytest.mark.asyncio
async def test_quota_natural():
    intent = await extract_command_intent("покажи квоту моделей", is_owner=True)
    assert intent is not None
    assert intent.command == "!quota"


@pytest.mark.asyncio
async def test_proactive_on():
    intent = await extract_command_intent("включи proactive", is_owner=True)
    assert intent is not None
    assert intent.command == "!proactive"
    assert intent.args.get("state") == "on"


@pytest.mark.asyncio
async def test_memory_recall_natural():
    intent = await extract_command_intent("вспомни про настройку Sentry", is_owner=True)
    assert intent is not None
    assert intent.command == "!memory"
    assert intent.subcommand == "recall"
    assert "sentry" in (intent.args.get("query") or "").lower()


@pytest.mark.asyncio
async def test_swarm_team_only_no_topic_lower_confidence():
    intent = await extract_command_intent("позови кодеров", is_owner=True)
    if intent is not None:
        assert intent.command == "!swarm"
        assert intent.args.get("team") == "coders"
        assert intent.confidence < 0.9  # no topic → not high confidence


@pytest.mark.asyncio
async def test_explicit_destructive_marked():
    intent = await extract_command_intent("!swarm reset all", is_owner=True)
    assert intent is not None
    assert intent.destructive is True
    assert intent.confidence == 1.0  # explicit user intent — execute as typed


@pytest.mark.asyncio
async def test_empty_text_returns_none():
    assert await extract_command_intent("", is_owner=True) is None
    assert await extract_command_intent("   ", is_owner=True) is None


@pytest.mark.asyncio
async def test_llm_fallback_mocked():
    """LLM fallback path: text not matching templates → mocked LM Studio returns intent."""
    fake_intent = CommandIntent(
        command="!cron",
        subcommand="schedule",
        args={"when": "ежедневно 09:00", "action": "summary"},
        confidence=0.82,
        original_text="...",
        rendered="!cron schedule daily 09:00 summary",
    )
    with patch(
        "src.core.command_intent_extractor._llm_extract",
        new=AsyncMock(return_value=fake_intent),
    ):
        intent = await extract_command_intent(
            "нечто абсолютно не подходящее под шаблоны xyzzy",
            is_owner=True,
            use_llm=True,
        )
    assert intent is not None
    assert intent.command == "!cron"
    assert intent.confidence == 0.82


# ---------------------------------------------------------------------------
# Wave 44-O-nlu-v2: extended pattern coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cron_zaplaniruy():
    intent = await extract_command_intent("запланируй мне отчёт каждый день", is_owner=True)
    assert intent is not None
    assert intent.command == "!cron"


@pytest.mark.asyncio
async def test_cron_napomni_mne():
    intent = await extract_command_intent("напомни мне про митинг завтра", is_owner=True)
    assert intent is not None
    assert intent.command == "!cron"


@pytest.mark.asyncio
async def test_memory_save_zapomni():
    intent = await extract_command_intent("запомни что Pavel любит свежий хлеб", is_owner=True)
    assert intent is not None
    assert intent.command == "!memory"
    assert intent.subcommand == "save"
    assert "pavel" in (intent.args.get("text") or "").lower()


@pytest.mark.asyncio
async def test_memory_save_sохрани():
    intent = await extract_command_intent(
        "сохрани заметку: Sentry token в 1Password", is_owner=True
    )
    assert intent is not None
    assert intent.command == "!memory"
    assert intent.subcommand == "save"


@pytest.mark.asyncio
async def test_memory_recall_chto_pomnish():
    intent = await extract_command_intent("вспомни про настройку DNS", is_owner=True)
    assert intent is not None
    assert intent.command == "!memory"
    assert intent.subcommand == "recall"


@pytest.mark.asyncio
async def test_inbox_pokazhi_vhodyaschie():
    intent = await extract_command_intent("покажи входящие", is_owner=True)
    assert intent is not None
    assert intent.command == "!inbox"
    assert intent.confidence >= 0.8


@pytest.mark.asyncio
async def test_inbox_chto_v_inbox():
    intent = await extract_command_intent("что в inbox", is_owner=True)
    assert intent is not None
    assert intent.command == "!inbox"


@pytest.mark.asyncio
async def test_cost_skolko_potratil():
    intent = await extract_command_intent("сколько потратил за неделю", is_owner=True)
    assert intent is not None
    assert intent.command == "!costs"


@pytest.mark.asyncio
async def test_cost_rashody_segodnya():
    intent = await extract_command_intent("расходы за сегодня", is_owner=True)
    assert intent is not None
    assert intent.command == "!costs"


@pytest.mark.asyncio
async def test_restart_perezapusti():
    intent = await extract_command_intent("перезапусти краба", is_owner=True)
    assert intent is not None
    assert intent.command == "!restart"


@pytest.mark.asyncio
async def test_restart_restart_word():
    intent = await extract_command_intent("restart please", is_owner=True)
    assert intent is not None
    assert intent.command == "!restart"


@pytest.mark.asyncio
async def test_models_kakie_modeli():
    intent = await extract_command_intent("какие модели сейчас активны", is_owner=True)
    assert intent is not None
    assert intent.command == "!models"


@pytest.mark.asyncio
async def test_models_spisok():
    intent = await extract_command_intent("список моделей", is_owner=True)
    assert intent is not None
    assert intent.command == "!models"


@pytest.mark.asyncio
async def test_dreaming_sny():
    intent = await extract_command_intent("покажи сны", is_owner=True)
    assert intent is not None
    assert intent.command == "!dreaming"


@pytest.mark.asyncio
async def test_dreaming_dream_diary():
    intent = await extract_command_intent("dream diary", is_owner=True)
    assert intent is not None
    assert intent.command == "!dreaming"


@pytest.mark.asyncio
async def test_proactive_media_on():
    intent = await extract_command_intent("включи реакции на медиа", is_owner=True)
    assert intent is not None
    assert intent.command == "!proactive"
    assert intent.subcommand == "media"
    assert intent.args.get("state") == "on"
    assert "media on" in intent.rendered.lower()


@pytest.mark.asyncio
async def test_proactive_media_off():
    intent = await extract_command_intent("отключи фото в proactive", is_owner=True)
    assert intent is not None
    assert intent.command == "!proactive"
    assert intent.args.get("state") == "off"


@pytest.mark.asyncio
async def test_swarm_task_board():
    intent = await extract_command_intent("покажи задачи команд", is_owner=True)
    assert intent is not None
    assert intent.command == "!swarm"
    assert "task" in (intent.rendered or "").lower()


@pytest.mark.asyncio
async def test_swarm_artifacts():
    intent = await extract_command_intent("покажи артефакты swarm", is_owner=True)
    assert intent is not None
    assert intent.command == "!swarm"
    assert "artifacts" in (intent.rendered or "").lower()


@pytest.mark.asyncio
async def test_swarm_summary():
    intent = await extract_command_intent("сводка swarm за день", is_owner=True)
    assert intent is not None
    assert intent.command == "!swarm"
    assert "summary" in (intent.rendered or "").lower()


@pytest.mark.asyncio
async def test_swarm_setup():
    intent = await extract_command_intent("настрой swarm с нуля", is_owner=True)
    assert intent is not None
    assert intent.command == "!swarm"
    assert "setup" in (intent.rendered or "").lower()


@pytest.mark.asyncio
async def test_llm_disabled_by_default():
    """Without use_llm=True, no HTTP call is made (network-free tests)."""
    with patch(
        "src.core.command_intent_extractor._llm_extract",
        new=AsyncMock(return_value=None),
    ) as mock_llm:
        intent = await extract_command_intent(
            "случайная фраза без шаблонов qwerty",
            is_owner=True,
        )
    assert intent is None
    mock_llm.assert_not_called()
