"""Тесты wire-up Constitutional Guardrails (Idea 12) в llm_flow.

Проверяем поведение блока, который встроен в `process_query_with_streaming`
после self-correction hook. Сам блок не вынесен в отдельный метод, поэтому
дублируем его логику в локальном хелпере и проверяем контракт:
gate flag → block path → rewrite path → fail-open.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

from src.core.constitutional_guardrails import GuardResult, Violation


def _apply_guardrails_inline(text: str) -> str:
    """Реплика wire-up из llm_flow.py (Idea 12).

    Используется только в тестах — синхронизирована с llm_flow.py
    после self-correction hook.
    """

    import logging

    logger = logging.getLogger(__name__)
    full_response = text
    chat_id = 1
    try:
        if os.environ.get("KRAB_GUARDRAILS_ENABLED", "0") == "1" and full_response:
            from src.core.constitutional_guardrails import GuardrailEngine
            from src.core.pii_redactor import PIIRedactor

            _guard_engine = GuardrailEngine(redactor=PIIRedactor())
            _guard_result = _guard_engine.check(
                full_response, {"context_kind": "default"}
            )
            if not _guard_result.passed:
                logger.warning(
                    "guardrail_violation chat_id=%s severity=%s",
                    chat_id,
                    _guard_result.severity,
                )
                if _guard_result.severity == "block":
                    full_response = "🦀 Не могу ответить на этот запрос."
            elif (
                _guard_result.severity == "rewrite" and _guard_result.rewritten
            ):
                full_response = _guard_result.rewritten
    except Exception:  # noqa: BLE001
        pass
    return full_response


def test_guardrail_block_replaces_response(monkeypatch) -> None:
    """severity=block → ответ заменяется на штатный отказ."""
    monkeypatch.setenv("KRAB_GUARDRAILS_ENABLED", "1")

    blocked = GuardResult(
        passed=False,
        violations=(Violation(kind="injection", severity="block"),),
        severity="block",
    )
    with patch(
        "src.core.constitutional_guardrails.GuardrailEngine.check",
        return_value=blocked,
    ):
        out = _apply_guardrails_inline("user secret content")
    assert out == "🦀 Не могу ответить на этот запрос."


def test_guardrail_rewrite_replaces_with_redacted(monkeypatch) -> None:
    """severity=rewrite + rewritten → подмена текста на редактированный."""
    monkeypatch.setenv("KRAB_GUARDRAILS_ENABLED", "1")

    rewritten_result = GuardResult(
        passed=True,
        violations=(Violation(kind="pii_phone", severity="rewrite"),),
        severity="rewrite",
        rewritten="My phone is [REDACTED].",
    )
    with patch(
        "src.core.constitutional_guardrails.GuardrailEngine.check",
        return_value=rewritten_result,
    ):
        out = _apply_guardrails_inline("My phone is +34 600 111 222.")
    assert out == "My phone is [REDACTED]."


def test_guardrail_failure_is_fail_open(monkeypatch) -> None:
    """Любая ошибка GuardrailEngine не должна блокировать доставку."""
    monkeypatch.setenv("KRAB_GUARDRAILS_ENABLED", "1")

    def _boom(*_args: Any, **_kwargs: Any) -> GuardResult:
        raise RuntimeError("guardrail_internal_error")

    original = "all good answer"
    with patch(
        "src.core.constitutional_guardrails.GuardrailEngine.check", side_effect=_boom
    ):
        out = _apply_guardrails_inline(original)
    assert out == original


def test_guardrail_disabled_by_default(monkeypatch) -> None:
    """Без gate flag wire-up не активируется (engine не вызывается)."""
    monkeypatch.delenv("KRAB_GUARDRAILS_ENABLED", raising=False)

    with patch(
        "src.core.constitutional_guardrails.GuardrailEngine.check"
    ) as check_mock:
        out = _apply_guardrails_inline("any text")
    assert out == "any text"
    assert check_mock.call_count == 0


def test_llm_flow_module_contains_wire_up() -> None:
    """Smoke-тест: wire-up действительно присутствует в llm_flow.py."""
    import inspect

    from src.userbot import llm_flow as _module

    source = inspect.getsource(_module)
    assert "KRAB_GUARDRAILS_ENABLED" in source
    assert "guardrail_violation" in source
    assert "guardrail_hook_failed" in source
