# -*- coding: utf-8 -*-
"""Wave 56-I-audit-rotation: ротация audit-логов с gzip-сжатием.

Поддерживает два аудит-лога Krab:
  - /tmp/krab_bash_audit.log         (bash_guard verdicts, JSONL)
  - ~/.openclaw/krab_runtime_state/agent_audit.jsonl  (agent multi-channel actions)

Политика:
  - Если файл > max_size_mb → переименовать в <name>.1.gz (gzip)
  - Старые архивы сдвинуть: .1.gz → .2.gz, ..., .(N-1).gz → .N.gz
  - Файлы сверх keep_count — удаляются
  - Оригинальный путь после ротации — пустой файл (ready for new writes)
  - Атомарность: rename() POSIX-атомарен, данные не теряются
"""

from __future__ import annotations

import gzip
import os
import shutil
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)

# Дефолтные пути audit-логов
_BASH_AUDIT_PATH = Path("/tmp/krab_bash_audit.log")
_AGENT_AUDIT_PATH = Path.home() / ".openclaw" / "krab_runtime_state" / "agent_audit.jsonl"


class AuditLogRotator:
    """Ротатор audit-логов с gzip-сжатием и хранением keep_count архивов."""

    def rotate_if_needed(
        self,
        log_path: Path,
        *,
        max_size_mb: int = 10,
        keep_count: int = 5,
    ) -> dict[str, Any]:
        """Проверить размер и при необходимости выполнить ротацию.

        Returns:
            dict с ключами:
              rotated     — bool, была ли ротация
              old_size_mb — float, размер до ротации
              rotated_to  — str, путь к новому .1.gz (или '' если не ротировали)
              removed     — list[str], удалённые старые архивы
        """
        result: dict[str, Any] = {
            "rotated": False,
            "old_size_mb": 0.0,
            "rotated_to": "",
            "removed": [],
        }

        if not log_path.exists():
            return result

        try:
            current_size = log_path.stat().st_size
        except OSError:
            return result

        result["old_size_mb"] = round(current_size / (1024 * 1024), 3)

        max_bytes = max_size_mb * 1024 * 1024
        if current_size <= max_bytes:
            # Ниже порога — ничего не делаем
            return result

        # --- Сдвиг существующих архивов: .N.gz (old) → удалить, .(N-1).gz → .N.gz ---
        removed: list[str] = []
        # Сдвигаем от старшего к младшему, чтобы не затереть
        for idx in range(keep_count, 0, -1):
            archive = _archive_path(log_path, idx)
            if not archive.exists():
                continue
            if idx >= keep_count:
                # Самый старый — удалить
                try:
                    archive.unlink()
                    removed.append(str(archive))
                    logger.debug("audit_rotator_removed_oldest", path=str(archive))
                except OSError as exc:
                    logger.warning("audit_rotator_remove_failed", path=str(archive), error=str(exc))
            else:
                # Сдвинуть .idx.gz → .(idx+1).gz
                next_archive = _archive_path(log_path, idx + 1)
                try:
                    archive.rename(next_archive)
                except OSError as exc:
                    logger.warning(
                        "audit_rotator_shift_failed",
                        from_=str(archive),
                        to=str(next_archive),
                        error=str(exc),
                    )

        # --- Сжать текущий лог в .1.gz ---
        target_gz = _archive_path(log_path, 1)
        try:
            _compress_to_gz(log_path, target_gz)
        except OSError as exc:
            logger.error("audit_rotator_compress_failed", path=str(log_path), error=str(exc))
            return result

        # --- Открыть новый пустой файл (atomic: просто truncate/create) ---
        try:
            log_path.write_bytes(b"")
        except OSError as exc:
            logger.error("audit_rotator_create_empty_failed", path=str(log_path), error=str(exc))
            # Ротация всё же произошла (gzip создан), отчитываемся
            result["rotated"] = True
            result["rotated_to"] = str(target_gz)
            result["removed"] = removed
            return result

        result["rotated"] = True
        result["rotated_to"] = str(target_gz)
        result["removed"] = removed
        logger.info(
            "audit_log_rotated",
            path=str(log_path),
            old_size_mb=result["old_size_mb"],
            archive=str(target_gz),
            removed_count=len(removed),
        )
        return result

    def rotate_all(
        self,
        *,
        max_size_mb: int = 10,
        keep_count: int = 5,
    ) -> dict[str, dict[str, Any]]:
        """Выполнить rotate_if_needed для обоих стандартных audit-логов."""
        bash_path = Path(os.environ.get("KRAB_BASH_AUDIT_PATH", str(_BASH_AUDIT_PATH)))
        agent_path = Path(
            os.environ.get(
                "KRAB_AGENT_AUDIT_PATH",
                str(_AGENT_AUDIT_PATH),
            )
        )
        return {
            "bash": self.rotate_if_needed(
                bash_path, max_size_mb=max_size_mb, keep_count=keep_count
            ),
            "agent": self.rotate_if_needed(
                agent_path, max_size_mb=max_size_mb, keep_count=keep_count
            ),
        }


