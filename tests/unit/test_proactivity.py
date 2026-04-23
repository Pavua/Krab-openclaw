# -*- coding: utf-8 -*-
"""
Тесты unified proactivity controller + LLM trigger classifier.

Покрытие:
  1-5:  ProactivityController — каждый уровень применяет правильные sub-settings
  6-8:  should_reply() gate — silent/reactive/attentive/engaged логика
  9:    set_level() persist + reload (без реального файла — монкипатч)
  10:   LLM classifier disabled path
  11:   LLM classifier rate-limit
  12:   LLM classifier heuristic fallback
  13:   LLM classifier async mock
  14:   ProactivityLevel.PROACTIVE allows_unsolicited
  15+:  edge cases: unknown level, numeric strings, case insensitive
"""

from __future__ import annotations

import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _reset_singleton():
    """Сбросить singleton ProactivityController перед каждым тестом."""
    import src.core.proactivity as pm

    pm._ctrl = None
    pm.ProactivityController._instance = None


@pytest.fixture(autouse=True)
def clean_env_and_singleton(monkeypatch, tmp_path):
    """Чистая среда: без env vars, без persist файла, без singleton."""
    monkeypatch.delenv("KRAB_PROACTIVITY_LEVEL", raising=False)
    monkeypatch.delenv("KRAB_PROACTIVITY_LLM_CLASSIFIER", raising=False)
    monkeypatch.delenv("KRAB_CLASSIFIER_RATE_LIMIT_SEC", raising=False)

    # Перенаправляем persist файл во временную директорию
    import src.core.proactivity as pm

    fake_dir = tmp_path / "krab_rt"
    fake_dir.mkdir()
    monkeypatch.setattr(pm, "_PERSIST_DIR", fake_dir)
    monkeypatch.setattr(pm, "_PERSIST_FILE", fake_dir / "proactivity.json")

    _reset_singleton()
    yield
    _reset_singleton()


# ---------------------------------------------------------------------------
# 1. Default level = attentive
# ---------------------------------------------------------------------------


def test_default_level_is_attentive():
    from src.core.proactivity import ProactivityLevel, get_level

    assert get_level() == ProactivityLevel.ATTENTIVE


# ---------------------------------------------------------------------------
# 2. silent level — правильные sub-settings
# ---------------------------------------------------------------------------


def test_silent_level_settings(monkeypatch):
    monkeypatch.setenv("KRAB_PROACTIVITY_LEVEL", "silent")
    _reset_singleton()
    from src.core.proactivity import (
        allows_unsolicited,
        get_autonomy_mode,
        get_reactions_mode,
        get_trigger_threshold,
    )

    assert get_autonomy_mode() == "strict"
    assert get_trigger_threshold() >= 9.0  # никогда не сработает
    assert get_reactions_mode() == "off"
    assert allows_unsolicited() is False


# ---------------------------------------------------------------------------
# 3. reactive level — settings
# ---------------------------------------------------------------------------


def test_reactive_level_settings(monkeypatch):
    monkeypatch.setenv("KRAB_PROACTIVITY_LEVEL", "reactive")
    _reset_singleton()
    from src.core.proactivity import (
        allows_unsolicited,
        get_autonomy_mode,
        get_reactions_mode,
        get_trigger_threshold,
    )

    assert get_autonomy_mode() == "strict"
    assert get_trigger_threshold() >= 9.0
    assert get_reactions_mode() == "contextual"
    assert allows_unsolicited() is False


# ---------------------------------------------------------------------------
# 4. attentive level — settings
# ---------------------------------------------------------------------------


def test_attentive_level_settings(monkeypatch):
    monkeypatch.setenv("KRAB_PROACTIVITY_LEVEL", "attentive")
    _reset_singleton()
    from src.core.proactivity import (
        allows_unsolicited,
        get_autonomy_mode,
        get_reactions_mode,
        get_trigger_threshold,
    )

    assert get_autonomy_mode() == "normal"
    assert get_trigger_threshold() == pytest.approx(0.7)
    assert get_reactions_mode() == "contextual"
    assert allows_unsolicited() is False


# ---------------------------------------------------------------------------
# 5. engaged level — settings
# ---------------------------------------------------------------------------


def test_engaged_level_settings(monkeypatch):
    monkeypatch.setenv("KRAB_PROACTIVITY_LEVEL", "engaged")
    _reset_singleton()
    from src.core.proactivity import (
        allows_unsolicited,
        get_autonomy_mode,
        get_reactions_mode,
        get_trigger_threshold,
    )

    assert get_autonomy_mode() == "chatty"
    assert get_trigger_threshold() == pytest.approx(0.5)
    assert get_reactions_mode() == "contextual"
    assert allows_unsolicited() is False


# ---------------------------------------------------------------------------
# 6. proactive level — settings + unsolicited
# ---------------------------------------------------------------------------


