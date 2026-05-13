# -*- coding: utf-8 -*-
"""
Wave 205: Memory leak detector — RSS/swap monitoring + trend analysis.

Background-задача периодически снимает срез памяти Krab-процесса (RSS, VMS,
swap), хранит rolling-окно последних N снимков и обнаруживает монотонный
рост RSS, который может указывать на утечку.

Триггер: nightly-audit зафиксировал суммарный swap 42GB за 7 дней при avg RSS
0.8GB — высокий swap при низком RSS обычно означает, что что-то накапливается
в памяти и вытесняется в swap. Возможные кандидаты:
- Telegram media cache (pyrofork)
- Voice STT/TTS буферы
- LLM context accumulator
- RAG embeddings (sqlite-vec memory cache)

Hard constraints:
- НЕ авто-рестартим Krab — только alert + лог.
- Snapshot файл — append-only JSONL, ограничен MAX_SNAPSHOTS (rotate).
- Tracemalloc включается только если KRAB_MEMORY_LEAK_TRACEMALLOC=1
  (overhead на хот-пути иначе).
- Sentry-warning rate-limited (1/24h), чтобы не флудить.

ENV:
- KRAB_MEMORY_LEAK_DETECTOR_ENABLED (default 1) — мастер-выключатель
- KRAB_MEMORY_LEAK_CHECK_INTERVAL_SEC (default 900 = 15 мин)
- KRAB_MEMORY_LEAK_THRESHOLD_MB_PER_HOUR (default 50)
- KRAB_MEMORY_LEAK_WINDOW_HOURS (default 24)
- KRAB_MEMORY_LEAK_TRACEMALLOC (default 0) — включить tracemalloc top10
- KRAB_RUNTIME_STATE_DIR — override корня (для тестов)

Prometheus метрики:
- krab_process_rss_bytes (gauge)
- krab_process_vms_bytes (gauge)
- krab_process_swap_bytes (gauge)
- krab_memory_leak_suspected (gauge 0/1)
- krab_memory_leak_growth_mb_per_hour (gauge)
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)

# Defaults — overridable через ENV.
DEFAULT_CHECK_INTERVAL_SEC = 900  # 15 минут
DEFAULT_THRESHOLD_MB_PER_HOUR = 50.0  # > 50 МБ/час за окно → flag leak
DEFAULT_WINDOW_HOURS = 24  # анализ тренда за 24 часа
DEFAULT_MAX_SNAPSHOTS = 100  # rolling window длина
DEFAULT_SENTRY_COOLDOWN_SEC = 86400  # 24h rate-limit для Sentry alerts

SNAPSHOTS_FILENAME = "memory_snapshots.jsonl"
SENTRY_COOLDOWN_FILENAME = "memory_leak_sentry_last.json"


@dataclass(frozen=True)
class MemorySnapshot:
    """Один снимок памяти процесса."""

    ts: float  # unix seconds
    iso: str  # ISO8601 UTC
    pid: int
    rss_bytes: int
    vms_bytes: int
    swap_bytes: int  # 0 если swap нечитаем (macOS user mode)
    num_threads: int
    open_files: int  # -1 если нечитаемо
    tracemalloc_top: list[dict[str, Any]] | None = None  # None если выключен

    def to_jsonl(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


# ───────────────────────── helpers ─────────────────────────


def _runtime_state_dir() -> Path:
    """Корень runtime-state — совместим с state_snapshots.py."""
    override = os.environ.get("KRAB_RUNTIME_STATE_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".openclaw" / "krab_runtime_state"


def _is_enabled() -> bool:
    """Default ON; OFF только если ENV явно = 0/false/no."""
    raw = (os.environ.get("KRAB_MEMORY_LEAK_DETECTOR_ENABLED", "1") or "").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _check_interval_sec() -> int:
    raw = os.environ.get("KRAB_MEMORY_LEAK_CHECK_INTERVAL_SEC")
    if not raw:
        return DEFAULT_CHECK_INTERVAL_SEC
    try:
        return max(60, int(raw))
    except (ValueError, TypeError):
        return DEFAULT_CHECK_INTERVAL_SEC


def _threshold_mb_per_hour() -> float:
    raw = os.environ.get("KRAB_MEMORY_LEAK_THRESHOLD_MB_PER_HOUR")
    if not raw:
        return DEFAULT_THRESHOLD_MB_PER_HOUR
    try:
        return max(1.0, float(raw))
    except (ValueError, TypeError):
        return DEFAULT_THRESHOLD_MB_PER_HOUR


def _window_hours() -> int:
    raw = os.environ.get("KRAB_MEMORY_LEAK_WINDOW_HOURS")
    if not raw:
        return DEFAULT_WINDOW_HOURS
    try:
        return max(1, int(raw))
    except (ValueError, TypeError):
        return DEFAULT_WINDOW_HOURS


def _tracemalloc_enabled() -> bool:
    raw = (os.environ.get("KRAB_MEMORY_LEAK_TRACEMALLOC", "0") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ───────────────────────── snapshot capture ─────────────────────────


def _capture_tracemalloc_top(limit: int = 10) -> list[dict[str, Any]] | None:
    """Top-N memory allocators (only if tracemalloc уже tracing)."""
    try:
        import tracemalloc

        if not tracemalloc.is_tracing():
            tracemalloc.start(25)  # 25 frames
            # Первый снимок будет пустоват — это OK.
        snap = tracemalloc.take_snapshot()
        stats = snap.statistics("lineno")[:limit]
        return [
            {
                "file": str(s.traceback[0].filename) if s.traceback else "<unknown>",
                "lineno": int(s.traceback[0].lineno) if s.traceback else 0,
                "size_kb": round(s.size / 1024.0, 1),
                "count": int(s.count),
            }
            for s in stats
        ]
    except Exception as exc:  # noqa: BLE001
        logger.debug("tracemalloc_capture_failed", error=str(exc))
        return None


def capture_snapshot() -> MemorySnapshot | None:
    """
    Снимает память собственного процесса.

    Возвращает None если psutil недоступен или процесс не существует.
    """
    try:
        import psutil  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        logger.debug("psutil_unavailable", error=str(exc))
        return None

    try:
        proc = psutil.Process(os.getpid())
        meminfo = proc.memory_info()
        rss = int(getattr(meminfo, "rss", 0) or 0)
        vms = int(getattr(meminfo, "vms", 0) or 0)

        # psutil.Process.memory_full_info() даёт swap, но требует root на macOS;
        # graceful fallback.
        swap = 0
        try:
            full = proc.memory_full_info()
            swap = int(getattr(full, "swap", 0) or 0)
        except (psutil.AccessDenied, AttributeError, OSError):
            # На macOS без root → AccessDenied. Возвращаем 0 — будет читаться
            # системный swap из CLI отчёта при необходимости.
            swap = 0

        threads = 0
        try:
            threads = int(proc.num_threads())
        except Exception:  # noqa: BLE001
            threads = 0

        open_files = -1
        try:
            open_files = len(proc.open_files())
        except (psutil.AccessDenied, OSError):
            open_files = -1

        tm_top: list[dict[str, Any]] | None = None
        if _tracemalloc_enabled():
            tm_top = _capture_tracemalloc_top(10)

        return MemorySnapshot(
            ts=time.time(),
            iso=_now_iso(),
            pid=proc.pid,
            rss_bytes=rss,
            vms_bytes=vms,
            swap_bytes=swap,
            num_threads=threads,
            open_files=open_files,
            tracemalloc_top=tm_top,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory_snapshot_failed", error=str(exc))
        return None


# ───────────────────────── storage (JSONL rolling) ─────────────────────────


def snapshots_path(runtime_state_dir: Path | None = None) -> Path:
    base = runtime_state_dir or _runtime_state_dir()
    return base / SNAPSHOTS_FILENAME


def _sentry_cooldown_path(runtime_state_dir: Path | None = None) -> Path:
    base = runtime_state_dir or _runtime_state_dir()
    return base / SENTRY_COOLDOWN_FILENAME


def append_snapshot(
    snap: MemorySnapshot,
    *,
    runtime_state_dir: Path | None = None,
    max_snapshots: int = DEFAULT_MAX_SNAPSHOTS,
) -> Path:
    """Append snapshot в JSONL + rotate до max_snapshots строк."""
    path = snapshots_path(runtime_state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Append.
    with path.open("a", encoding="utf-8") as fh:
        fh.write(snap.to_jsonl() + "\n")

    # Rotate: читаем всё → если > max → переписываем последние max.
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > max_snapshots:
            tail = lines[-max_snapshots:]
            tmp = path.with_suffix(".jsonl.tmp")
            tmp.write_text("\n".join(tail) + "\n", encoding="utf-8")
            os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory_snapshots_rotate_failed", error=str(exc))

    return path


def read_snapshots(
    runtime_state_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Читает все snapshots из JSONL. Поломанные строки skip."""
    path = snapshots_path(runtime_state_dir)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError as exc:
        logger.warning("memory_snapshots_read_failed", error=str(exc))
    return rows


