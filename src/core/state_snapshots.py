# -*- coding: utf-8 -*-
"""
Wave 49-F: периодические backup-снапшоты критичных state-файлов Krab.

Зачем:
1) Файлы в ~/.openclaw/krab_runtime_state/ (inbox_state.json,
   last_seen_messages.json, route_switches.jsonl, codex_quota_state.json,
   swarm_memory.json, runs_history.jsonl) могут быть corrupted при
   incomplete write / disk full / kill -9 mid-write.
2) Wave 33 закрыл session corruption recovery (Pyrogram .session), но
   user-state файлы не имели снапшотов → recovery невозможен.
3) Hourly snapshots + retention позволяют восстановить состояние в случае
   повреждения runtime-данных.

Hard constraints:
- НЕ менять hot-path writes (snapshots — read-only-from-source).
- Atomic copy: tmp + rename.
- Только retention в snapshots/ dir, originals НЕ трогаем.
- Snapshot dir = ~/.openclaw/krab_runtime_state/snapshots/<timestamp_iso>/<file>.bak

ENV:
- KRAB_STATE_SNAPSHOT_INTERVAL_MINUTES (default 60) — cadence планировщика.
- KRAB_RUNTIME_STATE_DIR — override корня (для тестов).
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)

# Список critical state-файлов для снапшотов.
# Read-only-from-source: snapshot копирует, не модифицирует originals.
STATE_FILES_TO_SNAPSHOT: tuple[str, ...] = (
    "inbox_state.json",
    "last_seen_messages.json",
    "route_switches.jsonl",
    "codex_quota_state.json",
    "swarm_memory.json",
    "runs_history.jsonl",
)

# Default retention: 24 hourly snapshots (~ 1 day) + max 7 days age.
DEFAULT_KEEP_COUNT = 24
DEFAULT_MAX_AGE_DAYS = 7

# Default cadence (minutes). Можно переопределить через ENV.
DEFAULT_INTERVAL_MINUTES = 60


def _runtime_state_dir() -> Path:
    """Возвращает корень runtime-state (с поддержкой ENV override)."""
    override = os.environ.get("KRAB_RUNTIME_STATE_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".openclaw" / "krab_runtime_state"


def _snapshot_root() -> Path:
    """Корневая директория со всеми snapshot-папками."""
    return _runtime_state_dir() / "snapshots"


def _now_utc_iso_compact() -> str:
    """ISO-timestamp без двоеточий — безопасен как имя директории."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


