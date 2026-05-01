"""
Кэш контактов — JSON-хранилище разрезолвенных Telegram peer-ов.

Структура файла:
    {
        "<username>": {
            "peer_id": 123456789,
            "display_name": "Иван Петров",
            "last_resolved_at": "2026-05-01T12:00:00",
            "aliases": ["Ваня", "Алексей из армии"]
        },
        ...
    }

TTL — 7 дней. По истечении запись считается устаревшей и требует пере-резолва.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)

# Путь к файлу кэша
_CACHE_PATH = Path(
    os.environ.get(
        "KRAB_CONTACT_CACHE_PATH",
        os.path.expanduser("~/.openclaw/krab_runtime_state/contact_cache.json"),
    )
)

# TTL для записей кэша (7 дней)
_TTL_DAYS = 7

# Лок для потокобезопасного доступа к файлу
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Внутренние helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Текущее время UTC в ISO-формате."""
    return datetime.now(UTC).isoformat()


def _is_expired(entry: dict[str, Any]) -> bool:
    """Проверяет, устарела ли запись (старше TTL_DAYS)."""
    resolved_at_str = entry.get("last_resolved_at")
    if not resolved_at_str:
        return True
    try:
        resolved_at = datetime.fromisoformat(resolved_at_str)
        # Добавляем tzinfo если отсутствует (обратная совместимость)
        if resolved_at.tzinfo is None:
            resolved_at = resolved_at.replace(tzinfo=UTC)
        return datetime.now(UTC) - resolved_at > timedelta(days=_TTL_DAYS)
    except ValueError:
        return True


def _load() -> dict[str, Any]:
    """Загружает кэш с диска. Возвращает пустой dict при ошибке."""
    try:
        if _CACHE_PATH.exists():
            return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("contact_cache_load_error", path=str(_CACHE_PATH), error=str(exc))
    return {}


def _save(data: dict[str, Any]) -> None:
    """Сохраняет кэш на диск атомарно (через временный файл)."""
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_CACHE_PATH)
    except Exception as exc:  # noqa: BLE001
        logger.warning("contact_cache_save_error", path=str(_CACHE_PATH), error=str(exc))


def _normalize_username(username: str) -> str:
    """Приводит username к нижнему регистру без @."""
    return username.lstrip("@").lower().strip()


# ---------------------------------------------------------------------------
# Публичный API
# ---------------------------------------------------------------------------


def lookup(target: str) -> dict[str, Any] | None:
    """
    Ищет контакт в кэше по username, alias или display_name.

    Args:
        target: @username, plain username, alias или display_name.

    Returns:
        Словарь с полями {peer_id, display_name, last_resolved_at, aliases, username}
        или None если не найдено / запись устарела.
    """
    needle = _normalize_username(target)

    with _lock:
        data = _load()

    # 1. Прямой поиск по username (ключ словаря)
    for username, entry in data.items():
        if _is_expired(entry):
            continue

        if username.lower() == needle:
            return {**entry, "username": username}

        # 2. Поиск по aliases
        for alias in entry.get("aliases", []):
            if alias.lower() == needle or alias.lower() == target.lower():
                return {**entry, "username": username}

        # 3. Поиск по display_name (exact lower)
        dn = (entry.get("display_name") or "").lower()
        if dn and dn == target.lower():
            return {**entry, "username": username}

    return None


def store(username: str, peer_id: int, display_name: str) -> None:
    """
    Сохраняет разрезолвенный peer в кэш.

    Args:
        username: Telegram username (с @ или без — нормализуется).
        peer_id: Числовой Telegram ID.
        display_name: Отображаемое имя пользователя/чата.
    """
    key = _normalize_username(username)
    if not key:
        return

    with _lock:
        data = _load()
        existing = data.get(key, {})
        data[key] = {
            "peer_id": peer_id,
            "display_name": display_name or key,
            "last_resolved_at": _now_iso(),
            # Сохраняем существующие aliases при обновлении
            "aliases": existing.get("aliases", []),
        }
        _save(data)

    logger.debug("contact_cache_stored", username=key, peer_id=peer_id, display_name=display_name)


def add_alias(peer_id: int, alias: str) -> bool:
    """
    Добавляет человеческий alias к контакту по peer_id.

    Пример: add_alias(123456, "Алексей из армии")

    Args:
        peer_id: Числовой Telegram ID для поиска записи.
        alias: Произвольная строка-псевдоним.

    Returns:
        True если alias добавлен, False если контакт не найден.
    """
    alias = alias.strip()
    if not alias:
        return False

    with _lock:
        data = _load()
        for username, entry in data.items():
            if entry.get("peer_id") == peer_id:
                aliases: list[str] = entry.get("aliases", [])
                # Не дублируем
                if alias not in aliases:
                    aliases.append(alias)
                    entry["aliases"] = aliases
                    data[username] = entry
                    _save(data)
                    logger.debug(
                        "contact_cache_alias_added", peer_id=peer_id, alias=alias, username=username
                    )
                return True

    logger.debug("contact_cache_alias_not_found", peer_id=peer_id, alias=alias)
    return False


def search(query: str) -> list[dict[str, Any]]:
    """
    Нечёткий поиск по display_name и aliases (substring, case-insensitive).

    Args:
        query: Строка поиска.

    Returns:
        Список совпадений [{username, peer_id, display_name, aliases, last_resolved_at}].
        Не включает устаревшие записи.
    """
    q = query.lower().strip()
    if not q:
        return []

    results: list[dict[str, Any]] = []

    with _lock:
        data = _load()

    for username, entry in data.items():
        if _is_expired(entry):
            continue

        # Поиск по display_name
        dn = (entry.get("display_name") or "").lower()
        if q in dn or q in username.lower():
            results.append({**entry, "username": username})
            continue

        # Поиск по aliases
        for alias in entry.get("aliases", []):
            if q in alias.lower():
                results.append({**entry, "username": username})
                break

    return results


def list_all() -> list[dict[str, Any]]:
    """
    Возвращает все актуальные (не устаревшие) контакты из кэша.

    Returns:
        Список [{username, peer_id, display_name, aliases, last_resolved_at}].
    """
    with _lock:
        data = _load()

    return [
        {**entry, "username": username}
        for username, entry in data.items()
        if not _is_expired(entry)
    ]


def evict_expired() -> int:
    """
    Удаляет устаревшие записи из кэша. Возвращает число удалённых.
    """
    with _lock:
        data = _load()
        expired_keys = [k for k, v in data.items() if _is_expired(v)]
        for k in expired_keys:
            del data[k]
        if expired_keys:
            _save(data)

    if expired_keys:
        logger.info("contact_cache_evicted", count=len(expired_keys))

    return len(expired_keys)