# ───────────────────────── leak detection ─────────────────────────


@dataclass(frozen=True)
class LeakAnalysis:
    """Результат анализа тренда RSS за окно."""

    window_hours: int
    samples: int
    first_rss_mb: float
    last_rss_mb: float
    delta_mb: float
    duration_hours: float
    growth_mb_per_hour: float
    suspected: bool
    threshold_mb_per_hour: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_trend(
    snapshots: list[dict[str, Any]] | None = None,
    *,
    runtime_state_dir: Path | None = None,
    window_hours: int | None = None,
    threshold_mb_per_hour: float | None = None,
) -> LeakAnalysis:
    """
    Анализирует RSS growth за last `window_hours`. Использует линейную
    дельту (last - first) / duration — простая, устойчивая к шуму метрика.

    Если данных меньше 2 точек или окно < threshold → suspected=False.
    """
    rows = snapshots if snapshots is not None else read_snapshots(runtime_state_dir)
    win_h = window_hours or _window_hours()
    thr = threshold_mb_per_hour or _threshold_mb_per_hour()

    if not rows or len(rows) < 2:
        return LeakAnalysis(
            window_hours=win_h,
            samples=len(rows) if rows else 0,
            first_rss_mb=0.0,
            last_rss_mb=0.0,
            delta_mb=0.0,
            duration_hours=0.0,
            growth_mb_per_hour=0.0,
            suspected=False,
            threshold_mb_per_hour=thr,
        )

    # Фильтр в окно.
    cutoff = time.time() - win_h * 3600
    windowed = [r for r in rows if float(r.get("ts", 0) or 0) >= cutoff]
    if len(windowed) < 2:
        windowed = rows[-2:]  # fallback: хотя бы последние 2

    first = windowed[0]
    last = windowed[-1]
    first_rss = float(first.get("rss_bytes", 0) or 0) / (1024 * 1024)
    last_rss = float(last.get("rss_bytes", 0) or 0) / (1024 * 1024)
    delta_mb = last_rss - first_rss
    duration_sec = float(last.get("ts", 0) or 0) - float(first.get("ts", 0) or 0)
    duration_h = max(duration_sec / 3600.0, 1e-6)
    growth = delta_mb / duration_h

    # Suspected: рост > threshold И минимум 4 точки И окно ≥ 1 час.
    suspected = growth > thr and len(windowed) >= 4 and duration_h >= 1.0

    return LeakAnalysis(
        window_hours=win_h,
        samples=len(windowed),
        first_rss_mb=round(first_rss, 2),
        last_rss_mb=round(last_rss, 2),
        delta_mb=round(delta_mb, 2),
        duration_hours=round(duration_h, 3),
        growth_mb_per_hour=round(growth, 2),
        suspected=suspected,
        threshold_mb_per_hour=thr,
    )


