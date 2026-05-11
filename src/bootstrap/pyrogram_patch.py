# -*- coding: utf-8 -*-
"""
Pyrofork SQLite session hardening — WAL + busy_timeout + VACUUM suppression.

Фикс Sentry PYTHON-FASTAPI-5A/5B/5C: "OperationalError: database is locked" в
``pyrogram.storage.sqlite_storage.update_usernames`` и ``update_peers``. Также
оборачиваем ``update_peers`` в retry-layer, который глотает "database is
locked" / "database table is locked" / "database disk image is malformed" —
последняя наблюдалась 25 раз в логах перед Session 32 restart. При 4+ параллельных
pyrofork-клиентах (main yung_nagato + traders/coders/analysts/creative) SQLite
roll-back journal блокирует пишущих соседей, даже если файлы .session разные —
пишущие соседи делят одинаковую схему конкурентных транзакций.

Root causes (множественные):
1. ``FileStorage.open()`` в pyrofork открывает sqlite3.connect с ``timeout=1``
   и без WAL. Под нагрузкой OperationalError прилетает в фоновые таски.
2. ``FileStorage.open()`` вызывает ``VACUUM`` unconditionally — VACUUM
   переписывает весь файл и требует exclusive lock. Если предыдущий процесс был
   SIGKILL'нут (launchctl kickstart -k), WAL-файлы (.wal / .shm) могут
   присутствовать. VACUUM в такой ситуации конфликтует с ними и вызывает
   corruption ("database disk image is malformed").
3. WAL pragmas применялись в предыдущей версии patch ПОСЛЕ вызова
   _orig_open — то есть ПОСЛЕ того, как VACUUM уже отработал. Порядок был
   неверным: VACUUM должен быть подавлен ДО того, как откроется соединение.

Решение:
- Полностью заменяем ``FileStorage.open()`` собственной реализацией вместо
  оборачивания оригинала.  Эта реализация:
  a) Открывает соединение с timeout=10 (вместо timeout=1 в pyrofork).
  b) Сразу применяет WAL + busy_timeout + synchronous=FULL + wal_autocheckpoint PRAGMA.
  c) Вызывает create() или update() — без завершающего VACUUM.
  d) Пропускает опасный ``VACUUM`` целиком: он требует exclusive lock и
     несовместим с WAL sidecar-файлами от предыдущего SIGKILL'нутого процесса.

Почему monkey-patch, а не форк: pyrofork не даёт публичного API задать PRAGMA
при открытии storage, а держать форк ради нескольких строк слишком дорого.
Патч идемпотентен (двойной импорт не повредит) и применяется один раз при
bootstrap — до создания первого Client().
"""

from __future__ import annotations

import logging
import os
import sqlite3

log = logging.getLogger(__name__)

_PATCH_APPLIED = False

# Таймаут подключения к SQLite (сек). Pyrofork использует 1s — слишком мало
# при конкурентном доступе swarm-клиентов.
_SQLITE_CONNECT_TIMEOUT = 10

# MEDIUM-3: Module-level fallback для _corrupt_flag если storage использует __slots__.
# При setattr → AttributeError на __slots__-классе флаг пишется сюда.
# is_storage_corrupt() проверяет оба пути — attr и этот dict.
_STORAGE_CORRUPT_FLAGS: dict[int, bool] = {}

# Wave 21-A: счётчик последовательных успешных read/write операций per-storage.
# При достижении порога (KRAB_STORAGE_CORRUPT_AUTO_CLEAR_THRESHOLD, default 100)
# автоматически сбрасываем _corrupt_flag — stale flag больше не блокирует
# обработку Telegram-сообщений бесконечно. Lazy init из Config при первом вызове.
_STORAGE_SUCCESS_COUNTS: dict[int, int] = {}  # id(storage) → consecutive successes
_STORAGE_AUTO_CLEAR_THRESHOLD: int | None = None  # lazy из Config

# Wave 24-D: auto-clear отключён по умолчанию.
# До Wave 24-D auto-clear скрывал симптомы: N успешных reads после malformed write
# снимали _corrupt_flag, но DB оставалась физически повреждённой (torn pages).
# Теперь recovery идёт через _main_session_integrity_preflight + sqlite .recover,
# а не через счётчик. Pre-Wave-24-D поведение: KRAB_STORAGE_CORRUPT_AUTO_CLEAR_ENABLED=1.
KRAB_STORAGE_CORRUPT_AUTO_CLEAR_ENABLED: bool = os.environ.get(
    "KRAB_STORAGE_CORRUPT_AUTO_CLEAR_ENABLED", "0"
).strip() in {"1", "true", "yes"}


