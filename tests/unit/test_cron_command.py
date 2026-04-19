# -*- coding: utf-8 -*-
"""
Юнит-тесты для команды !cron — управление OpenClaw cron jobs из Telegram.

Покрываемые сценарии:
  - _cron_format_schedule: every, cron, unknown
  - _cron_format_last_status: ok, error, no state
  - _cron_read_jobs: valid, missing file, bad json
  - _cron_write_jobs: записывает корректный payload
  - handle_cron list: пустой список, несколько jobs
  - handle_cron status: статистика enabled/disabled/errors
  - handle_cron enable/disable: по имени, по id, job not found, no arg
  - handle_cron run: найден, не найден, нет аргумента
  - handle_cron: неизвестная субкоманда (справка)
  - handle_cron: non-owner получает отказ
"""

from __future__ import annotations

import json
import pathlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.access_control import AccessLevel
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import (
    _cron_format_last_status,
    _cron_format_schedule,
    _cron_read_jobs,
    _cron_write_jobs,
    handle_cron,
)

# ---------------------------------------------------------------------------
# Вспомогательные данные
# ---------------------------------------------------------------------------

_JOB_EVERY = {
    "id": "job-every-1",
    "name": "Mercadona Restock",
    "enabled": True,
    "schedule": {"kind": "every", "everyMs": 3600000},
    "state": {"lastStatus": "ok", "consecutiveErrors": 0},
}

_JOB_CRON_WITH_TZ = {
    "id": "job-cron-2",
    "name": "Daily Report",
    "enabled": False,
    "schedule": {"kind": "cron", "expr": "0 8 * * *", "tz": "Europe/Madrid"},
    "state": {"lastStatus": "error", "consecutiveErrors": 2, "lastError": "rate limit"},
}

_JOB_CRON_NO_TZ = {
    "id": "job-cron-3",
    "name": "Nightly Diag",
    "enabled": True,
    "schedule": {"kind": "cron", "expr": "0 3 * * *"},
    "state": {"lastStatus": "ok", "consecutiveErrors": 0},
}

_JOB_EVERY_MIN = {
    "id": "job-min-4",
    "name": "Minute Task",
    "enabled": False,
    "schedule": {"kind": "every", "everyMs": 600000},
    "state": {},
}

_JOBS_JSON = {"version": 1, "jobs": [_JOB_EVERY, _JOB_CRON_WITH_TZ]}


# ---------------------------------------------------------------------------
# _cron_format_schedule
# ---------------------------------------------------------------------------


class TestCronFormatSchedule:
    """Тесты форматирования расписания job."""

    def test_every_hours(self):
        """everyMs кратен часу — отображается в часах."""
        job = {"schedule": {"kind": "every", "everyMs": 3600000}}
        assert _cron_format_schedule(job) == "каждые 1ч"

    def test_every_hours_multiple(self):
        """everyMs = 24ч."""
        job = {"schedule": {"kind": "every", "everyMs": 86400000}}
        assert _cron_format_schedule(job) == "каждые 24ч"

    def test_every_minutes(self):
        """everyMs кратен минуте — отображается в минутах."""
        job = {"schedule": {"kind": "every", "everyMs": 600000}}
        assert _cron_format_schedule(job) == "каждые 10м"

    def test_every_seconds(self):
        """everyMs — остаток в секундах."""
        job = {"schedule": {"kind": "every", "everyMs": 30000}}
        assert _cron_format_schedule(job) == "каждые 30с"

    def test_every_zero(self):
        """everyMs == 0 — fallback 'каждые ?'."""
        job = {"schedule": {"kind": "every", "everyMs": 0}}
        assert _cron_format_schedule(job) == "каждые ?"

    def test_every_missing(self):
        """everyMs отсутствует — fallback 'каждые ?'."""
        job = {"schedule": {"kind": "every"}}
        assert _cron_format_schedule(job) == "каждые ?"

    def test_cron_with_tz(self):
        """Cron с timezone — выводится в скобках."""
        job = {"schedule": {"kind": "cron", "expr": "0 8 * * *", "tz": "Europe/Madrid"}}
        result = _cron_format_schedule(job)
        assert "0 8 * * *" in result
        assert "Europe/Madrid" in result

    def test_cron_without_tz(self):
        """Cron без timezone — без скобок."""
        job = {"schedule": {"kind": "cron", "expr": "0 3 * * *"}}
        result = _cron_format_schedule(job)
        assert "0 3 * * *" in result
        assert "(" not in result

    def test_cron_no_expr(self):
        """Cron без expr — fallback '?'."""
        job = {"schedule": {"kind": "cron"}}
        result = _cron_format_schedule(job)
        assert "?" in result

    def test_unknown_kind(self):
        """Неизвестный kind — возвращается as-is."""
        job = {"schedule": {"kind": "interval"}}
        assert _cron_format_schedule(job) == "interval"

    def test_no_schedule_key(self):
        """Нет ключа schedule — fallback."""
        job = {}
        result = _cron_format_schedule(job)
        assert result == "unknown"


