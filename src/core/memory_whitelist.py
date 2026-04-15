"""
Whitelist чатов для Memory Layer (Track E).

Отвечает за единственный вопрос: индексировать ли сообщения из этого чата?

Архитектура:
  - Конфиг в `~/.openclaw/krab_memory/whitelist.json`
  - Два слоя: allow (id или title regex) + deny (id или title regex)
  - deny всегда перекрывает allow (explicit exclusion > explicit inclusion)
  - Wildcard `*` в allow = разрешить всё, кроме deny
  - Матч по chat_id приоритетнее матча по title

Принципы:
  1. **Privacy-by-default**: если whitelist не задан или конфиг не найден —
     `is_allowed()` возвращает False. Никаких "по умолчанию индексируем всё".
  2. **Audit-trail**: каждый `is_allowed` возвращает reason — почему был принят
     вердикт. Для `!memory stats` и privacy-review.
  3. **Hot-reload**: конфиг перечитывается с диска при изменении mtime файла.
     Важно для owner-scope операций без рестарта.

Интеграция:
  - `scripts/bootstrap_memory.py` фильтрует JSON-экспорт через whitelist.
  - `src/core/memory_indexer_worker.py` (Phase 4) фильтрует incoming messages.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


DEFAULT_CONFIG_PATH = Path("~/.openclaw/krab_memory/whitelist.json").expanduser()


@dataclass(frozen=True)
class WhitelistDecision:
    """Вердикт: индексировать ли чат. Reason нужен для аудита и dashboard."""

    allowed: bool
    reason: str  # "allow:id:<id>", "deny:title_regex:<pattern>", "no_match", ...


@dataclass
class WhitelistConfig:
    """
    In-memory представление конфига.

    allow_ids / deny_ids — точные совпадения по chat_id (строки).
    allow_title_regex / deny_title_regex — regex-паттерны для title.
    allow_all — если True, допускаются ВСЕ чаты не попавшие в deny.
    """

    allow_ids: set[str] = field(default_factory=set)
    deny_ids: set[str] = field(default_factory=set)
    allow_title_regex: list[re.Pattern[str]] = field(default_factory=list)
    deny_title_regex: list[re.Pattern[str]] = field(default_factory=list)
    allow_all: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "WhitelistConfig":
        """Фабрика из dict'а (после json.load). Ничего не валидирует жёстко."""

        def _compile_all(patterns: Iterable[str]) -> list[re.Pattern[str]]:
            return [re.compile(p, re.IGNORECASE) for p in patterns if p]

        allow = data.get("allow", {}) or {}
        deny = data.get("deny", {}) or {}

        # Поддержка старых/коротких форматов — строки или списки.
        allow_ids_raw = allow.get("ids") or []
        deny_ids_raw = deny.get("ids") or []

        return cls(
            allow_ids={str(x) for x in allow_ids_raw},
            deny_ids={str(x) for x in deny_ids_raw},
            allow_title_regex=_compile_all(allow.get("title_regex") or []),
            deny_title_regex=_compile_all(deny.get("title_regex") or []),
            allow_all=bool(data.get("allow_all", False)),
        )

    def to_dict(self) -> dict:
        """Обратная сериализация — для тестов и debug-вывода."""
        return {
            "allow_all": self.allow_all,
            "allow": {
                "ids": sorted(self.allow_ids),
                "title_regex": [p.pattern for p in self.allow_title_regex],
            },
            "deny": {
                "ids": sorted(self.deny_ids),
                "title_regex": [p.pattern for p in self.deny_title_regex],
            },
        }