def _get_auto_clear_threshold() -> int:
    """Возвращает порог авто-очистки, lazy-loaded из Config."""
    global _STORAGE_AUTO_CLEAR_THRESHOLD
    if _STORAGE_AUTO_CLEAR_THRESHOLD is None:
        try:
            from src.config import Config as _Cfg

            _STORAGE_AUTO_CLEAR_THRESHOLD = _Cfg.KRAB_STORAGE_CORRUPT_AUTO_CLEAR_THRESHOLD
        except Exception:  # noqa: BLE001 — до init config
            _STORAGE_AUTO_CLEAR_THRESHOLD = 100
    return _STORAGE_AUTO_CLEAR_THRESHOLD


def _record_storage_success(storage) -> None:
    """Увеличивает счётчик успешных операций; при достижении порога — сбрасывает corrupt flag.

    Вызывается из success-path read/write wrapper'ов. Idempotent: повторные
    вызовы после сброса флага безопасны (счётчик тоже обнуляется при сбросе).

    Wave 24-D: если KRAB_STORAGE_CORRUPT_AUTO_CLEAR_ENABLED=0 (default) — no-op.
    Пока storage помечен corrupt все writes заблокированы; recovery идёт через
    _main_session_integrity_preflight + sqlite .recover, не через счётчик.
    """
    if not KRAB_STORAGE_CORRUPT_AUTO_CLEAR_ENABLED:
        return  # Wave 24-D: auto-clear disabled by default — safer after Wave 24-D

    sid = id(storage)
    cnt = _STORAGE_SUCCESS_COUNTS.get(sid, 0) + 1
    _STORAGE_SUCCESS_COUNTS[sid] = cnt
    if cnt >= _get_auto_clear_threshold():
        was_corrupt = is_storage_corrupt(storage)
        if was_corrupt:
            # Storage реально работоспособен: N успешных операций подряд.
            # Сбрасываем stale flag — Krab перестанет рейзить DatabaseError
            # на каждом входящем Telegram-сообщении.
            try:
                if hasattr(storage, "_corrupt_flag"):
                    storage._corrupt_flag = False
                _STORAGE_CORRUPT_FLAGS.pop(sid, None)
                _STORAGE_SUCCESS_COUNTS.pop(sid, None)
                log.info(
                    "storage_corrupt_flag_auto_cleared",
                    extra={
                        "storage_class": type(storage).__name__,
                        "success_count": cnt,
                        "reason": "threshold_reached",
                    },
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "storage_corrupt_flag_auto_clear_failed",
                    extra={"error": str(exc)},
                )
        else:
            # Порог достигнут, но флаг уже не был set — просто сбрасываем счётчик.
            _STORAGE_SUCCESS_COUNTS.pop(sid, None)


