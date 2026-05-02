# -*- coding: utf-8 -*-
"""
Тесты многостратегийного Telegram peer-резолвера.

Покрываем:
1) числовой ID возвращается мгновенно (strategy=numeric_id)
2) "me" возвращается мгновенно (strategy=numeric_id, peer_id="me")
3) @username нормализуется (@ убирается перед передачей в resolve_peer)
4) t.me/ ссылка — извлекается username
5) стратегия resolve_peer — успех через mock client.resolve_peer
6) стратегия get_users — fallback когда resolve_peer падает
7) стратегия dialog_scan — fallback когда обе первые падают
8) все стратегии исчерпаны → ok=False, error_code=PEER_NOT_FOUND, tried_strategies
9) suggestions заполнены при неудаче
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.telegram_resolver import (
    _extract_tme_username,
    _is_username,
    _strip_at,
    resolve_peer,
)


# ---------------------------------------------------------------------------
# Вспомогательные builder'ы mock-объектов
# ---------------------------------------------------------------------------

def _make_input_peer(user_id: int = 42) -> MagicMock:
    peer = MagicMock()
    peer.user_id = user_id
    peer.channel_id = None
    peer.chat_id = None
    return peer


def _make_user(user_id: int = 42, username: str = "p0lrd",
               first_name: str = "Pavel", last_name: str = "") -> MagicMock:
    user = MagicMock()
    user.id = user_id
    user.username = username
    user.first_name = first_name
    user.last_name = last_name
    return user


def _make_dialog(chat_username: str | None, chat_id: int,
                 title: str = "", first_name: str = "") -> MagicMock:
    chat = MagicMock()
    chat.id = chat_id
    chat.username = chat_username
    chat.title = title
    chat.first_name = first_name
    dialog = MagicMock()
    dialog.chat = chat
    return dialog


def _client_all_fail() -> MagicMock:
    """Клиент, у которого все стратегии падают."""
    client = MagicMock()
    client.resolve_peer = AsyncMock(side_effect=Exception("PEER_NOT_FOUND"))
    client.get_users = AsyncMock(side_effect=Exception("USER_NOT_FOUND"))

    async def _empty_dialogs(*args, **kwargs):
        return
        yield  # сделать async generator

    client.get_dialogs = MagicMock(return_value=_empty_dialogs())
    return client


# ---------------------------------------------------------------------------
# Юнит-тесты вспомогательных функций
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_strip_at(self):
        assert _strip_at("@p0lrd") == "p0lrd"
        assert _strip_at("p0lrd") == "p0lrd"

    def test_is_username_valid(self):
        assert _is_username("p0lrd") is True
        assert _is_username("@p0lrd") is True
        assert _is_username("UserAbc123") is True

    def test_is_username_rejects_numeric(self):
        assert _is_username("12345") is False
        assert _is_username("-100123456") is False

    def test_is_username_rejects_short(self):
        # Telegram username минимум 5 символов (4+ после первой буквы)
        assert _is_username("abc") is False

    def test_extract_tme_username(self):
        assert _extract_tme_username("https://t.me/p0lrd") == "p0lrd"
        assert _extract_tme_username("https://telegram.me/p0lrd") == "p0lrd"
        assert _extract_tme_username("http://t.me/p0lrd") == "p0lrd"
        assert _extract_tme_username("https://t.me/p0lrd/123") == "p0lrd"
        assert _extract_tme_username("not_a_link") is None


# ---------------------------------------------------------------------------
# Основные тесты resolve_peer
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_numeric_id_returns_instantly():
    """Числовой ID не делает никаких Telegram-запросов."""
    client = MagicMock()
    result = await resolve_peer(client, "12345")

    assert result["ok"] is True
    assert result["peer_id"] == 12345
    assert result["strategy_used"] == "numeric_id"
    # Ни один async-метод не должен был вызываться
    client.resolve_peer.assert_not_called() if hasattr(client.resolve_peer, "assert_not_called") else None


@pytest.mark.asyncio
async def test_negative_numeric_id_returns_instantly():
    """Отрицательный chat_id (группы/каналы) тоже числовой."""
    client = MagicMock()
    result = await resolve_peer(client, "-1001234567890")

    assert result["ok"] is True
    assert result["peer_id"] == -1001234567890
    assert result["strategy_used"] == "numeric_id"


@pytest.mark.asyncio
async def test_me_returns_instantly():
    """"me" — специальный токен Pyrogram, возвращается без запросов."""
    client = MagicMock()
    result = await resolve_peer(client, "me")

    assert result["ok"] is True
    assert result["peer_id"] == "me"
    assert result["strategy_used"] == "numeric_id"


@pytest.mark.asyncio
async def test_me_case_insensitive():
    """ME / Me тоже распознаётся."""
    client = MagicMock()
    for variant in ("ME", "Me", "mE"):
        result = await resolve_peer(client, variant)
        assert result["ok"] is True
        assert result["peer_id"] == "me"


@pytest.mark.asyncio
async def test_username_normalized_strips_at():
    """@p0lrd → resolve_peer вызывается с "@p0lrd" (@ добавляется, если не было)."""
    peer = _make_input_peer(user_id=77)
    client = MagicMock()
    client.resolve_peer = AsyncMock(return_value=peer)

    result = await resolve_peer(client, "@p0lrd")

    assert result["ok"] is True
    assert result["peer_id"] == 77
    assert result["strategy_used"] == "resolve_peer"
    # resolve_peer должен был быть вызван с "@p0lrd"
    client.resolve_peer.assert_called_once_with("@p0lrd")


@pytest.mark.asyncio
async def test_username_without_at_gets_at_prepended():
    """p0lrd (без @) → resolve_peer вызывается с "@p0lrd"."""
    peer = _make_input_peer(user_id=99)
    client = MagicMock()
    client.resolve_peer = AsyncMock(return_value=peer)

    result = await resolve_peer(client, "p0lrd")

    assert result["ok"] is True
    client.resolve_peer.assert_called_once_with("@p0lrd")


@pytest.mark.asyncio
async def test_tme_link_extracted():
    """https://t.me/p0lrd → извлекает 'p0lrd', вызывает resolve_peer(@p0lrd)."""
    peer = _make_input_peer(user_id=55)
    client = MagicMock()
    client.resolve_peer = AsyncMock(return_value=peer)

    result = await resolve_peer(client, "https://t.me/p0lrd")

    assert result["ok"] is True
    assert result["peer_id"] == 55
    client.resolve_peer.assert_called_once_with("@p0lrd")


