# -*- coding: utf-8 -*-
"""S53 P4: тесты для `_local_primary_bypass` (Gateway bypass для local primary).

Покрытие:
1. env gate OFF (default) → None.
2. lm-studio-local/* prefix → POST на LM Studio :1234, content возвращается.
3. mlx-local-kv4/* prefix → POST на MLX :8088 + `enable_thinking=false`.
4. cloud-namespace модели (google/*, anthropic/*) → None (никаких HTTP вызовов).
5. has_photo=True → None (vision запросы остаются основному path).
6. backend HTTP 500 → None (graceful fallback).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.openclaw_client import OpenClawClient


@pytest.fixture
def client():
    """Минимальный OpenClawClient с замоканным config (как в test_openclaw_client)."""
    with patch("src.openclaw_client.config") as mock_config:
        mock_config.OPENCLAW_URL = "http://mock-claw"
        mock_config.OPENCLAW_TOKEN = "token"
        mock_config.LM_STUDIO_URL = "http://127.0.0.1:1234"
        mock_config.LM_STUDIO_API_KEY = ""
        mock_config.LM_STUDIO_NATIVE_REASONING_MODE = "off"
        mock_config.LOCAL_FALLBACK_ENABLED = True
        mock_config.HISTORY_WINDOW_MESSAGES = 20
        mock_config.HISTORY_WINDOW_MAX_CHARS = None
        mock_config.LOCAL_HISTORY_WINDOW_MESSAGES = 20
        mock_config.LOCAL_HISTORY_WINDOW_MAX_CHARS = 12000
        mock_config.RETRY_HISTORY_WINDOW_MESSAGES = 8
        mock_config.RETRY_HISTORY_WINDOW_MAX_CHARS = 4000
        mock_config.LOCAL_PREFERRED_VISION_MODEL = ""
        mock_config.TOOL_NARRATION_ENABLED = False
        mock_config.KRAB_GOOGLE_DIRECT_BYPASS_ENABLED = False
        mock_config.KRAB_VERTEX_DIRECT_BYPASS_ENABLED = False
        mock_config.KRAB_ANTHROPIC_VERTEX_DIRECT_BYPASS_ENABLED = False
        mock_config.KRAB_GEMMA_DIRECT_BYPASS_ENABLED = False
        mock_config.KRAB_CLI_BYPASS_ENABLED = False
        inst = OpenClawClient()
        inst._http_client = AsyncMock()
        # Готовим session с одним user-message — bypass читает self._sessions[chat_id].
        inst._sessions["chat-bypass"] = [{"role": "user", "content": "Привет"}]
        yield inst


def _fake_httpx_client(status: int = 200, content: str = "Локальный ответ"):
    """Builder для AsyncMock httpx.AsyncClient context-manager."""
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = {
        "choices": [{"message": {"content": content}}],
    }
    fake = AsyncMock()
    fake.post = AsyncMock(return_value=resp)
    fake.__aenter__.return_value = fake
    fake.__aexit__.return_value = False
    return fake, resp


@pytest.mark.asyncio
async def test_bypass_disabled_by_default_returns_none(
    client: OpenClawClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Без KRAB_LOCAL_PRIMARY_BYPASS_ENABLED bypass возвращает None."""
    monkeypatch.delenv("KRAB_LOCAL_PRIMARY_BYPASS_ENABLED", raising=False)

    fake, _ = _fake_httpx_client()
    with patch("src.openclaw_client.httpx.AsyncClient", return_value=fake):
        result = await client._local_primary_bypass(  # noqa: SLF001
            chat_id="chat-bypass",
            preferred_model_id="lm-studio-local/gemma-3-12b",
            has_photo=False,
            max_output_tokens=None,
        )

    assert result is None
    fake.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_bypass_lm_studio_local_prefix_returns_content(
    client: OpenClawClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`lm-studio-local/*` → POST на LM Studio :1234 → content из choices[0]."""
    monkeypatch.setenv("KRAB_LOCAL_PRIMARY_BYPASS_ENABLED", "1")
    # Чистим override чтобы тест шёл на дефолтный :1234.
    monkeypatch.delenv("KRAB_LOCAL_PRIMARY_BYPASS_URL", raising=False)

    fake, _resp = _fake_httpx_client(status=200, content="Привет из LM Studio")
    with patch("src.openclaw_client.httpx.AsyncClient", return_value=fake) as mock_cli:
        result = await client._local_primary_bypass(  # noqa: SLF001
            chat_id="chat-bypass",
            preferred_model_id="lm-studio-local/gemma-3-12b-it",
            has_photo=False,
            max_output_tokens=None,
        )

    assert result == "Привет из LM Studio"
    fake.post.assert_awaited_once()
    # URL: должен быть :1234, путь /v1/chat/completions
    call_args = fake.post.await_args
    assert call_args.args[0] == "http://127.0.0.1:1234/v1/chat/completions"
    # Модель — без namespace prefix
    payload = call_args.kwargs["json"]
    assert payload["model"] == "gemma-3-12b-it"
    assert payload["stream"] is False
    # httpx client создан без global env / verify=False
    _ = mock_cli  # silence unused-arg


@pytest.mark.asyncio
async def test_bypass_mlx_local_kv4_prefix_targets_8088(
    client: OpenClawClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`mlx-local-kv4/*` → POST на :8088 + chat_template_args.enable_thinking=False."""
    monkeypatch.setenv("KRAB_LOCAL_PRIMARY_BYPASS_ENABLED", "1")
    monkeypatch.delenv("MLX_LOCAL_KV4_URL", raising=False)

    fake, _ = _fake_httpx_client(status=200, content="MLX local ответ")
    # mlx_local_aliases.is_mlx_local_target проверяет URL → возвращаем True.
    with (
        patch("src.core.mlx_local_aliases.is_mlx_local_target", return_value=True),
        patch(
            "src.core.mlx_local_aliases.resolve_mlx_local_alias",
            return_value="/Volumes/4TB SSD/models/gemma-4-26B",
        ),
        patch("src.openclaw_client.httpx.AsyncClient", return_value=fake),
    ):
        result = await client._local_primary_bypass(  # noqa: SLF001
            chat_id="chat-bypass",
            preferred_model_id="mlx-local-kv4/gemma-4-26b",
            has_photo=False,
            max_output_tokens=512,
        )

    assert result == "MLX local ответ"
    call_args = fake.post.await_args
    assert call_args.args[0].startswith("http://127.0.0.1:8088"), call_args.args[0]
    payload = call_args.kwargs["json"]
    # MLX: thinking отключён + alias подставлен.
    assert payload["chat_template_args"]["enable_thinking"] is False
    assert payload["model"] == "/Volumes/4TB SSD/models/gemma-4-26B"
    assert payload["max_tokens"] == 512


@pytest.mark.asyncio
async def test_bypass_cloud_model_returns_none_no_http(
    client: OpenClawClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cloud-namespace модели → None и НИ ОДНОГО HTTP вызова."""
    monkeypatch.setenv("KRAB_LOCAL_PRIMARY_BYPASS_ENABLED", "1")

    fake, _ = _fake_httpx_client()
    with patch("src.openclaw_client.httpx.AsyncClient", return_value=fake):
        result = await client._local_primary_bypass(  # noqa: SLF001
            chat_id="chat-bypass",
            preferred_model_id="google/gemini-3-pro-preview",
            has_photo=False,
            max_output_tokens=None,
        )

    assert result is None
    fake.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_bypass_skipped_for_photo_requests(
    client: OpenClawClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """has_photo=True → bypass отказывается (vision остаётся primary path)."""
    monkeypatch.setenv("KRAB_LOCAL_PRIMARY_BYPASS_ENABLED", "1")

    fake, _ = _fake_httpx_client()
    with patch("src.openclaw_client.httpx.AsyncClient", return_value=fake):
        result = await client._local_primary_bypass(  # noqa: SLF001
            chat_id="chat-bypass",
            preferred_model_id="lm-studio-local/gemma-3-12b",
            has_photo=True,
            max_output_tokens=None,
        )

    assert result is None
    fake.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_bypass_http_500_returns_none(
    client: OpenClawClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Backend HTTP 500 → None (caller продолжает Gateway path)."""
    monkeypatch.setenv("KRAB_LOCAL_PRIMARY_BYPASS_ENABLED", "1")

    fake, _ = _fake_httpx_client(status=500, content="")
    with patch("src.openclaw_client.httpx.AsyncClient", return_value=fake):
        result = await client._local_primary_bypass(  # noqa: SLF001
            chat_id="chat-bypass",
            preferred_model_id="lm-studio-local/gemma-3-12b",
            has_photo=False,
            max_output_tokens=None,
        )

    assert result is None
    fake.post.assert_awaited_once()