def _execute_pragmas(conn) -> None:
    """Применяет hardening-PRAGMA к уже открытому sqlite3-соединению.

    Wave 64 (May 2026): journal_mode=DELETE + fullfsync=1 после повторяющегося
    cluster Sentry corruption events (AGE-15/AGE-12/AGE-9 за 2 недели).
    Root cause: WAL mode + concurrent writers + macOS sleep/wake → torn pages
    в WAL → next boot corruption ("database disk image is malformed").

    Изменения по сравнению с Wave 14-J:
    1. journal_mode=DELETE (а не WAL).
       Pyrogram session.db — single-writer (один Client per session). WAL даёт
       выгоду только при concurrent readers; здесь все 5 sessions
       (kraab + 4 swarm teams) изолированы, WAL не нужен. DELETE rollback
       journal удаляется при commit, нет SHM mmap, нет torn-page риска при
       macOS sleep/wake. wal_autocheckpoint удалён за ненадобностью.
    2. fullfsync=1 — обязательно на macOS.
       Текущий synchronous=FULL делает fsync(), но НЕ F_FULLFSYNC на macOS —
       APFS драйвер может задержать запись в block cache до 30+ секунд.
       fullfsync=1 форсирует physical disk write через F_FULLFSYNC fcntl
       (~5ms extra per write на SSD M4 — negligible при 10 writes/min).
       Apple SQLite docs прямо рекомендуют для macOS.

    Порядок важен:
    1. busy_timeout ПЕРВЫМ — чтобы сами последующие PRAGMA не падали на lock.
    2. journal_mode=DELETE — persists в заголовке файла; при существующей WAL
       база автоматически конвертируется (sqlite сам checkpoint'нёт перед
       переключением).
    3. synchronous=FULL — atomic write guarantee, защищает от torn pages при
       macOS sleep / OS-level write reorder. Session 33 P1 retained: 12+ часов
       uptime при synchronous=NORMAL + sleep cycle = textbook risk window.
    4. fullfsync=1 — macOS-specific F_FULLFSYNC enforcement (Wave 64).
    5. temp_store=MEMORY — temp tables/indexes в RAM (Wave 14-J).
    6. cache_size=-65536 — 64MB page cache (Wave 14-J).

    Примечание: ``auto_vacuum`` намеренно НЕ применяется здесь — изменение
    требует полного VACUUM на существующей базе (опасная операция).
    Пространство освобождается органически через free-page list.
    """
    # Wave 64: journal_mode=DELETE для session.db — устраняет торн-pages в WAL
    # под macOS sleep/wake cycle. F_FULLFSYNC форсирует disk write вместо
    # OS-cached fsync().
    for pragma in (
        "PRAGMA busy_timeout=5000",
        "PRAGMA journal_mode=DELETE",  # Wave 64: было WAL
        "PRAGMA synchronous=FULL",
        "PRAGMA fullfsync=1",  # Wave 64: macOS F_FULLFSYNC enforcement
        # Wave 14-J additions (preserved):
        "PRAGMA temp_store=MEMORY",
        "PRAGMA cache_size=-65536",
    ):
        try:
            conn.execute(pragma)
        except Exception as exc:  # noqa: BLE001
            # Фейл одной PRAGMA не должен ломать старт: логируем и едем дальше.
            log.warning("pyrogram_pragma_failed", extra={"pragma": pragma, "error": str(exc)})