# ---------------------------------------------------------------------------
# _cron_format_last_status
# ---------------------------------------------------------------------------


class TestCronFormatLastStatus:
    """Тесты форматирования последнего статуса job."""

    def test_ok_no_errors(self):
        """Статус ok, нет ошибок — без ⚠️."""
        job = {"state": {"lastStatus": "ok", "consecutiveErrors": 0}}
        result = _cron_format_last_status(job)
        assert result == "ok"
        assert "⚠️" not in result

    def test_error_with_consecutive(self):
        """Статус error + 2 ошибки подряд — добавляется ⚠️."""
        job = {"state": {"lastStatus": "error", "consecutiveErrors": 2}}
        result = _cron_format_last_status(job)
        assert "error" in result
        assert "⚠️" in result
        assert "2" in result

    def test_fallback_lastrunstatus(self):
        """lastStatus отсутствует, используется lastRunStatus."""
        job = {"state": {"lastRunStatus": "ok", "consecutiveErrors": 0}}
        result = _cron_format_last_status(job)
        assert "ok" in result

    def test_no_state(self):
        """Нет поля state — дефолт '—'."""
        result = _cron_format_last_status({})
        assert "—" in result

    def test_empty_state(self):
        """Пустой state — дефолт '—'."""
        result = _cron_format_last_status({"state": {}})
        assert "—" in result


# ---------------------------------------------------------------------------
# _cron_read_jobs
# ---------------------------------------------------------------------------


