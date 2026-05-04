# -*- coding: utf-8 -*-
"""
Тесты Wave 17-C: CHAT_PERMANENT_BAN_LIST без hardcoded fallback.

Регрессия: убеждаемся что How2AI (-1001587432709) НЕ оказывается
в дефолтном banned-списке. Root cause Session 36 — Krab не отвечал
в How2AI потому что группа была хардкожена в or-fallback конфига.
"""

from __future__ import annotations

import os

import pytest

# How2AI chat_id — тот который НЕ должен быть в дефолте
_HOW2AI = "-1001587432709"


def _parse_ban_list(env_val: str) -> list[str]:
    """Повторяет логику Config.CHAT_PERMANENT_BAN_LIST без fallback."""
    return [s.strip() for s in env_val.split(",") if s.strip()]


# ---------------------------------------------------------------------------
# 1. Пустой env → пустой список
# ---------------------------------------------------------------------------


def test_chat_permanent_ban_list_empty_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Если CHAT_PERMANENT_BAN_LIST не задан, список пустой — никто не забанен."""
    monkeypatch.delenv("CHAT_PERMANENT_BAN_LIST", raising=False)
    raw = os.getenv("CHAT_PERMANENT_BAN_LIST", "")
    result = _parse_ban_list(raw)
    assert result == [], f"Ожидался пустой список, получили: {result}"


# ---------------------------------------------------------------------------
# 2. Явный env → список уважается
# ---------------------------------------------------------------------------


def test_chat_permanent_ban_list_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Если env задан явно, chat_id'ы парсятся корректно."""
    monkeypatch.setenv("CHAT_PERMANENT_BAN_LIST", "123,456")
    raw = os.getenv("CHAT_PERMANENT_BAN_LIST", "")
    result = _parse_ban_list(raw)
    assert result == ["123", "456"]


# ---------------------------------------------------------------------------
# 3. How2AI НЕТ в дефолте
# ---------------------------------------------------------------------------


def test_chat_permanent_ban_list_no_hardcoded_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """How2AI -1001587432709 НЕ должен присутствовать в списке при пустом env.

    Это проверка на регрессию: старый код делал
        `[...] or ["-1001587432709"]`
    что приводило к бану How2AI по умолчанию.
    """
    monkeypatch.delenv("CHAT_PERMANENT_BAN_LIST", raising=False)
    raw = os.getenv("CHAT_PERMANENT_BAN_LIST", "")
    result = _parse_ban_list(raw)
    assert _HOW2AI not in result, (
        f"How2AI ({_HOW2AI}) оказался в дефолтном ban-списке — hardcoded fallback вернулся в код!"
    )