def apply_pyrogram_sqlite_hardening() -> bool:
    """
    Monkey-patch ``FileStorage.open`` и ``SQLiteStorage.update_usernames`` /
    ``update_peers`` (оба обёрнуты идентичным retry-layer'ом).

    Ключевое отличие от предыдущей версии: мы полностью заменяем open() вместо
    оборачивания оригинала, чтобы гарантировать порядок операций:
      1. connect() с длинным timeout
      2. WAL + busy_timeout pragmas ПЕРЕД любыми DDL-операциями
      3. create() или update()
      4. VACUUM — намеренно пропускаем (требует exclusive lock, несовместим с WAL)

    Возвращает True если патч применён (или уже был применён ранее).
    Идемпотентно: повторный вызов no-op.
    """
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return True

    try:
        from pyrogram.storage import file_storage as _fs
        from pyrogram.storage import sqlite_storage as _ss
    except Exception as exc:  # noqa: BLE001
        log.warning("pyrogram_patch_import_failed", extra={"error": str(exc)})
        return False

    # Полная замена FileStorage.open():
    # - применяем PRAGMA ДО create()/update() и без VACUUM на конце
    # - используем timeout=10 вместо timeout=1
    async def _patched_open(self) -> None:
        path = self.database
        file_exists = path.is_file()
        self.conn = sqlite3.connect(
            str(path),
            timeout=_SQLITE_CONNECT_TIMEOUT,
            check_same_thread=False,
        )
        # WAL + busy_timeout ПЕРЕД любыми write-операциями — чтобы create/update
        # уже работали в WAL-режиме и не конфликтовали с WAL sidecar-файлами.
        _execute_pragmas(self.conn)
        if not file_exists:
            self.create()
        else:
            self.update()
        # VACUUM намеренно пропущен: он требует exclusive lock и несовместим с
        # WAL-режимом при наличии сторонних WAL/SHM sidecar-файлов от предыдущего
        # процесса. Без VACUUM сессия продолжает работать нормально: SQLite сам
        # переиспользует страницы из free-page list.

    _fs.FileStorage.open = _patched_open

    # ------------------------------------------------------------------
    # Wave 14-J: generic safe-wrapper factory для write-методов SQLiteStorage.
    # Wave 16-F: после swallow malformed устанавливаем _corrupt_flag на storage.
    # ------------------------------------------------------------------
    # Все wrapped-методы — write-only path (нет полезного return value).
    # Глотаем три класса recoverable-ошибок:
    #   - "database is locked" / "database table is locked" — concurrency lock
    #   - "database disk image is malformed" — page checksum / WAL replay race;
    #     Telegram пересинхронизируется на следующем event'е.
    # Прочие исключения (no such table, syntax errors etc.) — пробрасываем.
    #
    # Wrapped methods:
    #   - update_usernames (Wave 5-B Session 33)
    #   - update_peers     (Session 32 — был отдельный wrap)
    #   - update_state     (Wave 14-J — fires on EVERY Telegram event)
    #   - remove_state     (Wave 14-J — write path, та же риск-поверхность)
    #
    # Не оборачиваем:
    #   - get_peer_by_* — read-path, raise KeyError для not-found
    #     (silent swallow здесь сломал бы peer resolution);
    #     вместо этого Wave 16-F wrap'ит их через _make_safe_read_method ниже:
    #     при _corrupt_flag=True сразу raise DatabaseError ("storage marked corrupt")
    #   - accessors (api_id/dc_id/...) — отдельный layer ниже с last-good cache
    def _make_safe_method(orig, op_name, *, fallback=None):
        """
        Factory: оборачивает async-метод SQLiteStorage в graceful-retry.
        Threadsafe, idempotent, single source of truth.

        ``fallback`` — значение, возвращаемое при swallow. Для большинства
        write-методов None достаточно; для ``update_state`` в read-mode
        нужен пустой list (Telegram resync на следующем event).
        """

        async def _safe(self, *args, **kwargs):
            try:
                result = await orig(self, *args, **kwargs)
                # Wave 21-A: write succeeded → фиксируем успех для авто-очистки.
                _record_storage_success(self)
                return result
            except Exception as exc:  # noqa: BLE001 — sqlite3.OperationalError/DatabaseError
                msg = str(exc).lower()
                # Best-effort размер payload для логирования (пропускаем
                # sentinel ``object`` — это read-mode без аргументов).
                count = None
                if args:
                    first = args[0]
                    if first is not object:
                        try:
                            count = len(first) if first is not None else 0
                        except TypeError:
                            count = None
                if "database is locked" in msg or "database table is locked" in msg:
                    log.warning(
                        "pyrogram_sqlite_locked",
                        extra={"op": op_name, "count": count},
                    )
                    return fallback() if callable(fallback) else fallback
                if "database disk image is malformed" in msg:
                    log.warning(
                        "pyrogram_sqlite_malformed_swallowed",
                        extra={"op": op_name, "count": count},
                    )
                    # Wave 16-F / MEDIUM-3: инвалидируем connection — ставим _corrupt_flag,
                    # чтобы следующий READ call немедленно получил DatabaseError
                    # вместо того, чтобы взорваться с необработанным crash в
                    # pyrogram event loop. Чистый recovery loop подхватит флаг
                    # в preflight и запустит sqlite3 .recover.
                    # __slots__-safe: если storage не поддерживает dynamic attrs —
                    # пишем в module-level fallback dict (is_storage_corrupt проверяет оба).
                    try:
                        self._corrupt_flag = True
                    except (AttributeError, TypeError) as _flag_exc:
                        log.warning(
                            "pyrogram_corrupt_flag_set_failed",
                            extra={
                                "storage_class": type(self).__name__,
                                "error": str(_flag_exc),
                                "hint": (
                                    "storage may use __slots__; using module-level fallback dict"
                                ),
                            },
                        )
                        _STORAGE_CORRUPT_FLAGS[id(self)] = True
                    return fallback() if callable(fallback) else fallback
                raise

        _safe.__name__ = orig.__name__
        _safe.__qualname__ = getattr(orig, "__qualname__", orig.__name__)
        return _safe

    # Список write-методов для wrap. Если в будущей pyrofork-версии метод
    # отсутствует — пропускаем без падения. Per-method fallback: для
    # update_state в read-mode возвращаем [] (empty list — Telegram resync),
    # для прочих None.
    wrapped_methods = (
        ("update_usernames", None),
        ("update_peers", None),
        ("update_state", list),  # read-mode возвращает list; write — игнорим return
        ("remove_state", None),
    )
    for _method_name, _fallback in wrapped_methods:
        _orig = getattr(_ss.SQLiteStorage, _method_name, None)
        if _orig is None:
            log.warning(
                "pyrogram_storage_method_missing",
                extra={"method": _method_name},
            )
            continue
        setattr(
            _ss.SQLiteStorage,
            _method_name,
            _make_safe_method(_orig, _method_name, fallback=_fallback),
        )

    # ------------------------------------------------------------------
    # Wave 16-F: wrap READ-методов с проверкой _corrupt_flag.
    # ------------------------------------------------------------------
    # При swallow malformed в write-пути выше _corrupt_flag=True.
    # Если conn в corrupt state — следующий READ гарантированно взорвётся
    # с необработанным sqlite3.DatabaseError прямо в pyrogram event loop.
    # Мы перехватываем это ДО crash: при _corrupt_flag raise DatabaseError
    # с маркером "storage marked corrupt", который runtime.py поймает
    # в except sqlite3.DatabaseError → trigger immediate recovery cycle.
    #
    # READ методы: get_peer_by_id / get_peer_by_username / get_peer_by_phone_number.
    # Они raise KeyError при not-found — это нормально; мы только добавляем
    # ранний выход при corrupt flag.
    def _make_safe_read_method(orig, op_name):
        """
        Factory: оборачивает async read-метод SQLiteStorage.
        Если storage помечен corrupt (_corrupt_flag=True) — немедленно
        raise sqlite3.DatabaseError с маркером для preflight.
        """

        async def _safe_read(self, *args, **kwargs):
            # Проверяем corrupt flag ДО любого обращения к conn.
            if getattr(self, "_corrupt_flag", False) or _STORAGE_CORRUPT_FLAGS.get(id(self), False):
                log.warning(
                    "pyrogram_read_rejected_corrupt_flag",
                    extra={"op": op_name},
                )
                raise sqlite3.DatabaseError(
                    "storage marked corrupt — connection invalidated after malformed write"
                )
            result = await orig(self, *args, **kwargs)
            # Wave 21-A: read succeeded → фиксируем успех для авто-очистки stale flag.
            _record_storage_success(self)
            return result

        _safe_read.__name__ = orig.__name__
        _safe_read.__qualname__ = getattr(orig, "__qualname__", orig.__name__)
        return _safe_read

    # Список read-методов для защиты.
    read_methods = (
        "get_peer_by_id",
        "get_peer_by_username",
        "get_peer_by_phone_number",
    )
    for _rmethod_name in read_methods:
        _rorig = getattr(_ss.SQLiteStorage, _rmethod_name, None)
        if _rorig is None:
            log.warning(
                "pyrogram_storage_read_method_missing",
                extra={"method": _rmethod_name},
            )
            continue
        setattr(
            _ss.SQLiteStorage,
            _rmethod_name,
            _make_safe_read_method(_rorig, _rmethod_name),
        )

    _PATCH_APPLIED = True
    log.info("pyrogram_sqlite_hardening_applied")
    return True


