# -*- coding: utf-8 -*-
"""
Тесты для verify_telegram_mcp_accounts.py.

Проверяем:
1. успешный smoke-отчёт по двум аккаунтам собирается без Telegram API;
2. ошибка одного аккаунта корректно отражается в итоговом JSON и коде возврата.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_module():
    """Импортирует verify_telegram_mcp_accounts.py напрямую по пути."""
    module_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "verify_telegram_mcp_accounts.py"
    )
    spec = importlib.util.spec_from_file_location("verify_telegram_mcp_accounts_module", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeBridge:
    """Минимальный fake bridge для smoke-проверки без реального Telegram."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def get_dialogs(self, limit: int):
        if self.fail:
            raise RuntimeError("peer invalid")
        return [{"id": 1, "title": "dialog", "limit": limit}]

    async def get_chat_history(self, chat_id: str, limit: int):
        return [{"id": 2, "chat_id": chat_id, "limit": limit}]

    async def search(self, query: str, limit: int):
        return [{"id": 3, "text": query, "limit": limit}]

    async def send_message(self, chat_id: str, text: str):
        return {"id": 4, "chat_id": chat_id, "text": text}


@pytest.mark.asyncio
async def test_verify_account_reports_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Успешный bridge должен собирать полный smoke-отчёт."""
    module = _load_module()
    monkeypatch.setattr(module, "TelegramBridge", lambda: _FakeBridge())

    report = await module.verify_account(
        label="krab",
        session_name="kraab",
        history_chat="p0lrd",
        search_query="Codex",
        dialogs_limit=5,
        history_limit=3,
        search_limit=3,
        send_test_chat="p0lrd",
    )

    assert report["ok"] is True
    assert report["dialogs_count"] == 1
    assert report["history_count"] == 1
    assert report["search_count"] == 1
    assert report["send_test"]["chat"] == "p0lrd"


@pytest.mark.asyncio
async def test_amain_returns_nonzero_when_any_account_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Если хотя бы один аккаунт падает, итоговый exit code должен быть 1."""
    module = _load_module()
    bridges = iter([_FakeBridge(), _FakeBridge(fail=True)])
    monkeypatch.setattr(module, "TelegramBridge", lambda: next(bridges))

    class _Args:
        history_chat = "p0lrd"
        search_query = "Codex"
        dialogs_limit = 5
        history_limit = 3
        search_limit = 3
        send_test_chat = ""

    exit_code = await module.amain(_Args())
    assert exit_code == 1