@pytest.mark.asyncio
async def test_resolve_peer_strategy_success():
    """Стратегия 1 (resolve_peer) успешна — возвращает peer_id из InputPeer."""
    peer = _make_input_peer(user_id=123)
    client = MagicMock()
    client.resolve_peer = AsyncMock(return_value=peer)

    result = await resolve_peer(client, "@testuser")

    assert result["ok"] is True
    assert result["peer_id"] == 123
    assert result["strategy_used"] == "resolve_peer"
    assert "tried_strategies" not in result or result.get("error_code") is None


@pytest.mark.asyncio
async def test_resolve_peer_uses_channel_id():
    """Если у InputPeer нет user_id, берётся channel_id."""
    peer = MagicMock()
    peer.user_id = None
    peer.channel_id = 987
    peer.chat_id = None

    client = MagicMock()
    client.resolve_peer = AsyncMock(return_value=peer)

    result = await resolve_peer(client, "@somechannel")

    assert result["ok"] is True
    assert result["peer_id"] == 987


@pytest.mark.asyncio
async def test_get_users_fallback():
    """Стратегия 2 (get_users) вызывается когда resolve_peer падает."""
    user = _make_user(user_id=200, username="p0lrd", first_name="Pavel")
    client = MagicMock()
    client.resolve_peer = AsyncMock(side_effect=Exception("peer not cached"))
    client.get_users = AsyncMock(return_value=user)

    result = await resolve_peer(client, "@p0lrd")

    assert result["ok"] is True
    assert result["peer_id"] == 200
    assert result["strategy_used"] == "get_users"
    assert result["username"] == "p0lrd"


@pytest.mark.asyncio
async def test_get_users_fallback_list_response():
    """get_users может вернуть список — резолвер берёт первый элемент."""
    user = _make_user(user_id=201, username="p0lrd")
    client = MagicMock()
    client.resolve_peer = AsyncMock(side_effect=Exception("fail"))
    client.get_users = AsyncMock(return_value=[user])

    result = await resolve_peer(client, "@p0lrd")

    assert result["ok"] is True
    assert result["peer_id"] == 201


@pytest.mark.asyncio
async def test_dialog_scan_fallback():
    """Стратегия 3 (dialog_scan) вызывается когда resolve_peer и get_users падают."""
    dialog = _make_dialog(chat_username="p0lrd", chat_id=300, first_name="Pavel")

    async def _dialogs(*args, **kwargs):
        yield dialog

    client = MagicMock()
    client.resolve_peer = AsyncMock(side_effect=Exception("fail"))
    client.get_users = AsyncMock(side_effect=Exception("fail"))
    client.get_dialogs = MagicMock(return_value=_dialogs())

    result = await resolve_peer(client, "@p0lrd")

    assert result["ok"] is True
    assert result["peer_id"] == 300
    assert result["strategy_used"] == "dialog_scan"