class TestCronReadJobs:
    """Тесты чтения jobs.json."""

    def test_reads_valid_file(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
        """Корректный jobs.json — возвращает список jobs."""
        cron_dir = tmp_path / ".openclaw" / "cron"
        cron_dir.mkdir(parents=True)
        (cron_dir / "jobs.json").write_text(json.dumps(_JOBS_JSON), encoding="utf-8")
        monkeypatch.setattr(pathlib.Path, "home", staticmethod(lambda: tmp_path))

        result = _cron_read_jobs()
        assert len(result) == 2
        assert result[0]["name"] == "Mercadona Restock"

    def test_missing_file_returns_empty(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Файл не существует — возвращает пустой список."""
        monkeypatch.setattr(pathlib.Path, "home", staticmethod(lambda: tmp_path))
        result = _cron_read_jobs()
        assert result == []

    def test_bad_json_returns_empty(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
        """Повреждённый JSON — возвращает пустой список."""
        cron_dir = tmp_path / ".openclaw" / "cron"
        cron_dir.mkdir(parents=True)
        (cron_dir / "jobs.json").write_text("NOT JSON", encoding="utf-8")
        monkeypatch.setattr(pathlib.Path, "home", staticmethod(lambda: tmp_path))
        result = _cron_read_jobs()
        assert result == []

    def test_jobs_key_missing(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
        """jobs.json без ключа 'jobs' — возвращает пустой список."""
        cron_dir = tmp_path / ".openclaw" / "cron"
        cron_dir.mkdir(parents=True)
        (cron_dir / "jobs.json").write_text(json.dumps({"version": 1}), encoding="utf-8")
        monkeypatch.setattr(pathlib.Path, "home", staticmethod(lambda: tmp_path))
        result = _cron_read_jobs()
        assert result == []


# ---------------------------------------------------------------------------
# _cron_write_jobs
# ---------------------------------------------------------------------------


class TestCronWriteJobs:
    """Тесты записи jobs.json."""

    def test_writes_valid_payload(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
        """Корректные jobs записываются с version=1."""
        cron_dir = tmp_path / ".openclaw" / "cron"
        cron_dir.mkdir(parents=True)
        monkeypatch.setattr(pathlib.Path, "home", staticmethod(lambda: tmp_path))

        jobs = [_JOB_EVERY]
        _cron_write_jobs(jobs)

        data = json.loads((cron_dir / "jobs.json").read_text(encoding="utf-8"))
        assert data["version"] == 1
        assert len(data["jobs"]) == 1
        assert data["jobs"][0]["name"] == "Mercadona Restock"

    def test_overwrites_existing(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
        """Существующий файл перезаписывается."""
        cron_dir = tmp_path / ".openclaw" / "cron"
        cron_dir.mkdir(parents=True)
        (cron_dir / "jobs.json").write_text(
            json.dumps({"version": 1, "jobs": [_JOB_EVERY, _JOB_CRON_WITH_TZ]}), encoding="utf-8"
        )
        monkeypatch.setattr(pathlib.Path, "home", staticmethod(lambda: tmp_path))

        _cron_write_jobs([_JOB_CRON_NO_TZ])
        data = json.loads((cron_dir / "jobs.json").read_text(encoding="utf-8"))
        assert len(data["jobs"]) == 1
        assert data["jobs"][0]["name"] == "Nightly Diag"


# ---------------------------------------------------------------------------
# Вспомогательная фабрика mock-объектов
# ---------------------------------------------------------------------------


def _make_owner_bot(jobs: list | None = None):
    """Создаёт bot с OWNER-уровнем доступа."""
    profile = SimpleNamespace(level=AccessLevel.OWNER)
    bot = SimpleNamespace(
        _get_access_profile=MagicMock(return_value=profile),
        _get_command_args=MagicMock(return_value=""),
    )
    return bot


def _make_non_owner_bot():
    """Создаёт bot с USER-уровнем доступа."""
    profile = SimpleNamespace(level=AccessLevel.GUEST)
    bot = SimpleNamespace(
        _get_access_profile=MagicMock(return_value=profile),
        _get_command_args=MagicMock(return_value=""),
    )
    return bot


def _make_message(text: str = "!cron") -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        from_user=SimpleNamespace(id=42),
        reply=AsyncMock(),
        edit=AsyncMock(),
    )


# ---------------------------------------------------------------------------
# handle_cron — non-owner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_cron_non_owner_raises():
    """Non-owner получает UserInputError."""
    bot = _make_non_owner_bot()
    bot._get_command_args = MagicMock(return_value="list")
    msg = _make_message("!cron list")
    with pytest.raises(UserInputError):
        await handle_cron(bot, msg)


# ---------------------------------------------------------------------------
# handle_cron list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_cron_list_empty(monkeypatch: pytest.MonkeyPatch):
    """!cron list — нет jobs → сообщение 'не найдены'."""
    bot = _make_owner_bot()
    bot._get_command_args = MagicMock(return_value="list")
    msg = _make_message("!cron list")

    monkeypatch.setattr(
        "src.handlers.command_handlers._cron_read_jobs",
        lambda: [],
    )
    await handle_cron(bot, msg)
    rendered = msg.reply.await_args.args[0]
    assert "не найдены" in rendered.lower() or "Cron jobs" in rendered


@pytest.mark.asyncio
async def test_handle_cron_list_shows_jobs(monkeypatch: pytest.MonkeyPatch):
    """!cron list — отображает все jobs с расписанием и статусом."""
    bot = _make_owner_bot()
    bot._get_command_args = MagicMock(return_value="list")
    msg = _make_message("!cron list")

    monkeypatch.setattr(
        "src.handlers.command_handlers._cron_read_jobs",
        lambda: [_JOB_EVERY, _JOB_CRON_WITH_TZ],
    )
    await handle_cron(bot, msg)
    rendered = msg.reply.await_args.args[0]
    assert "Mercadona Restock" in rendered
    assert "Daily Report" in rendered
    assert "✅" in rendered  # enabled job
    assert "⏸" in rendered  # disabled job


@pytest.mark.asyncio
async def test_handle_cron_list_no_args(monkeypatch: pytest.MonkeyPatch):
    """!cron без субкоманды — то же самое что list."""
    bot = _make_owner_bot()
    bot._get_command_args = MagicMock(return_value="")
    msg = _make_message("!cron")

    monkeypatch.setattr(
        "src.handlers.command_handlers._cron_read_jobs",
        lambda: [_JOB_EVERY],
    )
    await handle_cron(bot, msg)
    rendered = msg.reply.await_args.args[0]
    assert "Mercadona Restock" in rendered


# ---------------------------------------------------------------------------
# handle_cron status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_cron_status_counts(monkeypatch: pytest.MonkeyPatch):
    """!cron status — корректный подсчёт total/enabled/errors."""
    bot = _make_owner_bot()
    bot._get_command_args = MagicMock(return_value="status")
    msg = _make_message("!cron status")

    jobs = [_JOB_EVERY, _JOB_CRON_WITH_TZ, _JOB_CRON_NO_TZ, _JOB_EVERY_MIN]
    monkeypatch.setattr("src.handlers.command_handlers._cron_read_jobs", lambda: jobs)
    await handle_cron(bot, msg)
    rendered = msg.reply.await_args.args[0]
    assert "4" in rendered  # total
    assert "2" in rendered  # enabled (EVERY + NIGHTLY_DIAG)
    assert "1" in rendered  # errors (DAILY_REPORT has consecutiveErrors=2)


@pytest.mark.asyncio
async def test_handle_cron_status_empty(monkeypatch: pytest.MonkeyPatch):
    """!cron status — без jobs показывает нули."""
    bot = _make_owner_bot()
    bot._get_command_args = MagicMock(return_value="status")
    msg = _make_message("!cron status")

    monkeypatch.setattr("src.handlers.command_handlers._cron_read_jobs", lambda: [])
    await handle_cron(bot, msg)
    rendered = msg.reply.await_args.args[0]
    assert "0" in rendered


# ---------------------------------------------------------------------------
# handle_cron enable / disable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_cron_enable_no_arg_raises():
    """!cron enable без имени → UserInputError."""
    bot = _make_owner_bot()
    bot._get_command_args = MagicMock(return_value="enable")
    msg = _make_message("!cron enable")
    with pytest.raises(UserInputError):
        await handle_cron(bot, msg)


@pytest.mark.asyncio
async def test_handle_cron_disable_no_arg_raises():
    """!cron disable без имени → UserInputError."""
    bot = _make_owner_bot()
    bot._get_command_args = MagicMock(return_value="disable")
    msg = _make_message("!cron disable")
    with pytest.raises(UserInputError):
        await handle_cron(bot, msg)


@pytest.mark.asyncio
async def test_handle_cron_enable_job_not_found(monkeypatch: pytest.MonkeyPatch):
    """!cron enable <unknown> → UserInputError."""
    bot = _make_owner_bot()
    bot._get_command_args = MagicMock(return_value="enable Unknown Job")
    msg = _make_message("!cron enable Unknown Job")

    monkeypatch.setattr("src.handlers.command_handlers._cron_read_jobs", lambda: [_JOB_EVERY])
    with pytest.raises(UserInputError):
        await handle_cron(bot, msg)


@pytest.mark.asyncio
async def test_handle_cron_enable_by_name_success(monkeypatch: pytest.MonkeyPatch):
    """!cron enable <name> — CLI успешен → сообщение '✅ включён'."""
    bot = _make_owner_bot()
    bot._get_command_args = MagicMock(return_value="enable Daily Report")
    msg = _make_message("!cron enable Daily Report")

    monkeypatch.setattr(
        "src.handlers.command_handlers._cron_read_jobs", lambda: [_JOB_CRON_WITH_TZ]
    )
    monkeypatch.setattr(
        "src.handlers.command_handlers._cron_run_openclaw",
        AsyncMock(return_value=(True, "enabled")),
    )
    await handle_cron(bot, msg)
    rendered = msg.reply.await_args.args[0]
    assert "✅" in rendered
    assert "Daily Report" in rendered


@pytest.mark.asyncio
async def test_handle_cron_disable_by_id_success(monkeypatch: pytest.MonkeyPatch):
    """!cron disable <id> — CLI успешен → сообщение '⏸ выключен'."""
    bot = _make_owner_bot()
    bot._get_command_args = MagicMock(return_value="disable job-every-1")
    msg = _make_message("!cron disable job-every-1")

    monkeypatch.setattr("src.handlers.command_handlers._cron_read_jobs", lambda: [_JOB_EVERY])
    monkeypatch.setattr(
        "src.handlers.command_handlers._cron_run_openclaw",
        AsyncMock(return_value=(True, "disabled")),
    )
    await handle_cron(bot, msg)
    rendered = msg.reply.await_args.args[0]
    assert "⏸" in rendered
    assert "Mercadona Restock" in rendered


@pytest.mark.asyncio
async def test_handle_cron_enable_cli_fails_direct_patch(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """
    !cron enable — CLI вернул False (gateway offline) →
    патчим jobs.json напрямую и сообщаем пользователю.
    """
    cron_dir = tmp_path / ".openclaw" / "cron"
    cron_dir.mkdir(parents=True)
    initial = {"version": 1, "jobs": [dict(_JOB_CRON_WITH_TZ)]}
    (cron_dir / "jobs.json").write_text(json.dumps(initial), encoding="utf-8")
    monkeypatch.setattr(pathlib.Path, "home", staticmethod(lambda: tmp_path))

    bot = _make_owner_bot()
    bot._get_command_args = MagicMock(return_value="enable Daily Report")
    msg = _make_message("!cron enable Daily Report")

    monkeypatch.setattr(
        "src.handlers.command_handlers._cron_run_openclaw",
        AsyncMock(return_value=(False, "gateway not responding")),
    )
    await handle_cron(bot, msg)
    rendered = msg.reply.await_args.args[0]
    assert "✅" in rendered
    assert "gateway offline" in rendered.lower() or "direct patch" in rendered.lower()

    # Проверяем, что enabled стал True в файле
    data = json.loads((cron_dir / "jobs.json").read_text(encoding="utf-8"))
    assert data["jobs"][0]["enabled"] is True


# ---------------------------------------------------------------------------
# handle_cron run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_cron_run_no_arg_raises():
    """!cron run без имени → UserInputError."""
    bot = _make_owner_bot()
    bot._get_command_args = MagicMock(return_value="run")
    msg = _make_message("!cron run")
    with pytest.raises(UserInputError):
        await handle_cron(bot, msg)


@pytest.mark.asyncio
async def test_handle_cron_run_job_not_found(monkeypatch: pytest.MonkeyPatch):
    """!cron run <unknown> → UserInputError."""
    bot = _make_owner_bot()
    bot._get_command_args = MagicMock(return_value="run Nonexistent")
    msg = _make_message("!cron run Nonexistent")

    monkeypatch.setattr("src.handlers.command_handlers._cron_read_jobs", lambda: [_JOB_EVERY])
    with pytest.raises(UserInputError):
        await handle_cron(bot, msg)


@pytest.mark.asyncio
async def test_handle_cron_run_success(monkeypatch: pytest.MonkeyPatch):
    """!cron run <name> — CLI успешен → '✅ запущен'."""
    bot = _make_owner_bot()
    bot._get_command_args = MagicMock(return_value="run Mercadona Restock")
    msg = _make_message("!cron run Mercadona Restock")

    monkeypatch.setattr("src.handlers.command_handlers._cron_read_jobs", lambda: [_JOB_EVERY])
    monkeypatch.setattr(
        "src.handlers.command_handlers._cron_run_openclaw",
        AsyncMock(return_value=(True, "job started")),
    )
    await handle_cron(bot, msg)
    # reply — это промежуточное "⏳ запускаю"
    # edit — финальный результат
    rendered = msg.reply.return_value.edit.await_args.args[0]
    assert "✅" in rendered
    assert "Mercadona Restock" in rendered


@pytest.mark.asyncio
async def test_handle_cron_run_failure(monkeypatch: pytest.MonkeyPatch):
    """!cron run — CLI вернул ошибку → '❌ Ошибка'."""
    bot = _make_owner_bot()
    bot._get_command_args = MagicMock(return_value="run Daily Report")
    msg = _make_message("!cron run Daily Report")

    monkeypatch.setattr(
        "src.handlers.command_handlers._cron_read_jobs", lambda: [_JOB_CRON_WITH_TZ]
    )
    monkeypatch.setattr(
        "src.handlers.command_handlers._cron_run_openclaw",
        AsyncMock(return_value=(False, "gateway not responding")),
    )
    await handle_cron(bot, msg)
    rendered = msg.reply.return_value.edit.await_args.args[0]
    assert "❌" in rendered


@pytest.mark.asyncio
async def test_handle_cron_run_by_id(monkeypatch: pytest.MonkeyPatch):
    """!cron run — поиск по id (не имени)."""
    bot = _make_owner_bot()
    bot._get_command_args = MagicMock(return_value="run job-cron-2")
    msg = _make_message("!cron run job-cron-2")

    monkeypatch.setattr(
        "src.handlers.command_handlers._cron_read_jobs", lambda: [_JOB_CRON_WITH_TZ]
    )
    monkeypatch.setattr(
        "src.handlers.command_handlers._cron_run_openclaw",
        AsyncMock(return_value=(True, "")),
    )
    await handle_cron(bot, msg)
    rendered = msg.reply.return_value.edit.await_args.args[0]
    assert "✅" in rendered


# ---------------------------------------------------------------------------
# handle_cron: неизвестная субкоманда → справка
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_cron_unknown_subcommand_shows_help(monkeypatch: pytest.MonkeyPatch):
    """!cron foo — показывает справку со всеми командами."""
    bot = _make_owner_bot()
    bot._get_command_args = MagicMock(return_value="foo")
    msg = _make_message("!cron foo")

    await handle_cron(bot, msg)
    rendered = msg.reply.await_args.args[0]
    assert "list" in rendered
    assert "enable" in rendered
    assert "disable" in rendered
    assert "run" in rendered
    assert "status" in rendered


# ---------------------------------------------------------------------------
# Русские алиасы субкоманд
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_cron_status_alias_stat(monkeypatch: pytest.MonkeyPatch):
    """!cron stat — алиас для status."""
    bot = _make_owner_bot()
    bot._get_command_args = MagicMock(return_value="stat")
    msg = _make_message("!cron stat")

    monkeypatch.setattr("src.handlers.command_handlers._cron_read_jobs", lambda: [])
    await handle_cron(bot, msg)
    rendered = msg.reply.await_args.args[0]
    assert "Cron" in rendered or "статус" in rendered.lower()


@pytest.mark.asyncio
async def test_handle_cron_enable_ru_alias(monkeypatch: pytest.MonkeyPatch):
    """!cron вкл <name> — русский алиас enable."""
    bot = _make_owner_bot()
    bot._get_command_args = MagicMock(return_value="вкл Daily Report")
    msg = _make_message("!cron вкл Daily Report")

    monkeypatch.setattr(
        "src.handlers.command_handlers._cron_read_jobs", lambda: [_JOB_CRON_WITH_TZ]
    )
    monkeypatch.setattr(
        "src.handlers.command_handlers._cron_run_openclaw",
        AsyncMock(return_value=(True, "enabled")),
    )
    await handle_cron(bot, msg)
    rendered = msg.reply.await_args.args[0]
    assert "✅" in rendered


@pytest.mark.asyncio
async def test_handle_cron_disable_ru_alias(monkeypatch: pytest.MonkeyPatch):
    """!cron выкл <name> — русский алиас disable."""
    bot = _make_owner_bot()
    bot._get_command_args = MagicMock(return_value="выкл Mercadona Restock")
    msg = _make_message("!cron выкл Mercadona Restock")

    monkeypatch.setattr("src.handlers.command_handlers._cron_read_jobs", lambda: [_JOB_EVERY])
    monkeypatch.setattr(
        "src.handlers.command_handlers._cron_run_openclaw",
        AsyncMock(return_value=(True, "disabled")),
    )
    await handle_cron(bot, msg)
    rendered = msg.reply.await_args.args[0]
    assert "⏸" in rendered