def test_proactive_level_settings(monkeypatch):
    monkeypatch.setenv("KRAB_PROACTIVITY_LEVEL", "proactive")
    _reset_singleton()
    from src.core.proactivity import (
        allows_unsolicited,
        get_autonomy_mode,
        get_reactions_mode,
        get_trigger_threshold,
    )

    assert get_autonomy_mode() == "chatty"
    assert get_trigger_threshold() == pytest.approx(0.3)
    assert get_reactions_mode() == "aggressive"
    assert allows_unsolicited() is True


# ---------------------------------------------------------------------------
# 7. should_reply() — explicit mention всегда YES
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("level", ["silent", "reactive", "attentive", "engaged", "proactive"])
def test_should_reply_explicit_always_yes(monkeypatch, level):
    monkeypatch.setenv("KRAB_PROACTIVITY_LEVEL", level)
    _reset_singleton()
    from src.core.proactivity import should_reply

    result = should_reply("привет всем", chat_id="42", is_explicit_mention=True, is_group=True)
    assert result == "YES"


# ---------------------------------------------------------------------------
# 8. should_reply() — silent/reactive возвращают NO без explicit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("level", ["silent", "reactive"])
def test_should_reply_no_for_silent_reactive(monkeypatch, level):
    monkeypatch.setenv("KRAB_PROACTIVITY_LEVEL", level)
    _reset_singleton()
    from src.core.proactivity import should_reply

    # Вопрос в воздух — должен вернуть NO (нет implicit triggers)
    result = should_reply("кто знает как решить задачу?", chat_id="42", is_group=True)
    assert result == "NO"


# ---------------------------------------------------------------------------
# 9. should_reply() — attentive + implicit trigger должен вернуть YES
# ---------------------------------------------------------------------------


def test_should_reply_implicit_trigger_attentive(monkeypatch):
    monkeypatch.setenv("KRAB_PROACTIVITY_LEVEL", "attentive")
    _reset_singleton()
    from src.core.proactivity import should_reply

    # "подскажите" → implicit question, score 0.4 < threshold 0.7 → NO
    result = should_reply("подскажите пожалуйста", chat_id="42", is_group=True)
    assert result == "NO"


def test_should_reply_generic_ai_attentive(monkeypatch):
    monkeypatch.setenv("KRAB_PROACTIVITY_LEVEL", "attentive")
    _reset_singleton()
    from src.core.proactivity import should_reply

    # generic AI + "?" → score 0.55 < threshold 0.7 → NO
    result = should_reply("бот, что делать?", chat_id="42", is_group=True)
    assert result == "NO"


def test_should_reply_implicit_trigger_proactive(monkeypatch):
    """При threshold 0.3 generic AI alias + ? должен пройти (score 0.55 >= 0.3)."""
    monkeypatch.setenv("KRAB_PROACTIVITY_LEVEL", "proactive")
    _reset_singleton()
    from src.core.proactivity import should_reply

    result = should_reply("бот, что делать?", chat_id="99", is_group=True)
    assert result == "YES"


# ---------------------------------------------------------------------------
# 10. set_level() + persist
# ---------------------------------------------------------------------------


def test_set_level_persists(tmp_path):
    import src.core.proactivity as pm

    fake_file = tmp_path / "p.json"
    pm._PERSIST_FILE = fake_file
    pm._PERSIST_DIR = tmp_path
    _reset_singleton()

    from src.core.proactivity import ProactivityLevel, get_level, set_level

    set_level("engaged")
    assert get_level() == ProactivityLevel.ENGAGED
    assert fake_file.exists()
    import json

    data = json.loads(fake_file.read_text())
    assert data["level"] == "engaged"


# ---------------------------------------------------------------------------
# 11. LLM classifier — disabled path
# ---------------------------------------------------------------------------


def test_llm_classifier_disabled():
    from src.core.llm_trigger_classifier import classify, is_enabled

    # Env не установлен → disabled
    assert is_enabled() is False
    result = classify("подскажите")
    assert result.verdict == "UNCLEAR"
    assert result.source == "disabled"


# ---------------------------------------------------------------------------
# 12. LLM classifier — rate limit
# ---------------------------------------------------------------------------


def test_llm_classifier_rate_limit(monkeypatch):
    monkeypatch.setenv("KRAB_PROACTIVITY_LLM_CLASSIFIER", "1")
    monkeypatch.setenv("KRAB_CLASSIFIER_RATE_LIMIT_SEC", "30")

    import src.core.llm_trigger_classifier as clsf

    # Симулируем что вызов был только что
    clsf._last_call_ts["chat_42"] = time.monotonic()

    result = clsf.classify("hello?", chat_id="chat_42")
    assert result.verdict == "UNCLEAR"
    assert result.source == "rate_limited"


# ---------------------------------------------------------------------------
# 13. LLM classifier — heuristic fallback (yes pattern)
# ---------------------------------------------------------------------------


def test_llm_classifier_heuristic_yes():
    from src.core.llm_trigger_classifier import _heuristic_classify

    result = _heuristic_classify("как решить эту задачу?")
    assert result.verdict == "YES"
    assert result.source == "heuristic"


