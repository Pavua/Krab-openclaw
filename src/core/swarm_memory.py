# -*- coding: utf-8 -*-
"""
src/core/swarm_memory.py
~~~~~~~~~~~~~~~~~~~~~~~~
Персистентная память свёрма — хранит результаты прогонов команд
и предоставляет контекст для будущих запусков.

Зачем:
- без памяти каждый !swarm traders BTC начинается с нуля;
- с памятью каждый следующий прогон знает о предыдущих выводах команды;
- результаты сохраняются в JSON на диск (паттерн inbox_service.py).

Связь с проектом:
- используется из swarm.py/swarm_bus.py для inject контекста в роли;
- команда !swarm memory <team> для просмотра истории;
- хранилище: ~/.openclaw/krab_runtime_state/swarm_memory.json
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)

_STATE_PATH = Path.home() / ".openclaw" / "krab_runtime_state" / "swarm_memory.json"

# Сколько записей хранить на команду (FIFO)
_MAX_ENTRIES_PER_TEAM = 50

# Сколько последних записей инжектировать в system_hint ролей
_INJECT_RECENT_COUNT = 5

# Максимальная длина сжатого результата при сохранении
_RESULT_CLIP = 1500


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class SwarmRunRecord:
    """Запись об одном прогоне свёрма."""

    run_id: str
    team: str
    topic: str
    result_summary: str
    delegations: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now_utc_iso)
    duration_sec: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SwarmRunRecord:
        return cls(
            run_id=d.get("run_id", ""),
            team=d.get("team", ""),
            topic=d.get("topic", ""),
            result_summary=d.get("result_summary", ""),
            delegations=d.get("delegations", []),
            created_at=d.get("created_at", ""),
            duration_sec=d.get("duration_sec", 0.0),
            metadata=d.get("metadata", {}),
        )


class SwarmMemory:
    """
    Персистентное хранилище результатов свёрма.

    Данные: dict[team_name -> list[SwarmRunRecord]] в JSON.
    FIFO: при превышении _MAX_ENTRIES_PER_TEAM старые записи удаляются.
    """

    def __init__(self, state_path: Path | None = None) -> None:
        self._path = state_path or _STATE_PATH
        self._data: dict[str, list[dict[str, Any]]] = {}
        self._load()

    # -- persistence ----------------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            self._data = {}
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
            self._data = json.loads(raw) if raw.strip() else {}
            logger.info(
                "swarm_memory_loaded",
                teams=len(self._data),
                total=sum(len(v) for v in self._data.values()),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("swarm_memory_load_failed", error=str(exc))
            self._data = {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except Exception as exc:  # noqa: BLE001
            logger.error("swarm_memory_save_failed", error=str(exc))

    # -- public API -----------------------------------------------------------

    def save_run(
        self,
        *,
        team: str,
        topic: str,
        result: str,
        delegations: list[str] | None = None,
        duration_sec: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> SwarmRunRecord:
        """Сохраняет результат прогона свёрма."""
        record = SwarmRunRecord(
            run_id=f"{team}_{int(time.time())}",
            team=team,
            topic=topic.strip()[:500],
            result_summary=self._compress_result(result),
            delegations=delegations or [],
            duration_sec=round(duration_sec, 1),
            metadata=metadata or {},
        )

        team_key = team.lower()
        if team_key not in self._data:
            self._data[team_key] = []

        self._data[team_key].append(asdict(record))

        # FIFO trim
        if len(self._data[team_key]) > _MAX_ENTRIES_PER_TEAM:
            self._data[team_key] = self._data[team_key][-_MAX_ENTRIES_PER_TEAM:]

        self._save()
        logger.info(
            "swarm_memory_run_saved",
            team=team,
            run_id=record.run_id,
            topic_len=len(topic),
            result_len=len(record.result_summary),
        )
        return record

    def get_recent(self, team: str, count: int | None = None) -> list[SwarmRunRecord]:
        """Возвращает последние N записей для команды."""
        n = count or _INJECT_RECENT_COUNT
        entries = self._data.get(team.lower(), [])
        return [SwarmRunRecord.from_dict(e) for e in entries[-n:]]

    def get_context_for_injection(self, team: str, count: int | None = None) -> str:
        """
        Формирует текстовый блок для inject в system_hint ролей.

        Возвращает пустую строку если записей нет — в этом случае
        вызывающий код просто не добавляет блок.
        """
        records = self.get_recent(team, count)
        if not records:
            return ""

        lines = [f"--- Память команды {team} (последние {len(records)} прогонов) ---"]
        for rec in records:
            lines.append(f"[{rec.created_at}] Тема: {rec.topic}\nИтог: {rec.result_summary}")
            if rec.delegations:
                lines.append(f"Делегирования: {', '.join(rec.delegations)}")
            lines.append("")

        lines.append("--- Конец памяти ---")
        return "\n".join(lines)

    def get_team_stats(self, team: str) -> dict[str, Any]:
        """Статистика по команде."""
        entries = self._data.get(team.lower(), [])
        if not entries:
            return {"team": team, "total_runs": 0}

        records = [SwarmRunRecord.from_dict(e) for e in entries]
        return {
            "team": team,
            "total_runs": len(records),
            "first_run": records[0].created_at,
            "last_run": records[-1].created_at,
            "avg_duration_sec": round(sum(r.duration_sec for r in records) / len(records), 1),
            "topics_sample": [r.topic[:80] for r in records[-3:]],
        }

    def format_history(self, team: str, count: int = 5) -> str:
        """Форматирует историю команды для Telegram."""
        records = self.get_recent(team, count)
        if not records:
            return f"Команда **{team}** ещё не запускалась."

        stats = self.get_team_stats(team)
        lines = [f"🧠 **Память команды {team}** (всего прогонов: {stats['total_runs']})\n"]
        for rec in reversed(records):
            topic_short = rec.topic[:100] + ("..." if len(rec.topic) > 100 else "")
            result_short = rec.result_summary[:300] + (
                "..." if len(rec.result_summary) > 300 else ""
            )
            deleg = f" | делегирования: {', '.join(rec.delegations)}" if rec.delegations else ""
            lines.append(
                f"**{rec.created_at}** ({rec.duration_sec}с){deleg}\n"
                f"Тема: _{topic_short}_\n"
                f"{result_short}\n"
            )
        return "\n".join(lines)

    def clear_team(self, team: str) -> int:
        """Очищает память команды. Возвращает кол-во удалённых записей."""
        key = team.lower()
        count = len(self._data.get(key, []))
        if count:
            self._data.pop(key, None)
            self._save()
            logger.info("swarm_memory_team_cleared", team=team, cleared=count)
        return count

    def all_teams(self) -> list[str]:
        """Список команд с записями."""
        return list(self._data.keys())

    # -- internal -------------------------------------------------------------

    @staticmethod
    def _compress_result(result: str) -> str:
        """
        Сжимает результат прогона для хранения.

        Убирает emoji-заголовки свёрма и обрезает до _RESULT_CLIP.
        """
        text = result.strip()
        # Убираем заголовок "🐝 **Swarm Room: ..."
        for prefix in ("🐝 **Swarm Room:", "🐝 **Swarm Loop:"):
            if text.startswith(prefix):
                # Пропускаем первую строку
                nl = text.find("\n")
                if nl > 0:
                    text = text[nl + 1 :].strip()
                break

        if len(text) > _RESULT_CLIP:
            text = text[:_RESULT_CLIP] + "\n[...обрезано]"
        return text


# Singleton
swarm_memory = SwarmMemory()
