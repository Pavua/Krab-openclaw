# -*- coding: utf-8 -*-
"""
Generic hot-reload config mixin для JSON-based configs.

Pattern: mtime polling (не file watcher daemon) — simple, zero deps.

Пример миграции chat_filter_config.py:

    # До миграции:
    class ChatFilterConfig:
        def __init__(self, state_path):
            self._path = state_path
            self._rules = {}
            self._last_mtime = 0.0
            self._load()

        def _load(self):
            if not self._path.exists():
                return
            self._last_mtime = self._path.stat().st_mtime
            data = json.loads(self._path.read_text())
            for chat_id, cfg in data.items():
                self._rules[str(chat_id)] = ChatFilterRule(...)

        def _maybe_reload(self):
            current = self._path.stat().st_mtime
            if current > self._last_mtime + 0.1:
                self._rules.clear()
                self._load()

    # После миграции:
    class ChatFilterConfig:
        def __init__(self, state_path):
            self._config = HotReloadableConfig(
                path=state_path,
                parser=self._parse,
            )

        def _parse(self, raw: dict) -> dict[str, ChatFilterRule]:
            return {
                str(chat_id): ChatFilterRule(
                    chat_id=str(chat_id),
                    mode=cfg.get("mode", "active"),
                    updated_at=cfg.get("updated_at", time.time()),
                    note=cfg.get("note", ""),
                )
                for chat_id, cfg in raw.items()
            }

        def get_mode(self, chat_id):
            rules = self._config.get()  # авто-reload по mtime
            rule = rules.get(str(chat_id))
            ...

        def set_mode(self, chat_id, mode, note=""):
            rules = self._config.get()
            rules[str(chat_id)] = ChatFilterRule(...)
            self._config.save(rules, serializer=lambda r: {
                k: {"mode": v.mode, "updated_at": v.updated_at, "note": v.note}
                for k, v in r.items()
            })

Применимо для:
    - memory_whitelist.json
    - reminders_queue.json
    - swarm_channels.json
    - любого JSON-файла с горячей перезагрузкой
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from structlog import get_logger

logger = get_logger(__name__)


@dataclass
class HotReloadableConfig:
    """Generic hot-reload wrapper для JSON configs.

    Attributes:
        path: путь к JSON-файлу.
        parser: функция (dict) -> Any; преобразует raw JSON в нужный тип.
                По умолчанию возвращает сырой dict.
    """

    path: Path
    parser: Callable[[dict], Any] = field(default_factory=lambda: (lambda d: d))
    _last_mtime: float = field(default=0.0, init=False, repr=False)
    _state: Any = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        # dataclass не позволяет передавать Lock как default — создаём тут
        object.__setattr__(self, "_lock", threading.Lock())
        self._load()

    # ------------------------------------------------------------------
    # Internal

    def _load(self) -> None:
        """Загрузить из файла — обновляет _state и _last_mtime."""
        with self._lock:
            if not self.path.exists():
                self._state = self.parser({})
                self._last_mtime = 0.0
                return
            try:
                mtime = self.path.stat().st_mtime
                raw: dict = json.loads(self.path.read_text(encoding="utf-8"))
                self._state = self.parser(raw)
                self._last_mtime = mtime
                logger.debug("hot_reload_loaded", path=str(self.path))
            except Exception as e:  # noqa: BLE001
                logger.warning("hot_reload_load_failed", path=str(self.path), error=str(e))

    def _maybe_reload(self) -> bool:
        """Проверить mtime → перезагрузить если изменён.

        Returns:
            True если перезагрузка произошла.
        """
        if not self.path.exists():
            if self._last_mtime != 0.0:
                # Файл удалён — сбросить в пустое состояние
                self._state = self.parser({})
                self._last_mtime = 0.0
                logger.info("hot_reload_file_removed", path=str(self.path))
                return True
            return False
        try:
            current = self.path.stat().st_mtime
            if current > self._last_mtime + 0.1:
                logger.info(
                    "hot_reload_detected",
                    path=str(self.path),
                    old_mtime=self._last_mtime,
                    new_mtime=current,
                )
                self._load()
                return True
        except Exception as e:  # noqa: BLE001
            logger.warning("hot_reload_check_failed", path=str(self.path), error=str(e))
        return False

    # ------------------------------------------------------------------
    # Public API

    def get(self) -> Any:
        """Вернуть текущее состояние (с авто-проверкой mtime).

        Returns:
            Распарсенное состояние (результат parser).
        """
        self._maybe_reload()
        return self._state

    def save(
        self,
        new_state: Any,
        serializer: Callable[[Any], dict] | None = None,
    ) -> None:
        """Сохранить новое состояние на диск и обновить mtime.

        Args:
            new_state: новое состояние (будет сохранено).
            serializer: (state) -> dict; если None — new_state пишется напрямую.
        """
        with self._lock:
            data: dict = serializer(new_state) if serializer else new_state
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(data, indent=2, default=str, ensure_ascii=False),
                encoding="utf-8",
            )
            # Перечитываем mtime после записи — исключаем ложный hot-reload
            self._last_mtime = self.path.stat().st_mtime
            # Пересчитать состояние через parser (canonicalize)
            self._state = self.parser(data)
            logger.debug("hot_reload_saved", path=str(self.path))

    def force_reload(self) -> bool:
        """Принудительная перезагрузка с диска.

        Returns:
            True если состояние изменилось после перезагрузки.
        """
        old_snapshot = _json_snapshot(self._state)
        self._load()
        new_snapshot = _json_snapshot(self._state)
        changed = old_snapshot != new_snapshot
        if changed:
            logger.info("hot_reload_force_changed", path=str(self.path))
        return changed


# ------------------------------------------------------------------
# Helpers


def _json_snapshot(state: Any) -> str:
    """Детерминированный JSON-снимок для сравнения состояний."""
    try:
        return json.dumps(state, default=str, sort_keys=True, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return repr(state)
