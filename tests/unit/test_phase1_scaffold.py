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
