"""Wave 30-B: тесты для _bypass_sentry helper.

Проверяем:
1. add_bypass_breadcrumb() корректно вызывает sentry_sdk.add_breadcrumb
2. При ошибке импорта sentry_sdk → no exception (graceful)
3. Структура breadcrumb.data корректна
4. level override работает (default 'info')
5. bypass_kind корректно формирует category ('bypass.<kind>')
"""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_sentry() -> MagicMock:
    """Создаёт mock sentry_sdk с add_breadcrumb."""
    mock = MagicMock()
    mock.add_breadcrumb = MagicMock()
    return mock


def _call_helper(**kwargs) -> None:  # type: ignore[return]
    """Импортирует и вызывает add_bypass_breadcrumb с переданными kwargs."""
    # Перезагружаем модуль чтобы сбросить кэш env-gate
    if "src.integrations._bypass_sentry" in sys.modules:
        del sys.modules["src.integrations._bypass_sentry"]
    from src.integrations._bypass_sentry import add_bypass_breadcrumb

    add_bypass_breadcrumb(**kwargs)


# ---------------------------------------------------------------------------
# Тест 1: корректный вызов sentry_sdk.add_breadcrumb
# ---------------------------------------------------------------------------

def test_add_bypass_breadcrumb_calls_sentry() -> None:
    """add_bypass_breadcrumb() передаёт правильные аргументы в sentry_sdk.add_breadcrumb."""
    mock_sentry = _make_mock_sentry()

    with (
        patch.dict("sys.modules", {"sentry_sdk": mock_sentry}),
        patch.dict("os.environ", {"KRAB_BYPASS_SENTRY_BREADCRUMBS_ENABLED": "1"}),
    ):
        _call_helper(
            bypass_kind="cli",
            event="engaged",
            model="gpt-5.5",
            extra={"binary": "codex", "prompt_len": 128},
        )

    mock_sentry.add_breadcrumb.assert_called_once()
    call_kwargs = mock_sentry.add_breadcrumb.call_args[1]
    assert call_kwargs["category"] == "bypass.cli"
    assert call_kwargs["message"] == "cli_engaged"
    assert call_kwargs["level"] == "info"
    assert call_kwargs["data"]["model"] == "gpt-5.5"
    assert call_kwargs["data"]["binary"] == "codex"
    assert call_kwargs["data"]["prompt_len"] == 128


# ---------------------------------------------------------------------------
# Тест 2: graceful no-op при недоступном sentry_sdk
# ---------------------------------------------------------------------------

def test_add_bypass_breadcrumb_graceful_on_import_failure() -> None:
    """При ImportError sentry_sdk → функция не кидает исключение."""
    # Симулируем отсутствие sentry_sdk через None в sys.modules
    blocked: dict[str, ModuleType | None] = {"sentry_sdk": None}  # type: ignore[dict-item]

    with (
        patch.dict("sys.modules", blocked),
        patch.dict("os.environ", {"KRAB_BYPASS_SENTRY_BREADCRUMBS_ENABLED": "1"}),
    ):
        # Не должно кидать никаких исключений
        _call_helper(
            bypass_kind="vertex",
            event="failure",
            model="gemini-2.5-pro",
            extra={"error_type": "TimeoutError"},
            level="warning",
        )


# ---------------------------------------------------------------------------
# Тест 3: структура breadcrumb.data корректна (extra=None)
# ---------------------------------------------------------------------------

def test_add_bypass_breadcrumb_data_structure_without_extra() -> None:
    """При extra=None data содержит только ключ 'model'."""
    mock_sentry = _make_mock_sentry()

    with (
        patch.dict("sys.modules", {"sentry_sdk": mock_sentry}),
        patch.dict("os.environ", {"KRAB_BYPASS_SENTRY_BREADCRUMBS_ENABLED": "1"}),
    ):
        _call_helper(
            bypass_kind="google-direct",
            event="success",
            model="gemini-3-pro-preview",
        )

    call_kwargs = mock_sentry.add_breadcrumb.call_args[1]
    data = call_kwargs["data"]
    assert data == {"model": "gemini-3-pro-preview"}


# ---------------------------------------------------------------------------
# Тест 4: level override (warning вместо default info)
# ---------------------------------------------------------------------------

def test_add_bypass_breadcrumb_level_override() -> None:
    """level='warning' корректно прокидывается в sentry_sdk.add_breadcrumb."""
    mock_sentry = _make_mock_sentry()

    with (
        patch.dict("sys.modules", {"sentry_sdk": mock_sentry}),
        patch.dict("os.environ", {"KRAB_BYPASS_SENTRY_BREADCRUMBS_ENABLED": "1"}),
    ):
        _call_helper(
            bypass_kind="anthropic-vertex",
            event="timeout",
            model="claude-opus-4-7",
            extra={"timeout_sec": 300.0},
            level="warning",
        )

    call_kwargs = mock_sentry.add_breadcrumb.call_args[1]
    assert call_kwargs["level"] == "warning"


# ---------------------------------------------------------------------------
# Тест 5: bypass_kind корректно формирует category
# ---------------------------------------------------------------------------

def test_add_bypass_breadcrumb_category_composition() -> None:
    """category = 'bypass.<bypass_kind>' для всех поддерживаемых типов."""
    kinds_and_categories = [
        ("cli", "bypass.cli"),
        ("vertex", "bypass.vertex"),
        ("anthropic-vertex", "bypass.anthropic-vertex"),
        ("google-direct", "bypass.google-direct"),
        ("gemma", "bypass.gemma"),
    ]
    mock_sentry = _make_mock_sentry()

    with (
        patch.dict("sys.modules", {"sentry_sdk": mock_sentry}),
        patch.dict("os.environ", {"KRAB_BYPASS_SENTRY_BREADCRUMBS_ENABLED": "1"}),
    ):
        for bypass_kind, expected_category in kinds_and_categories:
            mock_sentry.add_breadcrumb.reset_mock()
            _call_helper(bypass_kind=bypass_kind, event="engaged", model="test-model")
            call_kwargs = mock_sentry.add_breadcrumb.call_args[1]
            assert call_kwargs["category"] == expected_category, (
                f"bypass_kind={bypass_kind!r}: "
                f"expected category={expected_category!r}, "
                f"got {call_kwargs['category']!r}"
            )
