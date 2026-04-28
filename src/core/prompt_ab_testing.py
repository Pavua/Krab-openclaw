# -*- coding: utf-8 -*-
"""
Prompt A/B testing framework — рамка для split-traffic экспериментов над
вариантами системных промптов с трекингом качества.

### Зачем это существует

Idea 24 backlog: чтобы итеративно улучшать system prompt assembly, нужна
численная обратная связь — какой вариант промпта даёт лучшие ответы. Без
A/B-фреймворка любые правки промпта — это «на глаз».

Что даёт этот модуль:

1. **Регистрация экспериментов** с произвольным числом variants и заданным
   traffic split (например `{"A": 0.5, "B": 0.5}` либо `{"control": 0.7,
   "treatment": 0.3}` для канари).
2. **Sticky assignment** — один и тот же `user_id` в рамках одного
   эксперимента всегда получает один и тот же variant. Это критично:
   мерять качество на «прыгающем» пользователе — это шум.
3. **Запись исходов** (`record_outcome`) с булевым success и числовым
   score. Хранится агрегат: count, sum, sum_squared (для variance), а не
   полный лог — это экономит и диск, и память.
4. **Stats compute** — sample size, mean, stddev и approximate 95%
   confidence interval (Wald, normal approx) для каждого варианта.

### Что НЕ делает

- Не вычисляет p-value / Bayesian posterior. Для серьёзной статистики —
  scipy / отдельный analysis pipeline. Тут — только базовые агрегаты,
  которых достаточно для decision support.
- Не подменяет system prompt автоматически. Это pure-data модуль; wire-up
  в `userbot/llm_flow.py` или сборку system prompt'а — отдельная задача
  в backlog.
- Не делает multi-armed bandit / Thompson sampling. Только статичный split.

### Соглашения

- **JSON-store** в `~/.openclaw/krab_runtime_state/ab_experiments.json`.
- **Sticky assignment** — детерминированное хэширование `(experiment, user_id)`
  через `hashlib.sha256`, биение [0, 1) по cumulative split. Это значит,
  что добавление НОВОГО эксперимента не сдвигает существующие назначения.
- **Гейт `KRAB_AB_TESTING_ENABLED`** — пока модуль pure, флаг сам ничего
  не отключает; его читают потенциальные потребители (system prompt builder).
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)


# Допуск на сумму traffic split: чтобы 0.7+0.3 не падало из-за float-погрешности.
_SPLIT_TOLERANCE: float = 1e-6


@dataclass(frozen=True)
class VariantStats:
    """Агрегированная статистика по одному variant в эксперименте.

    `confidence_interval_95` — приближение Wald (mean ± 1.96 * stderr).
    Возвращается как `(low, high)`; для sample_size < 2 — `None` (stderr
    не определён).
    """

    variant: str
    sample_size: int
    success_count: int
    success_rate: float
    mean_score: float
    stddev_score: float
    confidence_interval_95: tuple[float, float] | None


@dataclass(frozen=True)
class ExperimentStats:
    """Срез по всему эксперименту: сводка по всем variants."""

    experiment: str
    total_samples: int
    variants: list[VariantStats]


@dataclass
class _ExperimentDef:
    """Внутренняя запись об эксперименте: вариации, split, агрегаты."""

    name: str
    variants: dict[str, str]
    traffic_split: dict[str, float]
    # `outcomes[variant]` хранит агрегаты, а не raw лог: count, success,
    # sum_score, sum_score_sq. Этого достаточно для mean / stddev /
    # confidence interval, и не пухнет с числом записей.
    outcomes: dict[str, dict[str, float]] = field(default_factory=dict)


class ABTester:
    """Регистр экспериментов, sticky assignment и сбор исходов.

    Использование:
        tester = ab_tester  # module-level singleton
        tester.register_experiment(
            "system_prompt_tone",
            variants={"A": "...polite...", "B": "...crisp..."},
            traffic_split={"A": 0.5, "B": 0.5},
        )
        variant, prompt = tester.pick_variant("system_prompt_tone", user_id=42)
        ...
        tester.record_outcome(
            "system_prompt_tone", variant, user_id=42,
            success=True, score=0.87,
        )
        stats = tester.get_stats("system_prompt_tone")
    """

    def __init__(
        self,
        *,
        storage_path: Path | None = None,
        hash_fn: Callable[[str], int] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._storage_path: Path | None = storage_path
        self._experiments: dict[str, _ExperimentDef] = {}
        # Инжектируемое хэширование для тестов: обычное sha256 → int.
        self._hash_fn: Callable[[str], int] = hash_fn or _default_hash
        if storage_path is not None:
            self._load_from_disk()

    # ---- Configuration --------------------------------------------------

    def configure_default_path(self, storage_path: Path) -> None:
        """Устанавливает путь к persisted JSON и подгружает что есть."""
        with self._lock:
            self._storage_path = storage_path
            self._experiments = {}
            self._load_from_disk()

    # ---- Registration ---------------------------------------------------

    def register_experiment(
        self,
        name: str,
        variants: dict[str, str],
        *,
        traffic_split: dict[str, float] | None = None,
    ) -> None:
        """Создаёт или перерегистрирует эксперимент.

        - `variants` — словарь `{variant_name: prompt_text}`. Ключи — стабильные
          идентификаторы (например `"A"`, `"control"`).
        - `traffic_split` — нормированные доли (сумма == 1.0). Если не задан,
          распределение равномерное.

        Перерегистрация СОХРАНЯЕТ существующие outcomes для variants, имена
        которых не изменились. Удаление variant из словаря удаляет и его
        outcomes (мы не храним «orphan» агрегаты).
        """
        normalized_name = (name or "").strip()
        if not normalized_name:
            raise ValueError("experiment name must be non-empty")
        if not variants:
            raise ValueError("experiment must have at least one variant")
        if traffic_split is None:
            even = 1.0 / len(variants)
            traffic_split = {variant: even for variant in variants}
        # Проверка консистентности: ключи split == ключи variants, сумма == 1.
        if set(traffic_split.keys()) != set(variants.keys()):
            raise ValueError("traffic_split keys must match variants keys")
        total = float(sum(traffic_split.values()))
        if abs(total - 1.0) > _SPLIT_TOLERANCE:
            raise ValueError(f"traffic_split must sum to 1.0, got {total!r}")
        for variant, share in traffic_split.items():
            if share < 0:
                raise ValueError(f"negative split for {variant!r}: {share!r}")

        with self._lock:
            existing = self._experiments.get(normalized_name)
            preserved_outcomes: dict[str, dict[str, float]] = {}
            if existing is not None:
                for variant_name in variants.keys():
                    if variant_name in existing.outcomes:
                        preserved_outcomes[variant_name] = existing.outcomes[variant_name]

            self._experiments[normalized_name] = _ExperimentDef(
                name=normalized_name,
                variants=dict(variants),
                traffic_split=dict(traffic_split),
                outcomes=preserved_outcomes,
            )
            self._persist_to_disk()
        logger.info(
            "ab_experiment_registered",
            experiment=normalized_name,
            variant_count=len(variants),
            preserved_outcomes=len(preserved_outcomes),
        )

    def list_experiments(self) -> list[str]:
        """Снимок имён зарегистрированных экспериментов."""
        with self._lock:
            return sorted(self._experiments.keys())

    # ---- Variant selection ----------------------------------------------

    def pick_variant(self, experiment_name: str, user_id: Any) -> tuple[str, str]:
        """Возвращает `(variant_name, prompt_text)` для пары (эксперимент, user).

        Sticky: один и тот же `user_id` всегда получит один и тот же variant
        (пока traffic_split не меняется). Хэширование — sha256 от
        `f"{experiment}:{user_id}"`, далее [0, 1) по cumulative split.
        """
        normalized_name = (experiment_name or "").strip()
        with self._lock:
            experiment = self._experiments.get(normalized_name)
            if experiment is None:
                raise KeyError(f"unknown experiment: {experiment_name!r}")
            split_items = sorted(experiment.traffic_split.items())
        position = self._bucket(normalized_name, user_id)
        cumulative = 0.0
        # Итеративно ищем первый bucket, в который попадает position.
        # Сортировка по имени varianta делает результат стабильным при
        # любой реализации dict.
        for variant_name, share in split_items:
            cumulative += float(share)
            if position < cumulative:
                return variant_name, experiment.variants[variant_name]
        # Float corner-case: если position == 1.0 - epsilon и сумма дала
        # чуть меньше 1.0 (разница в пределах _SPLIT_TOLERANCE), вернуть
        # последний variant. Без этого был бы редкий KeyError.
        last_variant = split_items[-1][0]
        return last_variant, experiment.variants[last_variant]

    def _bucket(self, experiment: str, user_id: Any) -> float:
        """Детерминированное хэширование (experiment, user) → [0, 1)."""
        key = f"{experiment}:{user_id}"
        h = self._hash_fn(key)
        # 64-битная нормализация: hash_fn возвращает unsigned int произвольной
        # длины, режем по 2^64 чтобы получить устойчивое float-деление.
        return (h & ((1 << 64) - 1)) / float(1 << 64)

    # ---- Outcome recording ----------------------------------------------

    def record_outcome(
        self,
        experiment_name: str,
        variant: str,
        user_id: Any,
        *,
        success: bool,
        score: float,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Записывает результат для (эксперимент, variant).

        Хранит агрегаты, не raw журнал. `metadata` сейчас не персистится —
        принят для будущего (например, лог в отдельный JSONL). Параметр
        `user_id` используется только для defensive sanity-check в логе.
        """
        normalized_name = (experiment_name or "").strip()
        with self._lock:
            experiment = self._experiments.get(normalized_name)
            if experiment is None:
                raise KeyError(f"unknown experiment: {experiment_name!r}")
            if variant not in experiment.variants:
                raise KeyError(f"unknown variant {variant!r} for experiment {normalized_name!r}")
            score_value = float(score)
            agg = experiment.outcomes.setdefault(
                variant,
                {"count": 0.0, "success": 0.0, "sum_score": 0.0, "sum_score_sq": 0.0},
            )
            agg["count"] += 1.0
            if success:
                agg["success"] += 1.0
            agg["sum_score"] += score_value
            agg["sum_score_sq"] += score_value * score_value
            self._persist_to_disk()
        logger.debug(
            "ab_outcome_recorded",
            experiment=normalized_name,
            variant=variant,
            user_id=str(user_id),
            success=bool(success),
            score=score_value,
            metadata_keys=list((metadata or {}).keys()),
        )

    # ---- Stats ----------------------------------------------------------

    def get_stats(self, experiment_name: str) -> ExperimentStats:
        """Снимок агрегатов: sample size, mean, stddev, 95% CI на variant."""
        normalized_name = (experiment_name or "").strip()
        with self._lock:
            experiment = self._experiments.get(normalized_name)
            if experiment is None:
                raise KeyError(f"unknown experiment: {experiment_name!r}")
            variants_snapshot: list[VariantStats] = []
            total = 0
            for variant_name in sorted(experiment.variants.keys()):
                agg = experiment.outcomes.get(variant_name) or {}
                count = int(agg.get("count", 0) or 0)
                success_count = int(agg.get("success", 0) or 0)
                sum_score = float(agg.get("sum_score", 0.0) or 0.0)
                sum_score_sq = float(agg.get("sum_score_sq", 0.0) or 0.0)
                total += count
                if count <= 0:
                    variants_snapshot.append(
                        VariantStats(
                            variant=variant_name,
                            sample_size=0,
                            success_count=0,
                            success_rate=0.0,
                            mean_score=0.0,
                            stddev_score=0.0,
                            confidence_interval_95=None,
                        )
                    )
                    continue
                mean = sum_score / count
                # Variance = E[X^2] - E[X]^2; зажимаем в ноль на случай
                # численного «ушло чуть в минус» (бывает при больших count).
                variance = max(0.0, (sum_score_sq / count) - (mean * mean))
                stddev = math.sqrt(variance)
                ci: tuple[float, float] | None
                if count >= 2 and stddev > 0:
                    stderr = stddev / math.sqrt(count)
                    ci = (mean - 1.96 * stderr, mean + 1.96 * stderr)
                else:
                    ci = None
                variants_snapshot.append(
                    VariantStats(
                        variant=variant_name,
                        sample_size=count,
                        success_count=success_count,
                        success_rate=success_count / count,
                        mean_score=mean,
                        stddev_score=stddev,
                        confidence_interval_95=ci,
                    )
                )
        return ExperimentStats(
            experiment=normalized_name,
            total_samples=total,
            variants=variants_snapshot,
        )

    # ---- Persistence ----------------------------------------------------

    def _serialize(self) -> dict[str, Any]:
        return {
            name: {
                "variants": dict(exp.variants),
                "traffic_split": dict(exp.traffic_split),
                "outcomes": {variant: dict(agg) for variant, agg in exp.outcomes.items()},
            }
            for name, exp in self._experiments.items()
        }

    def _load_from_disk(self) -> None:
        path = self._storage_path
        if path is None or not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "ab_experiments_load_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return
        if not isinstance(raw, dict):
            logger.warning("ab_experiments_load_malformed", path=str(path))
            return
        loaded = 0
        for name, value in raw.items():
            if not isinstance(value, dict):
                continue
            variants = value.get("variants") or {}
            traffic_split = value.get("traffic_split") or {}
            outcomes = value.get("outcomes") or {}
            if not isinstance(variants, dict) or not isinstance(traffic_split, dict):
                continue
            try:
                self._experiments[str(name)] = _ExperimentDef(
                    name=str(name),
                    variants={str(k): str(v) for k, v in variants.items()},
                    traffic_split={str(k): float(v) for k, v in traffic_split.items()},
                    outcomes={
                        str(k): {str(kk): float(vv) for kk, vv in (v or {}).items()}
                        for k, v in outcomes.items()
                        if isinstance(v, dict)
                    },
                )
                loaded += 1
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "ab_experiment_entry_corrupt",
                    name=str(name),
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
        if loaded:
            logger.info("ab_experiments_loaded", count=loaded)

    def _persist_to_disk(self) -> None:
        path = self._storage_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(self._serialize(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except (OSError, TypeError) as exc:
            logger.warning(
                "ab_experiments_persist_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )


def _default_hash(key: str) -> int:
    """sha256 по строке → беззнаковое целое (256 бит)."""
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest, "big", signed=False)


# Module-level singleton — паттерн как в chat_ban_cache, silence_manager и пр.
# Конкретный путь конфигурируется через `ab_tester.configure_default_path(...)`
# из bootstrap (когда KRAB_AB_TESTING_ENABLED=1).
ab_tester = ABTester()
