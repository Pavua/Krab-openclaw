# -*- coding: utf-8 -*-
"""Tests для S57 / Phase 3.1: local_draft_verifier.

Pure observability module — verify:
- env gate (KRAB_LOCAL_DRAFT_VERIFY_ENABLED)
- sampling (KRAB_LOCAL_DRAFT_VERIFY_SAMPLE_RATE)
- prompt build + truncation
- score parsing
- failure handling (no raise)
- fire-and-forget pattern (does not block caller)
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from src.core import local_draft_verifier as ldv

# --------- env / sample helpers ---------


def test_is_verifier_enabled_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KRAB_LOCAL_DRAFT_VERIFY_ENABLED", raising=False)
    assert ldv.is_verifier_enabled() is False


@pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "on"])
def test_is_verifier_enabled_truthy(monkeypatch: pytest.MonkeyPatch, val: str) -> None:
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_ENABLED", val)
    assert ldv.is_verifier_enabled() is True


@pytest.mark.parametrize("val", ["0", "false", "no", "off", "", "garbage"])
def test_is_verifier_enabled_falsy(monkeypatch: pytest.MonkeyPatch, val: str) -> None:
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_ENABLED", val)
    assert ldv.is_verifier_enabled() is False


def test_get_sample_rate_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KRAB_LOCAL_DRAFT_VERIFY_SAMPLE_RATE", raising=False)
    assert ldv._get_sample_rate() == pytest.approx(0.2)


def test_get_sample_rate_custom(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_SAMPLE_RATE", "0.5")
    assert ldv._get_sample_rate() == pytest.approx(0.5)


def test_get_sample_rate_clamped_high(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_SAMPLE_RATE", "5.0")
    assert ldv._get_sample_rate() == 1.0


def test_get_sample_rate_clamped_low(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_SAMPLE_RATE", "-0.5")
    assert ldv._get_sample_rate() == 0.0


def test_get_sample_rate_invalid_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_SAMPLE_RATE", "not_a_number")
    assert ldv._get_sample_rate() == pytest.approx(0.2)


def test_get_verify_model_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KRAB_LOCAL_DRAFT_VERIFY_MODEL", raising=False)
    assert ldv._get_verify_model() == "google-vertex/gemini-2.5-flash"


def test_get_verify_model_custom(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_MODEL", "google/gemini-3-flash-preview")
    assert ldv._get_verify_model() == "google/gemini-3-flash-preview"


def test_get_timeout_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KRAB_LOCAL_DRAFT_VERIFY_TIMEOUT_SEC", raising=False)
    assert ldv._get_timeout_sec() == pytest.approx(30.0)


def test_get_timeout_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_TIMEOUT_SEC", "abc")
    assert ldv._get_timeout_sec() == pytest.approx(30.0)


def test_should_sample_zero() -> None:
    assert ldv._should_sample(0.0) is False


def test_should_sample_one() -> None:
    assert ldv._should_sample(1.0) is True


def test_should_sample_random(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ldv.random, "random", lambda: 0.1)
    assert ldv._should_sample(0.2) is True
    monkeypatch.setattr(ldv.random, "random", lambda: 0.9)
    assert ldv._should_sample(0.2) is False


# --------- prompt build + parsing ---------


def test_truncate_short() -> None:
    assert ldv._truncate("hello", 10) == "hello"


def test_truncate_long() -> None:
    out = ldv._truncate("x" * 100, 10)
    assert out.startswith("xxxxxxxxxx")
    assert out.endswith("…")
    assert len(out) == 11  # 10 + ellipsis


def test_truncate_empty() -> None:
    assert ldv._truncate("", 10) == ""


def test_build_verify_prompt_structure() -> None:
    msgs = ldv._build_verify_prompt("user q", "local resp")
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert "QUALITY_SCORE" in msgs[0]["content"]
    assert msgs[1]["role"] == "user"
    assert "user q" in msgs[1]["content"]
    assert "local resp" in msgs[1]["content"]


def test_build_verify_prompt_truncates_long_inputs() -> None:
    long_prompt = "a" * 10000
    long_response = "b" * 10000
    msgs = ldv._build_verify_prompt(long_prompt, long_response)
    # 2x truncate to 4000 + small overhead from labels + ellipsis
    user_content = msgs[1]["content"]
    assert len(user_content) < 10000


def test_parse_verify_result_full() -> None:
    raw = "QUALITY_SCORE: 8 | ISSUES: minor typo"
    out = ldv._parse_verify_result(raw)
    assert out["quality_score"] == 8
    assert "minor typo" in out["issues"]


def test_parse_verify_result_no_issues() -> None:
    raw = "QUALITY_SCORE: 10 | ISSUES: none"
    out = ldv._parse_verify_result(raw)
    assert out["quality_score"] == 10
    assert "none" in out["issues"].lower()


def test_parse_verify_result_no_score() -> None:
    raw = "unparseable garbage"
    out = ldv._parse_verify_result(raw)
    assert out["quality_score"] is None
    assert out["issues"] == ""


def test_parse_verify_result_score_out_of_range() -> None:
    raw = "QUALITY_SCORE: 99 | ISSUES: huh"
    out = ldv._parse_verify_result(raw)
    assert out["quality_score"] is None  # clamped to None when out of [0,10]


def test_parse_verify_result_empty() -> None:
    out = ldv._parse_verify_result("")
    assert out["quality_score"] is None
    assert out["raw"] == ""


# --------- verify_local_draft async ---------


@pytest.mark.asyncio
async def test_verify_skipped_when_env_disabled(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_ENABLED", "0")
    called = False

    async def _fake_complete(*args, **kwargs):
        nonlocal called
        called = True
        return "QUALITY_SCORE: 9"

    with patch("src.integrations.google_genai_direct.complete_direct", _fake_complete):
        await ldv.verify_local_draft(
            user_prompt="hi",
            local_response="hello",
            local_model="mlx-local-kv4/gemma",
            chat_id="42",
            request_id="r1",
        )
    assert called is False


@pytest.mark.asyncio
async def test_verify_skipped_when_not_sampled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_ENABLED", "1")
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_SAMPLE_RATE", "0.2")
    # Force NOT sampled
    monkeypatch.setattr(ldv.random, "random", lambda: 0.99)
    called = False

    async def _fake_complete(*args, **kwargs):
        nonlocal called
        called = True
        return ""

    with patch("src.integrations.google_genai_direct.complete_direct", _fake_complete):
        await ldv.verify_local_draft(
            user_prompt="hi",
            local_response="hello",
            local_model="mlx-local-kv4/gemma",
            chat_id="42",
            request_id="r1",
        )
    assert called is False


@pytest.mark.asyncio
async def test_verify_skipped_when_empty_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_ENABLED", "1")
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_SAMPLE_RATE", "1.0")
    called = False

    async def _fake_complete(*args, **kwargs):
        nonlocal called
        called = True
        return ""

    with patch("src.integrations.google_genai_direct.complete_direct", _fake_complete):
        await ldv.verify_local_draft(
            user_prompt="",
            local_response="hello",
            local_model="mlx-local-kv4/gemma",
            chat_id="42",
            request_id="r1",
        )
    assert called is False


@pytest.mark.asyncio
async def test_verify_started_when_sampled(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_ENABLED", "1")
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_SAMPLE_RATE", "1.0")
    fake = AsyncMock(return_value="QUALITY_SCORE: 7 | ISSUES: none")
    with patch("src.integrations.google_genai_direct.complete_direct", fake):
        await ldv.verify_local_draft(
            user_prompt="explain X",
            local_response="X is foo",
            local_model="mlx-local-kv4/gemma",
            chat_id="42",
            request_id="r1",
        )
    fake.assert_awaited_once()
    # validate kwargs passed
    _, kwargs = fake.call_args
    assert kwargs["model"] == ldv._get_verify_model()
    assert isinstance(kwargs["messages"], list) and len(kwargs["messages"]) == 2


@pytest.mark.asyncio
async def test_verify_extracts_quality_score(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_ENABLED", "1")
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_SAMPLE_RATE", "1.0")
    fake = AsyncMock(return_value="QUALITY_SCORE: 6 | ISSUES: vague")

    captured: dict = {}
    orig_parse = ldv._parse_verify_result

    def _spy(raw: str) -> dict:
        out = orig_parse(raw)
        captured.update(out)
        return out

    monkeypatch.setattr(ldv, "_parse_verify_result", _spy)
    with patch("src.integrations.google_genai_direct.complete_direct", fake):
        await ldv.verify_local_draft(
            user_prompt="q",
            local_response="resp",
            local_model="mlx-local-kv4/gemma",
            chat_id="42",
            request_id="r1",
        )
    assert captured["quality_score"] == 6
    assert "vague" in captured["issues"]


@pytest.mark.asyncio
async def test_verify_handles_cloud_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_ENABLED", "1")
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_SAMPLE_RATE", "1.0")

    async def _boom(*args, **kwargs):
        raise RuntimeError("vertex 500")

    with patch("src.integrations.google_genai_direct.complete_direct", _boom):
        # Should NOT raise
        result = await ldv.verify_local_draft(
            user_prompt="q",
            local_response="r",
            local_model="mlx-local-kv4/gemma",
            chat_id="42",
            request_id="r1",
        )
    assert result is None


@pytest.mark.asyncio
async def test_verify_handles_import_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Если google_genai_direct unavailable — должно log + return, не raise."""
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_ENABLED", "1")
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_SAMPLE_RATE", "1.0")

    async def _raise_import(*args, **kwargs):
        raise ImportError("no genai")

    with patch("src.integrations.google_genai_direct.complete_direct", _raise_import):
        await ldv.verify_local_draft(
            user_prompt="q",
            local_response="r",
            local_model="mlx-local-kv4/gemma",
            chat_id="42",
            request_id="r1",
        )


