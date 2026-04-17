"""Unit-тесты для `!memory stats` subcommand в command_handlers."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

# Env-vars должны быть до импорта src.* (иначе config.py падает на TELEGRAM_API_ID).
for _k, _v in {
    "TELEGRAM_API_ID": "0",
    "TELEGRAM_API_HASH": "test",
    "OWNER_ID": "0",
}.items():
    if not os.environ.get(_k):
        os.environ[_k] = _v

import pytest  # noqa: E402

from src.handlers import command_handlers as ch  # noqa: E402


def test_memory_stats_formats_output() -> None:
    """Все блоки присутствуют, числа форматируются с пробелами."""
    archive = {
        "exists": True,
        "messages": 42708,
        "chats": 26,
        "chunks": 9099,
        "size_mb": 42.3,
    }
    indexer = {
        "state": "running",
        "queue_size": 0,
        "queue_maxsize": 10000,
        "processed_total": 45,
        "failed": {},
    }
    validator = {
        "safe_total": 1247,
        "injection_blocked_total": 3,
        "confirmed_total": 1,
        "pending_count": 2,
    }

    out = ch.format_memory_stats(archive, indexer, validator)

    # RU-формат тысяч разделитель — пробел.
    assert "42 708" in out
    assert "9 099" in out
    assert "running" in out
    # Валидатор: допускаем с пробелом ("1 247") или без ("1247").
    assert "1 247" in out or "1247" in out
    assert "Pending: 2" in out
    assert "Memory Layer Stats" in out
    assert "Archive.db" in out
    assert "Indexer" in out
    assert "Validator" in out


def test_memory_stats_handles_missing_archive() -> None:
    """Graceful degradation — архив не инициализирован, валидатор не загружен."""
    archive = {"exists": False}
    indexer = {"state": "unavailable"}
    validator = {"error": "not loaded"}

    out = ch.format_memory_stats(archive, indexer, validator)

    assert "Not initialized" in out
    assert "not loaded" in out
    # Unavailable indexer — показываем только state.
    assert "`unavailable`" in out


def test_memory_stats_archive_error_fallback() -> None:
    """Если при чтении БД было исключение — показываем строку Error."""
    archive = {"exists": True, "error": "disk I/O error"}
    indexer = {"state": "stopped", "queue_size": 0, "processed_total": 0}
    validator = {"error": "not loaded"}

    out = ch.format_memory_stats(archive, indexer, validator)
    assert "Error: disk I/O error" in out


def test_fmt_int_ru_spaces_thousands() -> None:
    """Вспомогательная функция форматирования больших чисел."""
    assert ch._fmt_int_ru(0) == "0"
    assert ch._fmt_int_ru(999) == "999"
    assert ch._fmt_int_ru(1247) == "1 247"
    assert ch._fmt_int_ru(42708) == "42 708"
    assert ch._fmt_int_ru(1000000) == "1 000 000"


@pytest.mark.asyncio
async def test_handle_memory_stats_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    """handle_memory с action='stats' → вызывает _handle_memory_stats."""
    called = {"flag": False}

    async def _mock_stats(message: object) -> None:  # noqa: ARG001
        called["flag"] = True

    monkeypatch.setattr(ch, "_handle_memory_stats", _mock_stats)

    mock_message = MagicMock()
    mock_message.text = "!memory stats"
    mock_message.reply = AsyncMock()
    mock_bot = MagicMock()

    await ch.handle_memory(mock_bot, mock_message)
    assert called["flag"] is True


@pytest.mark.asyncio
async def test_handle_memory_recent_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """`!memory recent` поведение не сломано (обратная совместимость)."""
    monkeypatch.setattr(ch, "list_workspace_memory_entries", lambda limit, source_filter: [])

    mock_message = MagicMock()
    mock_message.text = "!memory recent"
    mock_message.reply = AsyncMock()
    mock_bot = MagicMock()

    await ch.handle_memory(mock_bot, mock_message)
    mock_message.reply.assert_awaited_once()
    (call_arg,) = mock_message.reply.call_args.args
    assert "нет подходящих записей" in call_arg


@pytest.mark.asyncio
async def test_handle_memory_unknown_subcommand_raises() -> None:
    """Неизвестная subcommand выбрасывает UserInputError с подсказкой."""
    from src.core.exceptions import UserInputError

    mock_message = MagicMock()
    mock_message.text = "!memory foobar"
    mock_bot = MagicMock()

    with pytest.raises(UserInputError) as exc_info:
        await ch.handle_memory(mock_bot, mock_message)
    # Подсказка должна содержать обе subcommand.
    assert "stats" in str(exc_info.value.user_message)
    assert "recent" in str(exc_info.value.user_message)


def test_collect_memory_archive_stats_missing_db(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """collect_archive_stats на несуществующей БД не бросает."""
    fake_path = tmp_path / "nonexistent.db"
    # Подменяем Path("~/.openclaw/...").expanduser() через monkeypatch на tmp_path.
    import pathlib

    original_expand = pathlib.Path.expanduser

    def fake_expand(self: pathlib.Path) -> pathlib.Path:
        if "krab_memory" in str(self):
            return fake_path
        return original_expand(self)

    monkeypatch.setattr(pathlib.Path, "expanduser", fake_expand)

    result = ch._collect_memory_archive_stats()
    assert result["exists"] is False


def test_collect_memory_indexer_stats_when_worker_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """При исключении в get_indexer — возвращается {'state': 'unavailable'}."""
    import src.core.memory_indexer_worker as worker_mod

    def _raise() -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(worker_mod, "get_indexer", _raise)

    result = ch._collect_memory_indexer_stats()
    assert result == {"state": "unavailable"}


def test_collect_memory_validator_stats_when_module_absent() -> None:
    """memory_validator модуля нет → возвращаем {'error': 'not loaded'}."""
    result = ch._collect_memory_validator_stats()
    # В чистом репозитории модуля нет — expect fallback.
    assert result == {"error": "not loaded"}