class StateSnapshotManager:
    """
    Менеджер периодических снапшотов critical state-файлов.

    Snapshots хранятся в `<runtime_state>/snapshots/<timestamp>/<file>.bak`.
    Все copy-операции атомарны (tmp + rename), originals никогда не
    модифицируются.
    """

    def __init__(
        self,
        *,
        runtime_state_dir: Path | None = None,
        files: tuple[str, ...] = STATE_FILES_TO_SNAPSHOT,
    ) -> None:
        self.runtime_state_dir = runtime_state_dir or _runtime_state_dir()
        self.snapshot_root = self.runtime_state_dir / "snapshots"
        self.files = files

    @property
    def interval_minutes(self) -> int:
        """Возвращает текущий interval (берётся из ENV каждый раз, для тестов)."""
        raw = os.environ.get("KRAB_STATE_SNAPSHOT_INTERVAL_MINUTES")
        if not raw:
            return DEFAULT_INTERVAL_MINUTES
        try:
            value = int(raw)
            return max(1, value)
        except (ValueError, TypeError):
            return DEFAULT_INTERVAL_MINUTES

    def snapshot_now(self, reason: str = "scheduled") -> dict[str, Any]:
        """
        Создаёт snapshot всех state-файлов в новой директории.

        Returns dict с timestamp, путём к директории, списком скопированных
        файлов и размером.
        """
        timestamp = _now_utc_iso_compact()
        target_dir = self.snapshot_root / timestamp
        target_dir.mkdir(parents=True, exist_ok=True)

        copied: list[dict[str, Any]] = []
        skipped: list[str] = []
        total_bytes = 0

        for filename in self.files:
            src = self.runtime_state_dir / filename
            if not src.exists() or not src.is_file():
                skipped.append(filename)
                continue
            try:
                size = self._atomic_copy(src, target_dir / f"{filename}.bak")
                total_bytes += size
                copied.append({"file": filename, "bytes": size})
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "state_snapshot_copy_failed",
                    file=filename,
                    error=str(exc),
                )
                skipped.append(filename)

        result = {
            "timestamp": timestamp,
            "reason": reason,
            "path": str(target_dir),
            "copied": copied,
            "skipped": skipped,
            "total_bytes": total_bytes,
        }
        logger.info(
            "state_snapshot_created",
            timestamp=timestamp,
            reason=reason,
            copied=len(copied),
            skipped=len(skipped),
            total_kb=round(total_bytes / 1024.0, 1),
        )
        return result

    def list_snapshots(self) -> list[dict[str, Any]]:
        """
        Возвращает список snapshots в reverse chronological order (новые первыми).
        """
        if not self.snapshot_root.exists():
            return []
        rows: list[dict[str, Any]] = []
        for entry in self.snapshot_root.iterdir():
            if not entry.is_dir():
                continue
            try:
                files = sorted(p.name for p in entry.iterdir() if p.is_file())
                size = sum(p.stat().st_size for p in entry.iterdir() if p.is_file())
                mtime = entry.stat().st_mtime
            except OSError as exc:
                logger.warning(
                    "state_snapshot_list_entry_failed",
                    entry=entry.name,
                    error=str(exc),
                )
                continue
            rows.append(
                {
                    "timestamp": entry.name,
                    "path": str(entry),
                    "files": files,
                    "file_count": len(files),
                    "total_bytes": size,
                    "mtime": mtime,
                }
            )
        # Reverse chronological: новые первыми.
        rows.sort(key=lambda r: r["timestamp"], reverse=True)
        return rows

    def restore(self, timestamp: str) -> dict[str, Any]:
        """
        Восстанавливает state-файлы из конкретного snapshot.

        Перед перезаписью каждый текущий файл бэкапится в
        `<runtime_state>/snapshots/_pre_restore_<ts>/<file>.bak` —
        чтобы при ошибке решения можно было откатиться.
        """
        ts = str(timestamp or "").strip()
        if not ts:
            raise ValueError("timestamp_empty")
        src_dir = self.snapshot_root / ts
        if not src_dir.exists() or not src_dir.is_dir():
            raise FileNotFoundError(f"snapshot_not_found:{ts}")

        # Pre-restore backup текущего состояния — safety net.
        pre_ts = f"_pre_restore_{_now_utc_iso_compact()}"
        pre_dir = self.snapshot_root / pre_ts
        pre_dir.mkdir(parents=True, exist_ok=True)

        restored: list[str] = []
        skipped: list[str] = []
        pre_backed_up: list[str] = []

        for filename in self.files:
            backup_file = src_dir / f"{filename}.bak"
            if not backup_file.exists():
                skipped.append(filename)
                continue
            target = self.runtime_state_dir / filename

            # Сохраняем текущий файл перед перезаписью (если есть).
            if target.exists():
                try:
                    self._atomic_copy(target, pre_dir / f"{filename}.bak")
                    pre_backed_up.append(filename)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "state_snapshot_pre_restore_backup_failed",
                        file=filename,
                        error=str(exc),
                    )

            try:
                self._atomic_copy(backup_file, target)
                restored.append(filename)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "state_snapshot_restore_failed",
                    file=filename,
                    error=str(exc),
                )
                skipped.append(filename)

        result = {
            "timestamp": ts,
            "restored": restored,
            "skipped": skipped,
            "pre_restore_backup": str(pre_dir),
            "pre_backed_up": pre_backed_up,
        }
        logger.info(
            "state_snapshot_restored",
            timestamp=ts,
            restored=len(restored),
            skipped=len(skipped),
            pre_backup=str(pre_dir),
        )
        return result

    def cleanup_old(
        self,
        *,
        keep_count: int = DEFAULT_KEEP_COUNT,
        max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    ) -> int:
        """
        Применяет retention-policy: оставляем последние `keep_count` снапшотов
        и удаляем всё старше `max_age_days`.

        Returns: количество удалённых snapshot-директорий.
        """
        rows = self.list_snapshots()
        if not rows:
            return 0

        # Не трогаем pre_restore backups — они нужны для отката после restore.
        regular = [r for r in rows if not r["timestamp"].startswith("_pre_restore_")]

        to_delete: set[str] = set()

        # 1) Per-count retention: всё за пределами keep_count.
        if len(regular) > keep_count:
            for r in regular[keep_count:]:
                to_delete.add(r["timestamp"])

        # 2) Per-age retention: всё старше max_age_days.
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).timestamp()
        for r in rows:
            # _pre_restore_ тоже подчищаем по возрасту — после max_age_days.
            if r["mtime"] < cutoff:
                to_delete.add(r["timestamp"])

        deleted = 0
        for ts in to_delete:
            target = self.snapshot_root / ts
            try:
                shutil.rmtree(target)
                deleted += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "state_snapshot_cleanup_failed",
                    timestamp=ts,
                    error=str(exc),
                )

        if deleted:
            logger.info(
                "state_snapshot_cleanup_done",
                deleted=deleted,
                keep_count=keep_count,
                max_age_days=max_age_days,
            )
        return deleted

    @staticmethod
    def _atomic_copy(src: Path, dst: Path) -> int:
        """
        Атомарное копирование: пишем в .tmp, потом rename.

        Сохраняет mtime/perm. Возвращает размер скопированного файла.
        """
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(dst.suffix + ".tmp")
        try:
            shutil.copy2(src, tmp)
            tmp.replace(dst)
        except Exception:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            raise
        return dst.stat().st_size


# Singleton (lazy: тесты могут пересоздавать через явный конструктор).
state_snapshot_manager = StateSnapshotManager()
