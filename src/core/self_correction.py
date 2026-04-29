# -*- coding: utf-8 -*-
"""
SelfCorrector — Feature H: Self-Correction Loop.

Cheap secondary model просматривает ответ Krab перед `.reply()` и помечает
очевидные галлюцинации / противоречия / несоответствия вопросу.

Режимы:
- lenient (default): логируем issues, отправляем ответ как есть (suggested_fix
  опционально применяется только в strict).
- strict: если cheap-model нашёл проблемы, возвращаем `suggested_fix` (если есть)
  либо просим caller инициировать regeneration (caller сам решает).

Skip-условия:
- response короче 50 символов
- ответ выглядит как command output / structured JSON
- deferred_action notice (определяется маркером, передаваемым caller'ом)

Cheap model:
- LM Studio local (qwen2.5-1.5b-instruct, phi-3-mini и пр.) — preferred
- Gemini Flash как fallback (если LM Studio недоступен)

Timeout: 5s. На любую ошибку — fail-open (returns ok=True, error=...).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field

import httpx
import structlog

logger = structlog.get_logger(__name__)


# ── Дефолты ────────────────────────────────────────────────────────────────

DEFAULT_TIMEOUT_SEC = 5.0
DEFAULT_MIN_LENGTH = 50
DEFAULT_MODEL = "qwen2.5-1.5b-instruct"
LM_STUDIO_URL = os.getenv("LM_STUDIO_URL", "http://localhost:1234").rstrip("/")
LM_STUDIO_CHAT_ENDPOINT = f"{LM_STUDIO_URL}/v1/chat/completions"

# Маркеры структурированного вывода / служебных сообщений — пропускаем проверку.
_DEFERRED_ACTION_MARKERS = (
    "напомню",
    "напомнил",
    "[deferred]",
    "[отложено]",
)
_COMMAND_OUTPUT_PREFIXES = (
    "✅",
    "❌",
    "⚠️",
    "```",
    "$ ",
    ">>>",
)


# ── DTO ────────────────────────────────────────────────────────────────────


@dataclass
class CorrectionResult:
    """Результат проверки cheap-model."""

    ok: bool
    issues: list[str] = field(default_factory=list)
    suggested_fix: str = ""
    latency_ms: float = 0.0
    skipped: bool = False
    skip_reason: str = ""
    error: str | None = None


# ── Skip helpers ───────────────────────────────────────────────────────────


def _looks_like_structured_json(text: str) -> bool:
    """Эвристика: ответ — JSON-объект/массив."""
    stripped = text.strip()
    if not stripped:
        return False
    if (stripped.startswith("{") and stripped.endswith("}")) or (
        stripped.startswith("[") and stripped.endswith("]")
    ):
        try:
            json.loads(stripped)
            return True
        except (ValueError, TypeError):
            return False
    return False


def _looks_like_command_output(text: str) -> bool:
    """Эвристика: ответ — вывод команды (icon-prefix, code-block, prompt)."""
    stripped = text.lstrip()
    return any(stripped.startswith(p) for p in _COMMAND_OUTPUT_PREFIXES)


def _looks_like_deferred_notice(text: str) -> bool:
    """Эвристика: ответ — deferred-action notice."""
    lowered = text.lower()
    return any(marker in lowered for marker in _DEFERRED_ACTION_MARKERS)


def should_skip(answer: str, *, min_length: int = DEFAULT_MIN_LENGTH) -> tuple[bool, str]:
    """Возвращает (skip, reason). Используется и тестами, и check_response."""
    if not answer or not answer.strip():
        return True, "empty_response"
    if len(answer.strip()) < min_length:
        return True, "too_short"
    if _looks_like_structured_json(answer):
        return True, "structured_json"
    if _looks_like_command_output(answer):
        return True, "command_output"
    if _looks_like_deferred_notice(answer):
        return True, "deferred_action"
    return False, ""


# ── Prompt ─────────────────────────────────────────────────────────────────


def _build_prompt(question: str, answer: str) -> str:
    """Короткий prompt для cheap-model — strict JSON output."""
    return f"""Ты — рецензент-фактчекер. Проверь ответ на: галлюцинации (выдуманные факты, имена, числа), противоречия и явное несоответствие вопросу.

ВОПРОС:
{question}

ОТВЕТ:
{answer}

Если всё ок — верни ok=true. Если есть проблема — ok=false, перечисли issues кратко (≤80 символов каждый), при возможности предложи suggested_fix (исправленная короткая версия ответа или "" если не очевидно).

