# -*- coding: utf-8 -*-
"""
LLMEnsemble — параллельный опрос двух моделей для критичных запросов.

Idea 11 (Session 28). На reasoning-тяжёлых задачах (или при явном `/ensemble`
флаге) обращаемся сразу к двум моделям, затем сводим ответы по выбранной
стратегии:

- ``vote``     — кластеризуем ответы по similarity; выбираем мажоритарный.
                 На двух моделях это либо «совпали → consensus», либо
                 «разошлись → берём первый non-empty» с низким agreement_score.
- ``best_of``  — каждая модель сама себя оценивает 1..10; выбираем максимум.
                 Если self-rate невозможен — fallback на первый non-empty.
- ``concat``   — склеиваем оба ответа с attribution headers.

Pure модуль: LLM-вызов делается через инжектируемый ``llm_callable`` —
``async (model: str, prompt: str) -> str``. Это позволяет в тестах мокать
без сетевых вызовов и не цеплять `openclaw_client` напрямую.

Cost-aware: ровно 2 модели; fail-fast — если первая модель отвалилась по
timeout раньше второй, ждём только вторую (и наоборот). Если обе
провалились — возвращаем degraded EnsembleResult с пустым final_answer.

Конфиг: feature flag ``KRAB_LLM_ENSEMBLE_ENABLED`` (default False) — опрашивается
вызывающим кодом, сам модуль остаётся pure.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Awaitable, Callable, Literal

logger = logging.getLogger(__name__)

# Тип LLM-вызова: принимает (model, prompt), возвращает строку-ответ.
LLMCallable = Callable[[str, str], Awaitable[str]]

Strategy = Literal["vote", "concat", "best_of"]

# Порог similarity для группировки ответов в `vote`.
_VOTE_SIMILARITY_THRESHOLD = 0.75

# Регексп для извлечения self-rate из ответа в режиме best_of.
_SELF_RATE_RE = re.compile(r"\bRATING[:\s]*([0-9]{1,2})(?:\s*/\s*10)?\b", re.IGNORECASE)


@dataclass(frozen=True)
class EnsembleResult:
    """Результат ансамблевого вызова."""

    final_answer: str
    individual_answers: list[str] = field(default_factory=list)
    agreement_score: float = 0.0  # 0..1, 1 = полный консенсус
    latency_ms: int = 0
    strategy: Strategy = "vote"
    models: list[str] = field(default_factory=list)
    degraded: bool = False  # True если хотя бы одна модель упала
    notes: str = ""


def is_enabled() -> bool:
    """Проверка фича-флага окружения."""
    return os.environ.get("KRAB_LLM_ENSEMBLE_ENABLED", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _similarity(a: str, b: str) -> float:
    """Грубая similarity для group-by-vote — нормализованный SequenceMatcher."""
    if not a or not b:
        return 0.0
    a_norm = " ".join(a.lower().split())
    b_norm = " ".join(b.lower().split())
    return SequenceMatcher(None, a_norm, b_norm).ratio()


def _extract_self_rate(answer: str) -> int | None:
    """Извлекает RATING:N/10 из ответа модели в режиме best_of."""
    m = _SELF_RATE_RE.search(answer or "")
    if not m:
        return None
    try:
        n = int(m.group(1))
    except ValueError:
        return None
    if 1 <= n <= 10:
        return n
    return None


def _strip_self_rate(answer: str) -> str:
    """Убирает строку RATING:N/10 из финального ответа (best_of)."""
    return _SELF_RATE_RE.sub("", answer or "").rstrip()


class LLMEnsemble:
    """Координатор параллельного опроса моделей."""

    def __init__(self, llm_callable: LLMCallable):
        self._call = llm_callable

    async def ensemble_query(
        self,
        prompt: str,
        *,
        models: list[str],
        strategy: Strategy = "vote",
        timeout_sec: float = 30.0,
    ) -> EnsembleResult:
        """Опрашивает модели параллельно и сводит ответы по стратегии."""
        if not models:
            return EnsembleResult(
                final_answer="",
                individual_answers=[],
                agreement_score=0.0,
                latency_ms=0,
                strategy=strategy,
                models=[],
                degraded=True,
                notes="no_models_provided",
            )

        # Cost-aware: ограничиваем 2-мя моделями.
        if len(models) > 2:
            logger.warning("ensemble_too_many_models requested=%d truncated_to=2", len(models))
            models = models[:2]

        # Single-model degraded режим — pass-through, без ансамбля.
        if len(models) == 1:
            return await self._single_model(prompt, models[0], strategy, timeout_sec)

        return await self._dual_model(prompt, models, strategy, timeout_sec)

    async def _single_model(
        self, prompt: str, model: str, strategy: Strategy, timeout_sec: float
    ) -> EnsembleResult:
        t0 = time.monotonic()
        try:
            answer = await asyncio.wait_for(self._call(model, prompt), timeout=timeout_sec)
        except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
            logger.warning(
                "ensemble_single_model_failed model=%s error_type=%s error=%s",
                model,
                type(exc).__name__,
                exc,
            )
            return EnsembleResult(
                final_answer="",
                individual_answers=[],
                agreement_score=0.0,
                latency_ms=int((time.monotonic() - t0) * 1000),
                strategy=strategy,
                models=[model],
                degraded=True,
                notes=f"single_model_failed:{type(exc).__name__}",
            )
        return EnsembleResult(
            final_answer=_strip_self_rate(answer) if strategy == "best_of" else answer,
            individual_answers=[answer],
            agreement_score=1.0,
            latency_ms=int((time.monotonic() - t0) * 1000),
            strategy=strategy,
            models=[model],
            degraded=True,  # фактически ансамбля не было
            notes="single_model_degraded",
        )

    async def _dual_model(
        self,
        prompt: str,
        models: list[str],
        strategy: Strategy,
        timeout_sec: float,
    ) -> EnsembleResult:
        # Для best_of добавляем инструкцию само-оценки.
        effective_prompt = prompt
        if strategy == "best_of":
            effective_prompt = (
                prompt + "\n\nВ конце ответа добавь отдельной строкой: RATING:<N>/10 "
                "— твоя честная самооценка качества ответа от 1 до 10."
            )

        t0 = time.monotonic()
        tasks = [
            asyncio.create_task(
                asyncio.wait_for(self._call(m, effective_prompt), timeout=timeout_sec),
                name=f"ensemble_{m}",
            )
            for m in models
        ]

        results: list[str | BaseException] = []
        for t in tasks:
            try:
                results.append(await t)
            except BaseException as exc:  # noqa: BLE001  — сохраняем все исключения
                results.append(exc)

        latency_ms = int((time.monotonic() - t0) * 1000)
        answers: list[str] = []
        failed: list[str] = []
        for model, res in zip(models, results, strict=True):
            if isinstance(res, BaseException):
                failed.append(f"{model}:{type(res).__name__}")
                logger.warning(
                    "ensemble_model_failed model=%s error_type=%s error=%s",
                    model,
                    type(res).__name__,
                    res,
                )
            else:
                answers.append(res)

        # Все модели провалились.
        if not answers:
            return EnsembleResult(
                final_answer="",
                individual_answers=[],
                agreement_score=0.0,
                latency_ms=latency_ms,
                strategy=strategy,
                models=models,
                degraded=True,
                notes="all_models_failed:" + ",".join(failed),
            )

        # Одна из двух моделей упала — fail-fast fallback на выжившую.
        degraded = len(answers) < len(models)
        if degraded and len(answers) == 1:
            return EnsembleResult(
                final_answer=_strip_self_rate(answers[0]) if strategy == "best_of" else answers[0],
                individual_answers=answers,
                agreement_score=0.0,
                latency_ms=latency_ms,
                strategy=strategy,
                models=models,
                degraded=True,
                notes="partial_failure:" + ",".join(failed),
            )

        # Обе модели вернули ответ — применяем стратегию.
        if strategy == "vote":
            final, agreement = self._strategy_vote(answers)
        elif strategy == "best_of":
            final, agreement = self._strategy_best_of(answers)
        elif strategy == "concat":
            final, agreement = self._strategy_concat(answers, models)
        else:  # pragma: no cover — Literal защищает
            final, agreement = answers[0], 0.0

        return EnsembleResult(
            final_answer=final,
            individual_answers=answers,
            agreement_score=agreement,
            latency_ms=latency_ms,
            strategy=strategy,
            models=models,
            degraded=False,
            notes="",
        )

    # --- Стратегии -----------------------------------------------------

    @staticmethod
    def _strategy_vote(answers: list[str]) -> tuple[str, float]:
        """Группируем по similarity; берём ответ из самой большой группы."""
        if not answers:
            return "", 0.0
        # Кластеризация: для каждого ответа собираем индексы похожих.
        groups: list[list[int]] = []
        for i, a in enumerate(answers):
            placed = False
            for g in groups:
                rep = answers[g[0]]
                if _similarity(a, rep) >= _VOTE_SIMILARITY_THRESHOLD:
                    g.append(i)
                    placed = True
                    break
            if not placed:
                groups.append([i])
        groups.sort(key=len, reverse=True)
        winner_group = groups[0]
        # Agreement = доля ответов в крупнейшем кластере.
        agreement = len(winner_group) / len(answers)
        # На равенстве кластеров (1+1) выбираем первый non-empty ответ.
        return answers[winner_group[0]], agreement

    @staticmethod
    def _strategy_best_of(answers: list[str]) -> tuple[str, float]:
        """Каждая модель само-оценивает; выбираем макс."""
        scored: list[tuple[int, int]] = []  # (rate, idx)
        for i, a in enumerate(answers):
            r = _extract_self_rate(a)
            if r is not None:
                scored.append((r, i))
        if not scored:
            # Никто не дал self-rate — fallback на первый ответ.
            return _strip_self_rate(answers[0]), 0.0
        scored.sort(reverse=True)
        best_rate, best_idx = scored[0]
        # Agreement как нормализованный rate (0..1).
        return _strip_self_rate(answers[best_idx]), best_rate / 10.0

    @staticmethod
    def _strategy_concat(answers: list[str], models: list[str]) -> tuple[str, float]:
        """Склеиваем ответы с attribution headers."""
        parts = []
        for model, ans in zip(models, answers, strict=False):
            parts.append(f"### Ответ от {model}\n\n{ans.strip()}")
        # Agreement = similarity между ответами (для двух).
        agreement = _similarity(answers[0], answers[1]) if len(answers) >= 2 else 1.0
        return "\n\n---\n\n".join(parts), agreement


__all__ = [
    "EnsembleResult",
    "LLMCallable",
    "LLMEnsemble",
    "Strategy",
    "is_enabled",
]
