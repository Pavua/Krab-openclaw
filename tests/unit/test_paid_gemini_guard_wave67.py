"""Wave 67: тесты hard runtime guard блокировки paid AI Studio Gemini.

Cover:
- guard блокирует requests к generativelanguage.googleapis.com (default block)
- guard пропускает Vertex (aiplatform.googleapis.com)
- warn mode: логирует, но пропускает
- disabled mode (=0): полный pass-through
- Gemma модели (gemma-*) allowed в block mode (Wave 25-E)
- explicit allow-list через env KRAB_PAID_GEMINI_ALLOW_LIST
- регистрация идемпотентна
"""

from __future__ import annotations

import httpx
import pytest

from src.integrations.paid_gemini_guard import (
    PaidGeminiGuardError,
    _extract_model_from_url,
    _guard_mode,
    _is_allowed_model,
    _is_gemma_model,
    _is_paid_gemini_url,
    register_paid_gemini_guard,
    unregister_paid_gemini_guard,
)

# ---------------------------------------------------------------------------
# Fixture: гарантирует, что между тестами guard инициализируется заново
# и не утекает в другие тесты.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_guard():
    """Перед каждым тестом снимаем patch, чтобы тест начинал с чистого состояния."""
    unregister_paid_gemini_guard()
    yield
    unregister_paid_gemini_guard()


@pytest.fixture
def _block_mode(monkeypatch):
    """Default block mode (=1)."""
    monkeypatch.setenv("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", "1")
    monkeypatch.delenv("KRAB_PAID_GEMINI_ALLOW_LIST", raising=False)


@pytest.fixture
def _warn_mode(monkeypatch):
    monkeypatch.setenv("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", "warn")
    monkeypatch.delenv("KRAB_PAID_GEMINI_ALLOW_LIST", raising=False)


@pytest.fixture
def _off_mode(monkeypatch):
    monkeypatch.setenv("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", "0")
    monkeypatch.delenv("KRAB_PAID_GEMINI_ALLOW_LIST", raising=False)


# ---------------------------------------------------------------------------
# Helpers — мок-транспорт, чтобы httpx не пытался реально ходить в сеть.
# ---------------------------------------------------------------------------


def _ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"ok": True})


# ---------------------------------------------------------------------------
# Pure helpers (без monkey-patch httpx).
# ---------------------------------------------------------------------------


class TestPureHelpers:
    """Unit-тесты вспомогательных функций (не требуют patched httpx)."""

    def test_extract_model_from_generate_content(self):
        url = (
            "https://generativelanguage.googleapis.com/v1beta/"
            "models/gemini-3-pro-preview:generateContent"
        )
        assert _extract_model_from_url(url) == "gemini-3-pro-preview"

    def test_extract_model_from_stream_generate(self):
        url = (
            "https://generativelanguage.googleapis.com/v1beta/"
            "models/gemini-2.5-flash:streamGenerateContent"
        )
        assert _extract_model_from_url(url) == "gemini-2.5-flash"

    def test_extract_model_without_method(self):
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemma-3-27b-it"
        assert _extract_model_from_url(url) == "gemma-3-27b-it"

    def test_extract_model_models_listing(self):
        url = "https://generativelanguage.googleapis.com/v1beta/models"
        assert _extract_model_from_url(url) == ""

    def test_extract_model_empty(self):
        assert _extract_model_from_url("") == ""
        assert _extract_model_from_url("not a url") == ""

    def test_is_paid_gemini_url_positive(self):
        assert _is_paid_gemini_url(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro:generateContent"
        )

    def test_is_paid_gemini_url_vertex_no(self):
        assert not _is_paid_gemini_url(
            "https://us-central1-aiplatform.googleapis.com/v1/projects/foo/locations/us-central1/publishers/google/models/gemini-3-pro:generateContent"
        )

    def test_is_paid_gemini_url_other_google(self):
        assert not _is_paid_gemini_url("https://storage.googleapis.com/bucket/object")

    def test_is_paid_gemini_url_empty(self):
        assert not _is_paid_gemini_url("")

    def test_is_gemma_model(self):
        assert _is_gemma_model("gemma-3-27b-it")
        assert _is_gemma_model("gemma-2-9b-it")
        assert not _is_gemma_model("gemini-3-pro-preview")
        assert not _is_gemma_model("")

    def test_is_allowed_model_gemma_always(self, monkeypatch):
        monkeypatch.delenv("KRAB_PAID_GEMINI_ALLOW_LIST", raising=False)
        assert _is_allowed_model("gemma-3-27b-it")

    def test_is_allowed_model_env_csv(self, monkeypatch):
        monkeypatch.setenv("KRAB_PAID_GEMINI_ALLOW_LIST", "gemini-2.5-flash,foo-bar")
        assert _is_allowed_model("gemini-2.5-flash")
        assert _is_allowed_model("foo-bar")
        assert not _is_allowed_model("gemini-3-pro-preview")

    def test_guard_mode_resolution(self, monkeypatch):
        monkeypatch.setenv("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", "1")
        assert _guard_mode() == "block"
        monkeypatch.setenv("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", "warn")
        assert _guard_mode() == "warn"
        monkeypatch.setenv("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", "0")
        assert _guard_mode() == "off"
        # default → block
        monkeypatch.delenv("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", raising=False)
        assert _guard_mode() == "block"


# ---------------------------------------------------------------------------
# Integration: patched httpx — синхронный клиент.
# ---------------------------------------------------------------------------