Ответь СТРОГО в JSON (без markdown):
{{"ok": <true|false>, "issues": ["..."], "suggested_fix": "..."}}"""


# ── SelfCorrector ──────────────────────────────────────────────────────────


class SelfCorrector:
    """Cheap-model fact-check / coherence wrapper."""

    def __init__(
        self,
        *,
        model: str | None = None,
        lm_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SEC,
        min_length: int = DEFAULT_MIN_LENGTH,
    ) -> None:
        self._model = model or os.getenv("KRAB_SELF_CORRECTION_MODEL", DEFAULT_MODEL)
        self._url = (lm_url or LM_STUDIO_CHAT_ENDPOINT).rstrip("/")
        self._timeout = timeout
        self._min_length = min_length

    async def check_response(
        self,
        question: str,
        answer: str,
        *,
        is_deferred: bool = False,
    ) -> CorrectionResult:
        """Проверить ответ. Fail-open: любая ошибка → ok=True + error.

        Args:
            question: исходный вопрос пользователя.
            answer: финальный текст ответа Krab после anti-parasite stripper.
            is_deferred: caller знает, что это deferred-action notice — пропускаем.
        """
        start = time.monotonic()

        if is_deferred:
            return CorrectionResult(ok=True, skipped=True, skip_reason="deferred_action_external")

        skip, reason = should_skip(answer, min_length=self._min_length)
        if skip:
            return CorrectionResult(ok=True, skipped=True, skip_reason=reason)

        prompt = _build_prompt(question, answer)
        try:
            result = await asyncio.wait_for(self._call_cheap_model(prompt), timeout=self._timeout)
        except asyncio.TimeoutError:
            elapsed = (time.monotonic() - start) * 1000
            logger.warning(
                "self_correction_timeout",
                timeout_sec=self._timeout,
                latency_ms=elapsed,
                model=self._model,
            )
            return CorrectionResult(ok=True, latency_ms=elapsed, error="timeout")
        except Exception as exc:  # noqa: BLE001
            elapsed = (time.monotonic() - start) * 1000
            logger.warning(
                "self_correction_call_failed",
                error=str(exc),
                error_type=type(exc).__name__,
                latency_ms=elapsed,
                model=self._model,
            )
            return CorrectionResult(
                ok=True, latency_ms=elapsed, error=f"{type(exc).__name__}: {exc}"
            )

        result.latency_ms = (time.monotonic() - start) * 1000
        if not result.ok:
            logger.info(
                "self_correction_issue_detected",
                issues_count=len(result.issues),
                latency_ms=result.latency_ms,
                model=self._model,
                has_fix=bool(result.suggested_fix),
            )
        return result

    async def _call_cheap_model(self, prompt: str) -> CorrectionResult:
        """HTTP POST в LM Studio + парсинг JSON."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                self._url,
                json={
                    "model": self._model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 256,
                    "stream": False,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            content = (data["choices"][0]["message"]["content"] or "").strip()

        return _parse_corrector_json(content)


def _parse_corrector_json(content: str) -> CorrectionResult:
    """Парсит JSON-ответ cheap-model. Снимает markdown code-fences."""
    raw = content.strip()
    if raw.startswith("```"):
        # ```json\n{...}\n```
        parts = raw.split("```")
        if len(parts) >= 2:
            raw = parts[1]
            if raw.lstrip().lower().startswith("json"):
                raw = raw.lstrip()[4:]
        raw = raw.strip()

    # Иногда модель возвращает текст до/после JSON — выдёргиваем первый объект.
    if not raw.startswith("{"):
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            raw = match.group(0)

    parsed = json.loads(raw)
    issues_raw = parsed.get("issues") or []
    if not isinstance(issues_raw, list):
        issues_raw = [str(issues_raw)]
    return CorrectionResult(
        ok=bool(parsed.get("ok", True)),
        issues=[str(it)[:200] for it in issues_raw],
        suggested_fix=str(parsed.get("suggested_fix", ""))[:4000],
    )


# ── Module-level singleton ────────────────────────────────────────────────

_singleton: SelfCorrector | None = None


def get_self_corrector() -> SelfCorrector:
    """Lazy singleton. Тесты могут вызвать reset_self_corrector()."""
    global _singleton
    if _singleton is None:
        _singleton = SelfCorrector()
    return _singleton


def reset_self_corrector() -> None:
    """Сброс singleton (для тестов / reload конфигурации)."""
    global _singleton
    _singleton = None


# ── Config helpers ─────────────────────────────────────────────────────────


def is_enabled() -> bool:
    """KRAB_SELF_CORRECTION_ENABLED — careful rollout, default OFF."""
    return os.getenv("KRAB_SELF_CORRECTION_ENABLED", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def is_strict() -> bool:
    """KRAB_SELF_CORRECTION_STRICT — default OFF (lenient log-only)."""
    return os.getenv("KRAB_SELF_CORRECTION_STRICT", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
