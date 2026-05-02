# -*- coding: utf-8 -*-
"""
src/core/skill_curator_state.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Atomic JSON store для CuratorState (Wave 15-C → 16-A, Steps 2-3/4).

Хранит per-team метаданные curator runs + global pause flag + active overlays в
``~/.openclaw/krab_runtime_state/curator/state.json``.

Запись через `tempfile + os.replace` — atomic, безопасно при concurrent
writes из cron + ручных команд.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)


CURATOR_STATE_PATH = Path.home() / ".openclaw" / "krab_runtime_state" / "curator" / "state.json"


@dataclass
class CuratorState:
    """Per-team curator run state.

    Поля:
    - ``last_run_at``  — ISO8601 (UTC) per team, момент последнего успешного proposal/dry-run.
    - ``paused``       — глобальный pause flag (отключает auto-runs).
    - ``run_count``    — счётчик запусков per team.
    - ``last_report_paths`` — путь к последнему сохранённому отчёту/proposal per team.
    - ``last_proposal_paths`` — путь к последнему LLM-proposal per team.
    - ``active_overlays`` — активные overlays: {team: {prompt, proposal_id, applied_at, version, archive_path}}.
    - ``last_apply_at`` — ISO8601 (UTC) per team, момент последнего apply (rate-limit).
    """

    last_run_at: dict[str, str] = field(default_factory=dict)
    paused: bool = False
    run_count: dict[str, int] = field(default_factory=dict)
    last_report_paths: dict[str, str] = field(default_factory=dict)
    last_proposal_paths: dict[str, str] = field(default_factory=dict)
    # Step 3: overlays (backward compat — пустой dict если ключа нет)
    active_overlays: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_apply_at: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> "CuratorState":
        """Читает state из JSON. При отсутствии/повреждении возвращает пустой state."""

        target = path or CURATOR_STATE_PATH
        if not target.exists():
            return cls()
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("curator_state_load_failed", path=str(target), error=str(exc))
            return cls()
        if not isinstance(data, dict):
            return cls()
        return cls(
            last_run_at=dict(data.get("last_run_at") or {}),
            paused=bool(data.get("paused", False)),
            run_count={k: int(v) for k, v in (data.get("run_count") or {}).items()},
            last_report_paths=dict(data.get("last_report_paths") or {}),
            last_proposal_paths=dict(data.get("last_proposal_paths") or {}),
            # Backward compat: поле может отсутствовать в старых state.json
            active_overlays=dict(data.get("active_overlays") or {}),
            last_apply_at=dict(data.get("last_apply_at") or {}),
        )

    def save_atomic(self, path: Path | None = None) -> Path:
        """Atomic write через tempfile + os.replace. Создаёт родительские директории."""

        target = path or CURATOR_STATE_PATH
        target.parent.mkdir(parents=True, exist_ok=True)

        payload: dict[str, Any] = asdict(self)
        # Append schema version for future migrations
        payload["schema_version"] = 1

        # NamedTemporaryFile в той же директории, чтобы os.replace был atomic.
        tmp_fd, tmp_name = tempfile.mkstemp(prefix=".state.", suffix=".tmp", dir=str(target.parent))
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
            os.replace(tmp_name, target)
        except Exception:
            # Уборка temp на ошибке
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return target

    # -- helpers -------------------------------------------------------------

    def mark_run(self, team: str, *, report_path: Path | str | None = None) -> None:
        """Обновляет last_run_at + инкрементит run_count для команды."""

        self.last_run_at[team] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.run_count[team] = int(self.run_count.get(team, 0)) + 1
        if report_path is not None:
            self.last_report_paths[team] = str(report_path)

    def mark_proposal(self, team: str, proposal_path: Path | str) -> None:
        """Записывает путь к последнему LLM-proposal."""

        self.last_proposal_paths[team] = str(proposal_path)

    # -- Step 3: overlay API -------------------------------------------------

    def apply_overlay(self, team: str, overlay_data: dict[str, Any]) -> None:
        """Записывает overlay для команды (в памяти, без save)."""

        self.active_overlays[team.lower()] = dict(overlay_data)

    def clear_overlay(self, team: str) -> None:
        """Удаляет overlay для команды (в памяти, без save)."""

        self.active_overlays.pop(team.lower(), None)

    def get_overlay(self, team: str) -> dict[str, Any] | None:
        """Возвращает активный overlay команды или None."""

        return self.active_overlays.get(team.lower())

    def mark_apply(self, team: str) -> None:
        """Обновляет last_apply_at для rate-limit проверок."""

        self.last_apply_at[team.lower()] = datetime.now(timezone.utc).isoformat(timespec="seconds")
