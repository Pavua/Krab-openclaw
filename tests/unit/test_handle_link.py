# -*- coding: utf-8 -*-
"""
Тесты обработчика !link — утилиты для ссылок.

Покрываем:
- _is_short_url: определение коротких доменов
- _format_link_preview: форматирование мета-блока
- _fetch_link_meta: парсинг HTML (title, og:title, description, og:image)
- _expand_url: возврат финального URL из HEAD-редиректа
- handle_link preview: корректный вызов и формат ответа
- handle_link expand: корректный вызов и формат ответа
- handle_link в reply: извлечение первого URL из reply-сообщения
- handle_link без аргументов и без reply: ошибка
- handle_link preview без URL: ошибка
- handle_link expand без URL: ошибка
- handle_link с неизвестной подкомандой: ошибка
- handle_link с прямым URL (без subcommand): preview
- handle_link в reply без ссылок: ошибка
- _fetch_link_meta: только <title> без og
- _fetch_link_meta: og перекрывает title
- _fetch_link_meta: description обрезается до 200 символов в форматере
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.handlers.command_handlers import (
    _fetch_link_meta,
    _format_link_preview,
    _expand_url,
    _is_short_url,
    _URL_RE,
    handle_link,
)
from src.core.exceptions import UserInputError


# ---------------------------------------------------------------------------
# _is_short_url
# ---------------------------------------------------------------------------


def test_is_short_url_known_short_domains():
    assert _is_short_url("https://bit.ly/abc123") is True
    assert _is_short_url("https://t.co/xyzw") is True
    assert _is_short_url("https://tinyurl.com/foobar") is True
    assert _is_short_url("https://vk.cc/abc") is True
    assert _is_short_url("https://clck.ru/abc") is True


def test_is_short_url_full_url_not_short():
    assert _is_short_url("https://example.com/some/long/path") is False
    assert _is_short_url("https://github.com/user/repo") is False
    assert _is_short_url("https://python.org") is False


def test_is_short_url_www_prefix_stripped():
    # www. должен стриппиться
    assert _is_short_url("https://www.bit.ly/abc") is True


def test_is_short_url_malformed():
    # Невалидный URL не должен падать
    result = _is_short_url("not-a-url")
    assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# _URL_RE — паттерн поиска URL
# ---------------------------------------------------------------------------


def test_url_re_finds_http_and_https():
    text = "Смотри https://example.com и http://test.org/path?q=1"
    found = _URL_RE.findall(text)
    assert "https://example.com" in found
    assert "http://test.org/path?q=1" in found


def test_url_re_no_match_plain_text():
    assert _URL_RE.findall("просто текст без ссылок") == []


def test_url_re_first_url_only():
    text = "Ссылка https://first.com и https://second.com"
    found = _URL_RE.findall(text)
    assert found[0] == "https://first.com"


# ---------------------------------------------------------------------------
# _format_link_preview
# ---------------------------------------------------------------------------


def test_format_link_preview_full():
    meta = {
        "title": "Example Page",
        "description": "Some description",
        "image": "https://example.com/og.jpg",
        "final_url": "https://example.com",
    }
    result = _format_link_preview(meta)
    assert "🔗 **Link Preview**" in result
    assert "─────" in result
    assert "Title: Example Page" in result
    assert "Description: Some description" in result
    assert "URL: https://example.com" in result
    assert "Image: https://example.com/og.jpg" in result


def test_format_link_preview_no_image():
    meta = {
        "title": "No Image",
        "description": "",
        "image": "",
        "final_url": "https://example.com",
    }
    result = _format_link_preview(meta)
    assert "Image:" not in result
    assert "Description:" not in result


def test_format_link_preview_description_truncated():
    long_desc = "x" * 300
    meta = {
        "title": "T",
        "description": long_desc,
        "image": "",
        "final_url": "https://example.com",
    }
    result = _format_link_preview(meta)
    # Описание обрезается до 200 символов
    desc_line = [l for l in result.splitlines() if l.startswith("Description:")][0]
    desc_value = desc_line[len("Description: "):]
    assert len(desc_value) <= 203  # 197 + "..."
    assert desc_value.endswith("...")


def test_format_link_preview_no_title():
    meta = {
        "title": "",
        "description": "desc",
        "image": "",
        "final_url": "https://example.com",
    }
    result = _format_link_preview(meta)
    assert "Title:" not in result
    assert "Description: desc" in result


# ---------------------------------------------------------------------------
# _fetch_link_meta — парсинг HTML
# ---------------------------------------------------------------------------


def _make_mock_response(html: str, url: str = "https://example.com") -> MagicMock:
    """Создаёт mock httpx-response с заданным HTML."""
    resp = MagicMock()
    resp.url = url
    resp.text = html
    return resp


@pytest.mark.asyncio
async def test_fetch_link_meta_title_only():
    html = "<html><head><title>My Title</title></head><body></body></html>"
    mock_resp = _make_mock_response(html)

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("src.handlers.command_handlers.httpx.AsyncClient", return_value=mock_client):
        meta = await _fetch_link_meta("https://example.com")

    assert meta["title"] == "My Title"
    assert meta["description"] == ""
    assert meta["image"] == ""


@pytest.mark.asyncio
async def test_fetch_link_meta_og_title_overrides_title():
    html = (
        '<html><head>'
        '<title>Plain Title</title>'
        '<meta property="og:title" content="OG Title" />'
        '</head></html>'
    )
    mock_resp = _make_mock_response(html)
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("src.handlers.command_handlers.httpx.AsyncClient", return_value=mock_client):
        meta = await _fetch_link_meta("https://example.com")

    assert meta["title"] == "OG Title"


@pytest.mark.asyncio
async def test_fetch_link_meta_og_description():
    html = (
        '<html><head>'
        '<meta property="og:description" content="OG Description" />'
        '</head></html>'
    )
    mock_resp = _make_mock_response(html)
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("src.handlers.command_handlers.httpx.AsyncClient", return_value=mock_client):
        meta = await _fetch_link_meta("https://example.com")

    assert meta["description"] == "OG Description"


@pytest.mark.asyncio
async def test_fetch_link_meta_meta_description_fallback():
    html = (
        '<html><head>'
        '<meta name="description" content="Meta Description" />'
        '</head></html>'
    )
    mock_resp = _make_mock_response(html)
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("src.handlers.command_handlers.httpx.AsyncClient", return_value=mock_client):
        meta = await _fetch_link_meta("https://example.com")

    assert meta["description"] == "Meta Description"


@pytest.mark.asyncio
async def test_fetch_link_meta_og_image():
    html = (
        '<html><head>'
        '<meta property="og:image" content="https://example.com/img.jpg" />'
        '</head></html>'
    )
    mock_resp = _make_mock_response(html)
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("src.handlers.command_handlers.httpx.AsyncClient", return_value=mock_client):
        meta = await _fetch_link_meta("https://example.com")

    assert meta["image"] == "https://example.com/img.jpg"


@pytest.mark.asyncio
async def test_fetch_link_meta_final_url_follows_redirect():
    html = "<html><head><title>Redirected</title></head></html>"
    mock_resp = _make_mock_response(html, url="https://final-destination.com/page")
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("src.handlers.command_handlers.httpx.AsyncClient", return_value=mock_client):
        meta = await _fetch_link_meta("https://bit.ly/abc")

    assert meta["final_url"] == "https://final-destination.com/page"


@pytest.mark.asyncio
async def test_fetch_link_meta_empty_page():
    html = "<html><head></head><body></body></html>"
    mock_resp = _make_mock_response(html)
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("src.handlers.command_handlers.httpx.AsyncClient", return_value=mock_client):
        meta = await _fetch_link_meta("https://example.com")

    assert meta["title"] == ""
    assert meta["description"] == ""
    assert meta["image"] == ""


# ---------------------------------------------------------------------------
# _expand_url
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expand_url_follows_redirect():
    mock_resp = MagicMock()
    mock_resp.url = "https://expanded.com/full-url"

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.head = AsyncMock(return_value=mock_resp)

    with patch("src.handlers.command_handlers.httpx.AsyncClient", return_value=mock_client):
        result = await _expand_url("https://bit.ly/short")

    assert result == "https://expanded.com/full-url"


@pytest.mark.asyncio
async def test_expand_url_no_redirect():
    url = "https://example.com/no-redirect"
    mock_resp = MagicMock()
    mock_resp.url = url

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.head = AsyncMock(return_value=mock_resp)

    with patch("src.handlers.command_handlers.httpx.AsyncClient", return_value=mock_client):
        result = await _expand_url(url)

    assert result == url


# ---------------------------------------------------------------------------
# Вспомогательные фикстуры для handle_link
# ---------------------------------------------------------------------------


def _make_bot(args: str = "") -> SimpleNamespace:
    """Создаёт mock бота с _get_command_args."""
    bot = SimpleNamespace(
        _get_command_args=lambda msg: args,
    )
    return bot


def _make_message(reply_text: str | None = None) -> SimpleNamespace:
    """Создаёт mock Message."""
    if reply_text is not None:
        reply_msg = SimpleNamespace(
            text=reply_text,
            caption=None,
        )
    else:
        reply_msg = None

    return SimpleNamespace(
        reply_to_message=reply_msg,
        reply=AsyncMock(),
    )


# ---------------------------------------------------------------------------
# handle_link — тесты на уровне команды
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_link_preview_success():
    """!link preview <URL> → парсит мета и выводит блок."""
    bot = _make_bot("preview https://example.com")
    msg = _make_message()

    meta = {
        "title": "Test Title",
        "description": "Test Desc",
        "image": "",
        "final_url": "https://example.com",
    }

    with patch(
        "src.handlers.command_handlers._fetch_link_meta",
        AsyncMock(return_value=meta),
    ):
        await handle_link(bot, msg)

    assert msg.reply.call_count == 2  # "⏳ Загружаю превью..." + результат
    final_call = msg.reply.call_args_list[-1]
    assert "Link Preview" in final_call[0][0]
    assert "Test Title" in final_call[0][0]


@pytest.mark.asyncio
async def test_handle_link_preview_no_url_raises():
    """!link preview без URL → UserInputError."""
    bot = _make_bot("preview")
    msg = _make_message()

    with pytest.raises(UserInputError) as exc_info:
        await handle_link(bot, msg)
    assert "Укажи URL" in exc_info.value.user_message


@pytest.mark.asyncio
async def test_handle_link_expand_success():
    """!link expand <URL> → разворачивает и выводит финальный URL."""
    bot = _make_bot("expand https://bit.ly/short")
    msg = _make_message()

    with patch(
        "src.handlers.command_handlers._expand_url",
        AsyncMock(return_value="https://real-page.com/full"),
    ):
        await handle_link(bot, msg)

    assert msg.reply.call_count == 2
    final_call = msg.reply.call_args_list[-1]
    text = final_call[0][0]
    assert "Expand" in text
    assert "https://real-page.com/full" in text


@pytest.mark.asyncio
async def test_handle_link_expand_no_change():
    """!link expand — если URL не изменился, выводит особое сообщение."""
    url = "https://example.com/no-redirect"
    bot = _make_bot(f"expand {url}")
    msg = _make_message()

    with patch(
        "src.handlers.command_handlers._expand_url",
        AsyncMock(return_value=url),
    ):
        await handle_link(bot, msg)

    final_call = msg.reply.call_args_list[-1]
    text = final_call[0][0]
    assert "не изменился" in text


@pytest.mark.asyncio
async def test_handle_link_expand_no_url_raises():
    """!link expand без URL → UserInputError."""
    bot = _make_bot("expand")
    msg = _make_message()

    with pytest.raises(UserInputError) as exc_info:
        await handle_link(bot, msg)
    assert "Укажи URL" in exc_info.value.user_message


@pytest.mark.asyncio
async def test_handle_link_reply_mode_success():
    """!link без аргументов в reply → парсит первую ссылку из reply."""
    bot = _make_bot("")
    msg = _make_message(reply_text="Смотри https://example.com/article тут")

    meta = {
        "title": "Article",
        "description": "",
        "image": "",
        "final_url": "https://example.com/article",
    }

    with patch(
        "src.handlers.command_handlers._fetch_link_meta",
        AsyncMock(return_value=meta),
    ) as mock_fetch:
        await handle_link(bot, msg)

    # Должен был вызвать fetch с первой найденной ссылкой
    mock_fetch.assert_called_once_with("https://example.com/article")
    assert msg.reply.call_count == 2


@pytest.mark.asyncio
async def test_handle_link_reply_no_urls_raises():
    """!link в reply без ссылок → UserInputError."""
    bot = _make_bot("")
    msg = _make_message(reply_text="просто текст без ссылок")

    with pytest.raises(UserInputError) as exc_info:
        await handle_link(bot, msg)
    assert "нет ссылок" in exc_info.value.user_message


@pytest.mark.asyncio
async def test_handle_link_no_args_no_reply_raises():
    """!link без аргументов и без reply → UserInputError."""
    bot = _make_bot("")
    msg = _make_message()  # reply_to_message=None

    with pytest.raises(UserInputError):
        await handle_link(bot, msg)


@pytest.mark.asyncio
async def test_handle_link_direct_url_as_preview():
    """!link https://example.com (без subcommand) → автоматически делает preview."""
    bot = _make_bot("https://example.com")
    msg = _make_message()

    meta = {
        "title": "Direct",
        "description": "",
        "image": "",
        "final_url": "https://example.com",
    }

    with patch(
        "src.handlers.command_handlers._fetch_link_meta",
        AsyncMock(return_value=meta),
    ):
        await handle_link(bot, msg)

    assert msg.reply.call_count == 2
    final_text = msg.reply.call_args_list[-1][0][0]
    assert "Link Preview" in final_text


