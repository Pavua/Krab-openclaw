# -*- coding: utf-8 -*-
"""
AnomalyDetector — детектор поведенческих аномалий Краба.

Идея 26 из бэклога: Краб ведёт baseline ключевых метрик
(длина ответа, response time p95, частота tool-call'ов, error rate, sentiment
drift, ...). При значимом сдвиге относительно sliding-window baseline
пользователю в owner DM улетает alert.

Этот модуль — pure detector (без alert delivery): принимает observations,
держит sliding window 24h на метрику, и по запросу возвращает список
аномалий со severity. Доставка алёртов делается отдельным consumer'ом
(см. backlog).

Алгоритм:
- На каждую метрику отдельный sliding window (deque) с TTL 24h.
- Window expiry — ленивый: prune'ится при `record_metric` и `detect_anomalies`.
- Baseline = mean window'а; разброс = population std.
- Z-score текущего значения относительно baseline:
    |z| > 3.0 → severity=high
    |z| > 2.0 → severity=medium
    иначе → не аномалия
- Для статистической стабильности нужно минимум 10 точек в окне (`min_samples`).
  Иначе — silent skip (нет baseline → нет аномалии).
- Для метрик без вариативности (std == 0) сравнение по z-score не имеет
  смысла, поэтому возвращаем «не аномалия». Это аккуратнее, чем кидать
  high severity на любое отклонение от константы.

Persistence:
- JSON-снапшот window'ов в `~/.openclaw/krab_runtime_state/anomaly_baselines.json`.
- Persist на каждый `record_metric` — подкорректировать просто, writes
  редкие (метрики нечастые, не на каждое сообщение).
- Lazy load на старте через `configure_default_path()`.

Конфиг:
- `KRAB_ANOMALY_DETECTION_ENABLED` (default False) — глобальный gate.
  Сам detector module-агностичен, gate проверяет caller перед `record_metric`.

Не решает:
- Не доставляет alert'ы. Только returns `list[Anomaly]`. Бэклог:
  proactive_watch consumer + cooldown per-metric, чтобы не флудить owner.
- Не делает trend detection (только spike). Sentiment drift — будущее
  расширение, нужен отдельный smoothed signal.
"""

from __future__ import annotations

import json
import math
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)

# Дефолтное окно — 24 часа. Для большинства метрик хватает.
_DEFAULT_WINDOW_HOURS: float = 24.0
# Минимум точек, при котором мы вообще считаем baseline стабильным.
# Меньше — silent skip: сначала надо собрать историю, потом проверять.
_DEFAULT_MIN_SAMPLES: int = 10
# Пороги z-score для tier'ов severity.
_Z_HIGH: float = 3.0
_Z_MEDIUM: float = 2.0


@dataclass(frozen=True)
class Anomaly:
    """Снимок одной обнаруженной аномалии. Иммутабельно — caller не должен
    мутировать наше внутреннее состояние."""

    metric: str
    current_value: float
    baseline_value: float
    std_dev: float
    z_score: float
    severity: str  # "high" | "medium"
    timestamp: datetime
    sample_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "current_value": self.current_value,
            "baseline_value": self.baseline_value,
            "std_dev": self.std_dev,
            "z_score": self.z_score,
            "severity": self.severity,
            "timestamp": self.timestamp.isoformat(),
            "sample_count": self.sample_count,
        }


@dataclass
class _MetricWindow:
    """Sliding window одной метрики: список (timestamp, value) с TTL prune."""

    samples: deque[tuple[datetime, float]] = field(default_factory=deque)


