"""
Тесты для src/skills/web_search.py
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Вспомогательные фикстуры
# ---------------------------------------------------------------------------


def _make_response(status_code: int = 200, json_data: dict | None = None, text: str = ""):
    """Создаёт мок httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.json = MagicMock(return_value=json_data or {})
    return resp


def _brave_payload(*results: dict) -> dict:
    """Формирует типичный ответ Brave Search API."""
    return {"web": {"results": list(results)}}


def _result(title="Заголовок", description="Описание", url="https://example.com"):
    return {"title": title, "description": description, "url": url}


# ---------------------------------------------------------------------------
# Тесты: нет API-ключа (fallback DuckDuckGo)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_api_key_returns_duckduckgo_link():
    """Без API-ключа возвращается ссылка на DuckDuckGo."""
    with patch("src.skills.web_search.config") as mock_cfg:
        mock_cfg.BRAVE_SEARCH_API_KEY = None
        from src.skills.web_search import search_web

        result = await search_web("python asyncio")
    assert "duckduckgo.com" in result
    assert "python+asyncio" in result


@pytest.mark.asyncio
async def test_no_api_key_empty_string_returns_duckduckgo_link():
    """Пустая строка ключа также ведёт к fallback."""
    with patch("src.skills.web_search.config") as mock_cfg:
        mock_cfg.BRAVE_SEARCH_API_KEY = ""
        from src.skills.web_search import search_web

        result = await search_web("test query")
    assert "duckduckgo.com" in result