def read_audit_log_with_archives(
    log_path: Path,
    *,
    keep_count: int = 5,
    since_ts: float | None = None,
    until_ts: float | None = None,
) -> list[str]:
    """Читать JSONL-строки из активного лога и gzip-архивов в хронологическом порядке.

    Wave 56-I: Wave 52-C analyzer должен использовать эту функцию вместо
    прямого open(log_path), чтобы покрывать ротированные архивы.

    Args:
        log_path:   путь к активному файлу лога
        keep_count: максимальный номер архива для сканирования
        since_ts:   unix timestamp начала окна (включительно)
        until_ts:   unix timestamp конца окна (включительно)

    Returns:
        Список JSONL-строк (без trailing newline).
        Порядок: от старых к новым (архивы → активный файл).
    """
    lines: list[str] = []

    # Читаем сначала архивы от старшего к младшему
    for idx in range(keep_count, 0, -1):
        archive = _archive_path(log_path, idx)
        if not archive.exists():
            continue
        try:
            with gzip.open(archive, "rt", encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    raw = raw.rstrip("\n")
                    if not raw:
                        continue
                    if since_ts is not None or until_ts is not None:
                        ts = _extract_ts(raw)
                        if since_ts is not None and ts is not None and ts < since_ts:
                            continue
                        if until_ts is not None and ts is not None and ts > until_ts:
                            continue
                    lines.append(raw)
        except (OSError, gzip.BadGzipFile):
            continue

    # Затем — активный файл
    if log_path.exists():
        try:
            with log_path.open("r", encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    raw = raw.rstrip("\n")
                    if not raw:
                        continue
                    if since_ts is not None or until_ts is not None:
                        ts = _extract_ts(raw)
                        if since_ts is not None and ts is not None and ts < since_ts:
                            continue
                        if until_ts is not None and ts is not None and ts > until_ts:
                            continue
                    lines.append(raw)
        except OSError:
            pass

    return lines


# ---------------------------------------------------------------------------
# Вспомогательные функции (приватные)
# ---------------------------------------------------------------------------


def _archive_path(log_path: Path, idx: int) -> Path:
    """Путь к архивному файлу с номером idx: <log>.<idx>.gz"""
    return log_path.with_name(log_path.name + f".{idx}.gz")


def _compress_to_gz(source: Path, dest: Path) -> None:
    """Сжать source → dest (.gz). Атомарно через tmp-файл."""
    tmp = dest.with_suffix(".tmp")
    try:
        with source.open("rb") as src_fh, gzip.open(tmp, "wb", compresslevel=6) as gz_fh:
            shutil.copyfileobj(src_fh, gz_fh)
        tmp.rename(dest)
    except Exception:
        # Убрать tmp-мусор при ошибке
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _extract_ts(line: str) -> float | None:
    """Быстро извлечь Unix timestamp из JSON-строки без полного parse.

    Ожидает поле "ts" в ISO 8601 или UNIX float/int формате.
    """
    import json  # noqa: PLC0415

    try:
        obj = json.loads(line)
        ts_val = obj.get("ts")
        if ts_val is None:
            return None
        if isinstance(ts_val, (int, float)):
            return float(ts_val)
        # ISO 8601: "2026-05-09T03:00:00Z"
        from datetime import datetime, timezone  # noqa: PLC0415

        dt = datetime.strptime(str(ts_val), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:  # noqa: BLE001
        return None


# Singleton для удобного импорта
audit_log_rotator = AuditLogRotator()
