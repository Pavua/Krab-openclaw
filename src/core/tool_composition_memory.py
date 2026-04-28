# -*- coding: utf-8 -*-
"""
Tool Composition Memory — память по комбинациям tools, решающим задачу.

Зачем:

В OpenClaw tool-loop модель часто выбирает tools по prompt-эвристикам без учёта
прошлой эффективности. Хотим накапливать статистику: «для класса задачи X
комбинация (web_search → tor_fetch → summarize) сработала за 3.2с со 100%
успехом, а одиночный (web_search) — за 1.8с но успех 60%». Дальше recommend_tools
подсказывает routing-слою предпочтения.

Это **pure module** — он только записывает и рекомендует. Wire-up в openclaw
tool execution loop делается отдельным session'ом (см. backlog Idea 7).

### Инварианты

- **Иммутабельный ключ паттерна.** `(task_class, tool_combination)` где
  `tool_combination` — frozen tuple **без сортировки** (порядок tools несёт
  смысл: web_search→tor_fetch ≠ tor_fetch→web_search).
- **Decay по возрасту.** Запись с `last_used_at` старше `_DECAY_DAYS` дней
  плавно теряет вес: эффективная частота умножается на `0.5 ** (age/30)`.
- **Persist per write.** После каждого `record_session` сериализуем JSON
  целиком — writes редкие (task сессии), а чтение из in-memory.
- **Отсутствие истории — graceful.** Пустой store → recommend_tools()
  возвращает []. Никаких ошибок, нет сюрпризов для caller'а.

### Не решает

- Не делает routing сам (это слой выше).
- Не учитывает контекст пользователя/чата (только task_class).
- Не разделяет частичные успехи (success — bool, не доля).
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)

# Период полураспада веса записи. Запись месячной давности учитывается с
# коэффициентом 0.5; двухмесячной — 0.25. Подобрано так, чтобы новые удачные
# комбинации догоняли исторические в течение нескольких недель.
_DECAY_DAYS: float = 30.0

# Возраст после которого запись больше не учитывается в recommend (но ещё
# хранится — может пригодиться для analytics). Hard cutoff на ~6 полупериодов.
_HARD_CUTOFF_DAYS: float = 180.0

_SLOW_LOAD_WARN_MS: float = 500.0


def _env_flag(name: str, default: bool = True) -> bool:
    """Читает env флаг (1/0/true/false). Default True если не задан."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class ToolPattern:
    """Агрегированная статистика по одной комбинации tools для task_class.

    Поля:
        task_class: семантический класс задачи (e.g. "web_search", "code_fix").
        tool_combination: упорядоченный tuple имён tools.
        success_count / fail_count: счётчики исходов.
        avg_latency_ms / avg_cost_usd: скользящее среднее (incremental update).
        last_used_at: для decay.
    """

    task_class: str
    tool_combination: tuple[str, ...]
    success_count: int = 0
    fail_count: int = 0
    avg_latency_ms: float = 0.0
    avg_cost_usd: float = 0.0
    last_used_at: str = ""
    # FIFO-список последних 20 latency значений — для будущей p95-аналитики.
    # Сейчас не используется в recommend, но накапливаем заранее.
    recent_latencies_ms: list[float] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.success_count + self.fail_count

    @property
    def success_rate(self) -> float:
        total = self.total
        if total == 0:
            return 0.0
        return self.success_count / total

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_class": self.task_class,
            "tool_combination": list(self.tool_combination),
            "success_count": self.success_count,
            "fail_count": self.fail_count,
            "avg_latency_ms": self.avg_latency_ms,
            "avg_cost_usd": self.avg_cost_usd,
            "last_used_at": self.last_used_at,
            "recent_latencies_ms": list(self.recent_latencies_ms[-20:]),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ToolPattern | None:
        try:
            combo_raw = raw.get("tool_combination") or []
            if not isinstance(combo_raw, list):
                return None
            combo = tuple(str(x) for x in combo_raw)
            task_class = str(raw.get("task_class") or "").strip()
            if not task_class or not combo:
                return None
            return cls(
                task_class=task_class,
                tool_combination=combo,
                success_count=int(raw.get("success_count") or 0),
                fail_count=int(raw.get("fail_count") or 0),
                avg_latency_ms=float(raw.get("avg_latency_ms") or 0.0),
                avg_cost_usd=float(raw.get("avg_cost_usd") or 0.0),
                last_used_at=str(raw.get("last_used_at") or ""),
                recent_latencies_ms=[
                    float(x) for x in (raw.get("recent_latencies_ms") or []) if isinstance(x, (int, float))
                ],
            )
        except (TypeError, ValueError):
            return None


