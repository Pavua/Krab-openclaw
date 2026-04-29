# -*- coding: utf-8 -*-
"""
Тесты SelfCorrector (Feature H — Self-Correction Loop).

Все вызовы LM Studio замоканы через monkeypatch httpx.AsyncClient.post.
Проверяем 5 сценариев: ok=True passthrough, ok=False strict re-gens,
lenient log-only, timeout fail-open, short-response skip.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import httpx
import pytest

from src.core.self_correction import (
    DEFAULT_MIN_LENGTH,
    CorrectionResult,
    SelfCorrector,
    _parse_corrector_json,
    is_enabled,
    is_strict,
    reset_self_corrector,
    should_skip,
)

# ── Helpers ────────────────────────────────────────────────────────────────


def _mk_lm_response(ok: bool, issues: list[str], fix: str = "") -> MagicMock:
    """Сделать fake httpx Response с заданным JSON body."""
    body = json.dumps({"ok": ok, "issues": issues, "suggested_fix": fix})
    resp = MagicMock(spec=httpx.Response)
    resp.json.return_value = {"choices": [{"message": {"content": body}}]}
    resp.raise_for_status = MagicMock()
    return resp


def _patch_post(monkeypatch, response_or_exc):
    """Подменить httpx.AsyncClient.post на AsyncMock."""

    async def _fake_post(self, url, **kwargs):  # noqa: ARG001
        if isinstance(response_or_exc, BaseException):
            raise response_or_exc
        return response_or_exc

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_self_corrector()
    yield
    reset_self_corrector()


# ── Test 1: ok=True passes through ─────────────────────────────────────────


def test_check_response_ok_true_passes_through(monkeypatch):
    _patch_post(monkeypatch, _mk_lm_response(ok=True, issues=[]))
    corrector = SelfCorrector(timeout=2.0, min_length=10)

    long_answer = "Это вполне разумный ответ на вопрос пользователя." * 2
    result = asyncio.run(corrector.check_response("В чём смысл жизни?", long_answer))

    assert result.ok is True
    assert result.issues == []
    assert result.skipped is False
    assert result.error is None
    assert result.latency_ms >= 0.0


# ── Test 2: ok=False with strict — fix is available ───────────────────────


def test_check_response_ok_false_returns_suggested_fix(monkeypatch):
    fix_text = "Исправленный ответ без галлюцинаций."
    _patch_post(
        monkeypatch,
        _mk_lm_response(ok=False, issues=["hallucinated date"], fix=fix_text),
    )
    corrector = SelfCorrector(timeout=2.0, min_length=10)

    answer = "Эйнштейн родился в 1812 году в Берлине." * 3
    result = asyncio.run(corrector.check_response("Когда родился Эйнштейн?", answer))

    assert result.ok is False
    assert "hallucinated date" in result.issues
    assert result.suggested_fix == fix_text
    assert result.skipped is False


# ── Test 3: lenient mode — module-level helpers report False/False by default


def test_lenient_mode_default_flags_disabled(monkeypatch):
    monkeypatch.delenv("KRAB_SELF_CORRECTION_ENABLED", raising=False)
    monkeypatch.delenv("KRAB_SELF_CORRECTION_STRICT", raising=False)
    assert is_enabled() is False
    assert is_strict() is False

    monkeypatch.setenv("KRAB_SELF_CORRECTION_ENABLED", "1")
    monkeypatch.setenv("KRAB_SELF_CORRECTION_STRICT", "0")
    assert is_enabled() is True
    assert is_strict() is False  # lenient — log-only


# ── Test 4: timeout fail-open ──────────────────────────────────────────────


def test_check_response_timeout_fail_open(monkeypatch):
    async def _slow_post(self, url, **kwargs):  # noqa: ARG001
        await asyncio.sleep(5)
        return _mk_lm_response(ok=True, issues=[])

    monkeypatch.setattr(httpx.AsyncClient, "post", _slow_post)
    corrector = SelfCorrector(timeout=0.05, min_length=10)

    answer = "Достаточно длинный ответ для прохождения skip-фильтра, продолжаем дальше."
    result = asyncio.run(corrector.check_response("вопрос?", answer))

    # Fail-open: ok=True даже когда cheap-model упал по timeout
    assert result.ok is True
    assert result.error == "timeout"
    assert result.skipped is False


def test_check_response_http_error_fail_open(monkeypatch):
    _patch_post(monkeypatch, httpx.ConnectError("connection refused"))
    corrector = SelfCorrector(timeout=2.0, min_length=10)

    answer = "Достаточно длинный ответ для прохождения skip-фильтра, продолжаем дальше."
    result = asyncio.run(corrector.check_response("вопрос?", answer))

    assert result.ok is True
    assert result.error is not None
    assert "ConnectError" in result.error


# ── Test 5: short responses skip ───────────────────────────────────────────


def test_check_response_short_answer_skipped(monkeypatch):
    # Должен пропустить до httpx — поэтому если httpx вызовется, тест упадёт.
    raised = {"called": False}

    async def _should_not_call(self, url, **kwargs):  # noqa: ARG001
        raised["called"] = True
        return _mk_lm_response(ok=True, issues=[])

    monkeypatch.setattr(httpx.AsyncClient, "post", _should_not_call)
    corrector = SelfCorrector(timeout=2.0, min_length=DEFAULT_MIN_LENGTH)

    short_answer = "Да, ок."
    result = asyncio.run(corrector.check_response("вопрос?", short_answer))

    assert result.ok is True
    assert result.skipped is True
    assert result.skip_reason == "too_short"
    assert raised["called"] is False


# ── Bonus skip-cases (sanity checks для should_skip) ──────────────────────


def test_should_skip_recognizes_structured_outputs():
    long_str = "x" * (DEFAULT_MIN_LENGTH + 10)
    assert should_skip("")[0] is True
    assert should_skip("привет")[0] is True  # too_short

    skip, reason = should_skip('{"ok": true, "value": 42, "padding": "xxxxxxxxxxxxxxxxxxx"}')
    assert skip is True and reason == "structured_json"

    skip, reason = should_skip(
        "✅ команда выполнена успешно — всё в полном порядке, спасибо большое за внимание."
    )
    assert skip is True and reason == "command_output"

    # deferred-action маркер
    skip, reason = should_skip(f"Напомню тебе через 10 минут о встрече. {long_str}")
    assert skip is True and reason == "deferred_action"

    # Длинный обычный ответ — НЕ skip
    skip, _ = should_skip(long_str)
    assert skip is False


def test_parse_corrector_json_strips_markdown():
    raw = "```json\n{\"ok\": false, \"issues\": [\"x\"], \"suggested_fix\": \"y\"}\n```"
    parsed = _parse_corrector_json(raw)
    assert parsed.ok is False
    assert parsed.issues == ["x"]
    assert parsed.suggested_fix == "y"


def test_parse_corrector_json_extracts_object_from_noise():
    raw = "Here is my answer: {\"ok\": true, \"issues\": [], \"suggested_fix\": \"\"} — end."
    parsed = _parse_corrector_json(raw)
    assert parsed.ok is True
    assert parsed.issues == []


def test_correction_result_dataclass_defaults():
    r = CorrectionResult(ok=True)
    assert r.issues == []
    assert r.suggested_fix == ""
    assert r.skipped is False
    assert r.error is None
