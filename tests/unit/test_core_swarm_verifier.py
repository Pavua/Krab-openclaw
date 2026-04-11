# -*- coding: utf-8 -*-
"""
tests/unit/test_core_swarm_verifier.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Тесты для src/core/swarm_verifier.py.

Покрываем:
1. quick_heuristic_check — пустой, короткий, error-like, нормальный, длинный.
2. verify_round_result — успешный LLM ответ, невалидный JSON, исключение,
   early exit при явном fail, деградация к heuristic.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.swarm_verifier import (
    _MIN_ACCEPTABLE_LEN,
    _MIN_MEANINGFUL_LEN,
    VerificationResult,
    quick_heuristic_check,
    verify_round_result,
)

# ---------------------------------------------------------------------------
# quick_heuristic_check
# ---------------------------------------------------------------------------


class TestQuickHeuristicCheck:
    def test_empty_string_fails(self) -> None:
        r = quick_heuristic_check("")
        assert r.passed is False
        assert r.score == 0.0
        assert len(r.issues) > 0

    def test_whitespace_only_fails(self) -> None:
        r = quick_heuristic_check("   \n\t  ")
        assert r.passed is False
        assert r.score == 0.0

    def test_too_short_fails(self) -> None:
        short = "A" * (_MIN_MEANINGFUL_LEN - 1)
        r = quick_heuristic_check(short)
        assert r.passed is False
        assert r.score < 0.4
        assert any("коротк" in issue.lower() for issue in r.issues)

    def test_between_meaningful_and_acceptable_not_passed(self) -> None:
        # Длина в диапазоне [_MIN_MEANINGFUL_LEN, _MIN_ACCEPTABLE_LEN)
        mid_len = (_MIN_MEANINGFUL_LEN + _MIN_ACCEPTABLE_LEN) // 2
        text = "X" * mid_len
        r = quick_heuristic_check(text)
        assert r.passed is False
        assert 0.0 < r.score < 0.7

    def test_acceptable_length_passes(self) -> None:
        text = "B" * _MIN_ACCEPTABLE_LEN
        r = quick_heuristic_check(text)
        assert r.passed is True
        assert r.score >= 0.6

    def test_long_result_high_score(self) -> None:
        long_text = "Детальный анализ рынка. " * 100  # ~2400 символов
        r = quick_heuristic_check(long_text)
        assert r.passed is True
        assert r.score >= 0.8

    def test_error_prefix_fails(self) -> None:
        r = quick_heuristic_check("Error: connection refused to OpenClaw")
        assert r.passed is False
        assert r.score <= 0.2

    def test_traceback_fails(self) -> None:
        r = quick_heuristic_check("Traceback (most recent call last):\n  File ...\nValueError: bad")
        assert r.passed is False
        assert r.score <= 0.2

    def test_http_error_fails(self) -> None:
        r = quick_heuristic_check("HTTPError 503: service unavailable from provider")
        assert r.passed is False
        assert r.score <= 0.2

    def test_none_literal_fails(self) -> None:
        r = quick_heuristic_check("None")
        assert r.passed is False
        assert r.score <= 0.2

    def test_no_response_fails(self) -> None:
        r = quick_heuristic_check("No response received from model after timeout")
        assert r.passed is False
        assert r.score <= 0.2

    def test_score_clamped_between_0_and_1(self) -> None:
        # VerificationResult.__post_init__ зажимает score
        vr = VerificationResult(passed=True, score=1.5)
        assert vr.score == 1.0
        vr2 = VerificationResult(passed=False, score=-0.3)
        assert vr2.score == 0.0

    def test_normal_analytical_text_passes(self) -> None:
        text = (
            "По результатам анализа рынка криптовалют на апрель 2026 года "
            "выявлены следующие тренды: рост институционального интереса к BTC, "
            "снижение волатильности ETH, активное развитие Layer-2 решений. "
            "Рекомендуется диверсификация портфеля с акцентом на stable-coins "
            "в период неопределённости. Ключевые риски: регуляторное давление в EU, "
            "нестабильность stablecoin-эмитентов. Итог: умеренно-позитивный прогноз."
        )
        r = quick_heuristic_check(text)
        assert r.passed is True
        assert r.score >= 0.65


# ---------------------------------------------------------------------------
# verify_round_result — async, требует мока OpenClaw
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_llm_success() -> None:
    """LLM вернул корректный JSON — используем его."""
    mock_client = AsyncMock()
    mock_client.send_message_stream.return_value = (
        '{"passed": true, "score": 0.85, "issues": [], "suggestions": ["Добавить источники"]}'
    )

    long_result = "Детальный анализ. " * 30
    r = await verify_round_result(
        team="analysts",
        topic="Рынок BTC Q2 2026",
        result=long_result,
        openclaw_client=mock_client,
    )

    assert r.passed is True
    assert r.score == 0.85
    assert r.suggestions == ["Добавить источники"]
    mock_client.send_message_stream.assert_awaited_once()


@pytest.mark.asyncio
async def test_verify_llm_json_in_markdown_block() -> None:
    """LLM обернул JSON в markdown — извлекаем корректно."""
    mock_client = AsyncMock()
    mock_client.send_message_stream.return_value = (
        "Вот оценка:\n```json\n"
        '{"passed": false, "score": 0.4, "issues": ["Нет цифр"], "suggestions": []}'
        "\n```"
    )

    long_result = "Анализ: " * 30
    r = await verify_round_result(
        team="traders",
        topic="Forex",
        result=long_result,
        openclaw_client=mock_client,
    )

    assert r.passed is False
    assert r.score == 0.4
    assert "Нет цифр" in r.issues


@pytest.mark.asyncio
async def test_verify_llm_no_json_falls_back_to_heuristic() -> None:
    """LLM не вернул JSON — деградируем к heuristic."""
    mock_client = AsyncMock()
    mock_client.send_message_stream.return_value = "Результат хороший, молодцы."

    long_result = "Подробный отчёт команды аналитиков. " * 20
    r = await verify_round_result(
        team="analysts",
        topic="Тест",
        result=long_result,
        openclaw_client=mock_client,
    )

    # Heuristic для достаточно длинного текста должна вернуть passed=True
    assert r.passed is True


@pytest.mark.asyncio
async def test_verify_llm_exception_falls_back_to_heuristic() -> None:
    """Исключение при LLM-вызове — деградируем к heuristic без крэша."""
    mock_client = AsyncMock()
    mock_client.send_message_stream.side_effect = RuntimeError("OpenClaw недоступен")

    long_result = "Хороший длинный результат анализа команды. " * 20
    r = await verify_round_result(
        team="coders",
        topic="Тест исключения",
        result=long_result,
        openclaw_client=mock_client,
    )

    # Heuristic: длинный текст → passed
    assert r.passed is True
    assert isinstance(r.score, float)


@pytest.mark.asyncio
async def test_verify_empty_result_early_exit() -> None:
    """Пустой результат — early exit без вызова LLM."""
    mock_client = AsyncMock()

    r = await verify_round_result(
        team="creative",
        topic="Тест",
        result="",
        openclaw_client=mock_client,
    )

    assert r.passed is False
    assert r.score == 0.0
    mock_client.send_message_stream.assert_not_awaited()


# ---------------------------------------------------------------------------
# Дополнительные тесты: score clamping, multiple error patterns,
# VerificationResult defaults, длинный текст, markdown-форматирование
# ---------------------------------------------------------------------------


class TestVerificationResultDefaults:
    def test_default_issues_is_empty_list(self) -> None:
        vr = VerificationResult(passed=True, score=0.7)
        assert vr.issues == []

    def test_default_suggestions_is_empty_list(self) -> None:
        vr = VerificationResult(passed=False, score=0.3)
        assert vr.suggestions == []

    def test_score_clamped_above_one(self) -> None:
        vr = VerificationResult(passed=True, score=2.5)
        assert vr.score == 1.0

    def test_score_clamped_below_zero(self) -> None:
        vr = VerificationResult(passed=False, score=-99.0)
        assert vr.score == 0.0

    def test_score_at_boundary_zero(self) -> None:
        vr = VerificationResult(passed=False, score=0.0)
        assert vr.score == 0.0

    def test_score_at_boundary_one(self) -> None:
        vr = VerificationResult(passed=True, score=1.0)
        assert vr.score == 1.0


class TestMultipleErrorPatternsInOneText:
    def test_traceback_and_exception_in_one_text(self) -> None:
        # Сразу несколько error-паттернов — должен сработать первый совпавший
        text = "Traceback (most recent call last):\n  ...\nHTTPError 500: bad gateway"
        r = quick_heuristic_check(text)
        assert r.passed is False
        assert r.score <= 0.2
        assert len(r.issues) > 0

    def test_error_prefix_with_connection_error(self) -> None:
        text = "Error: ConnectionError raised while calling OpenClaw API"
        r = quick_heuristic_check(text)
        assert r.passed is False
        assert r.score <= 0.2

    def test_no_response_pattern(self) -> None:
        text = "No response received from the swarm agent after 30 seconds of waiting"
        r = quick_heuristic_check(text)
        assert r.passed is False
        assert r.score <= 0.2


class TestVeryLongResult:
    def test_very_long_result_passes_with_high_score(self) -> None:
        # Более 5000 символов — score насыщается до max (0.90)
        text = "Детальный анализ рынка и данные. " * 160  # ~5280 символов
        r = quick_heuristic_check(text)
        assert r.passed is True
        assert r.score >= 0.85

    def test_very_long_result_score_not_exceed_one(self) -> None:
        text = "A" * 100_000
        r = quick_heuristic_check(text)
        assert r.passed is True
        assert 0.0 <= r.score <= 1.0


class TestMarkdownFormattedResult:
    def test_markdown_headers_and_bullets_passes(self) -> None:
        text = (
            "# Отчёт команды аналитиков\n\n"
            "## Выводы\n"
            "- Рост BTC на 12% за квартал.\n"
            "- ETH показал стабильность на уровне $2800.\n"
            "- Layer-2 решения ускорили транзакции на 40%.\n\n"
            "## Риски\n"
            "- Регуляторное давление в ЕС.\n"
            "- Нестабильность стейблкоинов.\n\n"
            "## Рекомендации\n"
            "Диверсифицировать портфель. Держать долю стейблкоинов не выше 20%.\n"
        )
        r = quick_heuristic_check(text)
        assert r.passed is True
        assert r.score >= 0.65

    def test_markdown_with_code_block_passes(self) -> None:
        text = (
            "Анализ завершён успешно. Основные метрики за апрель 2026 года:\n\n"
            "```\n"
            "BTC: +12.3%\n"
            "ETH: +4.1%\n"
            "SOL: +8.9%\n"
            "ADA: -1.2%\n"
            "DOT: +3.7%\n"
            "```\n\n"
            "Итог: диверсифицированный портфель вырос на 8.4% за апрель 2026 года.\n"
            "Рекомендуется зафиксировать часть прибыли по BTC и перебалансировать.\n"
        )
        r = quick_heuristic_check(text)
        assert r.passed is True
        assert r.score >= 0.6


@pytest.mark.asyncio
async def test_verify_llm_score_clamped_from_llm_response() -> None:
    """LLM вернул score за пределами [0, 1] — __post_init__ зажимает."""
    mock_client = AsyncMock()
    mock_client.send_message_stream.return_value = (
        '{"passed": true, "score": 1.8, "issues": [], "suggestions": []}'
    )

    long_result = "Отчёт команды. " * 40
    r = await verify_round_result(
        team="analysts",
        topic="Тест clamp score",
        result=long_result,
        openclaw_client=mock_client,
    )

    assert r.passed is True
    assert r.score == 1.0
