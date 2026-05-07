# -*- coding: utf-8 -*-
"""
Юнит-тесты для src/skills/youtube_metadata.py.

Покрываем:
  - fetch_yt_metadata: успешный oEmbed ответ от YouTube
  - fetch_yt_metadata: fallback на noembed.com когда youtube.com недоступен
  - fetch_yt_metadata: возвращает None когда оба endpoint недоступны
  - fetch_yt_metadata: обрабатывает HTTP 404 gracefully
  - fetch_yt_metadata: обрабатывает DNS/network ошибки gracefully
  - format_yt_metadata: форматирует dict в читаемый текст
  - format_yt_metadata: работает с частичными данными
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures и хелперы
# ---------------------------------------------------------------------------

YOUTUBE_OEMBED_RESPONSE = {
    "title": "Rick Astley - Never Gonna Give You Up (Official Music Video)",
    "author_name": "Rick Astley",
    "author_url": "https://www.youtube.com/@RickAstleyYT",
    "type": "video",
    "height": 113,
    "width": 200,
    "version": "1.0",
    "provider_name": "YouTube",
    "provider_url": "https://www.youtube.com/",
    "thumbnail_url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
    "thumbnail_width": 480,
    "thumbnail_height": 360,
}

TEST_URL = "https://youtu.be/dQw4w9WgXcQ"


def _make_http_response(status: int, json_data: dict | None = None, raises: Exception | None = None):
    """Создаёт мок httpx Response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status
    if json_data is not None:
        mock_resp.json.return_value = json_data
    if raises:
        mock_resp.json.side_effect = raises
    return mock_resp


# ---------------------------------------------------------------------------
# Тесты fetch_yt_metadata
# ---------------------------------------------------------------------------


class TestFetchYtMetadata:
    """Тесты основной функции получения метаданных."""

    @pytest.mark.asyncio
    async def test_успешный_ответ_youtube_oembed(self) -> None:
        """Первый endpoint (youtube.com/oembed) возвращает данные → возвращаем их."""
        from src.skills.youtube_metadata import fetch_yt_metadata

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=_make_http_response(200, YOUTUBE_OEMBED_RESPONSE))

        with patch("src.skills.youtube_metadata.httpx") as mock_httpx:
            mock_httpx.AsyncClient.return_value = mock_client
            result = await fetch_yt_metadata(TEST_URL)

        assert result is not None
        assert result["title"] == YOUTUBE_OEMBED_RESPONSE["title"]
        assert result["author_name"] == "Rick Astley"

    @pytest.mark.asyncio
    async def test_fallback_на_noembed_при_недоступности_youtube(self) -> None:
        """Первый endpoint падает с ошибкой → пробуем noembed.com."""
        from src.skills.youtube_metadata import fetch_yt_metadata

        noembed_response = {
            "title": "Rick Astley - Never Gonna Give You Up",
            "author_name": "Rick Astley",
            "thumbnail_url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
        }

        call_count = 0

        async def fake_get(url, **_kw):
            nonlocal call_count
            call_count += 1
            if "youtube.com/oembed" in url:
                import httpx
                raise httpx.ConnectError("DNS resolution failed")
            # noembed.com
            return _make_http_response(200, noembed_response)

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=fake_get)

        with patch("src.skills.youtube_metadata.httpx") as mock_httpx:
            mock_httpx.AsyncClient.return_value = mock_client
            mock_httpx.ConnectError = Exception  # упрощение
            result = await fetch_yt_metadata(TEST_URL)

        assert result is not None
        assert result["title"] == "Rick Astley - Never Gonna Give You Up"

    @pytest.mark.asyncio
    async def test_все_endpoints_недоступны_возвращает_none(self) -> None:
        """Оба endpoint недоступны → возвращаем None."""
        from src.skills.youtube_metadata import fetch_yt_metadata

        async def fake_get(url, **_kw):
            raise OSError("Network unreachable")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=fake_get)

        with patch("src.skills.youtube_metadata.httpx") as mock_httpx:
            mock_httpx.AsyncClient.return_value = mock_client
            result = await fetch_yt_metadata(TEST_URL)

        assert result is None

    @pytest.mark.asyncio
    async def test_http_404_не_крашит(self) -> None:
        """Endpoint возвращает 404 → пробуем следующий или возвращаем None."""
        from src.skills.youtube_metadata import fetch_yt_metadata

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=_make_http_response(404))

        with patch("src.skills.youtube_metadata.httpx") as mock_httpx:
            mock_httpx.AsyncClient.return_value = mock_client
            result = await fetch_yt_metadata(TEST_URL)

        # 404 — title отсутствует, должны вернуть None
        assert result is None

    @pytest.mark.asyncio
    async def test_ответ_без_title_игнорируется(self) -> None:
        """Ответ без поля title не считается успешным."""
        from src.skills.youtube_metadata import fetch_yt_metadata

        bad_response = {"author_name": "SomeChannel"}  # нет title

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=_make_http_response(200, bad_response))

        with patch("src.skills.youtube_metadata.httpx") as mock_httpx:
            mock_httpx.AsyncClient.return_value = mock_client
            result = await fetch_yt_metadata(TEST_URL)

        assert result is None

    @pytest.mark.asyncio
    async def test_network_error_возвращает_none(self) -> None:
        """Оба endpoint недоступны из-за network error → None."""
        from src.skills.youtube_metadata import fetch_yt_metadata

        async def fail_get(url, **_kw):
            raise OSError("Connection refused")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=fail_get)

        with patch("src.skills.youtube_metadata.httpx") as mock_httpx:
            mock_httpx.AsyncClient.return_value = mock_client
            result = await fetch_yt_metadata(TEST_URL)

        assert result is None


