# -*- coding: utf-8 -*-
"""
Unit-тесты `!recall <query>` (Memory Layer Phase 2 retrieval интеграция).

Покрывают:
  - пустой query → UserInputError с usage-подсказкой;
  - обычный recall c workspace + Memory Layer результатами → одна reply;
  - Memory Layer unavailable (httpx упал) → остальные секции всё равно возвращаются;
  - форматирование секции Memory Layer (score, mode, preview).
"""

from __future__ import annotations

# ── env-guard до импортов src.* ──────────────────────────────────────────
import os

for _k, _v in {
    "TELEGRAM_API_ID": "0",
    "TELEGRAM_API_HASH": "test",
    "OWNER_ID": "0",
}.items():
    if not os.environ.get(_k):
        os.environ[_k] = _v

from types import SimpleNamespace  # noqa: E402
from unittest.mock import AsyncMock  # noqa: E402

import pytest  # noqa: E402

import src.handlers.command_handlers as command_handlers_module  # noqa: E402
from src.core.exceptions import UserInputError  # noqa: E402
from src.handlers.command_handlers import (  # noqa: E402
    _format_memory_layer_section,
    handle_recall,
)

# ---------------------------------------------------------------------------
# _format_memory_layer_section — чистая ф-я, без MTProto.
# ---------------------------------------------------------------------------


def test_format_memory_layer_section_basic() -> None:
    """Форматируется номер + [mode score=...] + preview."""
    results = [
        {"chunk_id": "c1", "text": "dashboard redesign", "score": 0.87, "mode": "hybrid"},
    ]
    out = _format_memory_layer_section(results)
    assert "1." in out
    assert "[hybrid score=0.87]" in out
    assert "dashboard redesign" in out


def test_format_memory_layer_section_strips_newlines() -> None:
    """Переводы строк в preview заменяются на пробелы."""
    results = [
        {"chunk_id": "c1", "text": "line1\nline2\nline3", "score": 0.5, "mode": "fts"},
    ]
    out = _format_memory_layer_section(results)
    assert "line1 line2 line3" in out


def test_format_memory_layer_section_truncates_150_chars() -> None:
    """Preview обрезается до 150 символов."""
    long_text = "x" * 500
    results = [
        {"chunk_id": "c1", "text": long_text, "score": 0.3, "mode": "hybrid"},
    ]
    out = _format_memory_layer_section(results)
    # Не может быть больше ~150 символов preview + markup.
    assert "x" * 150 in out
    assert "x" * 151 not in out


def test_format_memory_layer_section_handles_missing_score() -> None:
    """None/отсутствующий score → '—'."""
    results = [
        {"chunk_id": "c1", "text": "test", "score": None, "mode": "fts"},
    ]
    out = _format_memory_layer_section(results)
    assert "score=—" in out


# ---------------------------------------------------------------------------
# handle_recall.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recall_empty_args_raises_user_input_error(monkeypatch) -> None:
    """!recall без аргументов → UserInputError с usage-подсказкой."""
    message = SimpleNamespace(reply=AsyncMock())
    bot = SimpleNamespace(_get_command_args=lambda _: "")

    with pytest.raises(UserInputError) as exc_info:
        await handle_recall(bot, message)

    assert "!recall" in (exc_info.value.user_message or "")


@pytest.mark.asyncio
async def test_recall_with_query_includes_memory_layer_section(monkeypatch) -> None:
    """Если endpoint возвращает результаты — они попадают в reply."""
    message = SimpleNamespace(reply=AsyncMock())
    bot = SimpleNamespace(_get_command_args=lambda _: "dashboard")

    monkeypatch.setattr(command_handlers_module, "recall_workspace_memory", lambda q: "")
    monkeypatch.setattr(command_handlers_module.memory_manager, "recall", lambda q: "")

    async def _fake_recall(query, limit=5):  # noqa: ARG001
        return [
            {
                "chunk_id": "c1",
                "text": "dashboard redesign",
                "score": 0.9,
                "mode": "hybrid",
            }
        ]

    monkeypatch.setattr(command_handlers_module, "_recall_memory_layer", _fake_recall)

    await handle_recall(bot, message)

    message.reply.assert_awaited_once()
    reply_text = message.reply.await_args.args[0]
    assert "Memory Layer archive" in reply_text
    assert "dashboard redesign" in reply_text


@pytest.mark.asyncio
async def test_recall_with_no_results_anywhere(monkeypatch) -> None:
    """Все источники пустые → 'Ничего не нашел'."""
    message = SimpleNamespace(reply=AsyncMock())
    bot = SimpleNamespace(_get_command_args=lambda _: "unknown_term_xyz")

    monkeypatch.setattr(command_handlers_module, "recall_workspace_memory", lambda q: "")
    monkeypatch.setattr(command_handlers_module.memory_manager, "recall", lambda q: "")

    async def _empty_recall(query, limit=5):  # noqa: ARG001
        return []

    monkeypatch.setattr(command_handlers_module, "_recall_memory_layer", _empty_recall)

    await handle_recall(bot, message)

    message.reply.assert_awaited_once()
    reply_text = message.reply.await_args.args[0]
    assert "Ничего не нашел" in reply_text


@pytest.mark.asyncio
async def test_recall_memory_layer_failure_does_not_break_other_sections(
    monkeypatch,
) -> None:
    """Если endpoint недоступен — workspace/vector секции всё равно рендерятся."""
    message = SimpleNamespace(reply=AsyncMock())
    bot = SimpleNamespace(_get_command_args=lambda _: "GPT-5.4")

    monkeypatch.setattr(
        command_handlers_module,
        "recall_workspace_memory",
        lambda q: "- [2026-03-10.md] GPT-5.4 заблокирован",
    )
    monkeypatch.setattr(command_handlers_module.memory_manager, "recall", lambda q: "")

    async def _fail_recall(query, limit=5):  # noqa: ARG001
        return []  # Endpoint fail → пустой список, секция не добавляется

    monkeypatch.setattr(command_handlers_module, "_recall_memory_layer", _fail_recall)

    await handle_recall(bot, message)

    reply_text = message.reply.await_args.args[0]
    assert "OpenClaw workspace" in reply_text
    assert "GPT-5.4" in reply_text
    # Секция Memory Layer отсутствует (не было результатов).
    assert "Memory Layer archive" not in reply_text


@pytest.mark.asyncio
async def test_recall_memory_layer_handles_httpx_error(monkeypatch) -> None:
    """_recall_memory_layer возвращает [] при любом httpx-ошибке."""
    from src.handlers.command_handlers import _recall_memory_layer

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **kw):
            raise ConnectionError("panel is down")

    monkeypatch.setattr(
        command_handlers_module.httpx, "AsyncClient", lambda *a, **kw: _FakeClient()
    )

    results = await _recall_memory_layer("anything")
    assert results == []


@pytest.mark.asyncio
async def test_recall_memory_layer_handles_non_ok_response(monkeypatch) -> None:
    """_recall_memory_layer возвращает [] если endpoint вернул ok=False."""
    from src.handlers.command_handlers import _recall_memory_layer

    class _FakeResp:
        def json(self):
            return {"ok": False, "error": "archive_db_missing"}

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **kw):
            return _FakeResp()

    monkeypatch.setattr(
        command_handlers_module.httpx, "AsyncClient", lambda *a, **kw: _FakeClient()
    )

    results = await _recall_memory_layer("hello")
    assert results == []
