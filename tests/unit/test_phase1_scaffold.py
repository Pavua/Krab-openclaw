# -*- coding: utf-8 -*-
"""
Phase 1 scaffold validation tests (Session 24).

Проверяет что новая structure для Code Splits готова без breaking
существующего ``command_handlers.py`` / ``web_app.py``.

См. ``docs/CODE_SPLITS_PLAN.md`` § "Phase 1: Prep".
"""

from __future__ import annotations

import pytest


def test_commands_package_importable():
    """src.handlers.commands импортируется без ошибок."""
    import src.handlers.commands  # noqa: F401


def test_commands_shared_exports_4_helpers():
    """src.handlers.commands._shared экспортирует 4 helpers."""
    from src.handlers.commands._shared import (
        _format_size_gb,
        _parse_toggle_arg,
        _reply_tech,
        _split_text_for_telegram,
    )

    assert callable(_reply_tech)
    assert callable(_parse_toggle_arg)
    assert callable(_format_size_gb)
    assert callable(_split_text_for_telegram)


def test_commands_shared_format_size_gb_basic():
    """_format_size_gb — basic semantics сохранены при extraction."""
    from src.handlers.commands._shared import _format_size_gb

    assert _format_size_gb(0) == "n/a"
    assert _format_size_gb(-5) == "n/a"
    assert _format_size_gb("invalid") == "n/a"  # type: ignore[arg-type]
    assert _format_size_gb(2.5) == "2.50 GB"
    assert _format_size_gb(13.7) == "13.70 GB"


def test_commands_shared_parse_toggle_on_off():
    """_parse_toggle_arg на on/off возвращает True/False."""
    from src.handlers.commands._shared import _parse_toggle_arg

    assert _parse_toggle_arg("on", field_name="test") is True
    assert _parse_toggle_arg("OFF", field_name="test") is False
    assert _parse_toggle_arg("On", field_name="test") is True


def test_commands_shared_parse_toggle_invalid_raises():
    """_parse_toggle_arg invalid input → UserInputError."""
    from src.core.exceptions import UserInputError
    from src.handlers.commands._shared import _parse_toggle_arg

    with pytest.raises(UserInputError):
        _parse_toggle_arg("yes", field_name="my_flag")


def test_commands_shared_split_text_short_returns_single():
    """_split_text_for_telegram короткий текст → 1 chunk."""
    from src.handlers.commands._shared import _split_text_for_telegram

    result = _split_text_for_telegram("hello world")
    assert result == ["hello world"]


def test_commands_shared_split_text_long_splits():
    """_split_text_for_telegram длинный текст → ≥2 chunks (limit=10 для теста)."""
    from src.handlers.commands._shared import _split_text_for_telegram

    text = "line1\nline2\nline3\nline4\nline5"
    chunks = _split_text_for_telegram(text, limit=10)
    assert len(chunks) >= 2
    # Все chunks под лимит
    assert all(len(c) <= 10 for c in chunks)


def test_web_routers_package_importable():
    """src.modules.web_routers импортируется без ошибок."""
    import src.modules.web_routers  # noqa: F401


def test_router_context_dataclass_creation():
    """RouterContext создаётся с минимальным набором полей."""
    from pathlib import Path

    from src.modules.web_routers._context import RouterContext

    ctx = RouterContext(
        deps={"foo": "bar"},
        project_root=Path("/tmp/test"),
        web_api_key_fn=lambda: "key",
        assert_write_access_fn=lambda *a, **k: None,
    )
    assert ctx.deps == {"foo": "bar"}
    assert ctx.project_root == Path("/tmp/test")
    assert ctx.rate_state == {}
    assert ctx.idempotency_state == {}


def test_router_context_get_dep_alias():
    """RouterContext.get_dep — alias для self.deps.get(name, default)."""
    from pathlib import Path

    from src.modules.web_routers._context import RouterContext

    ctx = RouterContext(
        deps={"k1": "v1"},
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "x",
        assert_write_access_fn=lambda *a, **k: None,
    )
    assert ctx.get_dep("k1") == "v1"
    assert ctx.get_dep("missing") is None
    assert ctx.get_dep("missing", "default") == "default"