class ToolCompositionMemory:
    """Persistent store комбинаций tools с recommend по task_class.

    Используется как module-level singleton (`tool_composition_memory` ниже).
    Принимает `storage_path` в конструкторе для unit-тестов.
    """

    def __init__(
        self,
        *,
        storage_path: Path | None = None,
        now_fn: Callable[[], datetime] | None = None,
        enabled: bool | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._storage_path: Path | None = storage_path
        self._patterns: dict[tuple[str, tuple[str, ...]], ToolPattern] = {}
        self._now_fn: Callable[[], datetime] = now_fn or (lambda: datetime.now(timezone.utc))
        # Если caller явно передал enabled — уважаем; иначе env-флаг.
        if enabled is None:
            self._enabled = _env_flag("KRAB_TOOL_COMPOSITION_MEMORY_ENABLED", default=True)
        else:
            self._enabled = bool(enabled)
        if storage_path is not None:
            self._load_from_disk()

    # ---- Configuration ---------------------------------------------------

    def configure_default_path(self, storage_path: Path) -> None:
        """Устанавливает путь и подгружает то, что лежит на диске."""
        with self._lock:
            self._storage_path = storage_path
            self._patterns = {}
            self._load_from_disk()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, value: bool) -> None:
        self._enabled = bool(value)

    # ---- Core API --------------------------------------------------------

    def record_session(
        self,
        task_class: str,
        tools_used: list[str],
        *,
        success: bool,
        latency_ms: float = 0.0,
        cost_usd: float = 0.0,
    ) -> None:
        """Записывает результат одной session: какая комбинация tools была
        использована и чем закончилась.

        Idempotency: повторный вызов с теми же tools для task_class агрегирует
        счётчики (incremental average), а не перезаписывает.
        """
        if not self._enabled:
            return
        normalized_class = (task_class or "").strip()
        combo = tuple(str(t).strip() for t in (tools_used or []) if str(t).strip())
        if not normalized_class or not combo:
            # Без класса или без tools нечего запоминать — не шумим логом
            # на каждый вызов, просто молча выходим.
            return
        now_iso = self._now_fn().isoformat()
        key = (normalized_class, combo)
        with self._lock:
            pattern = self._patterns.get(key)
            if pattern is None:
                pattern = ToolPattern(
                    task_class=normalized_class,
                    tool_combination=combo,
                    last_used_at=now_iso,
                )
                self._patterns[key] = pattern
            # Incremental average. n_prev → новый avg = (avg_prev*n_prev + x) / (n_prev+1).
            n_prev = pattern.total
            if success:
                pattern.success_count += 1
            else:
                pattern.fail_count += 1
            n_new = n_prev + 1
            if latency_ms > 0:
                pattern.avg_latency_ms = (pattern.avg_latency_ms * n_prev + float(latency_ms)) / n_new
                pattern.recent_latencies_ms.append(float(latency_ms))
                if len(pattern.recent_latencies_ms) > 20:
                    pattern.recent_latencies_ms = pattern.recent_latencies_ms[-20:]
            if cost_usd > 0:
                pattern.avg_cost_usd = (pattern.avg_cost_usd * n_prev + float(cost_usd)) / n_new
            pattern.last_used_at = now_iso
            self._persist_to_disk()
        logger.info(
            "tool_composition_recorded",
            task_class=normalized_class,
            tools=list(combo),
            success=success,
            latency_ms=round(float(latency_ms), 1),
            cost_usd=round(float(cost_usd), 4),
        )

    def recommend_tools(
        self,
        task_class: str,
        *,
        top_k: int = 3,
        min_samples: int = 2,
    ) -> list[list[str]]:
        """Возвращает топ-K tool-combination для task_class по убыванию score.

        Score = success_rate * log1p(effective_frequency), где
        effective_frequency = total * decay_factor.

        `min_samples`: требуемый минимум total для попадания в выдачу
        (защита от случайных одноразовых записей).

        Возвращает список list[str] (а не tuple), чтобы caller мог легко
        сериализовать в JSON или передать как argument.
        """
        normalized_class = (task_class or "").strip()
        if not normalized_class or top_k <= 0:
            return []
        now = self._now_fn()
        scored: list[tuple[float, tuple[str, ...]]] = []
        with self._lock:
            for (cls_key, combo), pattern in self._patterns.items():
                if cls_key != normalized_class:
                    continue
                if pattern.total < min_samples:
                    continue
                age_days = self._age_days(pattern.last_used_at, now)
                if age_days > _HARD_CUTOFF_DAYS:
                    continue
                decay = 0.5 ** (age_days / _DECAY_DAYS) if age_days > 0 else 1.0
                effective_freq = pattern.total * decay
                # log1p чтобы 100 запусков не задавили 5 запусков с 100% успехом.
                score = pattern.success_rate * math.log1p(effective_freq)
                scored.append((score, combo))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [list(combo) for _, combo in scored[:top_k]]

    def list_patterns(self, task_class: str | None = None) -> list[dict[str, Any]]:
        """Снимок паттернов (опционально отфильтрованных по task_class).

        Возвращает копии dict'ов, не внутренние объекты.
        """
        with self._lock:
            patterns_copy = list(self._patterns.values())
        result = [p.to_dict() for p in patterns_copy]
        if task_class:
            normalized = task_class.strip()
            result = [r for r in result if r["task_class"] == normalized]
        return result

    def clear(self, task_class: str | None = None) -> int:
        """Сбрасывает паттерны. Если task_class задан — только для него.

        Возвращает количество удалённых паттернов.
        """
        with self._lock:
            if task_class is None:
                count = len(self._patterns)
                self._patterns = {}
            else:
                normalized = task_class.strip()
                keys = [k for k in self._patterns if k[0] == normalized]
                count = len(keys)
                for k in keys:
                    del self._patterns[k]
            if count:
                self._persist_to_disk()
        if count:
            logger.info("tool_composition_cleared", count=count, task_class=task_class)
        return count

    # ---- Internal helpers ------------------------------------------------

    @staticmethod
    def _age_days(iso_ts: str, now: datetime) -> float:
        if not iso_ts:
            return 0.0
        try:
            dt = datetime.fromisoformat(iso_ts)
        except (TypeError, ValueError):
            return 0.0
        delta = now - dt
        return max(delta.total_seconds() / 86400.0, 0.0)

    def _load_from_disk(self) -> None:
        t0 = time.monotonic()
        path = self._storage_path
        if path is None or not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8") or "[]")
        except (json.JSONDecodeError, OSError) as exc:
            elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
            logger.warning(
                "tool_composition_load_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
                elapsed_ms=elapsed_ms,
            )
            return
        # Поддерживаем оба формата: список dict (новый) и dict (если кто-то
        # руками отредактировал). Прощаем — главное не упасть.
        items: list[dict[str, Any]] = []
        if isinstance(raw, list):
            items = [x for x in raw if isinstance(x, dict)]
        elif isinstance(raw, dict):
            # legacy: dict[str, dict] без нашего ключа
            items = [v for v in raw.values() if isinstance(v, dict)]
        loaded = 0
        skipped = 0
        for item in items:
            pattern = ToolPattern.from_dict(item)
            if pattern is None:
                skipped += 1
                continue
            self._patterns[(pattern.task_class, pattern.tool_combination)] = pattern
            loaded += 1
        elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
        if loaded or skipped:
            logger.info(
                "tool_composition_loaded",
                loaded=loaded,
                skipped=skipped,
                elapsed_ms=elapsed_ms,
            )
        if elapsed_ms > _SLOW_LOAD_WARN_MS:
            logger.warning(
                "tool_composition_slow_load",
                loaded=loaded,
                elapsed_ms=elapsed_ms,
                threshold_ms=_SLOW_LOAD_WARN_MS,
            )

    def _persist_to_disk(self) -> None:
        path = self._storage_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = [p.to_dict() for p in self._patterns.values()]
            path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except (OSError, TypeError) as exc:
            logger.warning(
                "tool_composition_persist_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )


# Module-level singleton — pattern совпадает с chat_ban_cache, silence_manager,
# inbox_service, krab_scheduler. Конкретный путь конфигурируется вызовом
# `tool_composition_memory.configure_default_path(...)` из bootstrap.
tool_composition_memory = ToolCompositionMemory()


# Также явный re-export, удобно для импортов из тестов.
__all__ = [
    "ToolCompositionMemory",
    "ToolPattern",
    "tool_composition_memory",
]