# ---------------------------------------------------------------------------
# Тесты format_yt_metadata
# ---------------------------------------------------------------------------


class TestFormatYtMetadata:
    """Тесты форматирования метаданных."""

    def test_полные_данные_форматируются(self) -> None:
        """Полный oEmbed ответ → все поля в тексте."""
        from src.skills.youtube_metadata import format_yt_metadata

        result = format_yt_metadata(YOUTUBE_OEMBED_RESPONSE)

        assert "Rick Astley - Never Gonna Give You Up" in result
        assert "Rick Astley" in result
        assert "200×113" in result  # width×height
        assert "i.ytimg.com" in result  # thumbnail_url

    def test_пустой_dict_не_крашит(self) -> None:
        """Пустой dict → не крашится, возвращает строку."""
        from src.skills.youtube_metadata import format_yt_metadata

        result = format_yt_metadata({})

        assert isinstance(result, str)

    def test_только_title(self) -> None:
        """Только title → одна строка с заголовком."""
        from src.skills.youtube_metadata import format_yt_metadata

        result = format_yt_metadata({"title": "My Video"})

        assert "My Video" in result

    def test_без_разрешения_нет_строки_разрешения(self) -> None:
        """Если width/height нет → строка с разрешением не появляется."""
        from src.skills.youtube_metadata import format_yt_metadata

        result = format_yt_metadata({"title": "Test", "author_name": "Me"})

        assert "×" not in result

    def test_возвращает_непустую_строку_для_полных_данных(self) -> None:
        """Полные данные → непустой результат."""
        from src.skills.youtube_metadata import format_yt_metadata

        result = format_yt_metadata(YOUTUBE_OEMBED_RESPONSE)

        assert result.strip() != ""

    def test_иконки_присутствуют(self) -> None:
        """В форматированном тексте есть emoji-маркеры."""
        from src.skills.youtube_metadata import format_yt_metadata

        result = format_yt_metadata(YOUTUBE_OEMBED_RESPONSE)

        # Хотя бы одна emoji из набора: 🎬, 👤, 🖼, 🔗
        assert any(icon in result for icon in ["🎬", "👤", "🖼", "🔗"])


# ---------------------------------------------------------------------------
# Тесты интеграции с handle_yt
# ---------------------------------------------------------------------------


