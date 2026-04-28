# -*- coding: utf-8 -*-
"""
CostAwareRouter — выбор модели по сложности задачи и остатку бюджета.

Чистый модуль без побочных эффектов: классифицирует промпт по эвристикам и
рекомендует модель из доступного списка. Wire-up в openclaw_client откладывается
на отдельный шаг (см. backlog Idea 8).

Классы задач:
- trivial      — очень короткие реплики, одиночные ключевые слова
- simple       — короткие приветствия, yes/no, короткие фразы
- standard     — обычные текстовые запросы 30–200 символов
- code         — код или явные code-keywords (напиши, функция, code, debug)
- reasoning    — рассуждение/математика/планирование (почему, объясни, multi-step)
- multimodal   — есть медиа-вложение

Логика recommend_model:
1. multimodal → vision-capable модель (если есть)
2. budget_remaining_usd <= 0 → принудительно flash/cheap
3. low budget (<1.0) → downgrade reasoning/code до standard
4. otherwise — preferred per task_class из tiers
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .logger import get_logger

logger = get_logger(__name__)

TaskClass = Literal["trivial", "simple", "standard", "code", "reasoning", "multimodal"]

# Регэкспы для эвристик
_CODE_FENCE_RE = re.compile(r"```|^\s{4,}\S", re.MULTILINE)
_CODE_KEYWORDS_RE = re.compile(
    r"\b(напиши|написать|функци[яюи]|код|class\s|def\s|import\s|debug|"
    r"баг|исправь|рефактор|code|implement|refactor|stacktrace|traceback)\b",
    re.IGNORECASE,
)
_REASONING_KEYWORDS_RE = re.compile(
    r"\b(почему|объясни|обоснуй|докажи|посчитай|вычисли|план|"
    r"explain|why|prove|reason|step.by.step|plan)\b",
    re.IGNORECASE,
)
_MATH_RE = re.compile(
    r"\d+\s*[+\-*/^=]\s*\d+|\b(integral|derivative|matrix|eigen)\b", re.IGNORECASE
)
_GREETING_RE = re.compile(
    r"^(привет|здравствуй|hi|hello|hey|спасибо|thanks|ok|ок|да|нет|yes|no|ага|угу)[\s!.?]*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ModelTiers:
    """Соответствие классов задач кандидатным моделям (по убыванию приоритета)."""

    cheap: tuple[str, ...] = ("gemini-3-flash-preview", "gemini-2.5-flash", "haiku")
    standard: tuple[str, ...] = ("gpt-5.5", "gemini-3-pro-preview", "gemini-2.5-pro-preview")
    premium: tuple[str, ...] = ("opus-4.7", "gpt-5.5-pro", "gemini-3-pro-preview")
    vision: tuple[str, ...] = ("gemini-3-pro-preview", "gpt-5.5", "opus-4.7")


_DEFAULT_TIERS = ModelTiers()

# Бюджетные пороги (USD)
_BUDGET_DEPLETED = 0.0
_BUDGET_LOW = 1.0


class CostAwareRouter:
    """Маршрутизатор моделей с учётом класса задачи и остатка бюджета."""

    def __init__(self, tiers: ModelTiers | None = None) -> None:
        self._tiers = tiers or _DEFAULT_TIERS

    # ── Классификация ────────────────────────────────────────────

    def classify_task(
        self,
        prompt: str,
        *,
        has_media: bool = False,
        has_tools: bool = False,
    ) -> TaskClass:
        """Определить класс задачи по эвристикам.

        has_tools зарезервирован под будущее использование (тула-цепочки часто
        тяжелее), сейчас участвует только как мягкий boost к standard.
        """
        if has_media:
            return "multimodal"

        text = (prompt or "").strip()
        length = len(text)

        # Trivial / simple для коротких текстов без code-структуры
        if length < 30 and not _CODE_FENCE_RE.search(text):
            words = text.split()
            # Одиночное слово/токен без greeting — trivial
            if len(words) <= 1 and not _GREETING_RE.match(text):
                return "trivial"
            return "simple"

        # Code: фенсы или явные ключевые слова
        if _CODE_FENCE_RE.search(text) or _CODE_KEYWORDS_RE.search(text):
            return "code"

        # Reasoning: ключевые слова или математика, либо длинный multi-step текст
        if _REASONING_KEYWORDS_RE.search(text) or _MATH_RE.search(text):
            return "reasoning"
        if length > 600 and text.count("\n") >= 3:
            return "reasoning"

        # Короткие приветствия не отловленные ранее
        if _GREETING_RE.match(text):
            return "simple"

        if has_tools and length < 200:
            return "standard"

        if length <= 200:
            return "standard"

        return "standard"

    # ── Рекомендация модели ──────────────────────────────────────

    def recommend_model(
        self,
        task_class: TaskClass,
        budget_remaining_usd: float,
        available_models: list[str] | tuple[str, ...],
    ) -> str | None:
        """Подобрать первую доступную модель из подходящего тира.

        Возвращает None, если available_models пуст. При исчерпанном бюджете
        переключает на cheap-тир. При низком бюджете downgrade-ит premium до
        standard.
        """
        if not available_models:
            logger.warning("cost_router_no_available_models", task_class=task_class)
            return None

        available = list(available_models)

        # Multimodal — отдельный путь
        if task_class == "multimodal":
            picked = self._first_match(self._tiers.vision, available)
            if picked is None:
                # Деградация на standard, если vision-модель не доступна
                picked = self._first_match(self._tiers.standard, available) or available[0]
            return picked

        # Жёсткий budget gate
        if budget_remaining_usd <= _BUDGET_DEPLETED:
            picked = self._first_match(self._tiers.cheap, available) or available[0]
            logger.info(
                "cost_router_budget_depleted",
                task_class=task_class,
                budget=budget_remaining_usd,
                model=picked,
            )
            return picked

        # Low budget — downgrade premium-классов
        low_budget = budget_remaining_usd < _BUDGET_LOW

        if task_class in ("trivial", "simple"):
            tier = self._tiers.cheap
        elif task_class == "standard":
            tier = self._tiers.standard
        elif task_class in ("code", "reasoning"):
            tier = self._tiers.standard if low_budget else self._tiers.premium
        else:  # pragma: no cover — defensive
            tier = self._tiers.standard

        picked = (
            self._first_match(tier, available)
            or self._first_match(self._tiers.standard, available)
            or self._first_match(self._tiers.cheap, available)
            or available[0]
        )
        logger.debug(
            "cost_router_recommended",
            task_class=task_class,
            budget=budget_remaining_usd,
            model=picked,
        )
        return picked

    # ── Helpers ──────────────────────────────────────────────────

    @staticmethod
    def _first_match(preferred: tuple[str, ...], available: list[str]) -> str | None:
        """Найти первую модель из preferred, присутствующую в available (по подстроке)."""
        avail_lower = [m.lower() for m in available]
        for cand in preferred:
            cand_l = cand.lower()
            for i, a in enumerate(avail_lower):
                if cand_l in a or a in cand_l:
                    return available[i]
        return None


# Singleton (lazy use — wire-up в openclaw_client откладывается)
cost_aware_router = CostAwareRouter()