@pytest.mark.asyncio
async def test_handle_link_unknown_subcommand_raises():
    """!link foobar → UserInputError с подсказкой."""
    bot = _make_bot("foobar")
    msg = _make_message()

    with pytest.raises(UserInputError) as exc_info:
        await handle_link(bot, msg)
    assert "Неизвестная подкоманда" in exc_info.value.user_message


@pytest.mark.asyncio
async def test_handle_link_preview_network_error_raises():
    """Сетевая ошибка в _fetch_link_meta → UserInputError."""
    bot = _make_bot("preview https://example.com")
    msg = _make_message()

    with patch(
        "src.handlers.command_handlers._fetch_link_meta",
        AsyncMock(side_effect=Exception("Connection refused")),
    ):
        with pytest.raises(UserInputError) as exc_info:
            await handle_link(bot, msg)
        assert "Не удалось загрузить" in exc_info.value.user_message


@pytest.mark.asyncio
async def test_handle_link_expand_network_error_raises():
    """Сетевая ошибка в _expand_url → UserInputError."""
    bot = _make_bot("expand https://bit.ly/short")
    msg = _make_message()

    with patch(
        "src.handlers.command_handlers._expand_url",
        AsyncMock(side_effect=httpx.ConnectError("timeout")),
    ):
        with pytest.raises(UserInputError) as exc_info:
            await handle_link(bot, msg)
        assert "Не удалось развернуть" in exc_info.value.user_message