class TestSyncClient:
    def test_guard_blocks_paid_generativelanguage_calls(self, _block_mode):
        """httpx.Client request к paid AI Studio → raises PaidGeminiGuardError."""
        register_paid_gemini_guard()

        transport = httpx.MockTransport(_ok_handler)
        with httpx.Client(transport=transport) as client:
            with pytest.raises(PaidGeminiGuardError) as excinfo:
                client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models/"
                    "gemini-3-pro-preview:generateContent"
                )

        assert excinfo.value.model == "gemini-3-pro-preview"
        assert "generativelanguage.googleapis.com" in str(excinfo.value)

    def test_guard_allows_vertex_endpoint(self, _block_mode):
        """Vertex AI endpoint (aiplatform.googleapis.com) → passes through."""
        register_paid_gemini_guard()

        transport = httpx.MockTransport(_ok_handler)
        with httpx.Client(transport=transport) as client:
            resp = client.get(
                "https://us-central1-aiplatform.googleapis.com/v1/projects/foo/"
                "locations/us-central1/publishers/google/models/"
                "gemini-3-pro-preview:generateContent"
            )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_guard_warn_mode_logs_but_passes(self, _warn_mode, capsys):
        """KRAB_BLOCK_PAID_GEMINI_AI_STUDIO=warn → log + pass-through.

        structlog по умолчанию пишет в stdout через ConsoleRenderer (а не через
        стандартный logging), поэтому ловим через capsys, а не caplog.
        """
        register_paid_gemini_guard()

        transport = httpx.MockTransport(_ok_handler)
        with httpx.Client(transport=transport) as client:
            resp = client.get(
                "https://generativelanguage.googleapis.com/v1beta/models/"
                "gemini-3-pro-preview:generateContent"
            )

        assert resp.status_code == 200
        # Warn-event должен попасть в stdout/stderr (structlog ConsoleRenderer).
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "paid_gemini_guard_warning" in combined

    def test_guard_disabled_passes_all(self, _off_mode):
        """KRAB_BLOCK_PAID_GEMINI_AI_STUDIO=0 → no interception."""
        register_paid_gemini_guard()

        transport = httpx.MockTransport(_ok_handler)
        with httpx.Client(transport=transport) as client:
            resp = client.get(
                "https://generativelanguage.googleapis.com/v1beta/models/"
                "gemini-3-pro-preview:generateContent"
            )
        assert resp.status_code == 200

    def test_gemma_model_allowed(self, _block_mode):
        """gemma- модели → allowed (Wave 25-E free tier через AI Studio)."""
        register_paid_gemini_guard()

        transport = httpx.MockTransport(_ok_handler)
        with httpx.Client(transport=transport) as client:
            resp = client.get(
                "https://generativelanguage.googleapis.com/v1beta/models/"
                "gemma-3-27b-it:generateContent"
            )
        assert resp.status_code == 200

    def test_env_allow_list_substring(self, _block_mode, monkeypatch):
        """KRAB_PAID_GEMINI_ALLOW_LIST позволяет explicit модели."""
        monkeypatch.setenv("KRAB_PAID_GEMINI_ALLOW_LIST", "gemini-2.5-flash")
        register_paid_gemini_guard()

        transport = httpx.MockTransport(_ok_handler)
        with httpx.Client(transport=transport) as client:
            resp = client.get(
                "https://generativelanguage.googleapis.com/v1beta/models/"
                "gemini-2.5-flash:generateContent"
            )
        assert resp.status_code == 200

        # Pro по-прежнему блокируется.
        with httpx.Client(transport=transport) as client:
            with pytest.raises(PaidGeminiGuardError):
                client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models/"
                    "gemini-3-pro-preview:generateContent"
                )


# ---------------------------------------------------------------------------
# Integration: patched httpx — async клиент.
# ---------------------------------------------------------------------------


class TestAsyncClient:
    @pytest.mark.asyncio
    async def test_async_blocks_paid(self, _block_mode):
        register_paid_gemini_guard()

        transport = httpx.MockTransport(_ok_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(PaidGeminiGuardError):
                await client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models/"
                    "gemini-3-pro-preview:generateContent"
                )

    @pytest.mark.asyncio
    async def test_async_allows_vertex(self, _block_mode):
        register_paid_gemini_guard()

        transport = httpx.MockTransport(_ok_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            resp = await client.get(
                "https://us-central1-aiplatform.googleapis.com/v1/projects/foo/"
                "locations/us-central1/publishers/google/models/"
                "gemini-3-pro-preview:generateContent"
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_async_gemma_allowed(self, _block_mode):
        register_paid_gemini_guard()

        transport = httpx.MockTransport(_ok_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            resp = await client.get(
                "https://generativelanguage.googleapis.com/v1beta/models/"
                "gemma-3-12b-it:generateContent"
            )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Регистрация: идемпотентность + восстановление.
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_register_idempotent(self, _block_mode):
        assert register_paid_gemini_guard()
        # Повторный вызов — без побочных эффектов и тоже True.
        assert register_paid_gemini_guard()

    def test_unregister_restores_original_init(self, _block_mode):
        # Запоминаем оригинал ДО patch.
        orig_init = httpx.Client.__init__
        register_paid_gemini_guard()
        assert httpx.Client.__init__ is not orig_init
        unregister_paid_gemini_guard()
        assert httpx.Client.__init__ is orig_init

    def test_unregister_without_register_noop(self):
        # Никаких exceptions при unregister без register.
        unregister_paid_gemini_guard()

    def test_user_event_hooks_preserved(self, _block_mode):
        """Если пользователь передал свой event_hook, наш hook не должен его
        затирать — append к существующему списку."""
        register_paid_gemini_guard()

        called = {"user_hook": False}

        def user_hook(request):
            called["user_hook"] = True

        transport = httpx.MockTransport(_ok_handler)
        with httpx.Client(
            transport=transport,
            event_hooks={"request": [user_hook]},
        ) as client:
            client.get("https://example.com/")

        assert called["user_hook"]
