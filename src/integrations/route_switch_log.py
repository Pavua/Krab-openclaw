"""Wave 48-B: ring-buffer лог переключений модели routing.

Хранит последние ~50 событий ``model_fallback_engaged`` в JSONL — для
on-demand display в `!routes` Telegram command. Append-only, idempotent,
graceful degradation если файл повреждён.

Hook: вызывается из `openclaw_client.py` рядом с
``logger.warning("model_fallback_engaged", ...)``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.logger import get_logger

logger = get_logger(__name__)

# Файл-лог хранится в общей директории runtime state
LOG_FILE = Path.home() / ".openclaw/krab_runtime_state/route_switches.jsonl"

# Максимум хранимых записей (FIFO)
MAX_ENTRIES = 50


def append_switch(
    *,
    from_model: str,
    to_model: str,
    reason: str,
    kind: str | None = None,
) -> None:
    """Добавляет одну запись о switch в jsonl ring-buffer.

    Никогда не raise: ошибки IO просто пропускаются (telemetry only).
    """
    entry: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "from": from_model,
        "to": to_model,
        "reason": reason,
    }
    if kind:
        entry["kind"] = kind

    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Прочитаем текущий хвост, отрежем по MAX_ENTRIES-1, перепишем atomically
        existing: list[str] = []
        if LOG_FILE.exists():
            try:
                existing = LOG_FILE.read_text(encoding="utf-8").splitlines()
            except OSError:
                existing = []
        # Сохраняем последние MAX_ENTRIES-1 + добавляем новую
        keep = existing[-(MAX_ENTRIES - 1) :] if len(existing) >= MAX_ENTRIES else existing
        keep.append(json.dumps(entry, ensure_ascii=False))
        tmp = LOG_FILE.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
        tmp.replace(LOG_FILE)
    except Exception as exc:  # noqa: BLE001
        logger.debug("route_switch_log_append_failed", error=str(exc))


def read_recent(limit: int = 5) -> list[dict[str, Any]]:
    """Читает последние ``limit`` записей из лога.

    Невалидные строки тихо пропускаются. Возвращает пустой список если
    файла нет или он пуст.
    """
    if not LOG_FILE.exists():
        return []
    try:
        lines = LOG_FILE.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        logger.debug("route_switch_log_read_failed", error=str(exc))
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                out.append(parsed)
        except (ValueError, TypeError):
            continue
    return out


__all__ = ["LOG_FILE", "MAX_ENTRIES", "append_switch", "read_recent"]