@pytest.mark.asyncio
async def test_handle_link_reply_uses_caption_if_no_text():
    """Если reply.text пустой — берём caption."""
    bot = _make_bot("")
    reply_msg = SimpleNamespace(
        text=None,
        caption="Ссылка здесь: https://example.com/from-caption",
    )
    msg = SimpleNamespace(
        reply_to_message=reply_msg,
        reply=AsyncMock(),
    )

    meta = {
        "title": "Caption URL",
        "description": "",
        "image": "",
        "final_url": "https://example.com/from-caption",
    }

    with patch(
        "src.handlers.command_handlers._fetch_link_meta",
        AsyncMock(return_value=meta),
    ) as mock_fetch:
        await handle_link(bot, msg)

    mock_fetch.assert_called_once_with("https://example.com/from-caption")


@pytest.mark.asyncio
async def test_handle_link_reply_with_multiple_urls_uses_first():
    """В reply несколько URL — берём только первый."""
    bot = _make_bot("")
    msg = _make_message(
        reply_text="First https://first.com then https://second.com"
    )

    meta = {
        "title": "First",
        "description": "",
        "image": "",
        "final_url": "https://first.com",
    }

    with patch(
        "src.handlers.command_handlers._fetch_link_meta",
        AsyncMock(return_value=meta),
    ) as mock_fetch:
        await handle_link(bot, msg)

    # Должен был передать именно первый URL
    mock_fetch.assert_called_once_with("https://first.com")


# ---------------------------------------------------------------------------
# Проверка экспорта через __init__
# ---------------------------------------------------------------------------


def test_handle_link_exported_from_handlers_package():
    """handle_link доступна через src.handlers."""
    from src.handlers import handle_link as exported  # noqa: PLC0415
    from src.handlers.command_handlers import handle_link as original  # noqa: PLC0415
    assert exported is original
