"""Wave 23-A: тесты для Vertex AI direct SDK bypass."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.integrations import google_vertex_direct as gvd

# -----------------------------------------------------------------------------
# Helpers / fixtures
# -----------------------------------------------------------------------------


def _patched_genai_client(text: str = "ok") -> MagicMock:
    """Создаёт mock google.genai.Client с предзаданным response.text."""
    fake_resp = SimpleNamespace(text=text)
    fake_models = MagicMock()
    fake_models.generate_content.return_value = fake_resp
    fake_client = MagicMock()
    fake_client.models = fake_models
    return fake_client


# -----------------------------------------------------------------------------
# 1. is_vertex_enabled — default ON / OFF
# -----------------------------------------------------------------------------


def test_is_vertex_enabled_default_on(monkeypatch):
    monkeypatch.delenv("KRAB_VERTEX_BYPASS_ENABLED", raising=False)
    assert gvd.is_vertex_enabled() is True


def test_is_vertex_enabled_explicit_off(monkeypatch):
    monkeypatch.setenv("KRAB_VERTEX_BYPASS_ENABLED", "0")
    assert gvd.is_vertex_enabled() is False


# -----------------------------------------------------------------------------
# 2. is_vertex_model — префикс
# -----------------------------------------------------------------------------


def test_is_vertex_model_matches_prefix():
    assert gvd.is_vertex_model("google-vertex/gemini-2.5-pro") is True
    assert gvd.is_vertex_model("google-vertex/gemini-2.5-flash") is True


def test_is_vertex_model_rejects_other_prefixes():
    assert gvd.is_vertex_model("google/gemini-3-pro-preview") is False
    assert gvd.is_vertex_model("codex-cli/gpt-5") is False
    assert gvd.is_vertex_model("") is False
    assert gvd.is_vertex_model("gemini-2.5-pro") is False


# -----------------------------------------------------------------------------
# 3. _strip_prefix
# -----------------------------------------------------------------------------


def test_strip_prefix_removes_vertex_prefix():
    assert gvd._strip_prefix("google-vertex/gemini-2.5-pro") == "gemini-2.5-pro"
    # bare model — без изменений
    assert gvd._strip_prefix("gemini-2.5-flash") == "gemini-2.5-flash"


# -----------------------------------------------------------------------------
# 4. complete_via_vertex — успешный call
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_via_vertex_success(monkeypatch):
    """Mock google.genai.Client + GenerateContentConfig → возвращаем text."""
    fake_client = _patched_genai_client(text="hello from vertex")

    fake_genai_module = MagicMock()
    fake_genai_module.Client.return_value = fake_client
    fake_types_module = MagicMock()
    fake_types_module.GenerateContentConfig = MagicMock(return_value=MagicMock())

    monkeypatch.setitem(sys.modules, "google.genai", fake_genai_module)
    monkeypatch.setitem(sys.modules, "google.genai.types", fake_types_module)

    # Прячем фактический import google package если он есть в окружении
    fake_google_pkg = MagicMock()
    fake_google_pkg.genai = fake_genai_module
    monkeypatch.setitem(sys.modules, "google", fake_google_pkg)

    text = await gvd.complete_via_vertex(
        model="google-vertex/gemini-2.5-pro",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert text == "hello from vertex"
    # Client был вызван с vertexai=True
    fake_genai_module.Client.assert_called_once()
    kwargs = fake_genai_module.Client.call_args.kwargs
    assert kwargs.get("vertexai") is True
    assert kwargs.get("project") == gvd.DEFAULT_PROJECT
    assert kwargs.get("location") == gvd.DEFAULT_LOCATION


# -----------------------------------------------------------------------------
# 5. complete_via_vertex — empty response handling
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_via_vertex_empty_response(monkeypatch):
    fake_client = _patched_genai_client(text="")

    fake_genai_module = MagicMock()
    fake_genai_module.Client.return_value = fake_client
    fake_types_module = MagicMock()
    fake_types_module.GenerateContentConfig = MagicMock(return_value=MagicMock())

    monkeypatch.setitem(sys.modules, "google.genai", fake_genai_module)
    monkeypatch.setitem(sys.modules, "google.genai.types", fake_types_module)
    fake_google_pkg = MagicMock()
    fake_google_pkg.genai = fake_genai_module
    monkeypatch.setitem(sys.modules, "google", fake_google_pkg)

    text = await gvd.complete_via_vertex(
        model="google-vertex/gemini-2.5-flash",
        messages=[{"role": "user", "content": "ping"}],
    )
    # Пустая строка — без exception
    assert text == ""


# -----------------------------------------------------------------------------
# 6. complete_via_vertex — ADC missing → exception propagated
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_via_vertex_adc_missing_raises(monkeypatch):
    """Если Client init поднимает исключение (например ADC не найден) — пробрасывается."""
    fake_genai_module = MagicMock()
    fake_genai_module.Client.side_effect = RuntimeError("ADC not found")
    fake_types_module = MagicMock()
    fake_types_module.GenerateContentConfig = MagicMock(return_value=MagicMock())

    monkeypatch.setitem(sys.modules, "google.genai", fake_genai_module)
    monkeypatch.setitem(sys.modules, "google.genai.types", fake_types_module)
    fake_google_pkg = MagicMock()
    fake_google_pkg.genai = fake_genai_module
    monkeypatch.setitem(sys.modules, "google", fake_google_pkg)

    with pytest.raises(RuntimeError, match="ADC not found"):
        await gvd.complete_via_vertex(
            model="google-vertex/gemini-2.5-pro",
            messages=[{"role": "user", "content": "x"}],
        )


# -----------------------------------------------------------------------------
# 7. complete_via_vertex — custom project/location override
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_via_vertex_custom_project_location(monkeypatch):
    fake_client = _patched_genai_client(text="custom-loc")

    fake_genai_module = MagicMock()
    fake_genai_module.Client.return_value = fake_client
    fake_types_module = MagicMock()
    fake_types_module.GenerateContentConfig = MagicMock(return_value=MagicMock())

    monkeypatch.setitem(sys.modules, "google.genai", fake_genai_module)
    monkeypatch.setitem(sys.modules, "google.genai.types", fake_types_module)
    fake_google_pkg = MagicMock()
    fake_google_pkg.genai = fake_genai_module
    monkeypatch.setitem(sys.modules, "google", fake_google_pkg)

    await gvd.complete_via_vertex(
        model="google-vertex/gemini-2.5-pro",
        messages=[{"role": "user", "content": "y"}],
        project="my-project",
        location="europe-west4",
    )
    kwargs = fake_genai_module.Client.call_args.kwargs
    assert kwargs.get("project") == "my-project"
    assert kwargs.get("location") == "europe-west4"


# -----------------------------------------------------------------------------
# 8. complete_via_vertex — multi-role messages → склейка с префиксами
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_via_vertex_multi_role_messages(monkeypatch):
    fake_client = _patched_genai_client(text="multi-role-ok")

    fake_genai_module = MagicMock()
    fake_genai_module.Client.return_value = fake_client
    fake_types_module = MagicMock()
    fake_types_module.GenerateContentConfig = MagicMock(return_value=MagicMock())

    monkeypatch.setitem(sys.modules, "google.genai", fake_genai_module)
    monkeypatch.setitem(sys.modules, "google.genai.types", fake_types_module)
    fake_google_pkg = MagicMock()
    fake_google_pkg.genai = fake_genai_module
    monkeypatch.setitem(sys.modules, "google", fake_google_pkg)

    text = await gvd.complete_via_vertex(
        model="google-vertex/gemini-2.5-flash",
        messages=[
            {"role": "system", "content": "you are helpful"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "bye"},
        ],
    )
    assert text == "multi-role-ok"
    # Проверяем что generate_content получил единый prompt-string с префиксами
    call_kwargs = fake_client.models.generate_content.call_args.kwargs
    contents = call_kwargs.get("contents", "")
    assert "[Контекст]:" in contents
    assert "[Пользователь]:" in contents
    assert "[Ассистент]:" in contents
    # Strip префикса передан правильно (bare model)
    assert call_kwargs.get("model") == "gemini-2.5-flash"