# ---------------------------------------------------------------------------
# Тесты: построение запроса (query building)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_included_in_request_url():
    """Поисковый запрос передаётся в URL к Brave API."""
    mock_resp = _make_response(200, _brave_payload(_result()))
    with (
        patch("src.skills.web_search.config") as mock_cfg,
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_cfg.BRAVE_SEARCH_API_KEY = "test_key"
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        from src.skills.web_search import search_web

        await search_web("кот")

    called_url = mock_client.get.call_args[0][0]
    assert "кот" in called_url


@pytest.mark.asyncio
async def test_request_count_param_is_3():
    """Параметр count=3 передаётся в запрос."""
    mock_resp = _make_response(200, _brave_payload(_result()))
    with (
        patch("src.skills.web_search.config") as mock_cfg,
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_cfg.BRAVE_SEARCH_API_KEY = "key"
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        from src.skills.web_search import search_web

        await search_web("query")

    called_url = mock_client.get.call_args[0][0]
    assert "count=3" in called_url


@pytest.mark.asyncio
async def test_auth_header_sent():
    """Заголовок X-Subscription-Token содержит API-ключ."""
    mock_resp = _make_response(200, _brave_payload(_result()))
    with (
        patch("src.skills.web_search.config") as mock_cfg,
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_cfg.BRAVE_SEARCH_API_KEY = "secret_brave_key"
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        from src.skills.web_search import search_web

        await search_web("anything")

    headers = mock_client.get.call_args[1]["headers"]
    assert headers["X-Subscription-Token"] == "secret_brave_key"


# ---------------------------------------------------------------------------
# Тесты: парсинг результатов
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_result_parsed_correctly():
    """Один результат правильно форматируется."""
    payload = _brave_payload(_result("Py Docs", "Официальная дока", "https://docs.python.org"))
    mock_resp = _make_response(200, payload)
    with (
        patch("src.skills.web_search.config") as mock_cfg,
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_cfg.BRAVE_SEARCH_API_KEY = "k"
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        from src.skills.web_search import search_web

        result = await search_web("python")

    assert "Py Docs" in result
    assert "Официальная дока" in result
    assert "https://docs.python.org" in result


@pytest.mark.asyncio
async def test_multiple_results_all_included():
    """Все три результата присутствуют в выводе."""
    payload = _brave_payload(
        _result("R1", "Desc1", "https://r1.com"),
        _result("R2", "Desc2", "https://r2.com"),
        _result("R3", "Desc3", "https://r3.com"),
    )
    mock_resp = _make_response(200, payload)
    with (
        patch("src.skills.web_search.config") as mock_cfg,
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_cfg.BRAVE_SEARCH_API_KEY = "k"
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        from src.skills.web_search import search_web

        result = await search_web("multi")

    for label in ("R1", "R2", "R3", "https://r1.com", "https://r2.com", "https://r3.com"):
        assert label in result


@pytest.mark.asyncio
async def test_result_missing_optional_fields():
    """Результат без description/url не вызывает исключения."""
    payload = {"web": {"results": [{"title": "Только заголовок"}]}}
    mock_resp = _make_response(200, payload)
    with (
        patch("src.skills.web_search.config") as mock_cfg,
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_cfg.BRAVE_SEARCH_API_KEY = "k"
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        from src.skills.web_search import search_web

        result = await search_web("fallback")

    assert "Только заголовок" in result


# ---------------------------------------------------------------------------
# Тесты: пустые результаты
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_results_returns_not_found_message():
    """Пустой список results → сообщение «ничего не найдено»."""
    mock_resp = _make_response(200, {"web": {"results": []}})
    with (
        patch("src.skills.web_search.config") as mock_cfg,
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_cfg.BRAVE_SEARCH_API_KEY = "k"
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        from src.skills.web_search import search_web

        result = await search_web("xyzzy nothing found")

    assert "ничего не найдено" in result.lower()


@pytest.mark.asyncio
async def test_missing_web_key_in_response():
    """Ответ без ключа 'web' не вызывает исключения."""
    mock_resp = _make_response(200, {})
    with (
        patch("src.skills.web_search.config") as mock_cfg,
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_cfg.BRAVE_SEARCH_API_KEY = "k"
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        from src.skills.web_search import search_web

        result = await search_web("empty web")

    assert "ничего не найдено" in result.lower()


# ---------------------------------------------------------------------------
# Тесты: обработка ошибок HTTP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_200_status_returns_error_with_code():
    """Статус не 200 → сообщение об ошибке с кодом."""
    mock_resp = _make_response(429, text="Too Many Requests")
    with (
        patch("src.skills.web_search.config") as mock_cfg,
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_cfg.BRAVE_SEARCH_API_KEY = "k"
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        from src.skills.web_search import search_web

        result = await search_web("rate limited")

    assert "429" in result
    assert "❌" in result


@pytest.mark.asyncio
async def test_401_unauthorized_returns_error():
    """HTTP 401 → сообщение об ошибке."""
    mock_resp = _make_response(401, text="Unauthorized")
    with (
        patch("src.skills.web_search.config") as mock_cfg,
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_cfg.BRAVE_SEARCH_API_KEY = "bad_key"
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        from src.skills.web_search import search_web

        result = await search_web("secret")

    assert "401" in result


@pytest.mark.asyncio
async def test_http_network_exception_returns_error_message():
    """Сетевое исключение httpx → graceful error message."""
    import httpx

    with (
        patch("src.skills.web_search.config") as mock_cfg,
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_cfg.BRAVE_SEARCH_API_KEY = "k"
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        from src.skills.web_search import search_web

        result = await search_web("broken net")

    assert "❌" in result
    assert "Connection refused" in result


@pytest.mark.asyncio
async def test_os_error_handled_gracefully():
    """OSError (напр. DNS) также перехватывается."""
    with (
        patch("src.skills.web_search.config") as mock_cfg,
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_cfg.BRAVE_SEARCH_API_KEY = "k"
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=OSError("Name or service not known"))
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        from src.skills.web_search import search_web

        result = await search_web("dns fail")

    assert "❌" in result


# ---------------------------------------------------------------------------
# Тест: URL-извлечение — форматирование markdown-ссылки
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_url_formatted_as_markdown_link():
    """URL и заголовок оборачиваются в markdown-ссылку."""
    payload = _brave_payload(_result("OpenAI", "AI company", "https://openai.com"))
    mock_resp = _make_response(200, payload)
    with (
        patch("src.skills.web_search.config") as mock_cfg,
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_cfg.BRAVE_SEARCH_API_KEY = "k"
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        from src.skills.web_search import search_web

        result = await search_web("ai")

    # Формат: [Title](url)
    assert "[OpenAI](https://openai.com)" in result


# ---------------------------------------------------------------------------
# Тест: заголовок результатов присутствует
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_output_contains_search_header():
    """Вывод содержит заголовок «Результаты поиска»."""
    payload = _brave_payload(_result())
    mock_resp = _make_response(200, payload)
    with (
        patch("src.skills.web_search.config") as mock_cfg,
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_cfg.BRAVE_SEARCH_API_KEY = "k"
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        from src.skills.web_search import search_web

        result = await search_web("anything")

    assert "Результаты поиска" in result
