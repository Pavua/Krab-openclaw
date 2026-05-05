"""Wave 23-C: тесты для Anthropic Claude via Vertex AI direct bypass."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.integrations import anthropic_vertex_direct as avd

# -----------------------------------------------------------------------------
# Helpers / fixtures
# -----------------------------------------------------------------------------


def _patched_anthropic_vertex_client(text: str = "ok") -> MagicMock:
    """Создаёт mock AnthropicVertex client с предзаданным response."""
    # AnthropicVertex().messages.create() возвращает объект с .content[0].text
    fake_content_block = SimpleNamespace(text=text)
    fake_response = SimpleNamespace(content=[fake_content_block])
    fake_messages = MagicMock()
    fake_messages.create.return_value = fake_response
    fake_client = MagicMock()
    fake_client.messages = fake_messages
    return fake_client


def _inject_anthropic_mock(monkeypatch, client: MagicMock) -> MagicMock:
    """Подставляет mock anthropic.AnthropicVertex в sys.modules."""
    fake_anthropic = MagicMock()
    fake_anthropic.AnthropicVertex.return_value = client
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)
    return fake_anthropic


# -----------------------------------------------------------------------------
# 1. is_anthropic_vertex_enabled — default ON / OFF
# -----------------------------------------------------------------------------


def test_is_anthropic_vertex_enabled_default_on(monkeypatch):
    """Без env-переменной bypass включён по умолчанию."""
    monkeypatch.delenv("KRAB_ANTHROPIC_VERTEX_BYPASS_ENABLED", raising=False)
    assert avd.is_anthropic_vertex_enabled() is True


def test_is_anthropic_vertex_enabled_explicit_off(monkeypatch):
    """KRAB_ANTHROPIC_VERTEX_BYPASS_ENABLED=0 → False."""
    monkeypatch.setenv("KRAB_ANTHROPIC_VERTEX_BYPASS_ENABLED", "0")
    assert avd.is_anthropic_vertex_enabled() is False


# -----------------------------------------------------------------------------
# 2. is_anthropic_vertex_model — проверка префикса
# -----------------------------------------------------------------------------


def test_is_anthropic_vertex_model_matches_prefix():
    """Все anthropic-vertex/* модели распознаются корректно."""
    assert avd.is_anthropic_vertex_model("anthropic-vertex/claude-opus-4-7") is True
    assert avd.is_anthropic_vertex_model("anthropic-vertex/claude-sonnet-4-6") is True
    assert avd.is_anthropic_vertex_model("anthropic-vertex/claude-haiku-4-5") is True


def test_is_anthropic_vertex_model_rejects_other_prefixes():
    """Другие провайдеры и bare model names — False."""
    assert avd.is_anthropic_vertex_model("google-vertex/gemini-2.5-pro") is False
    assert avd.is_anthropic_vertex_model("google/gemini-3-pro-preview") is False
    assert avd.is_anthropic_vertex_model("codex-cli/gpt-5") is False
    assert avd.is_anthropic_vertex_model("claude-opus-4-7") is False
    assert avd.is_anthropic_vertex_model("") is False


# -----------------------------------------------------------------------------
# 3. _strip_prefix
# -----------------------------------------------------------------------------


def test_strip_prefix_removes_anthropic_vertex_prefix():
    """Функция корректно удаляет PREFIX."""
    assert avd._strip_prefix("anthropic-vertex/claude-opus-4-7") == "claude-opus-4-7"
    assert avd._strip_prefix("anthropic-vertex/claude-sonnet-4-6") == "claude-sonnet-4-6"
    # bare model — без изменений
    assert avd._strip_prefix("claude-haiku-4-5") == "claude-haiku-4-5"


# -----------------------------------------------------------------------------
# 4. _build_messages_for_anthropic — system отдельно, user/assistant в messages
# -----------------------------------------------------------------------------


def test_build_messages_for_anthropic_separates_system():
    """system роль → первый возвращаемый элемент, chat_messages — только user/assistant."""
    system, chat = avd._build_messages_for_anthropic(
        [
            {"role": "system", "content": "you are helpful"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
            {"role": "user", "content": "bye"},
        ]
    )
    assert system == "you are helpful"
    assert len(chat) == 3
    assert chat[0] == {"role": "user", "content": "hello"}
    assert chat[1] == {"role": "assistant", "content": "hi there"}
    assert chat[2] == {"role": "user", "content": "bye"}


# -----------------------------------------------------------------------------
# 5. _build_messages_for_anthropic — пустой ввод → default user message
# -----------------------------------------------------------------------------


def test_build_messages_for_anthropic_empty_input():
    """Пустой список messages → system=None, chat=[{role:user, content:''}]."""
    system, chat = avd._build_messages_for_anthropic([])
    assert system is None
    assert len(chat) == 1
    assert chat[0]["role"] == "user"
    assert chat[0]["content"] == ""


def test_build_messages_for_anthropic_only_system():
    """Только system-сообщение → system str, chat=[{role:user, content:''}]."""
    system, chat = avd._build_messages_for_anthropic(
        [
            {"role": "system", "content": "context only"},
        ]
    )
    assert system == "context only"
    assert len(chat) == 1
    assert chat[0]["role"] == "user"


# -----------------------------------------------------------------------------
# 6. complete_via_anthropic_vertex — успешный call
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_via_anthropic_vertex_success(monkeypatch):
    """Mock AnthropicVertex → возвращаем text из first ContentBlock."""
    fake_client = _patched_anthropic_vertex_client(text="hello from claude on vertex")
    fake_anthropic = _inject_anthropic_mock(monkeypatch, fake_client)

    text = await avd.complete_via_anthropic_vertex(
        model="anthropic-vertex/claude-opus-4-7",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert text == "hello from claude on vertex"
    # AnthropicVertex был вызван с project_id и region
    fake_anthropic.AnthropicVertex.assert_called_once()
    call_kwargs = fake_anthropic.AnthropicVertex.call_args.kwargs
    assert call_kwargs.get("project_id") == avd.DEFAULT_PROJECT
    assert call_kwargs.get("region") == avd.DEFAULT_REGION
    # messages.create был вызван с bare model name (без prefix)
    fake_client.messages.create.assert_called_once()
    create_kwargs = fake_client.messages.create.call_args.kwargs
    assert create_kwargs.get("model") == "claude-opus-4-7"


# -----------------------------------------------------------------------------
# 7. complete_via_anthropic_vertex — empty response handling
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_via_anthropic_vertex_empty_response(monkeypatch):
    """Пустой ContentBlock.text → возвращаем '' без exception."""
    fake_client = _patched_anthropic_vertex_client(text="")
    _inject_anthropic_mock(monkeypatch, fake_client)

    text = await avd.complete_via_anthropic_vertex(
        model="anthropic-vertex/claude-sonnet-4-6",
        messages=[{"role": "user", "content": "ping"}],
    )
    assert text == ""


# -----------------------------------------------------------------------------
# 8. complete_via_anthropic_vertex — env override project/region
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_via_anthropic_vertex_env_override(monkeypatch):
    """KRAB_ANTHROPIC_VERTEX_PROJECT / REGION env vars подхватываются."""
    fake_client = _patched_anthropic_vertex_client(text="env-override-ok")
    fake_anthropic = _inject_anthropic_mock(monkeypatch, fake_client)

    monkeypatch.setenv("KRAB_ANTHROPIC_VERTEX_PROJECT", "my-custom-project")
    monkeypatch.setenv("KRAB_ANTHROPIC_VERTEX_REGION", "us-central1")

    await avd.complete_via_anthropic_vertex(
        model="anthropic-vertex/claude-haiku-4-5",
        messages=[{"role": "user", "content": "test"}],
    )
    call_kwargs = fake_anthropic.AnthropicVertex.call_args.kwargs
    assert call_kwargs.get("project_id") == "my-custom-project"
    assert call_kwargs.get("region") == "us-central1"


# -----------------------------------------------------------------------------
# 9. complete_via_anthropic_vertex — kwarg override project/region
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_via_anthropic_vertex_kwarg_override(monkeypatch):
    """Явные kwargs project/region имеют приоритет над env и defaults."""
    fake_client = _patched_anthropic_vertex_client(text="kwarg-ok")
    fake_anthropic = _inject_anthropic_mock(monkeypatch, fake_client)

    await avd.complete_via_anthropic_vertex(
        model="anthropic-vertex/claude-opus-4-5",
        messages=[{"role": "user", "content": "x"}],
        project="explicit-project",
        region="europe-west4",
    )
    call_kwargs = fake_anthropic.AnthropicVertex.call_args.kwargs
    assert call_kwargs.get("project_id") == "explicit-project"
    assert call_kwargs.get("region") == "europe-west4"


# -----------------------------------------------------------------------------
# 10. complete_via_anthropic_vertex — exception propagated
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_via_anthropic_vertex_exception_propagated(monkeypatch):
    """Если AnthropicVertex.messages.create поднимает исключение — оно пробрасывается."""
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = RuntimeError("quota exceeded")
    _inject_anthropic_mock(monkeypatch, fake_client)

    with pytest.raises(RuntimeError, match="quota exceeded"):
        await avd.complete_via_anthropic_vertex(
            model="anthropic-vertex/claude-sonnet-4-5",
            messages=[{"role": "user", "content": "fail me"}],
        )