def is_patch_applied() -> bool:
    """Тестовый хук: проверить, применён ли патч."""
    return _PATCH_APPLIED


def _reset_for_tests() -> None:
    """Только для тестов: сбрасывает флаг, чтобы можно было re-apply в фикстуре."""
    global _PATCH_APPLIED
    _PATCH_APPLIED = False
    global _SESSION_GUARD_APPLIED
    _SESSION_GUARD_APPLIED = False
    # Wave 21-A: сброс счётчиков и threshold-кэша между тестами.
    global _STORAGE_AUTO_CLEAR_THRESHOLD
    _STORAGE_AUTO_CLEAR_THRESHOLD = None
    _STORAGE_SUCCESS_COUNTS.clear()
    _STORAGE_CORRUPT_FLAGS.clear()


# ---------------------------------------------------------------------------
# Session.start guard против "NoneType.to_bytes" race (Sentry PYTHON-FASTAPI-6G)
# ---------------------------------------------------------------------------
#
# Симптом: AttributeError "'NoneType' object has no attribute 'to_bytes'" при
# !restart / restart_userbot. Pyrofork вызывает storage.api_id() / dc_id() и
# packs значения через ``Int(value)`` → ``value.to_bytes(...)``. Если в момент
# рестарта parallel-клиент (swarm) уже закрыл/переоткрыл sqlite-storage,
# подзапрос ``SELECT api_id FROM sessions`` возвращает NULL → None → crash.
#
# История: предыдущая версия патчила ``SQLiteStorage._get`` напрямую. Это
# ломало pyrogram-инспекцию: оригинальный ``_get`` использует
# ``inspect.stack()[2].function`` чтобы вычислить имя accessor-колонки в SQL
# (``SELECT api_id FROM sessions``). Любая обёртка над _get добавляет лишний
# фрейм — stack[2] оказывается ``_accessor`` вместо ``api_id``, и SQL падает с
# ``no such column: _accessor``.
#
# Правильный подход: патчим **сами accessor-методы** (api_id/dc_id/...). Когда
# обёртка вызывает оригинальный accessor (имя сохраняется!) — chain
# accessor → _accessor → _get → inspect.stack()[2] видит правильное имя
# accessor'а, и SQL формируется корректно. Кэш last-good ведём per-storage по
# имени accessor'а, чтобы на None / OperationalError возвращать предыдущее
# валидное значение.
#
# Защита двухслойная:
#   1) Wrap accessors (api_id, dc_id, test_mode, auth_key, date, user_id,
#      is_bot) — на None / Exception возвращаем cached last-good.
#   2) ``_safe_session_start`` оборачивает Session.start в retry-loop
#      (до 3 попыток с экспоненциальной паузой) — ловим AttributeError, в
#      сообщении которого есть "to_bytes".

