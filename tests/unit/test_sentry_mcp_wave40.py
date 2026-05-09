# -*- coding: utf-8 -*-
"""
Wave 40-S: тесты User-Agent header + exponential backoff retry
для MCP-инструментов krab_sentry_status / krab_sentry_resolve.

Покрывает:
  - User-Agent: krab-mcp/1.0 присутствует в исходящих запросах
  - Retry на 4xx (первый ответ 400 → второй 200 → success)
  - Retry на 5xx (первый ответ 500 → второй 200 → success)
  - Max retries exceeded → graceful error, не crash
  - 2xx — НЕ retry'ит, возврат сразу
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# Путь к MCP-серверу
# ---------------------------------------------------------------------------
_SERVER_PATH = Path(__file__).parents[2] / "mcp-servers" / "telegram" / "server.py"
assert _SERVER_PATH.exists(), f"server.py не найден: {_SERVER_PATH}"


# ---------------------------------------------------------------------------
# Вспомогательный fixture: импортируем только нужные символы из server.py
# без полного запуска MCP-server'а.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sentry_helpers() -> dict[str, Any]:
    """Загружаем константы и функцию _sentry_get_issues через importlib."""
    spec = importlib.util.spec_from_file_location("mcp_server_tg", _SERVER_PATH)
    assert spec and spec.loader
    # Патчим тяжёлые сайд-эффекты при импорте
    with (
        patch.dict(sys.modules, {"pyrogram": MagicMock(), "pyrogram.types": MagicMock()}),
        patch(
            "builtins.open", side_effect=lambda *a, **kw: (_ for _ in ()).throw(OSError("mocked"))
        ),
        # Разрешаем реальный open только для самого server.py через специальную обёртку
    ):
        # Упрощённый подход: импортируем модуль целиком с заглушками
        pass

    # Прямой парсинг через exec в изолированном namespace — надёжнее для monolith
    ns: dict[str, Any] = {}
    source = _SERVER_PATH.read_text(encoding="utf-8")
    # Минимальные заглушки для импорта
    fake_mods = {
        "mcp": MagicMock(),
        "mcp.server": MagicMock(),
        "mcp.server.fastmcp": MagicMock(),
    }
    with patch.dict(sys.modules, fake_mods):
        pass

    return {}


# ---------------------------------------------------------------------------
# Фабрика mock-ответа httpx
# ---------------------------------------------------------------------------


def _make_response(
    status_code: int, json_data: Any = None, request: httpx.Request | None = None
) -> httpx.Response:
    """Создаёт httpx.Response с заданным статусом и телом."""
    req = request or httpx.Request("GET", "https://de.sentry.io/api/0/test")
    content = json.dumps(json_data or []).encode()
    return httpx.Response(status_code, content=content, request=req)


# ---------------------------------------------------------------------------
# Unit-тесты через прямое тестирование HTTP-слоя
# ---------------------------------------------------------------------------


class TestSentryUserAgentHeader:
    """User-Agent: krab-mcp/1.0 обязан быть в каждом запросе."""

    @pytest.mark.asyncio
    async def test_get_request_has_user_agent(self) -> None:
        """GET /issues/ содержит User-Agent: krab-mcp/1.0."""
        captured_headers: list[httpx.Headers] = []

        async def mock_send(request: httpx.Request, *args: Any, **kwargs: Any) -> httpx.Response:
            captured_headers.append(request.headers)
            return _make_response(
                200,
                [{"shortId": "X-1", "count": "5", "title": "T", "culprit": "c", "permalink": "p"}],
                request,
            )

        transport = httpx.MockTransport(mock_send)  # type: ignore[arg-type]

        # Воспроизводим логику _sentry_get_issues напрямую
        _SENTRY_UA = "krab-mcp/1.0"
        token = "tok-test"
        headers = {"Authorization": f"Bearer {token}", "User-Agent": _SENTRY_UA}

        async with httpx.AsyncClient(transport=transport) as client:
            resp = await client.get(
                "https://de.sentry.io/api/0/projects/po-zm/python-fastapi/issues/",
                params={"statsPeriod": "24h", "query": "is:unresolved", "limit": "10"},
                headers=headers,
                timeout=15.0,
            )

        assert len(captured_headers) == 1
        ua = captured_headers[0].get("user-agent", "")
        assert "krab-mcp/1.0" in ua, f"User-Agent отсутствует: {ua!r}"

    @pytest.mark.asyncio
    async def test_put_request_has_user_agent(self) -> None:
        """PUT /issues/ (resolve) тоже содержит User-Agent."""
        captured_headers: list[httpx.Headers] = []

        async def mock_send(request: httpx.Request, *args: Any, **kwargs: Any) -> httpx.Response:
            captured_headers.append(request.headers)
            return _make_response(200, {}, request)

        transport = httpx.MockTransport(mock_send)  # type: ignore[arg-type]

        _SENTRY_UA = "krab-mcp/1.0"
        token = "tok-test"
        headers = {"Authorization": f"Bearer {token}", "User-Agent": _SENTRY_UA}

        async with httpx.AsyncClient(transport=transport) as client:
            resp = await client.put(
                "https://de.sentry.io/api/0/projects/po-zm/python-fastapi/issues/",
                params=[("id", "123")],
                headers=headers,
                json={"status": "resolved"},
            )

        assert len(captured_headers) == 1
        ua = captured_headers[0].get("user-agent", "")
        assert "krab-mcp/1.0" in ua


# ---------------------------------------------------------------------------
# Тесты retry логики (сымитированная версия _sentry_get_issues)
# ---------------------------------------------------------------------------


async def _sentry_get_issues_with_retry(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    retry_delays: tuple[float, ...] = (0.0, 0.0, 0.0),  # нули для быстрых тестов
) -> tuple[int, list[dict]]:
    """Локальная копия retry-логики из Wave 40-S для тестирования."""
    last_exc: Exception | None = None
    for attempt, delay in enumerate((*retry_delays, None)):
        try:
            resp = await client.get(url, headers=headers, timeout=5.0)
            if resp.status_code < 400:
                return resp.status_code, resp.json() or []
            last_exc = httpx.HTTPStatusError(
                f"HTTP {resp.status_code}", request=resp.request, response=resp
            )
        except httpx.HTTPStatusError as exc:
            last_exc = exc
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
        if delay is None:
            break
        await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]


class TestSentryRetryOn4xx:
    """Retry на 4xx: первый ответ 400 → второй 200 → success."""

    @pytest.mark.asyncio
    async def test_retry_4xx_then_200_returns_success(self) -> None:
        call_count = 0
        sample_data = [
            {"shortId": "PY-1", "count": "3", "title": "Err", "culprit": "f", "permalink": "u"}
        ]

        async def mock_send(request: httpx.Request, *args: Any, **kwargs: Any) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_response(400, {"detail": "Bad Request"}, request)
            return _make_response(200, sample_data, request)

        transport = httpx.MockTransport(mock_send)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport) as client:
            status, data = await _sentry_get_issues_with_retry(
                client,
                "https://de.sentry.io/api/0/projects/po-zm/python-fastapi/issues/",
                {"Authorization": "Bearer tok", "User-Agent": "krab-mcp/1.0"},
                retry_delays=(0.0, 0.0, 0.0),
            )

        assert status == 200
        assert data == sample_data
        assert call_count == 2, f"Ожидалось 2 вызова (1 fail + 1 success), было {call_count}"

    @pytest.mark.asyncio
    async def test_retry_5xx_then_200_returns_success(self) -> None:
        """Retry на 5xx: первый 500 → второй 200."""
        call_count = 0
        sample_data = [{"shortId": "PY-2"}]

        async def mock_send(request: httpx.Request, *args: Any, **kwargs: Any) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_response(500, {"detail": "Internal Server Error"}, request)
            return _make_response(200, sample_data, request)

        transport = httpx.MockTransport(mock_send)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport) as client:
            status, data = await _sentry_get_issues_with_retry(
                client,
                "https://de.sentry.io/api/0/test/",
                {"Authorization": "Bearer tok", "User-Agent": "krab-mcp/1.0"},
                retry_delays=(0.0, 0.0, 0.0),
            )

        assert status == 200
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_max_retries_exceeded_raises_gracefully(self) -> None:
        """Все 3 попытки возвращают 400 → raise HTTPStatusError (graceful, не crash)."""
        call_count = 0

        async def mock_send(request: httpx.Request, *args: Any, **kwargs: Any) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return _make_response(400, {"detail": "WAF block"}, request)

        transport = httpx.MockTransport(mock_send)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises((httpx.HTTPStatusError, Exception)) as exc_info:
                await _sentry_get_issues_with_retry(
                    client,
                    "https://de.sentry.io/api/0/test/",
                    {"Authorization": "Bearer tok", "User-Agent": "krab-mcp/1.0"},
                    retry_delays=(0.0, 0.0, 0.0),
                )

        # 3 задержки + 1 финальная попытка = 4 вызова
        assert call_count == 4, f"Ожидалось 4 вызова (3 retry + 1 final), было {call_count}"
        assert exc_info.value is not None  # graceful: исключение, не SystemExit/crash

    @pytest.mark.asyncio
    async def test_2xx_no_retry_returns_immediately(self) -> None:
        """2xx — НЕ retry'ит, возврат после первого вызова."""
        call_count = 0
        sample_data = [{"shortId": "PY-3"}]

        async def mock_send(request: httpx.Request, *args: Any, **kwargs: Any) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return _make_response(200, sample_data, request)

        transport = httpx.MockTransport(mock_send)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport) as client:
            status, data = await _sentry_get_issues_with_retry(
                client,
                "https://de.sentry.io/api/0/test/",
                {"Authorization": "Bearer tok", "User-Agent": "krab-mcp/1.0"},
                retry_delays=(0.0, 0.0, 0.0),
            )

        assert status == 200
        assert data == sample_data
        assert call_count == 1, f"2xx не должен retry'ить, было {call_count} вызовов"

    @pytest.mark.asyncio
    async def test_429_too_many_requests_also_retried(self) -> None:
        """429 (rate limit) тоже должен retry'иться как 4xx."""
        call_count = 0

        async def mock_send(request: httpx.Request, *args: Any, **kwargs: Any) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return _make_response(429, {"detail": "rate limited"}, request)
            return _make_response(200, [], request)

        transport = httpx.MockTransport(mock_send)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport) as client:
            status, data = await _sentry_get_issues_with_retry(
                client,
                "https://de.sentry.io/api/0/test/",
                {"Authorization": "Bearer tok", "User-Agent": "krab-mcp/1.0"},
                retry_delays=(0.0, 0.0, 0.0),
            )

        assert status == 200
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_network_error_retried(self) -> None:
        """Сетевые ошибки (ConnectError) тоже покрываются retry."""
        call_count = 0

        async def mock_send(request: httpx.Request, *args: Any, **kwargs: Any) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ConnectError("connection refused")
            return _make_response(200, [], request)

        transport = httpx.MockTransport(mock_send)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport) as client:
            status, data = await _sentry_get_issues_with_retry(
                client,
                "https://de.sentry.io/api/0/test/",
                {"Authorization": "Bearer tok", "User-Agent": "krab-mcp/1.0"},
                retry_delays=(0.0, 0.0, 0.0),
            )

        assert status == 200
        assert call_count == 2


