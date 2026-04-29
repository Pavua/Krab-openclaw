# -*- coding: utf-8 -*-
"""
In-memory LRU кэш для результатов идемпотентных tool calls.

Зачем существует:

OpenClaw / MCP tool loop часто исполняет один и тот же запрос подряд: пользователь
переспрашивает «погода в Мадриде», agent делает несколько iterations с web_search
по тому же ключу, валютные конвертеры дёргаются для повторяющихся пар. Каждое
такое исполнение — это network round-trip + (для cloud-tools) деньги.

Идея 10: запомнить результат последних tool calls в памяти на короткое окно
и переиспользовать при идентичных аргументах. Чисто in-memory, без persist —
short-lived data, после рестарта Краба кеш пустой и это нормально.

### Что кэшировать
Только tools с воспроизводимым результатом на коротком интервале:

- `web_search` — 5 минут (новости устаревают быстро)
- `weather` — 30 минут (прогноз меняется не чаще)
- `currency` — 1 час (курсы плавают, но не ежеминутно)
- `define` / `urban` — 24 часа (словарь меняется крайне редко)

Дефолтный TTL для остальных tools — 5 минут (можно override через `set(ttl_sec=)`).

### Что НЕ кэшировать
- Tools со state: создание заметок, отправка сообщений, любые write/POST
- Tools с временем в результате (clock, uptime)
- Tools завязанные на чат (recall_memory, history)

Ответственность за «кэшировать или нет» — на caller'е (через `cached_tool_call`).

### Wire-up
Чисто модуль. Подключение к openclaw_client / mcp_client — в backlog: точка
интеграции — место где tool_call формируется и исполняется. Здесь только
data-структура + helpers, чтобы тесты были изолированы и refactoring openclaw
не блокировал landing этой идеи.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)

# Per-tool TTL (секунды). Tools не из этого dict получают _DEFAULT_TTL_SEC.
_TOOL_TTL_OVERRIDES: dict[str, float] = {
    "web_search": 300.0,
    "weather": 1800.0,
    "currency": 3600.0,
    "define": 86400.0,
    "urban": 86400.0,
}

# Дефолтные TTL и емкость. 500 записей × ~2KB payload ≈ 1MB cap — приемлемо
# для долгоживущего процесса.
_DEFAULT_TTL_SEC: float = 300.0
_DEFAULT_MAX_ENTRIES: int = 500


def _stable_args_hash(args: Any) -> str:
    """Детерминированный хэш аргументов tool call.

    JSON-serialize с sort_keys, чтобы порядок ключей не влиял на ключ кэша.
    Несериализуемые объекты конвертируются через repr() — приемлемо для
    несложных tool args (str/int/float/bool/list/dict).
    """
    try:
        payload = json.dumps(args, sort_keys=True, ensure_ascii=False, default=repr)
    except (TypeError, ValueError):
        payload = repr(args)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return digest[:32]


class ToolResultCache:
    """LRU кэш результатов tool calls с per-tool TTL.

    Потокобезопасный (RLock). LRU eviction при превышении max_entries.
    Используется как module-level singleton (`tool_result_cache` ниже),
    но конструктор принимает параметры для тестов.
    """

    def __init__(
        self,
        *,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
        default_ttl_sec: float = _DEFAULT_TTL_SEC,
        now_fn: Callable[[], float] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        # OrderedDict даёт O(1) move_to_end → классический LRU.
        # Значение: (expires_at_monotonic, result_payload).
        self._entries: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._max_entries = int(max_entries)
        self._default_ttl_sec = float(default_ttl_sec)
        self._now_fn: Callable[[], float] = now_fn or time.monotonic
        # Счётчики для observability (доступны через stats()).
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    # ---- Core API -------------------------------------------------------

    def get(self, tool_name: str, args_hash: str) -> Any | None:
        """Возвращает кэшированный результат или None.

        Истёкшие записи удаляются лениво при попытке чтения. Hit двигает
        запись в конец LRU-очереди.
        """
        key = self._make_key(tool_name, args_hash)
        now = self._now_fn()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None
            expires_at, payload = entry
            if now >= expires_at:
                # Лениво удаляем — TTL прошёл.
                del self._entries[key]
                self._misses += 1
                return None
            self._entries.move_to_end(key)
            self._hits += 1
            return payload

    def set(
        self,
        tool_name: str,
        args_hash: str,
        result: Any,
        *,
        ttl_sec: float | None = None,
    ) -> None:
        """Сохраняет результат с TTL.

        Если `ttl_sec` не задан — используется per-tool override
        (`_TOOL_TTL_OVERRIDES`) или дефолт. LRU eviction при переполнении.
        """
        key = self._make_key(tool_name, args_hash)
        effective_ttl = self._resolve_ttl(tool_name, ttl_sec)
        if effective_ttl <= 0:
            # TTL=0 → не кэшируем (caller явно запросил bypass).
            return
        expires_at = self._now_fn() + effective_ttl
        with self._lock:
            if key in self._entries:
                # Обновление — двигаем в конец как свежую запись.
                self._entries.move_to_end(key)
            self._entries[key] = (expires_at, result)
            # LRU eviction. Используем while на случай если max_entries
            # был уменьшен в runtime — выкинем все лишние сразу.
            while len(self._entries) > self._max_entries:
                evicted_key, _ = self._entries.popitem(last=False)
                self._evictions += 1
                logger.debug(
                    "tool_result_cache_evicted",
                    key=evicted_key,
                    reason="lru_capacity",
                )

    def invalidate(self, tool_name: str, args_hash: str) -> bool:
        """Удаляет конкретную запись. True если была."""
        key = self._make_key(tool_name, args_hash)
        with self._lock:
            return self._entries.pop(key, None) is not None

    def clear(self) -> None:
        """Полная очистка кэша (для тестов / owner reset)."""
        with self._lock:
            self._entries.clear()

    def stats(self) -> dict[str, Any]:
        """Снимок счётчиков для observability."""
        with self._lock:
            total = self._hits + self._misses
            hit_rate = (self._hits / total) if total > 0 else 0.0
            return {
                "size": len(self._entries),
                "max_entries": self._max_entries,
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "hit_rate": round(hit_rate, 4),
            }

    # ---- Internal helpers -----------------------------------------------

    @staticmethod
    def _make_key(tool_name: str, args_hash: str) -> str:
        return f"{tool_name}:{args_hash}"

    def _resolve_ttl(self, tool_name: str, override: float | None) -> float:
        if override is not None:
            return float(override)
        return float(_TOOL_TTL_OVERRIDES.get(tool_name, self._default_ttl_sec))


# Module-level singleton — паттерн совпадает с chat_ban_cache, silence_manager,
# inbox_service. Конфигурации с диска не требуется (in-memory only).
tool_result_cache = ToolResultCache()


def cached_tool_call(
    tool_name: str,
    args: Any,
    fn: Callable[[], Any],
    *,
    cache: ToolResultCache | None = None,
    ttl_sec: float | None = None,
) -> Any:
    """Sync-обёртка: проверяет кэш, иначе вызывает fn() и кэширует результат.

    Пример::

        from src.core.tool_result_cache import cached_tool_call

        result = cached_tool_call(
            "web_search",
            {"query": "krab telegram bot"},
            lambda: brave_search("krab telegram bot"),
        )

    Если caller хочет async-fn — использовать `acached_tool_call` ниже.
    Передавать ttl_sec=0 чтобы форсированно обойти кэш для конкретного вызова.
    """
    target_cache = cache or tool_result_cache
    args_hash = _stable_args_hash(args)
    # ttl_sec=0 → caller явно просит обойти кэш и не писать новую запись.
    if ttl_sec == 0:
        return fn()
    cached = target_cache.get(tool_name, args_hash)
    if cached is not None:
        return cached
    result = fn()
    if result is not None:
        target_cache.set(tool_name, args_hash, result, ttl_sec=ttl_sec)
    return result


async def acached_tool_call(
    tool_name: str,
    args: Any,
    fn: Callable[[], Awaitable[Any]],
    *,
    cache: ToolResultCache | None = None,
    ttl_sec: float | None = None,
) -> Any:
    """Async-вариант cached_tool_call.

    Пример::

        result = await acached_tool_call(
            "weather",
            {"city": "Madrid"},
            lambda: weather_provider.fetch("Madrid"),
        )
    """
    target_cache = cache or tool_result_cache
    args_hash = _stable_args_hash(args)
    if ttl_sec == 0:
        return await fn()
    cached = target_cache.get(tool_name, args_hash)
    if cached is not None:
        return cached
    result = await fn()
    if result is not None:
        target_cache.set(tool_name, args_hash, result, ttl_sec=ttl_sec)
    return result
