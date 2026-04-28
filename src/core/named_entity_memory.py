# -*- coding: utf-8 -*-
"""Named Entity Memory — структурированный graph поверх unstructured archive.

Идея 13: Krab аккумулирует именованные сущности (person/project/place/thing) из
разговоров. Когда сущность снова упоминается — caller (LLM agent или regex
extractor) может попросить summary «что мы знаем» и впрыснуть его в context.

Этот модуль — pure store + lookup API. Никакой авто-экстракции из сообщений
здесь нет (это backlog: regex/NER/LLM extractor отдельным слоем).

### Инварианты

- **Идемпотентно.** `record_mention(name)` несколько раз: первая создаёт
  Entity, последующие инкрементят `mentions_count` и обновляют `last_seen_at`.
  Если приходят `attributes={...}` — они **сливаются** поверх (не затирают),
  чтобы накопленные знания не терялись.
- **Persist per write.** Каждый `record_mention` / `forget` пишет JSON-файл.
  Writes ожидаются редкими (десятки в день), reads — частые, но идут из
  in-memory dict.
- **Fuzzy lookup.** `lookup("Anna")` находит запись для "anna" / "Anya" если
  совпало алиасом или substring (case-insensitive). rapidfuzz используется
  если установлен; иначе fallback на простой substring + lowercase.
- **Stable canonical key.** Внутренний ключ — lowercase canonical name.
  Aliases хранятся как набор; каждый alias тоже индексируется в `_alias_index`
  для быстрого lookup без полного скана.

### Не решает

- Не извлекает entities из текста — это caller-side.
- Не делает entity disambiguation («Anna художница» vs «Anna соседка»).
  Backlog: либо разные `type` уровни, либо явное `entity_id` от caller.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)


# Поддерживаемые типы сущностей. Не enum, чтобы caller мог расширить (custom).
KNOWN_ENTITY_TYPES: frozenset[str] = frozenset(
    {"person", "project", "place", "thing", "organization", "event", "other"}
)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Entity:
    """Одна именованная сущность.

    `name` — canonical (как впервые записали), но lookup нечувствителен к
    регистру. `aliases` — нормализованный set lowercase-вариантов имени.
    `attributes` — свободная dict-структура (профессия, владелец, адрес и т.п.).
    """

    name: str
    type: str = "other"
    aliases: set[str] = field(default_factory=set)
    attributes: dict[str, Any] = field(default_factory=dict)
    mentions_count: int = 0
    first_seen_at: str = field(default_factory=_utcnow_iso)
    last_seen_at: str = field(default_factory=_utcnow_iso)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["aliases"] = sorted(self.aliases)
        return data

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Entity:
        aliases_raw = raw.get("aliases") or []
        if isinstance(aliases_raw, list):
            aliases = {str(a).strip().lower() for a in aliases_raw if str(a).strip()}
        else:
            aliases = set()
        attrs = raw.get("attributes") or {}
        return cls(
            name=str(raw.get("name") or "").strip(),
            type=str(raw.get("type") or "other").strip() or "other",
            aliases=aliases,
            attributes=dict(attrs) if isinstance(attrs, dict) else {},
            mentions_count=int(raw.get("mentions_count") or 0),
            first_seen_at=str(raw.get("first_seen_at") or _utcnow_iso()),
            last_seen_at=str(raw.get("last_seen_at") or _utcnow_iso()),
        )


def _try_rapidfuzz_score(query: str, candidate: str) -> float | None:
    """Возвращает 0..100 score через rapidfuzz, или None если библиотеки нет."""
    try:
        from rapidfuzz import fuzz  # type: ignore
    except ImportError:
        return None
    try:
        return float(fuzz.WRatio(query, candidate))
    except Exception:  # pragma: no cover — defensive
        return None


class EntityStore:
    """Потокобезопасное хранилище named entities с persist на диск.

    Используется как module-level singleton (`named_entity_memory` ниже).
    `storage_path` в конструкторе — для тестов; в рантайме инициализируется
    через `configure_default_path()` из bootstrap.
    """

    def __init__(
        self,
        *,
        storage_path: Path | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._storage_path: Path | None = storage_path
        self._entities: dict[str, Entity] = {}
        # alias_lower → canonical_key. Включает и сам canonical (как alias),
        # и все добавленные aliases. Перестраивается при load / record / forget.
        self._alias_index: dict[str, str] = {}
        self._now_fn: Callable[[], datetime] = now_fn or (lambda: datetime.now(timezone.utc))
        if storage_path is not None:
            self._load_from_disk()

    # ---- Configuration --------------------------------------------------

    def configure_default_path(self, storage_path: Path) -> None:
        """Устанавливает путь и подгружает то что есть на диске. Вызывается из bootstrap."""
        with self._lock:
            self._storage_path = storage_path
            self._entities = {}
            self._alias_index = {}
            self._load_from_disk()

    # ---- Core API -------------------------------------------------------

    def record_mention(
        self,
        name: str,
        type: str = "other",
        *,
        attributes: dict[str, Any] | None = None,
        aliases: list[str] | tuple[str, ...] | None = None,
    ) -> Entity:
        """Записывает упоминание сущности. Идемпотентна, мерджит attributes.

        Возвращает копию Entity (не внутреннюю ссылку), чтобы caller не мог
        случайно мутировать стор.
        """
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("entity name must be non-empty")
        canonical_key = clean_name.lower()
        clean_type = (type or "other").strip().lower() or "other"
        now_iso = self._now_fn().isoformat()

        with self._lock:
            existing_key = self._alias_index.get(canonical_key)
            if existing_key is not None:
                entity = self._entities[existing_key]
                entity.mentions_count += 1
                entity.last_seen_at = now_iso
                if attributes:
                    # merge — не overwrite (накопленные знания не теряем)
                    entity.attributes.update(attributes)
                if aliases:
                    for alias in aliases:
                        norm = (alias or "").strip().lower()
                        if norm and norm not in entity.aliases:
                            entity.aliases.add(norm)
                            self._alias_index[norm] = existing_key
                # Отдельный case: caller дал более конкретный type чем "other"
                if clean_type != "other" and entity.type == "other":
                    entity.type = clean_type
            else:
                entity = Entity(
                    name=clean_name,
                    type=clean_type,
                    aliases={canonical_key},
                    attributes=dict(attributes or {}),
                    mentions_count=1,
                    first_seen_at=now_iso,
                    last_seen_at=now_iso,
                )
                if aliases:
                    for alias in aliases:
                        norm = (alias or "").strip().lower()
                        if norm:
                            entity.aliases.add(norm)
                self._entities[canonical_key] = entity
                for alias in entity.aliases:
                    self._alias_index[alias] = canonical_key

            self._persist_to_disk()
            logger.info(
                "named_entity_recorded",
                name=clean_name,
                type=entity.type,
                mentions=entity.mentions_count,
            )
            return self._copy(entity)

    def lookup(self, name: str) -> Entity | None:
        """Находит entity по имени или алиасу. Fuzzy match если нет точного.

        1. Точный case-insensitive match по alias index.
        2. Substring match (lowercase) — kraab in 'kraab voice gateway'.
        3. rapidfuzz WRatio >=85 (если доступна).
        """
        query = (name or "").strip().lower()
        if not query:
            return None
        with self._lock:
            # Точное совпадение
            key = self._alias_index.get(query)
            if key is not None:
                return self._copy(self._entities[key])

            # Substring fallback — query в alias или alias в query
            for alias, canonical_key in self._alias_index.items():
                if query in alias or alias in query:
                    return self._copy(self._entities[canonical_key])

            # rapidfuzz fallback (если установлена)
            best_score = 0.0
            best_key: str | None = None
            for alias, canonical_key in self._alias_index.items():
                score = _try_rapidfuzz_score(query, alias)
                if score is not None and score >= 85.0 and score > best_score:
                    best_score = score
                    best_key = canonical_key
            if best_key is not None:
                return self._copy(self._entities[best_key])

        return None

    def aliases_for(self, name: str) -> list[str]:
        """Возвращает отсортированный список алиасов для сущности (или [])."""
        entity = self.lookup(name)
        if entity is None:
            return []
        return sorted(entity.aliases)

    def top_entities(self, limit: int = 10) -> list[Entity]:
        """Top-N сущностей по mentions_count, ties сорт по last_seen_at desc."""
        if limit <= 0:
            return []
        with self._lock:
            # mentions desc, при равенстве — по last_seen_at desc (свежие первыми).
            sorted_list = sorted(
                self._entities.values(),
                key=lambda e: e.last_seen_at,
                reverse=True,
            )
            sorted_list.sort(key=lambda e: e.mentions_count, reverse=True)
            return [self._copy(e) for e in sorted_list[:limit]]

    def all_entities(self) -> list[Entity]:
        """Снимок всех сущностей. Возвращает копии."""
        with self._lock:
            return [self._copy(e) for e in self._entities.values()]

    def forget(self, name: str) -> bool:
        """Удаляет сущность. True если была найдена и удалена."""
        query = (name or "").strip().lower()
        if not query:
            return False
        with self._lock:
            key = self._alias_index.get(query)
            if key is None:
                return False
            entity = self._entities.pop(key, None)
            if entity is not None:
                for alias in list(entity.aliases):
                    if self._alias_index.get(alias) == key:
                        del self._alias_index[alias]
            self._persist_to_disk()
        logger.info("named_entity_forgotten", name=name)
        return True

    # ---- Internal helpers -----------------------------------------------

    @staticmethod
    def _copy(entity: Entity) -> Entity:
        return Entity(
            name=entity.name,
            type=entity.type,
            aliases=set(entity.aliases),
            attributes=dict(entity.attributes),
            mentions_count=entity.mentions_count,
            first_seen_at=entity.first_seen_at,
            last_seen_at=entity.last_seen_at,
        )

    def _load_from_disk(self) -> None:
        path = self._storage_path
        if path is None or not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "named_entity_memory_load_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return
        if not isinstance(raw, dict):
            logger.warning("named_entity_memory_load_malformed", path=str(path))
            return
        loaded = 0
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            try:
                entity = Entity.from_dict(value)
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "named_entity_memory_entry_corrupt",
                    key=str(key),
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                continue
            if not entity.name:
                continue
            canonical_key = entity.name.lower()
            # canonical всегда в aliases — гарантируем
            entity.aliases.add(canonical_key)
            self._entities[canonical_key] = entity
            for alias in entity.aliases:
                self._alias_index[alias] = canonical_key
            loaded += 1
        if loaded:
            logger.info("named_entity_memory_loaded", loaded=loaded)

    def _persist_to_disk(self) -> None:
        path = self._storage_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {key: entity.to_dict() for key, entity in self._entities.items()}
            path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except (OSError, TypeError) as exc:
            logger.warning(
                "named_entity_memory_persist_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )


# Module-level singleton — паттерн совпадает с chat_ban_cache, silence_manager.
named_entity_memory = EntityStore()
