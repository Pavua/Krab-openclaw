# -*- coding: utf-8 -*-
"""Wave 139: forensic tracking 5xx-ошибок owner-панели.

Wave 122 audit middleware фиксирует факт запроса (method/path/status), Wave 131 —
гистограмму latency. Но при `status >= 500` теряется traceback / тело ответа,
что мешает root-cause анализу explicit HTTPException(500) или async-стек'ов,
которые не долетают до Sentry.

Этот модуль реализует:
  * `ErrorEventLogger.record(...)` — append JSONL запись в дневной файл
    `owner_panel_errors-YYYY-MM-DD.jsonl`. Активный симлинк/копия
    `owner_panel_errors.jsonl` всегда указывает на сегодняшний день.
  * Ротация: при первом write нового дня старые файлы старше `keep_days=7`
    удаляются (silent best-effort).
  * Запись содержит: ts, method, path, status, error_class, error_message,
    traceback (если есть), body_sample (первые 500 байт ответа), client_ip,
    auth_prefix.

Hot-path safety: все write/IO операции в try/except; никогда не raise наружу.
Singleton (lazy) для переиспользования между middleware и тестами.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.core.logger import get_logger

logger = get_logger(__name__)


DEFAULT_LOG_DIR: Path = Path("~/.openclaw/krab_runtime_state").expanduser()
DEFAULT_LOG_NAME: str = "owner_panel_errors.jsonl"
DEFAULT_KEEP_DAYS: int = 7
# Sample tела ответа: первые 500 байт, чтобы forensic не разнесло диск.
DEFAULT_BODY_SAMPLE_LIMIT: int = 500


def _today_fn_default() -> date:
    return datetime.now(timezone.utc).date()


class ErrorEventLogger:
    """JSONL writer с daily-ротацией. Append-only, hot-path-safe.

    Args:
        log_dir: каталог для файлов (default `~/.openclaw/krab_runtime_state`).
        keep_days: сколько дневных файлов хранить (старше — удаляются).
        today_fn: инжектируемая функция «сегодняшняя дата» (для тестов).
        body_sample_limit: лимит body_sample в байтах.
    """

    def __init__(
        self,
        log_dir: Path | str | None = None,
        *,
        keep_days: int = DEFAULT_KEEP_DAYS,
        today_fn: Callable[[], date] | None = None,
        body_sample_limit: int = DEFAULT_BODY_SAMPLE_LIMIT,
    ) -> None:
        self._log_dir: Path = Path(str(log_dir)) if log_dir is not None else DEFAULT_LOG_DIR
        self._keep_days: int = max(1, int(keep_days))
        self._today_fn: Callable[[], date] = today_fn or _today_fn_default
        self._body_sample_limit: int = max(0, int(body_sample_limit))
        self._lock = threading.Lock()
        self._last_rotation_day: date | None = None
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            # bootstrap не должен падать — write позже тоже молчит
            pass

    @property
    def log_dir(self) -> Path:
        return self._log_dir

    def _dated_path(self, day: date) -> Path:
        return self._log_dir / f"owner_panel_errors-{day.isoformat()}.jsonl"

    def _active_path(self) -> Path:
        """Alias на сегодняшний дневной файл (для удобства tail -f)."""
        return self._log_dir / DEFAULT_LOG_NAME

    def _rotate_if_new_day(self, day: date) -> list[str]:
        """Удалить файлы старше `keep_days`. Возвращает список удалённых путей."""
        removed: list[str] = []
        if self._last_rotation_day == day:
            return removed
        try:
            entries = list(self._log_dir.glob("owner_panel_errors-*.jsonl"))
        except OSError:
            entries = []
        # cutoff = day - keep_days; всё что строго старше — удаляем.
        cutoff_ordinal = day.toordinal() - self._keep_days
        for entry in entries:
            try:
                stem = entry.stem  # owner_panel_errors-2026-05-12
                # Парсим дату из суффикса; пропускаем если формат не тот.
                suffix = stem.replace("owner_panel_errors-", "", 1)
                file_day = datetime.strptime(suffix, "%Y-%m-%d").date()
            except ValueError:
                continue
            if file_day.toordinal() <= cutoff_ordinal:
                try:
                    entry.unlink()
                    removed.append(str(entry))
                except OSError:
                    pass
        self._last_rotation_day = day
        return removed

    def _build_payload(
        self,
        *,
        ts_unix: float,
        method: str,
        path: str,
        status: int,
        error_class: str | None,
        error_message: str | None,
        traceback_text: str | None,
        body_sample: bytes | str | None,
        client_ip: str | None,
        auth_prefix: str | None,
    ) -> dict[str, Any]:
        body_str: str | None = None
        if body_sample is not None:
            if isinstance(body_sample, bytes):
                try:
                    body_str = body_sample[: self._body_sample_limit].decode(
                        "utf-8", errors="replace"
                    )
                except Exception:  # noqa: BLE001
                    body_str = None
            else:
                body_str = str(body_sample)[: self._body_sample_limit]
        return {
            "ts": ts_unix,
            "ts_iso": datetime.fromtimestamp(ts_unix, tz=timezone.utc).isoformat(),
            "method": method,
            "path": path,
            "status": int(status),
            "error_class": error_class,
            "error_message": (error_message[:500] if error_message else None),
            "traceback": traceback_text,
            "body_sample": body_str,
            "client_ip": client_ip,
            "auth_prefix": auth_prefix,
        }

    def record(
        self,
        *,
        method: str,
        path: str,
        status: int,
        error_class: str | None = None,
        error_message: str | None = None,
        traceback_text: str | None = None,
        body_sample: bytes | str | None = None,
        client_ip: str | None = None,
        auth_prefix: str | None = None,
        ts_unix: float | None = None,
    ) -> bool:
        """Записать одно 5xx-событие. Возвращает True если запись успешна."""
        try:
            day = self._today_fn()
            ts = ts_unix if ts_unix is not None else time.time()
            payload = self._build_payload(
                ts_unix=ts,
                method=method,
                path=path,
                status=status,
                error_class=error_class,
                error_message=error_message,
                traceback_text=traceback_text,
                body_sample=body_sample,
                client_ip=client_ip,
                auth_prefix=auth_prefix,
            )
            line = json.dumps(payload, ensure_ascii=False) + "\n"
            with self._lock:
                self._rotate_if_new_day(day)
                dated = self._dated_path(day)
                with dated.open("a", encoding="utf-8") as fh:
                    fh.write(line)
                # Обновляем alias-файл копией сегодняшней записи (best-effort).
                try:
                    with self._active_path().open("a", encoding="utf-8") as fh2:
                        fh2.write(line)
                except OSError:
                    pass
            return True
        except (OSError, ValueError, TypeError) as exc:
            logger.warning(
                "owner_panel_error_log_write_failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return False

    def read_recent(self, *, day: date | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Прочитать последние `limit` событий за день (default — сегодня)."""
        target_day = day if day is not None else self._today_fn()
        dated = self._dated_path(target_day)
        if not dated.exists():
            return []
        try:
            with dated.open("r", encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError:
            return []
        result: list[dict[str, Any]] = []
        for raw in lines[-max(1, int(limit)) :]:
            raw = raw.strip()
            if not raw:
                continue
            try:
                result.append(json.loads(raw))
            except (json.JSONDecodeError, ValueError):
                continue
        return result


# ---------------------------------------------------------------------------
# Singleton (lazy) — переиспользуется middleware'ом и тестами.
# ---------------------------------------------------------------------------

_DEFAULT_LOGGER: ErrorEventLogger | None = None
_DEFAULT_LOCK = threading.Lock()


def get_default_error_logger() -> ErrorEventLogger:
    """Возвращает singleton ErrorEventLogger (создаётся при первом обращении)."""
    global _DEFAULT_LOGGER
    if _DEFAULT_LOGGER is not None:
        return _DEFAULT_LOGGER
    with _DEFAULT_LOCK:
        if _DEFAULT_LOGGER is None:
            # Опциональный override через env (для отладки).
            override = os.getenv("KRAB_OWNER_PANEL_ERROR_LOG_DIR", "").strip()
            log_dir: Path | None = Path(override) if override else None
            _DEFAULT_LOGGER = ErrorEventLogger(log_dir=log_dir)
    return _DEFAULT_LOGGER


def reset_default_error_logger_for_tests() -> None:
    """Сбрасывает singleton — только для тестов."""
    global _DEFAULT_LOGGER
    _DEFAULT_LOGGER = None