def test_router_context_state_dicts_independent():
    """rate_state и idempotency_state — независимые dict instances per ctx."""
    from pathlib import Path

    from src.modules.web_routers._context import RouterContext

    ctx1 = RouterContext(
        deps={},
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "x",
        assert_write_access_fn=lambda *a, **k: None,
    )
    ctx2 = RouterContext(
        deps={},
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "x",
        assert_write_access_fn=lambda *a, **k: None,
    )
    ctx1.rate_state["foo"] = "bar"
    assert "foo" not in ctx2.rate_state, "state не должен быть shared между instances"


# --- Phase 2 foundation tests (Session 25) ---


def test_helpers_get_public_base_url_default(monkeypatch):
    """_helpers.get_public_base_url — default 127.0.0.1 + port."""
    from src.modules.web_routers._helpers import get_public_base_url

    monkeypatch.delenv("WEB_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("WEB_HOST", raising=False)
    assert get_public_base_url(default_port=8080) == "http://127.0.0.1:8080"
    assert get_public_base_url(default_port=9999) == "http://127.0.0.1:9999"


def test_helpers_get_public_base_url_env_override(monkeypatch):
    """WEB_PUBLIC_BASE_URL env — explicit override (с trim trailing slash)."""
    from src.modules.web_routers._helpers import get_public_base_url

    monkeypatch.setenv("WEB_PUBLIC_BASE_URL", "https://krab.example.com/")
    assert get_public_base_url() == "https://krab.example.com"


def test_helpers_assert_write_access_no_key_open(monkeypatch):
    """Без WEB_API_KEY — open access (no exception)."""
    from src.modules.web_routers._helpers import assert_write_access

    monkeypatch.delenv("WEB_API_KEY", raising=False)
    # Не должно бросать
    assert_write_access("", "")
    assert_write_access("anything", "another")


def test_helpers_assert_write_access_valid_key(monkeypatch):
    """Корректный header или token — passes."""
    from src.modules.web_routers._helpers import assert_write_access

    monkeypatch.setenv("WEB_API_KEY", "secret-xyz")
    assert_write_access("secret-xyz", "")
    assert_write_access("", "secret-xyz")


def test_helpers_assert_write_access_invalid_raises_403(monkeypatch):
    """Неверный ключ → HTTPException(403)."""
    from fastapi import HTTPException

    from src.modules.web_routers._helpers import assert_write_access

    monkeypatch.setenv("WEB_API_KEY", "secret-xyz")
    with pytest.raises(HTTPException) as exc:
        assert_write_access("wrong", "")
    assert exc.value.status_code == 403


def test_router_context_assert_write_access_method(monkeypatch):
    """RouterContext.assert_write_access делегирует в _helpers."""
    from pathlib import Path

    from fastapi import HTTPException

    from src.modules.web_routers._context import RouterContext

    monkeypatch.setenv("WEB_API_KEY", "k123")
    ctx = RouterContext(
        deps={},
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "k123",
        assert_write_access_fn=lambda *a, **k: None,
    )
    ctx.assert_write_access("k123", "")  # OK
    with pytest.raises(HTTPException):
        ctx.assert_write_access("bad", "")


def test_helpers_collect_policy_matrix_snapshot_basic(monkeypatch):
    """Phase 2 Wave H: promoted policy_matrix snapshot возвращает dict с ключами."""
    from src.modules.web_routers._helpers import collect_policy_matrix_snapshot

    monkeypatch.delenv("WEB_API_KEY", raising=False)
    snap = collect_policy_matrix_snapshot()
    assert isinstance(snap, dict)
    # build_policy_matrix должен вернуть structured payload — сверяем
    # минимум что это dict не пустой (точная shape проверяется на уровне
    # capability_registry tests).
    assert snap, "policy matrix snapshot не должен быть пустым"


def test_helpers_collect_policy_matrix_snapshot_runtime_lite_passed(monkeypatch):
    """runtime_lite arg прокидывается в build_policy_matrix без mutation."""
    from src.modules.web_routers import _helpers as helpers_mod

    captured: dict = {}

    def _fake_build_policy_matrix(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "runtime_seen": kwargs.get("runtime_lite")}

    monkeypatch.setattr(
        "src.core.capability_registry.build_policy_matrix",
        _fake_build_policy_matrix,
    )
    monkeypatch.setenv("WEB_API_KEY", "abc")

    runtime = {"key": "value"}
    out = helpers_mod.collect_policy_matrix_snapshot(runtime_lite=runtime)
    assert out == {"ok": True, "runtime_seen": runtime}
    assert captured["runtime_lite"] == runtime
    assert captured["web_write_requires_key"] is True


def test_router_context_policy_matrix_snapshot_delegates(monkeypatch):
    """RouterContext.policy_matrix_snapshot — thin delegating wrapper."""
    from pathlib import Path

    from src.modules.web_routers._context import RouterContext

    def _fake(**kwargs):
        return {"delegated": True, "rl": kwargs.get("runtime_lite")}

    monkeypatch.setattr(
        "src.modules.web_routers._helpers.collect_policy_matrix_snapshot",
        _fake,
    )
    ctx = RouterContext(
        deps={},
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda *a, **k: None,
    )
    out = ctx.policy_matrix_snapshot(runtime_lite={"x": 1})
    assert out == {"delegated": True, "rl": {"x": 1}}


def test_router_context_public_base_url_method(monkeypatch):
    """RouterContext.public_base_url делегирует и учитывает default_port."""
    from pathlib import Path

    from src.modules.web_routers._context import RouterContext

    monkeypatch.delenv("WEB_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("WEB_HOST", raising=False)
    ctx = RouterContext(
        deps={},
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda *a, **k: None,
        default_port=8765,
    )
    assert ctx.public_base_url() == "http://127.0.0.1:8765"


# -------------------------------------------------------------------------
# Phase 2 Wave P (Session 25): collect_runtime_lite_via_provider helper
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_helpers_collect_runtime_lite_via_provider_none_returns_empty():
    """Provider=None → пустой dict, без exception."""
    from src.modules.web_routers._helpers import collect_runtime_lite_via_provider

    out = await collect_runtime_lite_via_provider(None)
    assert out == {}


@pytest.mark.asyncio
async def test_helpers_collect_runtime_lite_via_provider_async_provider():
    """Async provider awaited; dict returned as-is."""
    from src.modules.web_routers._helpers import collect_runtime_lite_via_provider

    async def provider(*, force_refresh: bool = False):
        return {"state": "ok", "force_refresh": force_refresh}

    out = await collect_runtime_lite_via_provider(provider, force_refresh=True)
    assert out == {"state": "ok", "force_refresh": True}


@pytest.mark.asyncio
async def test_helpers_collect_runtime_lite_via_provider_sync_provider():
    """Sync provider returning dict — without await."""
    from src.modules.web_routers._helpers import collect_runtime_lite_via_provider

    def provider(*, force_refresh: bool = False):
        return {"sync": True, "fr": force_refresh}

    out = await collect_runtime_lite_via_provider(provider, force_refresh=False)
    assert out == {"sync": True, "fr": False}


@pytest.mark.asyncio
async def test_helpers_collect_runtime_lite_via_provider_legacy_signature():
    """Provider без force_refresh — fallback к bare call (TypeError ladder)."""
    from src.modules.web_routers._helpers import collect_runtime_lite_via_provider

    async def legacy_provider():
        return {"legacy": True}

    out = await collect_runtime_lite_via_provider(legacy_provider, force_refresh=True)
    assert out == {"legacy": True}


@pytest.mark.asyncio
async def test_router_context_collect_runtime_lite_force_refresh(monkeypatch):
    """RouterContext.collect_runtime_lite пробрасывает force_refresh."""
    from pathlib import Path

    from src.modules.web_routers._context import RouterContext

    captured: dict[str, bool] = {}

    async def provider(*, force_refresh: bool = False):
        captured["force_refresh"] = force_refresh
        return {"ok": True}

    ctx = RouterContext(
        deps={},
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda *a, **k: None,
        runtime_lite_provider=provider,
    )
    out = await ctx.collect_runtime_lite(force_refresh=True)
    assert out == {"ok": True}
    assert captured["force_refresh"] is True


@pytest.mark.asyncio
async def test_router_context_collect_runtime_lite_no_provider_returns_empty():
    """RouterContext без provider → {}."""
    from pathlib import Path

    from src.modules.web_routers._context import RouterContext

    ctx = RouterContext(
        deps={},
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda *a, **k: None,
    )
    out = await ctx.collect_runtime_lite()
    assert out == {}
