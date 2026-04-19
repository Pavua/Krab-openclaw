# -*- coding: utf-8 -*-
"""
Тесты команды !whois.

Покрываем:
- _parse_whois_output: парсинг ключевых полей из реального вывода whois
- handle_whois: пустой аргумент -> UserInputError
- handle_whois: успешный lookup с замоканным subprocess
- handle_whois: домен не найден (No match)
- handle_whois: timeout subprocess
- handle_whois: whois не установлен (FileNotFoundError)
- handle_whois: URL в аргументе -> домен извлекается корректно
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import (
    _parse_whois_output,
    handle_whois,
)

# ---------------------------------------------------------------------------
# Образцы реального вывода whois
# ---------------------------------------------------------------------------

_WHOIS_EXAMPLE_COM = (
    "Domain Name: EXAMPLE.COM\n"
    "Registry Domain ID: 2336799_DOMAIN_COM-VRSN\n"
    "Creation Date: 1995-08-14T04:00:00Z\n"
    "Registry Expiry Date: 2028-08-13T04:00:00Z\n"
    "Registrar: RESERVED-Internet Assigned Numbers Authority\n"
    "Name Server: A.IANA-SERVERS.NET\n"
    "Name Server: B.IANA-SERVERS.NET\n"
    "DNSSEC: signedDelegation\n"
)

_WHOIS_GODADDY = (
    "Domain Name: OPENAI.COM\n"
    "Creation Date: 2015-04-01T00:00:00Z\n"
    "Expiration Date: 2026-04-01T00:00:00Z\n"
    "Registrar: GoDaddy.com, LLC\n"
    "Name Server: ns73.domaincontrol.com\n"
    "Name Server: ns74.domaincontrol.com\n"
)

_WHOIS_NOT_FOUND = "No match for domain NONEXISTENT12345XYZ.COM."

_WHOIS_RUSSIAN_STYLE = (
    "domain: EXAMPLE.RU\n"
    "nserver: ns1.example.ru\n"
    "nserver: ns2.example.ru\n"
    "created: 2010-05-01T00:00:00Z\n"
    "paid-till: 2025-05-01T00:00:00Z\n"
)


# ---------------------------------------------------------------------------
# Тесты _parse_whois_output
# ---------------------------------------------------------------------------


class TestParseWhoisOutput:
    """Юнит-тесты парсера whois-вывода."""

    def test_example_com_registrar(self) -> None:
        fields = _parse_whois_output(_WHOIS_EXAMPLE_COM)
        assert "RESERVED" in str(fields.get("registrar", ""))

    def test_example_com_created_date(self) -> None:
        fields = _parse_whois_output(_WHOIS_EXAMPLE_COM)
        assert fields.get("created") == "1995-08-14"

    def test_example_com_expires_date(self) -> None:
        fields = _parse_whois_output(_WHOIS_EXAMPLE_COM)
        assert fields.get("expires") == "2028-08-13"

    def test_example_com_nameservers(self) -> None:
        fields = _parse_whois_output(_WHOIS_EXAMPLE_COM)
        ns = fields.get("nameservers", [])
        assert isinstance(ns, list)
        assert len(ns) == 2
        assert "a.iana-servers.net" in ns
        assert "b.iana-servers.net" in ns

    def test_nameservers_deduplicated(self) -> None:
        raw = "Name Server: ns1.example.com\nName Server: ns1.example.com\n"
        fields = _parse_whois_output(raw)
        ns = fields.get("nameservers", [])
        assert ns.count("ns1.example.com") == 1

    def test_godaddy_registrar(self) -> None:
        fields = _parse_whois_output(_WHOIS_GODADDY)
        assert "GoDaddy" in str(fields.get("registrar", ""))

    def test_godaddy_dates(self) -> None:
        fields = _parse_whois_output(_WHOIS_GODADDY)
        assert fields.get("created") == "2015-04-01"
        assert fields.get("expires") == "2026-04-01"

    def test_russian_registry_nserver(self) -> None:
        fields = _parse_whois_output(_WHOIS_RUSSIAN_STYLE)
        ns = fields.get("nameservers", [])
        assert "ns1.example.ru" in ns

    def test_russian_registry_paid_till(self) -> None:
        fields = _parse_whois_output(_WHOIS_RUSSIAN_STYLE)
        assert fields.get("expires") == "2025-05-01"

    def test_empty_raw_returns_empty(self) -> None:
        fields = _parse_whois_output("")
        assert fields.get("registrar") is None
        assert fields.get("created") is None
        assert fields.get("nameservers") == []

    def test_date_without_time_not_truncated(self) -> None:
        raw = "Creation Date: 2020-01-15\n"
        fields = _parse_whois_output(raw)
        assert fields.get("created") == "2020-01-15"

    def test_nameservers_trailing_dot_stripped(self) -> None:
        raw = "Name Server: ns1.example.com.\n"
        fields = _parse_whois_output(raw)
        ns = fields.get("nameservers", [])
        assert "ns1.example.com" in ns


# ---------------------------------------------------------------------------
# Фабрика фиктивных объектов bot/message
# ---------------------------------------------------------------------------


def _make_bot(args: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        _get_command_args=lambda msg: args,
    )


def _make_message() -> SimpleNamespace:
    status = SimpleNamespace(edit=AsyncMock())
    msg = SimpleNamespace(
        chat=SimpleNamespace(id=100),
        reply=AsyncMock(return_value=status),
        _status=status,
    )
    return msg


# ---------------------------------------------------------------------------
# Вспомогательные заглушки subprocess
# ---------------------------------------------------------------------------


def _make_fake_proc(raw_output: str, returncode: int = 0):
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(raw_output.encode(), b""))
    proc.terminate = MagicMock()
    return proc


# ---------------------------------------------------------------------------
# Тесты handle_whois
# ---------------------------------------------------------------------------


class TestHandleWhois:
    @pytest.mark.asyncio
    async def test_empty_args_raises_user_input_error(self) -> None:
        """Пустой аргумент вызывает UserInputError."""
        bot = _make_bot("")
        message = _make_message()
        with pytest.raises(UserInputError) as exc_info:
            await handle_whois(bot, message)
        assert "whois" in exc_info.value.user_message.lower()

    @pytest.mark.asyncio
    async def test_url_stripped_to_domain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """URL в аргументе преобразуется в домен."""
        bot = _make_bot("https://example.com/path?q=1")
        message = _make_message()

        captured: list[str] = []

        async def _fake_exec(*args, **kwargs):
            captured.append(args[1])
            return _make_fake_proc(_WHOIS_EXAMPLE_COM)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
        monkeypatch.setattr(
            "src.handlers.command_handlers.clean_subprocess_env",
            lambda: None,
            raising=False,
        )

        await handle_whois(bot, message)
        assert captured == ["example.com"]

    @pytest.mark.asyncio
    async def test_successful_lookup_formats_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Успешный lookup: reply содержит все 4 ключевых поля."""
        bot = _make_bot("example.com")
        message = _make_message()

        async def _fake_exec(*args, **kwargs):
            return _make_fake_proc(_WHOIS_EXAMPLE_COM)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
        monkeypatch.setattr(
            "src.handlers.command_handlers.clean_subprocess_env",
            lambda: None,
            raising=False,
        )

        await handle_whois(bot, message)

        edit_text = message._status.edit.await_args.args[0]
        assert "Registrar:" in edit_text
        assert "Created:" in edit_text
        assert "Expires:" in edit_text
        assert "Nameservers:" in edit_text
        assert "example.com" in edit_text

    @pytest.mark.asyncio
    async def test_dates_without_time_component(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Даты в ответе без временной части (только YYYY-MM-DD)."""
        bot = _make_bot("example.com")
        message = _make_message()

        async def _fake_exec(*args, **kwargs):
            return _make_fake_proc(_WHOIS_EXAMPLE_COM)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
        monkeypatch.setattr(
            "src.handlers.command_handlers.clean_subprocess_env",
            lambda: None,
            raising=False,
        )

        await handle_whois(bot, message)
        edit_text = message._status.edit.await_args.args[0]
        assert "T00:00:00Z" not in edit_text
        assert "1995-08-14" in edit_text
        assert "2028-08-13" in edit_text

    @pytest.mark.asyncio
    async def test_not_found_domain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """«No match» -> сообщение о ненайденном домене."""
        bot = _make_bot("nonexistent12345xyz.com")
        message = _make_message()

        async def _fake_exec(*args, **kwargs):
            return _make_fake_proc(_WHOIS_NOT_FOUND, returncode=1)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
        monkeypatch.setattr(
            "src.handlers.command_handlers.clean_subprocess_env",
            lambda: None,
            raising=False,
        )

        await handle_whois(bot, message)
        edit_text = message._status.edit.await_args.args[0]
        assert "❌" in edit_text

    @pytest.mark.asyncio
    async def test_timeout_returns_error_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Timeout subprocess -> сообщение об ошибке."""
        bot = _make_bot("slow-domain.com")
        message = _make_message()

        async def _fake_exec(*args, **kwargs):
            proc = MagicMock()
            proc.returncode = None
            proc.communicate = AsyncMock()
            proc.terminate = MagicMock()
            return proc

        async def _fake_wait_for(coro, timeout):
            raise asyncio.TimeoutError

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
        monkeypatch.setattr(asyncio, "wait_for", _fake_wait_for)
        monkeypatch.setattr(
            "src.handlers.command_handlers.clean_subprocess_env",
            lambda: None,
            raising=False,
        )

        await handle_whois(bot, message)
        edit_text = message._status.edit.await_args.args[0]
        assert "❌" in edit_text
        assert "timeout" in edit_text.lower()

    @pytest.mark.asyncio
    async def test_whois_not_installed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FileNotFoundError -> сообщение об отсутствии утилиты whois."""
        bot = _make_bot("example.com")
        message = _make_message()

        async def _no_whois(*args, **kwargs):
            raise FileNotFoundError("whois not found")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _no_whois)
        monkeypatch.setattr(
            "src.handlers.command_handlers.clean_subprocess_env",
            lambda: None,
            raising=False,
        )

        await handle_whois(bot, message)
        edit_text = message._status.edit.await_args.args[0]
        assert "❌" in edit_text
        assert "whois" in edit_text.lower()

    @pytest.mark.asyncio
    async def test_missing_fields_show_dash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Если поле отсутствует в whois-выводе, показывается «-»."""
        bot = _make_bot("minimal.com")
        message = _make_message()
        minimal_raw = "Domain Name: MINIMAL.COM\n"

        async def _fake_exec(*args, **kwargs):
            return _make_fake_proc(minimal_raw)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
        monkeypatch.setattr(
            "src.handlers.command_handlers.clean_subprocess_env",
            lambda: None,
            raising=False,
        )

        await handle_whois(bot, message)
        edit_text = message._status.edit.await_args.args[0]
        assert "Registrar: \u2014" in edit_text
        assert "Created: \u2014" in edit_text
        assert "Expires: \u2014" in edit_text
        assert "Nameservers: \u2014" in edit_text

    @pytest.mark.asyncio
    async def test_sends_status_reply_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Сначала отправляется статусное сообщение, потом edit с результатом."""
        bot = _make_bot("example.com")
        message = _make_message()

        async def _fake_exec(*args, **kwargs):
            return _make_fake_proc(_WHOIS_EXAMPLE_COM)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
        monkeypatch.setattr(
            "src.handlers.command_handlers.clean_subprocess_env",
            lambda: None,
            raising=False,
        )

        await handle_whois(bot, message)

        assert message.reply.called
        assert message._status.edit.called

    @pytest.mark.asyncio
    async def test_nameservers_in_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Nameservers выводятся через запятую."""
        bot = _make_bot("example.com")
        message = _make_message()

        async def _fake_exec(*args, **kwargs):
            return _make_fake_proc(_WHOIS_EXAMPLE_COM)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
        monkeypatch.setattr(
            "src.handlers.command_handlers.clean_subprocess_env",
            lambda: None,
            raising=False,
        )

        await handle_whois(bot, message)
        edit_text = message._status.edit.await_args.args[0]
        assert "a.iana-servers.net" in edit_text
        assert "b.iana-servers.net" in edit_text