_SESSION_GUARD_APPLIED = False
_MAX_START_RETRIES = 3
_START_RETRY_BASE_DELAY = 0.4  # сек; реальные паузы 0.4 / 0.8 / 1.6

# Accessor-методы SQLiteStorage, которые читают/пишут колонку sessions-таблицы
# через ``_accessor(value=object)`` → ``_get()`` chain. Pack-pipeline pyrogram
# вызывает их без аргумента (read-mode), и именно read-mode возвращает None,
# что ломает Int(value).to_bytes(...). Колонки соответствуют SCHEMA в
# pyrogram/storage/sqlite_storage.py.
_ACCESSOR_NAMES = (
    "dc_id",
    "api_id",
    "test_mode",
    "auth_key",
    "date",
    "user_id",
    "is_bot",
)


def _is_to_bytes_race(exc: BaseException) -> bool:
    """Признак характерной AttributeError из pyrogram pack-pipeline."""
    if not isinstance(exc, AttributeError):
        return False
    msg = str(exc)
    return "to_bytes" in msg and "NoneType" in msg


def _make_safe_accessor(name: str, original):
    """
    Строит async-обёртку над accessor-методом SQLiteStorage.

    Поведение:
    - read-mode (без value): если оригинал вернул None или поднял исключение —
      возвращаем cached last-good (если есть). Не-None результат кэшируем.
    - write-mode (с value): прозрачно делегируем оригиналу, write не кэшируем
      (новое значение появится в БД и будет прочитано на следующем read).

    Важно: вызываем именно ``original(self, *args, **kwargs)`` — имя функции
    сохраняется в frame, и ``inspect.stack()[2].function`` внутри pyrogram._get
    вернёт правильное имя колонки (api_id / dc_id / ...).
    """

    async def safe_accessor(self, value=object, *args, **kwargs):
        cache = getattr(self, "_krab_last_good", None)
        if cache is None:
            cache = {}
            try:
                self._krab_last_good = cache
            except Exception:  # noqa: BLE001 — на случай __slots__
                pass

        # write-mode — никаких подмен.
        if value is not object:
            return await original(self, value, *args, **kwargs)

        try:
            result = await original(self)
        except Exception as exc:  # noqa: BLE001 — sqlite3.OperationalError и др.
            cached = cache.get(name)
            if cached is not None:
                log.warning(
                    "pyrogram_storage_accessor_failed_using_cache",
                    extra={
                        "accessor": name,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )
                return cached
            raise

        if result is None:
            cached = cache.get(name)
            if cached is not None:
                log.warning(
                    "pyrogram_storage_accessor_none_using_cache",
                    extra={"accessor": name},
                )
                return cached
            return result

        cache[name] = result
        return result

    # Сохраняем имя — чтобы inspect.stack() в pyrogram._get видел правильное
    # имя колонки, а не "safe_accessor".
    safe_accessor.__name__ = name
    safe_accessor.__qualname__ = f"SQLiteStorage.{name}"
    return safe_accessor


def apply_pyrogram_session_guard() -> bool:
    """
    Patch accessor-методов SQLiteStorage + Session.start retry-loop для
    защиты от NoneType.to_bytes race в restart_userbot path.

    Идемпотентен. Возвращает True если patched (или уже был).
    """
    global _SESSION_GUARD_APPLIED
    if _SESSION_GUARD_APPLIED:
        return True

    try:
        import asyncio

        from pyrogram.session import session as _sess_mod
        from pyrogram.storage import sqlite_storage as _ss
    except Exception as exc:  # noqa: BLE001
        log.warning("pyrogram_session_guard_import_failed", extra={"error": str(exc)})
        return False

    # ------------------------------------------------------------------
    # Слой 1: wrap accessors (api_id/dc_id/test_mode/auth_key/date/...)
    # ------------------------------------------------------------------
    for accessor_name in _ACCESSOR_NAMES:
        try:
            original = getattr(_ss.SQLiteStorage, accessor_name)
        except AttributeError:
            log.warning(
                "pyrogram_storage_accessor_missing",
                extra={"accessor": accessor_name},
            )
            continue
        wrapped = _make_safe_accessor(accessor_name, original)
        setattr(_ss.SQLiteStorage, accessor_name, wrapped)

    # ------------------------------------------------------------------
    # Слой 2: retry на Session.start при NoneType.to_bytes race
    # ------------------------------------------------------------------
    _orig_start = _sess_mod.Session.start

    async def _safe_session_start(self):
        last_exc: BaseException | None = None
        for attempt in range(1, _MAX_START_RETRIES + 1):
            try:
                return await _orig_start(self)
            except AttributeError as exc:
                if not _is_to_bytes_race(exc):
                    raise
                last_exc = exc
                if attempt >= _MAX_START_RETRIES:
                    log.error(
                        "pyrogram_session_start_to_bytes_race_exhausted",
                        extra={"attempts": attempt, "error": str(exc)},
                    )
                    raise
                delay = _START_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                log.warning(
                    "pyrogram_session_start_to_bytes_race_retry",
                    extra={"attempt": attempt, "delay_s": delay, "error": str(exc)},
                )
                await asyncio.sleep(delay)
        # Теоретически недостижимо — но raise страхует от silent return None.
        if last_exc is not None:
            raise last_exc
        return None

    _sess_mod.Session.start = _safe_session_start

    _SESSION_GUARD_APPLIED = True
    log.info(
        "pyrogram_session_guard_applied",
        extra={"accessors_wrapped": list(_ACCESSOR_NAMES)},
    )
    return True


def is_session_guard_applied() -> bool:
    """Тестовый хук."""
    return _SESSION_GUARD_APPLIED


# ---------------------------------------------------------------------------
# Wave 16-F: публичный API для проверки и сброса _corrupt_flag на storage.
# ---------------------------------------------------------------------------

_CORRUPT_FLAG_ATTR = "_corrupt_flag"
_CORRUPT_MARKER = "storage marked corrupt"


def is_storage_corrupt(storage: object) -> bool:
    """Возвращает True если storage помечен corrupt после malformed swallow.

    Проверяет оба пути:
    1. _corrupt_flag attr на объекте (стандартный случай).
    2. _STORAGE_CORRUPT_FLAGS[id(storage)] — fallback для __slots__-классов,
       у которых setattr(_corrupt_flag) вызывает AttributeError.
    """
    return bool(getattr(storage, _CORRUPT_FLAG_ATTR, False)) or _STORAGE_CORRUPT_FLAGS.get(
        id(storage), False
    )


def clear_storage_corrupt_flag(storage: object) -> None:
    """Сбрасывает corrupt flag и счётчик успехов после ручного recovery.

    Wave 21-A: также очищает _STORAGE_SUCCESS_COUNTS — счётчик должен
    начать с нуля после явного сброса, чтобы авто-очистка не срабатывала
    по накопленным до recovery данным.
    """
    sid = id(storage)
    was_corrupt = is_storage_corrupt(storage)
    try:
        setattr(storage, _CORRUPT_FLAG_ATTR, False)
    except Exception:  # noqa: BLE001 — __slots__ guard
        pass
    # Убираем из fallback dict и счётчика независимо от __slots__.
    _STORAGE_CORRUPT_FLAGS.pop(sid, None)
    _STORAGE_SUCCESS_COUNTS.pop(sid, None)
    if was_corrupt:
        log.info(
            "storage_corrupt_flag_cleared_manual",
            extra={"storage_class": type(storage).__name__},
        )


def is_corrupt_marker_error(exc: BaseException) -> bool:
    """Проверяет, что DatabaseError содержит наш маркер от read-wrapper."""
    return isinstance(exc, sqlite3.DatabaseError) and _CORRUPT_MARKER in str(exc)