def test_llm_classifier_heuristic_no():
    from src.core.llm_trigger_classifier import _heuristic_classify

    result = _heuristic_classify("хахаха")
    assert result.verdict == "NO"
    assert result.source == "heuristic"


def test_llm_classifier_heuristic_unclear():
    from src.core.llm_trigger_classifier import _heuristic_classify

    result = _heuristic_classify("Привет всем")
    assert result.verdict == "UNCLEAR"
    assert result.source == "heuristic"


# ---------------------------------------------------------------------------
# 14. LLM classifier async — mock LLM call, timeout → heuristic fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_classifier_async_timeout(monkeypatch):
    monkeypatch.setenv("KRAB_PROACTIVITY_LLM_CLASSIFIER", "1")
    monkeypatch.setenv("KRAB_CLASSIFIER_RATE_LIMIT_SEC", "0")

    import src.core.llm_trigger_classifier as clsf

    clsf._last_call_ts.clear()

    # Патчим asyncio.wait_for чтобы бросил TimeoutError немедленно
    async def raise_timeout(coro, timeout):
        coro.close()
        raise asyncio.TimeoutError()

    with patch("src.core.llm_trigger_classifier.asyncio.wait_for", side_effect=raise_timeout):
        result = await clsf.classify_async("подскажите?", chat_id="tst_timeout")


# ---------------------------------------------------------------------------
# 15. LLM classifier async — mock LLM call success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_classifier_async_success(monkeypatch):
    monkeypatch.setenv("KRAB_PROACTIVITY_LLM_CLASSIFIER", "1")
    monkeypatch.setenv("KRAB_CLASSIFIER_RATE_LIMIT_SEC", "0")

    import src.core.llm_trigger_classifier as clsf

    clsf._last_call_ts.clear()
    clsf._CLASSIFIER_TIMEOUT = 2.0

    async def fake_llm(text, context_hint=""):
        return "YES", "вопрос требует ответа"

    with patch.object(clsf, "_call_llm_async", side_effect=fake_llm):
        result = await clsf.classify_async("подскажите?", chat_id="tst2")

    assert result.verdict == "YES"
    assert result.source == "llm"
    assert "вопрос" in result.reason


# ---------------------------------------------------------------------------
# 16. Numeric level string
# ---------------------------------------------------------------------------


def test_numeric_level_string(monkeypatch):
    monkeypatch.setenv("KRAB_PROACTIVITY_LEVEL", "3")
    _reset_singleton()
    from src.core.proactivity import ProactivityLevel, get_level

    assert get_level() == ProactivityLevel.ENGAGED


# ---------------------------------------------------------------------------
# 17. Unknown level → falls back to default attentive
# ---------------------------------------------------------------------------


def test_unknown_level_defaults_to_attentive(monkeypatch):
    monkeypatch.setenv("KRAB_PROACTIVITY_LEVEL", "superchatty")
    _reset_singleton()
    from src.core.proactivity import ProactivityLevel, get_level

    assert get_level() == ProactivityLevel.ATTENTIVE


# ---------------------------------------------------------------------------
# 18. should_reply() — DM always YES (не silent)
# ---------------------------------------------------------------------------


def test_should_reply_dm_always_yes(monkeypatch):
    monkeypatch.setenv("KRAB_PROACTIVITY_LEVEL", "attentive")
    _reset_singleton()
    from src.core.proactivity import should_reply

    result = should_reply("просто слово", chat_id="1", is_group=False)
    assert result == "YES"


# ---------------------------------------------------------------------------
# 19. should_reply() — reply_to_krab = YES на любом уровне кроме... тоже YES
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("level", ["silent", "reactive", "attentive"])
def test_should_reply_reply_to_krab_yes(monkeypatch, level):
    monkeypatch.setenv("KRAB_PROACTIVITY_LEVEL", level)
    _reset_singleton()
    from src.core.proactivity import should_reply

    result = should_reply("нет", chat_id="1", is_reply_to_krab=True, is_group=True)
    assert result == "YES"


# ---------------------------------------------------------------------------
# 20. _parse_llm_response — парсинг
# ---------------------------------------------------------------------------


def test_parse_llm_response_yes():
    from src.core.llm_trigger_classifier import _parse_llm_response

    raw = "VERDICT: YES\nREASON: Пользователь задал прямой вопрос."
    v, r = _parse_llm_response(raw)
    assert v == "YES"
    assert "вопрос" in r.lower()


def test_parse_llm_response_no():
    from src.core.llm_trigger_classifier import _parse_llm_response

    raw = "Verdict: No\nReason: Просто смех"
    v, r = _parse_llm_response(raw)
    assert v == "NO"


def test_parse_llm_response_garbage():
    from src.core.llm_trigger_classifier import _parse_llm_response

    v, r = _parse_llm_response("random garbage text here")
    assert v == "UNCLEAR"
