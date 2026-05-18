# -*- coding: utf-8 -*-
"""S57 / Phase 3.1: local LLM draft verifier via Vertex Gemini Flash.

После local Gemma draft → P(0.2) sample → fire-and-forget Vertex Gemini
Flash verify call. Logs divergence score (semantic similarity / quality
delta). Async, off critical path.

Foundation for S58+ confidence-gated routing decisions. Pure observability:
не влияет на user-facing ответ, не блокирует hot path.

ENV:
- KRAB_LOCAL_DRAFT_VERIFY_ENABLED (default 0)
- KRAB_LOCAL_DRAFT_VERIFY_SAMPLE_RATE (default 0.2)
- KRAB_LOCAL_DRAFT_VERIFY_MODEL (default google-vertex/gemini-2.5-flash)
- KRAB_LOCAL_DRAFT_VERIFY_TIMEOUT_SEC (default 30)
"""

from __future__ import annotations

import os
import random
import re
import time
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)


_DEFAULT_SAMPLE_RATE = 0.2
_DEFAULT_MODEL = "google-vertex/gemini-2.5-flash"
_DEFAULT_TIMEOUT_SEC = 30.0
_MAX_PROMPT_CHARS = 4000  # обрезаем длинные prompt/response чтобы не раздувать verify
_MAX_RESPONSE_CHARS = 4000


def _env_truthy(name: str, default: str = "0") -> bool:
    return str(os.environ.get(name, default)).strip().lower() in {"1", "true", "yes", "on"}


def _get_sample_rate() -> float:
    raw = os.environ.get("KRAB_LOCAL_DRAFT_VERIFY_SAMPLE_RATE", str(_DEFAULT_SAMPLE_RATE))
    try:
        rate = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_SAMPLE_RATE
    # clamp [0, 1]
    if rate < 0.0:
        return 0.0
    if rate > 1.0:
        return 1.0
    return rate


def _get_verify_model() -> str:
    return os.environ.get("KRAB_LOCAL_DRAFT_VERIFY_MODEL", _DEFAULT_MODEL).strip() or _DEFAULT_MODEL


def _get_timeout_sec() -> float:
    raw = os.environ.get("KRAB_LOCAL_DRAFT_VERIFY_TIMEOUT_SEC", str(_DEFAULT_TIMEOUT_SEC))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT_SEC


def is_verifier_enabled() -> bool:
    """True if KRAB_LOCAL_DRAFT_VERIFY_ENABLED=1 (default OFF)."""
    return _env_truthy("KRAB_LOCAL_DRAFT_VERIFY_ENABLED", "0")


def _should_sample(rate: float) -> bool:
    """Bernoulli sample. Extracted so tests могут monkeypatch random."""
    if rate <= 0.0:
        return False
    if rate >= 1.0:
        return True
    return random.random() < rate


def _truncate(text: str, max_chars: int) -> str:
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"