@pytest.mark.asyncio
async def test_verify_fire_and_forget_does_not_block_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_task pattern: caller продолжает работу, verifier выполняется в фоне."""
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_ENABLED", "1")
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_SAMPLE_RATE", "1.0")

    completed_event = asyncio.Event()

    async def _slow(*args, **kwargs):
        await asyncio.sleep(0.05)
        completed_event.set()
        return "QUALITY_SCORE: 8"

    with patch("src.integrations.google_genai_direct.complete_direct", _slow):
        task = asyncio.create_task(
            ldv.verify_local_draft(
                user_prompt="q",
                local_response="r",
                local_model="mlx-local-kv4/gemma",
                chat_id="42",
                request_id="r1",
            )
        )
        # Caller "continues" immediately — verifier still running
        assert not completed_event.is_set()
        await task
        assert completed_event.is_set()


@pytest.mark.asyncio
async def test_verify_returns_none_always(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_ENABLED", "1")
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_SAMPLE_RATE", "1.0")
    fake = AsyncMock(return_value="QUALITY_SCORE: 9 | ISSUES: none")
    with patch("src.integrations.google_genai_direct.complete_direct", fake):
        result = await ldv.verify_local_draft(
            user_prompt="q",
            local_response="r",
            local_model="mlx-local-kv4/gemma",
            chat_id="42",
            request_id="r1",
        )
    assert result is None


@pytest.mark.asyncio
async def test_verify_uses_custom_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_ENABLED", "1")
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_SAMPLE_RATE", "1.0")
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_MODEL", "google/gemini-3-flash-preview")
    fake = AsyncMock(return_value="QUALITY_SCORE: 8")
    with patch("src.integrations.google_genai_direct.complete_direct", fake):
        await ldv.verify_local_draft(
            user_prompt="q",
            local_response="r",
            local_model="mlx-local-kv4/gemma",
            chat_id="42",
            request_id="r1",
        )
    _, kwargs = fake.call_args
    assert kwargs["model"] == "google/gemini-3-flash-preview"


@pytest.mark.asyncio
async def test_verify_uses_custom_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_ENABLED", "1")
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_SAMPLE_RATE", "1.0")
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_TIMEOUT_SEC", "5")
    fake = AsyncMock(return_value="QUALITY_SCORE: 8")
    with patch("src.integrations.google_genai_direct.complete_direct", fake):
        await ldv.verify_local_draft(
            user_prompt="q",
            local_response="r",
            local_model="mlx-local-kv4/gemma",
            chat_id="42",
            request_id="r1",
        )
    _, kwargs = fake.call_args
    assert kwargs["timeout_sec"] == 5.0


@pytest.mark.asyncio
async def test_verify_logs_divergence_score(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_ENABLED", "1")
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_SAMPLE_RATE", "1.0")
    fake = AsyncMock(return_value="QUALITY_SCORE: 7 | ISSUES: none")
    with patch("src.integrations.google_genai_direct.complete_direct", fake):
        await ldv.verify_local_draft(
            user_prompt="q",
            local_response="r",
            local_model="mlx-local-kv4/gemma",
            chat_id="42",
            request_id="r1",
        )
    out = capsys.readouterr().out
    assert "local_draft_verify_divergence_score" in out
    assert "divergence_score=3" in out  # 10 - 7


@pytest.mark.asyncio
async def test_verify_no_divergence_log_when_score_unparseable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_ENABLED", "1")
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_SAMPLE_RATE", "1.0")
    fake = AsyncMock(return_value="totally unparseable")
    with patch("src.integrations.google_genai_direct.complete_direct", fake):
        await ldv.verify_local_draft(
            user_prompt="q",
            local_response="r",
            local_model="mlx-local-kv4/gemma",
            chat_id="42",
            request_id="r1",
        )
    out = capsys.readouterr().out
    # ok event still fires but divergence event should not (score is None)
    assert "local_draft_verify_divergence_score" not in out
    assert "local_draft_verify_ok" in out