@pytest.mark.asyncio
async def test_dialog_scan_title_match():
    """dialog_scan находит по title (нечёткое вхождение)."""
    dialog = _make_dialog(chat_username=None, chat_id=400, title="Pavel Durov Fan")

    async def _dialogs(*args, **kwargs):
        yield dialog

    client = MagicMock()
    client.resolve_peer = AsyncMock(side_effect=Exception("fail"))
    client.get_users = AsyncMock(side_effect=Exception("fail"))
    client.get_dialogs = MagicMock(return_value=_dialogs())

    # Ищем по части title
    result = await resolve_peer(client, "Pavel Durov Fan")

    assert result["ok"] is True
    assert result["peer_id"] == 400
    assert result["strategy_used"] == "dialog_scan"


@pytest.mark.asyncio
async def test_all_strategies_fail():
    """Если все стратегии провалились → ok=False, PEER_NOT_FOUND, tried_strategies."""
    async def _empty_dialogs(*args, **kwargs):
        return
        yield  # async generator

    client = MagicMock()
    client.resolve_peer = AsyncMock(side_effect=Exception("fail"))
    client.get_users = AsyncMock(side_effect=Exception("fail"))
    client.get_dialogs = MagicMock(return_value=_empty_dialogs())

    result = await resolve_peer(client, "@nonexistentuser")

    assert result["ok"] is False
    assert result["error_code"] == "PEER_NOT_FOUND"
    assert result["peer_id"] is None
    assert result["strategy_used"] is None
    assert isinstance(result["tried_strategies"], list)
    assert len(result["tried_strategies"]) > 0


@pytest.mark.asyncio
async def test_all_strategies_fail_tried_strategies_order():
    """tried_strategies содержит все три стратегии в правильном порядке."""
    async def _empty_dialogs(*args, **kwargs):
        return
        yield

    client = MagicMock()
    client.resolve_peer = AsyncMock(side_effect=Exception("fail"))
    client.get_users = AsyncMock(side_effect=Exception("fail"))
    client.get_dialogs = MagicMock(return_value=_empty_dialogs())

    result = await resolve_peer(client, "@testuser")

    tried = result["tried_strategies"]
    assert "resolve_peer" in tried
    assert "get_users" in tried
    assert "dialog_scan" in tried
    # Порядок: resolve_peer первым
    assert tried.index("resolve_peer") < tried.index("dialog_scan")


@pytest.mark.asyncio
async def test_suggestions_on_failure_username():
    """При неудаче для username-цели suggestions содержат полезные подсказки."""
    async def _empty_dialogs(*args, **kwargs):
        return
        yield

    client = MagicMock()
    client.resolve_peer = AsyncMock(side_effect=Exception("fail"))
    client.get_users = AsyncMock(side_effect=Exception("fail"))
    client.get_dialogs = MagicMock(return_value=_empty_dialogs())

    result = await resolve_peer(client, "@p0lrd")

    assert result["ok"] is False
    suggestions = result["suggestions"]
    assert isinstance(suggestions, list)
    assert len(suggestions) > 0
    # Хотя бы одна подсказка упоминает username или peer_id
    combined = " ".join(suggestions).lower()
    assert "p0lrd" in combined or "peer_id" in combined or "username" in combined


@pytest.mark.asyncio
async def test_suggestions_on_failure_non_username():
    """Для нераспознанных строк suggestions предлагают правильный формат."""
    async def _empty_dialogs(*args, **kwargs):
        return
        yield

    client = MagicMock()
    client.resolve_peer = AsyncMock(side_effect=Exception("fail"))
    client.get_users = AsyncMock(side_effect=Exception("fail"))
    client.get_dialogs = MagicMock(return_value=_empty_dialogs())

    # Строка не является username (слишком короткая или не буквенная)
    result = await resolve_peer(client, "xyz")  # < 5 символов → не username

    assert result["ok"] is False
    suggestions = result["suggestions"]
    combined = " ".join(suggestions).lower()
    # Должна быть подсказка про @username или chat_id
    assert "username" in combined or "chat_id" in combined


@pytest.mark.asyncio
async def test_whitespace_stripped_from_target():
    """Пробелы вокруг target обрезаются."""
    peer = _make_input_peer(user_id=50)
    client = MagicMock()
    client.resolve_peer = AsyncMock(return_value=peer)

    result = await resolve_peer(client, "  @p0lrd  ")

    assert result["ok"] is True
    assert result["peer_id"] == 50


@pytest.mark.asyncio
async def test_int_target_converted_to_str():
    """resolve_peer принимает int — конвертируется в строку."""
    client = MagicMock()
    result = await resolve_peer(client, 99999)  # type: ignore[arg-type]

    assert result["ok"] is True
    assert result["peer_id"] == 99999
    assert result["strategy_used"] == "numeric_id"