def _build_verify_prompt(user_prompt: str, local_response: str) -> list[dict[str, Any]]:
    """Build messages for Flash verifier.

    Returns OpenAI-compatible messages list (system + user). Verifier
    инструктирован вернуть quality_score (0-10) + issues flag.
    """
    system = (
        "You are a quality evaluator for AI assistant responses. Given a USER PROMPT "
        "and a LOCAL MODEL RESPONSE, rate the response quality on a 0-10 scale "
        "(0=incoherent/wrong, 10=excellent). Be strict but fair.\n\n"
        "Output format (single line, no extra text):\n"
        "QUALITY_SCORE: <integer 0-10> | ISSUES: <none|brief description>"
    )
    user = (
        f"USER PROMPT:\n{_truncate(user_prompt, _MAX_PROMPT_CHARS)}\n\n"
        f"LOCAL MODEL RESPONSE:\n{_truncate(local_response, _MAX_RESPONSE_CHARS)}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


_SCORE_RE = re.compile(r"QUALITY_SCORE\s*:\s*(\d+)", re.IGNORECASE)
_ISSUES_RE = re.compile(r"ISSUES\s*:\s*(.+?)(?:$|\n)", re.IGNORECASE | re.DOTALL)


def _parse_verify_result(raw: str) -> dict[str, Any]:
    """Extract quality_score (int 0-10) и issues (str) из verifier output."""
    score: int | None = None
    issues: str = ""
    if not raw:
        return {"quality_score": None, "issues": "", "raw": ""}
    m = _SCORE_RE.search(raw)
    if m:
        try:
            parsed = int(m.group(1))
            if 0 <= parsed <= 10:
                score = parsed
        except (TypeError, ValueError):
            pass
    m2 = _ISSUES_RE.search(raw)
    if m2:
        issues = m2.group(1).strip()[:300]
    return {"quality_score": score, "issues": issues, "raw": raw[:500]}


async def verify_local_draft(
    *,
    user_prompt: str,
    local_response: str,
    local_model: str,
    chat_id: str,
    request_id: str,
) -> None:
    """Fire-and-forget verifier. Always returns None.

    Logs (structlog):
    - local_draft_verify_skipped (env disabled / not sampled / empty input)
    - local_draft_verify_started (sampling hit)
    - local_draft_verify_ok (verifier returned + parsed score)
    - local_draft_verify_failed (cloud call error)

    Never raises. Caller can `asyncio.create_task(...)` без await.
    """

    # S62 W6: lazy import — best-effort counter, не должен ломать verifier.
    def _bump(status: str) -> None:
        try:
            from src.core.metrics.idle_skip import inc_verifier_sample

            inc_verifier_sample(status)
        except Exception:  # noqa: BLE001
            pass

    if not is_verifier_enabled():
        _bump("skipped_env_disabled")
        logger.debug(
            "local_draft_verify_skipped",
            reason="env_disabled",
            chat_id=chat_id,
            request_id=request_id,
        )
        return

    if not local_response or not user_prompt:
        _bump("skipped_empty_input")
        logger.debug(
            "local_draft_verify_skipped",
            reason="empty_input",
            chat_id=chat_id,
            request_id=request_id,
            has_prompt=bool(user_prompt),
            has_response=bool(local_response),
        )
        return

    sample_rate = _get_sample_rate()
    if not _should_sample(sample_rate):
        _bump("skipped_not_sampled")
        logger.debug(
            "local_draft_verify_skipped",
            reason="not_sampled",
            chat_id=chat_id,
            request_id=request_id,
            sample_rate=sample_rate,
        )
        return

    _bump("sampled")
    verify_model = _get_verify_model()
    timeout_sec = _get_timeout_sec()

    logger.info(
        "local_draft_verify_started",
        chat_id=chat_id,
        request_id=request_id,
        local_model=local_model,
        verify_model=verify_model,
        sample_rate=sample_rate,
        response_chars=len(local_response),
    )

    started_at = time.perf_counter()
    try:
        # Lazy import чтобы избежать circular deps + heavy SDK при module load
        from ..integrations.google_genai_direct import complete_direct

        messages = _build_verify_prompt(user_prompt, local_response)
        raw_output = await complete_direct(
            model=verify_model,
            messages=messages,
            timeout_sec=timeout_sec,
            max_output_tokens=200,
        )
    except Exception as exc:  # noqa: BLE001 — fire-and-forget, log only
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        logger.warning(
            "local_draft_verify_failed",
            chat_id=chat_id,
            request_id=request_id,
            local_model=local_model,
            verify_model=verify_model,
            elapsed_ms=elapsed_ms,
            error=str(exc)[:200],
            error_type=type(exc).__name__,
        )
        return

    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    parsed = _parse_verify_result(raw_output or "")
    logger.info(
        "local_draft_verify_ok",
        chat_id=chat_id,
        request_id=request_id,
        local_model=local_model,
        verify_model=verify_model,
        elapsed_ms=elapsed_ms,
        quality_score=parsed["quality_score"],
        issues=parsed["issues"],
        raw_len=len(raw_output or ""),
    )
    # Divergence score = (10 - quality_score) для удобства downstream alerts
    # (high score = high divergence). Логируем отдельным событием для
    # последующего Prometheus scrape / dashboards.
    if parsed["quality_score"] is not None:
        divergence = 10 - parsed["quality_score"]
        logger.info(
            "local_draft_verify_divergence_score",
            chat_id=chat_id,
            request_id=request_id,
            local_model=local_model,
            verify_model=verify_model,
            quality_score=parsed["quality_score"],
            divergence_score=divergence,
        )