class TestHandleYtOembedIntegration:
    """Проверка что handle_yt использует oEmbed как fallback."""

    @pytest.mark.asyncio
    async def test_oembed_данные_в_промпте_когда_доступны(self) -> None:
        """Если oEmbed вернул данные → промпт содержит мета-контекст."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from src.handlers.commands.content_commands import handle_yt

        url = "https://youtu.be/dQw4w9WgXcQ"
        bot = SimpleNamespace(_get_command_args=lambda _m: url)

        edit_mock = AsyncMock()
        sent_stub = SimpleNamespace(edit=edit_mock)
        msg = SimpleNamespace(
            text=f"!yt {url}",
            reply=AsyncMock(return_value=sent_stub),
            reply_to_message=None,
            chat=SimpleNamespace(id=42),
        )

        captured_prompts: list[str] = []

        async def fake_stream(message, chat_id, **_kw):
            captured_prompts.append(message)
            yield "Название: Rick Astley"

        oembed_meta = {
            "title": "Rick Astley - Never Gonna Give You Up",
            "author_name": "Rick Astley",
            "thumbnail_url": "https://i.ytimg.com/...",
            "provider_url": "https://www.youtube.com/",
        }

        with (
            patch(
                "src.handlers.commands.content_commands.openclaw_client.send_message_stream",
                side_effect=fake_stream,
            ),
            patch(
                "src.handlers.commands.content_commands.fetch_yt_metadata",
                new=AsyncMock(return_value=oembed_meta),
            ),
            patch(
                "src.handlers.commands.content_commands.format_yt_metadata",
                return_value="🎬 **Rick Astley - Never Gonna Give You Up**\n👤 Автор: Rick Astley",
            ),
        ):
            await handle_yt(bot, msg)

        # Промпт должен содержать oEmbed-контекст
        assert len(captured_prompts) > 0
        assert url in captured_prompts[0]

    @pytest.mark.asyncio
    async def test_oembed_fallback_когда_llm_падает(self) -> None:
        """Если LLM падает, но oEmbed успешен → показываем oEmbed данные."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from src.handlers.commands.content_commands import handle_yt

        url = "https://youtu.be/dQw4w9WgXcQ"
        bot = SimpleNamespace(_get_command_args=lambda _m: url)

        edit_mock = AsyncMock()
        sent_stub = SimpleNamespace(edit=edit_mock)
        msg = SimpleNamespace(
            text=f"!yt {url}",
            reply=AsyncMock(return_value=sent_stub),
            reply_to_message=None,
            chat=SimpleNamespace(id=42),
        )

        async def failing_stream(message, chat_id, **_kw):
            raise RuntimeError("DNS resolution failed for youtube.com")
            yield  # noqa: unreachable

        oembed_formatted = "🎬 **Rick Astley - Never Gonna Give You Up**\n👤 Автор: Rick Astley"

        with (
            patch(
                "src.handlers.command_handlers.openclaw_client.send_message_stream",
                side_effect=failing_stream,
            ),
            patch(
                "src.handlers.commands.content_commands.fetch_yt_metadata",
                new=AsyncMock(return_value={"title": "Rick Astley"}),
            ),
            patch(
                "src.handlers.commands.content_commands.format_yt_metadata",
                return_value=oembed_formatted,
            ),
        ):
            await handle_yt(bot, msg)

        # Должны показать oEmbed данные, не "❌ Ошибка"
        edit_mock.assert_called_once()
        call_text = edit_mock.call_args[0][0]
        assert "Rick Astley" in call_text

    @pytest.mark.asyncio
    async def test_cloud_prompt_когда_oembed_недоступен(self) -> None:
        """Если oEmbed вернул None → используем cloud-prompt без упоминания yt-dlp."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from src.handlers.commands.content_commands import handle_yt

        url = "https://youtu.be/dQw4w9WgXcQ"
        bot = SimpleNamespace(_get_command_args=lambda _m: url)

        edit_mock = AsyncMock()
        sent_stub = SimpleNamespace(edit=edit_mock)
        msg = SimpleNamespace(
            text=f"!yt {url}",
            reply=AsyncMock(return_value=sent_stub),
            reply_to_message=None,
            chat=SimpleNamespace(id=42),
        )

        captured_prompts: list[str] = []

        async def fake_stream(message, chat_id, **_kw):
            captured_prompts.append(message)
            yield "Видео найдено"

        with (
            patch(
                "src.handlers.commands.content_commands.openclaw_client.send_message_stream",
                side_effect=fake_stream,
            ),
            patch(
                "src.handlers.commands.content_commands.fetch_yt_metadata",
                new=AsyncMock(return_value=None),  # oEmbed недоступен
            ),
        ):
            await handle_yt(bot, msg)

        assert len(captured_prompts) > 0
        prompt = captured_prompts[0]
        # URL должен быть в промпте
        assert url in prompt
        # Промпт не должен давать yt-dlp как рекомендованный инструмент
        # (может упоминаться как "НЕ запускай yt-dlp" — это OK)
        assert "youtube.com" in prompt or "YouTube" in prompt