# ───────────────────────── Sentry rate-limit ─────────────────────────


def _sentry_can_fire(
    runtime_state_dir: Path | None = None,
    *,
    cooldown_sec: int = DEFAULT_SENTRY_COOLDOWN_SEC,
) -> bool:
    """True если с last Sentry warning прошло > cooldown_sec."""
    path = _sentry_cooldown_path(runtime_state_dir)
    if not path.exists():
        return True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        last_ts = float(data.get("last_ts", 0) or 0)
        return (time.time() - last_ts) >= cooldown_sec
    except Exception:  # noqa: BLE001
        return True


def _sentry_mark_fired(runtime_state_dir: Path | None = None) -> None:
    path = _sentry_cooldown_path(runtime_state_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"last_ts": time.time(), "iso": _now_iso()}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("sentry_cooldown_write_failed", error=str(exc))


def _emit_sentry_warning(analysis: LeakAnalysis, snap: MemorySnapshot) -> None:
    """Шлёт warning в Sentry. Silent-fail если sentry_sdk недоступен."""
    try:
        from src.core.sentry_integration import capture_message

        capture_message(
            "memory_leak_suspected",
            level="warning",
            growth_mb_per_hour=analysis.growth_mb_per_hour,
            threshold=analysis.threshold_mb_per_hour,
            window_hours=analysis.window_hours,
            samples=analysis.samples,
            rss_mb=round(snap.rss_bytes / (1024 * 1024), 2),
            swap_mb=round(snap.swap_bytes / (1024 * 1024), 2),
            pid=snap.pid,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("sentry_emit_failed", error=str(exc))


# ───────────────────────── prometheus state ─────────────────────────

# Module-level state — экспортируется в /metrics через collect.py.
# Mutable singletons по паттерну src.core.metrics.process.
_LAST_RSS_BYTES: list[int] = [0]
_LAST_VMS_BYTES: list[int] = [0]
_LAST_SWAP_BYTES: list[int] = [0]
_LAST_GROWTH_MB_PER_HOUR: list[float] = [0.0]
_LEAK_SUSPECTED_FLAG: list[int] = [0]


def get_prometheus_state() -> dict[str, float | int]:
    """Snapshot текущих значений для /metrics rendering."""
    return {
        "krab_process_rss_bytes": _LAST_RSS_BYTES[0],
        "krab_process_vms_bytes": _LAST_VMS_BYTES[0],
        "krab_process_swap_bytes": _LAST_SWAP_BYTES[0],
        "krab_memory_leak_growth_mb_per_hour": _LAST_GROWTH_MB_PER_HOUR[0],
        "krab_memory_leak_suspected": _LEAK_SUSPECTED_FLAG[0],
    }


# ───────────────────────── orchestrator ─────────────────────────


def run_once(runtime_state_dir: Path | None = None) -> dict[str, Any]:
    """
    Один полный цикл: snapshot → append → analyze → update prom state →
    маркируем sentry-alert при необходимости.

    Возвращает dict с deatils. Idempotent при отсутствии psutil/permission —
    просто пропускает шаги.
    """
    if not _is_enabled():
        return {"enabled": False}

    snap = capture_snapshot()
    if snap is None:
        return {"enabled": True, "captured": False}

    append_snapshot(snap, runtime_state_dir=runtime_state_dir)

    # Update Prometheus gauges.
    _LAST_RSS_BYTES[0] = snap.rss_bytes
    _LAST_VMS_BYTES[0] = snap.vms_bytes
    _LAST_SWAP_BYTES[0] = snap.swap_bytes

    analysis = analyze_trend(runtime_state_dir=runtime_state_dir)
    _LAST_GROWTH_MB_PER_HOUR[0] = analysis.growth_mb_per_hour
    _LEAK_SUSPECTED_FLAG[0] = 1 if analysis.suspected else 0

    sentry_fired = False
    if analysis.suspected and _sentry_can_fire(runtime_state_dir):
        _emit_sentry_warning(analysis, snap)
        _sentry_mark_fired(runtime_state_dir)
        sentry_fired = True
        logger.warning(
            "memory_leak_suspected",
            growth_mb_per_hour=analysis.growth_mb_per_hour,
            threshold=analysis.threshold_mb_per_hour,
            window_hours=analysis.window_hours,
            samples=analysis.samples,
            rss_mb=round(snap.rss_bytes / (1024 * 1024), 2),
        )
    else:
        logger.info(
            "memory_snapshot_recorded",
            rss_mb=round(snap.rss_bytes / (1024 * 1024), 2),
            vms_mb=round(snap.vms_bytes / (1024 * 1024), 2),
            swap_mb=round(snap.swap_bytes / (1024 * 1024), 2),
            growth_mb_per_hour=analysis.growth_mb_per_hour,
        )

    return {
        "enabled": True,
        "captured": True,
        "snapshot": asdict(snap),
        "analysis": analysis.to_dict(),
        "sentry_fired": sentry_fired,
    }


async def background_loop(runtime_state_dir: Path | None = None) -> None:
    """
    Background-задача для запуска из userbot_bridge bootstrap.

    Выполняет run_once каждые KRAB_MEMORY_LEAK_CHECK_INTERVAL_SEC секунд.
    Любые exception ловятся — задача никогда не падает целиком.
    """
    import asyncio

    if not _is_enabled():
        logger.info("memory_leak_detector_disabled")
        return

    interval = _check_interval_sec()
    logger.info(
        "memory_leak_detector_started",
        interval_sec=interval,
        threshold_mb_per_hour=_threshold_mb_per_hour(),
        window_hours=_window_hours(),
        tracemalloc=_tracemalloc_enabled(),
    )

    while True:
        try:
            run_once(runtime_state_dir)
        except Exception as exc:  # noqa: BLE001
            logger.warning("memory_leak_detector_tick_failed", error=str(exc))
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("memory_leak_detector_cancelled")
            raise