class AnomalyDetector:
    """Sliding-window z-score detector с persist-снапшотом.

    Используется как module-level singleton (`anomaly_detector` ниже).
    Конструктор принимает path/now_fn только для тестов — runtime
    bootstrap дёргает `configure_default_path`.
    """

    def __init__(
        self,
        *,
        storage_path: Path | None = None,
        now_fn: Callable[[], datetime] | None = None,
        window_hours: float = _DEFAULT_WINDOW_HOURS,
        min_samples: int = _DEFAULT_MIN_SAMPLES,
    ) -> None:
        self._lock = threading.RLock()
        self._storage_path: Path | None = storage_path
        self._windows: dict[str, _MetricWindow] = {}
        self._now_fn: Callable[[], datetime] = now_fn or (lambda: datetime.now(timezone.utc))
        self._window_hours: float = float(window_hours)
        self._min_samples: int = int(min_samples)
        if storage_path is not None:
            self._load_from_disk()

    # ---- Configuration --------------------------------------------------

    def configure_default_path(self, storage_path: Path) -> None:
        """Сетит путь к JSON-снапшоту и подгружает с диска. Bootstrap-only."""
        with self._lock:
            self._storage_path = storage_path
            self._windows = {}
            self._load_from_disk()

    # ---- Core API -------------------------------------------------------

    def record_metric(
        self,
        name: str,
        value: float,
        *,
        ts: datetime | None = None,
    ) -> None:
        """Регистрирует одну точку метрики. Lazy-prune окна по TTL и persist."""
        metric = self._normalize_name(name)
        if not metric:
            return
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            logger.warning(
                "anomaly_record_value_invalid",
                metric=metric,
                value=repr(value),
            )
            return
        if not math.isfinite(numeric):
            # NaN/inf не дадут осмысленного baseline и сломают std/mean.
            logger.warning(
                "anomaly_record_value_non_finite",
                metric=metric,
                value=repr(value),
            )
            return
        timestamp = ts or self._now()
        # Если caller передал naive datetime, чиним до UTC, чтобы сравнения
        # с _now() (timezone-aware) не падали с TypeError.
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        with self._lock:
            window = self._windows.setdefault(metric, _MetricWindow())
            window.samples.append((timestamp, numeric))
            self._prune_window(metric, window)
            self._persist_to_disk()

    def detect_anomalies(self) -> list[Anomaly]:
        """Возвращает список обнаруженных аномалий по всем известным метрикам.

        Аномалия = последняя точка окна, чей |z-score| относительно baseline
        (mean/std остального окна) превышает порог. Для метрик с
        sample_count < min_samples или std == 0 — silent skip.
        """
        result: list[Anomaly] = []
        with self._lock:
            for metric, window in list(self._windows.items()):
                self._prune_window(metric, window)
                if len(window.samples) < self._min_samples:
                    continue
                values = [v for _, v in window.samples]
                # Baseline и std считаем по «истории без последней точки» —
                # иначе current_value сам себя втягивает в baseline и z-score
                # системно занижается. На больших окнах разница незначительна,
                # на min_samples=10 — заметна.
                history = values[:-1]
                current_ts, current_value = window.samples[-1]
                if not history:
                    continue
                mean = sum(history) / len(history)
                variance = sum((x - mean) ** 2 for x in history) / len(history)
                std = math.sqrt(variance)
                if std == 0.0:
                    continue
                z = (current_value - mean) / std
                abs_z = abs(z)
                if abs_z > _Z_HIGH:
                    severity = "high"
                elif abs_z > _Z_MEDIUM:
                    severity = "medium"
                else:
                    continue
                result.append(
                    Anomaly(
                        metric=metric,
                        current_value=current_value,
                        baseline_value=mean,
                        std_dev=std,
                        z_score=z,
                        severity=severity,
                        timestamp=current_ts,
                        sample_count=len(window.samples),
                    )
                )
        return result

    def metric_names(self) -> list[str]:
        """Снимок имён зарегистрированных метрик (копия)."""
        with self._lock:
            return list(self._windows.keys())

    def clear(self, name: str | None = None) -> None:
        """Полная очистка одной метрики или всех (для owner-команд / тестов)."""
        with self._lock:
            if name is None:
                self._windows = {}
            else:
                self._windows.pop(self._normalize_name(name), None)
            self._persist_to_disk()

    # ---- Internal helpers -----------------------------------------------

    def _now(self) -> datetime:
        return self._now_fn()

    @staticmethod
    def _normalize_name(name: str) -> str:
        return str(name or "").strip()

    def _prune_window(self, metric: str, window: _MetricWindow) -> None:
        """Удаляет точки старше window_hours. Caller держит lock."""
        if not window.samples:
            return
        cutoff = self._now() - timedelta(hours=self._window_hours)
        while window.samples and window.samples[0][0] < cutoff:
            window.samples.popleft()
        if not window.samples:
            # Пустое окно держать не имеет смысла, persist'нем без него.
            self._windows.pop(metric, None)

    def _load_from_disk(self) -> None:
        path = self._storage_path
        if path is None or not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "anomaly_baselines_load_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return
        if not isinstance(raw, dict):
            logger.warning("anomaly_baselines_load_malformed", path=str(path))
            return
        now = self._now()
        cutoff = now - timedelta(hours=self._window_hours)
        loaded = 0
        skipped = 0
        for metric, payload in raw.items():
            if not isinstance(payload, list):
                skipped += 1
                continue
            window = _MetricWindow()
            for item in payload:
                if not isinstance(item, dict):
                    continue
                ts_raw = item.get("ts")
                value_raw = item.get("value")
                try:
                    ts = datetime.fromisoformat(str(ts_raw))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    value = float(value_raw)
                except (TypeError, ValueError):
                    continue
                if ts < cutoff or not math.isfinite(value):
                    continue
                window.samples.append((ts, value))
            if window.samples:
                self._windows[str(metric)] = window
                loaded += 1
            else:
                skipped += 1
        if loaded or skipped:
            logger.info(
                "anomaly_baselines_loaded",
                loaded=loaded,
                skipped=skipped,
            )

    def _persist_to_disk(self) -> None:
        path = self._storage_path
        if path is None:
            return
        snapshot: dict[str, list[dict[str, Any]]] = {}
        for metric, window in self._windows.items():
            snapshot[metric] = [
                {"ts": ts.isoformat(), "value": value} for ts, value in window.samples
            ]
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(snapshot, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except (OSError, TypeError) as exc:
            logger.warning(
                "anomaly_baselines_persist_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )


# Module-level singleton — pattern совпадает с chat_ban_cache, silence_manager,
# inbox_service. Конкретный путь конфигурируется вызовом
# `anomaly_detector.configure_default_path(...)` из bootstrap.
anomaly_detector = AnomalyDetector()
