# -*- coding: utf-8 -*-
"""
Юнит-тесты для scripts/e2e_smoke_test.py.

Покрытие:
1. _assert_response: пустой ответ → fail (min_length)
2. _assert_response: must_not_contain срабатывает
3. _assert_response: must_contain не найдено → fail
4. _assert_response: max_length превышен → fail
5. _assert_response: все проверки проходят → None
6. _render_report: содержит PASS/FAIL строки
7. E2ESmokeRunner.run_one: MCP send_message ошибка → FAIL без краша
8. E2ESmokeRunner.run_one: таймаут → FAIL с reason
9. E2ESmokeRunner.run_one: успешный ответ → PASS
10. TEST_CASES: набор содержит обязательные тесты
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Добавляем корень проекта в sys.path для прямого импорта scripts/
_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Импортируем модуль напрямую (не пакет — scripts/ без __init__.py)
import importlib.util

_SPEC = importlib.util.spec_from_file_location(
    "e2e_smoke_test",
    _ROOT / "scripts" / "e2e_smoke_test.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
# Регистрируем в sys.modules до exec_module — нужно для dataclasses (cls.__module__)
sys.modules["e2e_smoke_test"] = _MOD
_SPEC.loader.exec_module(_MOD)

# Сокращения для удобства
_assert_response = _MOD._assert_response
_render_report = _MOD._render_report
TestCase = _MOD.TestCase
TestResult = _MOD.TestResult
E2ESmokeRunner = _MOD.E2ESmokeRunner
TEST_CASES = _MOD.TEST_CASES


# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _case(**kw) -> TestCase:
    """Создать TestCase с дефолтами."""
    defaults = dict(
        name="test_stub",
        message="привет",
        must_contain=[],
        must_not_contain=[],
        min_length=1,
        description="stub",
    )
    defaults.update(kw)
    return TestCase(**defaults)


# ---------------------------------------------------------------------------
# _assert_response
# ---------------------------------------------------------------------------


class TestAssertResponse:
    def test_empty_response_fails_min_length(self):
        case = _case(min_length=1)
        result = _assert_response(case, "")
        assert result is not None
        assert "короткий" in result or "min" in result.lower()

    def test_must_not_contain_triggers_fail(self):
        case = _case(must_not_contain=["Мой Господин"])
        result = _assert_response(case, "Привет, Мой Господин!")
        assert result is not None
        assert "Мой Господин" in result

    def test_must_contain_not_found_fails(self):
        case = _case(must_contain=["pong"])
        result = _assert_response(case, "я здесь")
        assert result is not None
        assert "pong" in result

    def test_max_length_exceeded_fails(self):
        case = _case(max_length=5)
        result = _assert_response(case, "a" * 10)
        assert result is not None
        assert "длинный" in result or "max" in result.lower()

    def test_all_checks_pass_returns_none(self):
        case = _case(
            must_contain=["pong"],
            must_not_contain=["Господин"],
            min_length=3,
            max_length=100,
        )
        result = _assert_response(case, "pong — я в порядке!")
        assert result is None

    def test_must_contain_multiple_first_missing(self):
        case = _case(must_contain=["foo"])
        result = _assert_response(case, "bar baz")
        assert result is not None

    def test_must_contain_found_passes(self):
        case = _case(must_contain=["Level", "Proactivity"])
        result = _assert_response(case, "⚡ Proactivity Level: attentive\nthreshold: 0.7")
        assert result is None


# ---------------------------------------------------------------------------
# _render_report
# ---------------------------------------------------------------------------


class TestRenderReport:
    def _make_results(self, passed: bool) -> list[TestResult]:
        case = _case(name="sample_test", description="описание")
        return [TestResult(case=case, passed=passed, actual_text="ответ краба", elapsed=1.5)]

    def test_report_contains_pass_marker(self):
        results = self._make_results(passed=True)
        report = _render_report(results, elapsed_total=5.0)
        assert "PASS" in report or "✅" in report

    def test_report_contains_fail_marker(self):
        results = self._make_results(passed=False)
        report = _render_report(results, elapsed_total=5.0)
        assert "FAIL" in report or "❌" in report

    def test_report_contains_test_name(self):
        results = self._make_results(passed=True)
        report = _render_report(results, elapsed_total=5.0)
        assert "sample_test" in report

    def test_report_summary_fraction(self):
        r1 = TestResult(case=_case(name="t1"), passed=True, actual_text="ok", elapsed=1.0)
        r2 = TestResult(case=_case(name="t2"), passed=False, actual_text="", failure_reason="timeout", elapsed=60.0)
        report = _render_report([r1, r2], elapsed_total=62.0)
        assert "1/2" in report


# ---------------------------------------------------------------------------
# E2ESmokeRunner.run_one (async)
# ---------------------------------------------------------------------------


class TestRunnerRunOne:
    """Тесты для E2ESmokeRunner.run_one с мокнутым MCP."""

    def _make_runner(self, chat_id: int = 12345) -> E2ESmokeRunner:
        runner = E2ESmokeRunner(chat_id=chat_id, timeout=5.0, verbose=False)
        return runner

    @pytest.mark.asyncio
    async def test_send_failure_returns_fail_result(self):
        runner = self._make_runner()
        runner.mcp = MagicMock()
        runner.mcp.send_message = AsyncMock(side_effect=RuntimeError("network error"))
        case = _case(name="fail_send")
        result = await runner.run_one(case)
        assert not result.passed
        assert "send failed" in result.failure_reason

    @pytest.mark.asyncio
    async def test_timeout_returns_fail_result(self):
        runner = self._make_runner()
        runner.mcp = MagicMock()
        runner.mcp.send_message = AsyncMock(return_value={"id": 100})
        runner.mcp.get_history = AsyncMock(return_value=[])
        # Устанавливаем очень маленький timeout
        runner.timeout = 0.1
        case = _case(name="timeout_case")
        result = await runner.run_one(case)
        assert not result.passed
        assert "timeout" in result.failure_reason.lower()

    @pytest.mark.asyncio
    async def test_successful_reply_passes(self):
        runner = self._make_runner()
        runner.mcp = MagicMock()
        runner.mcp.send_message = AsyncMock(return_value={"id": 100})
        # История возвращает ответ от Краба (incoming, id > 100)
        runner.mcp.get_history = AsyncMock(
            return_value=[
                {"id": 101, "text": "pong — всё ок!", "outgoing": False},
            ]
        )
        case = _case(
            name="success_case",
            must_contain=["pong"],
            must_not_contain=["error"],
            min_length=3,
        )
        result = await runner.run_one(case)
        assert result.passed
        assert result.actual_text == "pong — всё ок!"

    @pytest.mark.asyncio
    async def test_forbidden_pattern_in_reply_fails(self):
        runner = self._make_runner()
        runner.mcp = MagicMock()
        runner.mcp.send_message = AsyncMock(return_value={"id": 200})
        runner.mcp.get_history = AsyncMock(
            return_value=[
                {"id": 201, "text": "Конечно, Мой Господин!", "outgoing": False},
            ]
        )
        case = _case(
            name="forbidden_case",
            must_not_contain=["Мой Господин"],
        )
        result = await runner.run_one(case)
        assert not result.passed
        assert "Мой Господин" in result.failure_reason


# ---------------------------------------------------------------------------
# TEST_CASES набор
# ---------------------------------------------------------------------------


class TestTestCases:
    """Проверяем что набор тестов содержит обязательные кейсы."""

    def _names(self) -> set[str]:
        return {c.name for c in TEST_CASES}

    def test_identity_basic_present(self):
        assert "identity_basic" in self._names()

    def test_phantom_guard_present(self):
        assert "phantom_action_guard" in self._names()

    def test_ping_present(self):
        assert "ping_short" in self._names()

    def test_no_gospodin_check_in_identity(self):
        """Тест identity_basic должен запрещать 'Мой Господин'."""
        case = next(c for c in TEST_CASES if c.name == "identity_basic")
        assert "Мой Господин" in case.must_not_contain

    def test_phantom_guard_forbids_phantom_actions(self):
        """Тест phantom_action_guard должен запрещать 'передал'."""
        case = next(c for c in TEST_CASES if c.name == "phantom_action_guard")
        assert any("передал" in pat for pat in case.must_not_contain)

    def test_all_cases_have_name_and_message(self):
        for c in TEST_CASES:
            assert c.name, f"TestCase без name: {c}"
            assert c.message, f"TestCase {c.name} без message"
