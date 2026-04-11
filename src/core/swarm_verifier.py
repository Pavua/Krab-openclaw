# -*- coding: utf-8 -*-
"""
src/core/swarm_verifier.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Verifier/critic loop для swarm rounds (Phase 8 Master Plan).

После завершения round проверяет качество итогового результата:
- быстрая эвристическая проверка без LLM (длина, структура, error-like текст);
- полная LLM-верификация через OpenClaw с critic prompt.
"""

from __future__ import annotations

import re
import traceback
from dataclasses import dataclass, field
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)

# Минимальная длина результата, которую считаем значимой
_MIN_MEANINGFUL_LEN = 80

# Минимальная длина для "достаточно длинного" результата (пройдёт heuristic)
_MIN_ACCEPTABLE_LEN = 200

# Паттерны, характерные для error-сообщений или пустых fallback
_ERROR_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^\s*error[:\s]", re.IGNORECASE),
    re.compile(r"^\s*traceback", re.IGNORECASE),
    re.compile(r"\bException\b"),
    re.compile(r"HTTPError|TimeoutError|ConnectionError", re.IGNORECASE),
    re.compile(r"^\s*\[?\s*none\s*\]?\s*$", re.IGNORECASE),
    re.compile(r"^\s*n/?a\s*$", re.IGNORECASE),
    re.compile(r"no response|empty response|failed to", re.IGNORECASE),
]

# Промпт для LLM-верификатора
_CRITIC_SYSTEM_PROMPT = (
    "Ты — независимый верификатор качества. Оцени предоставленный результат "
    "аналитического раунда и верни структурированный ответ СТРОГО в формате JSON:\n"
    '{"passed": true/false, "score": 0.0-1.0, '
    '"issues": ["...", ...], "suggestions": ["...", ...]}\n\n'
    "Критерии оценки:\n"
    "- passed=true если score >= 0.6 и нет критических проблем\n"
    "- issues: конкретные недостатки (пустой список если нет)\n"
    "- suggestions: конкретные улучшения (пустой список если нет)\n"
    "Отвечай ТОЛЬКО JSON, без пояснений."
)


@dataclass
class VerificationResult:
    """Результат верификации swarm round."""

    passed: bool
    score: float  # 0.0–1.0
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Зажимаем score в допустимый диапазон
        self.score = max(0.0, min(1.0, self.score))


def quick_heuristic_check(result: str) -> VerificationResult:
    """
    Быстрая проверка без LLM: длина, структура, наличие error-паттернов.

    Не делает сетевых вызовов — подходит для hot path.
    """
    if not result or not result.strip():
        return VerificationResult(
            passed=False,
            score=0.0,
            issues=["Результат пустой"],
            suggestions=["Повторить round с более конкретным топиком"],
        )

    stripped = result.strip()
    length = len(stripped)

    issues: list[str] = []
    suggestions: list[str] = []

    # Проверяем error-паттерны
    for pattern in _ERROR_PATTERNS:
        if pattern.search(stripped):
            issues.append("Результат похож на error-сообщение или fallback")
            suggestions.append("Проверить доступность OpenClaw и провайдера модели")
            logger.warning(
                "swarm_verifier_error_pattern_detected",
                pattern=pattern.pattern,
                result_len=length,
            )
            return VerificationResult(passed=False, score=0.1, issues=issues, suggestions=suggestions)

    # Проверяем минимальную длину
    if length < _MIN_MEANINGFUL_LEN:
        issues.append(f"Результат слишком короткий ({length} символов, минимум {_MIN_MEANINGFUL_LEN})")
        suggestions.append("Увеличить SWARM_ROLE_MAX_OUTPUT_TOKENS или уточнить топик")
        return VerificationResult(passed=False, score=0.2, issues=issues, suggestions=suggestions)

    if length < _MIN_ACCEPTABLE_LEN:
        issues.append(f"Результат короткий ({length} символов), возможно поверхностный анализ")
        suggestions.append("Добавить конкретику в формулировку темы")
        # Частично прошёл — проходим, но с пониженным score
        score = 0.55 + (length - _MIN_MEANINGFUL_LEN) / (_MIN_ACCEPTABLE_LEN - _MIN_MEANINGFUL_LEN) * 0.1
        return VerificationResult(passed=False, score=round(score, 2), issues=issues, suggestions=suggestions)

    # Базовый score по длине (насыщается после 2000 символов)
    score = min(1.0, 0.65 + (length - _MIN_ACCEPTABLE_LEN) / 2000 * 0.25)
    return VerificationResult(passed=True, score=round(score, 2), issues=issues, suggestions=suggestions)


async def verify_round_result(
    team: str,
    topic: str,
    result: str,
    *,
    openclaw_client: Any,
) -> VerificationResult:
    """
    Верифицирует результат swarm round через LLM critic в OpenClaw.

    Сначала прогоняет quick_heuristic_check — если явный fail,
    возвращает его немедленно без LLM-вызова.
    """
    import json as _json

    # Быстрая проверка перед LLM-вызовом
    heuristic = quick_heuristic_check(result)
    if not heuristic.passed and heuristic.score < 0.2:
        logger.info(
            "swarm_verifier_heuristic_early_exit",
            team=team,
            score=heuristic.score,
            issues=heuristic.issues,
        )
        return heuristic

    logger.info("swarm_verifier_llm_start", team=team, topic_len=len(topic), result_len=len(result))

    prompt = (
        f"Команда: {team}\n"
        f"Топик: {topic}\n\n"
        f"Результат раунда:\n{result}\n\n"
        "Оцени качество этого результата."
    )

    try:
        raw = await openclaw_client.send_message_stream(
            message=prompt,
            chat_id=f"swarm_verifier_{team}",
            system_prompt=_CRITIC_SYSTEM_PROMPT,
            force_cloud=True,
            max_output_tokens=512,
        )

        # Извлекаем JSON из ответа (может быть обёрнут в markdown code block)
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not json_match:
            logger.warning("swarm_verifier_no_json_in_response", team=team, raw_len=len(raw))
            # Fallback: возвращаем heuristic если LLM не вернул JSON
            return heuristic

        data = _json.loads(json_match.group())
        verdict = VerificationResult(
            passed=bool(data.get("passed", False)),
            score=float(data.get("score", 0.5)),
            issues=list(data.get("issues", [])),
            suggestions=list(data.get("suggestions", [])),
        )

        logger.info(
            "swarm_verifier_llm_done",
            team=team,
            passed=verdict.passed,
            score=verdict.score,
            issues_count=len(verdict.issues),
        )
        return verdict

    except Exception as exc:  # noqa: BLE001
        logger.error(
            "swarm_verifier_llm_failed",
            team=team,
            error=str(exc),
            error_type=type(exc).__name__,
            traceback=traceback.format_exc(),
        )
        # Деградируем к heuristic при ошибке LLM
        return heuristic