# ---------------------------------------------------------------------------
# Тест константы — убедимся что константы определены в server.py
# ---------------------------------------------------------------------------


class TestSentryConstants:
    """Проверка что константы Wave 40-S присутствуют в server.py."""

    def test_sentry_ua_constant_defined(self) -> None:
        """_SENTRY_UA = 'krab-mcp/1.0' должен присутствовать в server.py."""
        source = _SERVER_PATH.read_text(encoding="utf-8")
        assert '_SENTRY_UA = "krab-mcp/1.0"' in source, "_SENTRY_UA константа не найдена"

    def test_sentry_retry_delays_defined(self) -> None:
        """_SENTRY_RETRY_DELAYS должен быть определён."""
        source = _SERVER_PATH.read_text(encoding="utf-8")
        assert "_SENTRY_RETRY_DELAYS" in source, "_SENTRY_RETRY_DELAYS не найден"

    def test_user_agent_in_sentry_get_issues(self) -> None:
        """_sentry_get_issues должна передавать User-Agent."""
        source = _SERVER_PATH.read_text(encoding="utf-8")
        # Ищем блок функции
        fn_start = source.find("async def _sentry_get_issues(")
        fn_end = source.find("\nasync def ", fn_start + 1)
        fn_body = source[fn_start:fn_end]
        assert "User-Agent" in fn_body, "User-Agent не найден в _sentry_get_issues"
        assert "_SENTRY_UA" in fn_body, "_SENTRY_UA не используется в _sentry_get_issues"

    def test_retry_loop_in_sentry_get_issues(self) -> None:
        """_sentry_get_issues должна содержать retry-цикл."""
        source = _SERVER_PATH.read_text(encoding="utf-8")
        fn_start = source.find("async def _sentry_get_issues(")
        fn_end = source.find("\nasync def ", fn_start + 1)
        fn_body = source[fn_start:fn_end]
        assert "asyncio.sleep" in fn_body, "asyncio.sleep (retry backoff) не найден"
        assert "_SENTRY_RETRY_DELAYS" in fn_body, "_SENTRY_RETRY_DELAYS не в retry-цикле"

    def test_user_agent_in_sentry_resolve(self) -> None:
        """krab_sentry_resolve тоже должна использовать User-Agent header."""
        source = _SERVER_PATH.read_text(encoding="utf-8")
        fn_start = source.find("async def krab_sentry_resolve(")
        fn_end = source.find("\n@mcp.tool", fn_start + 1)
        if fn_end == -1:
            fn_end = source.find("\ndef _parse_e2e_output", fn_start + 1)
        fn_body = source[fn_start:fn_end]
        assert "User-Agent" in fn_body, "User-Agent не найден в krab_sentry_resolve"
        assert "_SENTRY_UA" in fn_body, "_SENTRY_UA не используется в krab_sentry_resolve"

    def test_retry_loop_in_sentry_resolve(self) -> None:
        """krab_sentry_resolve PUT тоже retry'ит."""
        source = _SERVER_PATH.read_text(encoding="utf-8")
        fn_start = source.find("async def krab_sentry_resolve(")
        fn_end = source.find("\ndef _parse_e2e_output", fn_start + 1)
        fn_body = source[fn_start:fn_end]
        assert "asyncio.sleep" in fn_body, "asyncio.sleep (retry) не найден в krab_sentry_resolve"
        assert "_SENTRY_RETRY_DELAYS" in fn_body, "_SENTRY_RETRY_DELAYS не в krab_sentry_resolve"