class MemoryWhitelist:
    """
    Проверяет чаты против whitelist-конфига.

    Args:
        config_path: путь к JSON-конфигу. Если None — DEFAULT_CONFIG_PATH.
        config: явно переданный WhitelistConfig (приоритетнее, чем загрузка
            с диска). Полезно для тестов и runtime-overrides.
    """

    def __init__(
        self,
        config_path: Path | None = None,
        config: WhitelistConfig | None = None,
    ) -> None:
        self._path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self._config: WhitelistConfig = config or WhitelistConfig()
        self._config_mtime: float = 0.0

        if config is None:
            self._reload_from_disk(force=True)

    # ------------------------------------------------------------------
    # Публичный API.
    # ------------------------------------------------------------------

    def is_allowed(
        self, chat_id: str, chat_title: str | None = None
    ) -> WhitelistDecision:
        """
        Основной decision gate. Возвращает и вердикт, и его причину.

        Порядок:
          1. Reload с диска если изменился.
          2. deny_ids — точный match → False.
          3. deny_title_regex — regex match → False.
          4. allow_ids — точный match → True.
          5. allow_title_regex — regex match → True.
          6. allow_all — True.
          7. Иначе — False (privacy-by-default).
        """
        self._reload_from_disk()
        cid = str(chat_id)

        # Deny первым — explicit exclusion всегда выигрывает.
        if cid in self._config.deny_ids:
            return WhitelistDecision(False, f"deny:id:{cid}")

        if chat_title:
            for pattern in self._config.deny_title_regex:
                if pattern.search(chat_title):
                    return WhitelistDecision(
                        False, f"deny:title_regex:{pattern.pattern}"
                    )

        # Allow.
        if cid in self._config.allow_ids:
            return WhitelistDecision(True, f"allow:id:{cid}")

        if chat_title:
            for pattern in self._config.allow_title_regex:
                if pattern.search(chat_title):
                    return WhitelistDecision(
                        True, f"allow:title_regex:{pattern.pattern}"
                    )

        if self._config.allow_all:
            return WhitelistDecision(True, "allow_all")

        return WhitelistDecision(False, "no_match")

    def filter_chats(
        self, chats: Iterable[tuple[str, str | None]]
    ) -> list[tuple[str, str | None, WhitelistDecision]]:
        """
        Пропускает итерируемое (chat_id, chat_title) через `is_allowed`
        и возвращает список (cid, title, decision) — полезно для bootstrap
        JSON-парсера, чтобы в одну итерацию собрать и allow, и deny с reason'ами.
        """
        return [(cid, title, self.is_allowed(cid, title)) for cid, title in chats]

    @property
    def config(self) -> WhitelistConfig:
        self._reload_from_disk()
        return self._config

    # ------------------------------------------------------------------
    # Управление конфигом.
    # ------------------------------------------------------------------

    def _reload_from_disk(self, force: bool = False) -> None:
        """Перечитывает конфиг, если mtime изменилось. Тихо игнорирует ошибки."""
        try:
            stat = self._path.stat()
        except FileNotFoundError:
            # Отсутствие файла валидно — privacy-by-default, all чаты deny.
            if force or self._config_mtime != 0.0:
                self._config = WhitelistConfig()
                self._config_mtime = 0.0
            return

        if not force and stat.st_mtime == self._config_mtime:
            return

        try:
            with self._path.open("r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, json.JSONDecodeError):
            # Битый JSON — оставляем предыдущее состояние.
            # Bootstrap/worker должен поймать и залогировать.
            return

        self._config = WhitelistConfig.from_dict(raw)
        self._config_mtime = stat.st_mtime

    def save(self) -> None:
        """Записывает текущий конфиг на диск. Создаёт родительскую директорию."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("w", encoding="utf-8") as fh:
            json.dump(self.config.to_dict(), fh, indent=2, ensure_ascii=False)
        # Обновим mtime-кэш, чтобы следующий reload не триггерился.
        self._config_mtime = self._path.stat().st_mtime

    # ------------------------------------------------------------------
    # Права доступа к файлу.
    # ------------------------------------------------------------------

    def enforce_permissions(self) -> None:
        """
        Применяет chmod 600 к конфигу и chmod 700 к директории.
        Вызывается из bootstrap_memory.py / indexer_worker после save().
        """
        if self._path.exists():
            os.chmod(self._path, 0o600)
        if self._path.parent.exists():
            os.chmod(self._path.parent, 0o700)
