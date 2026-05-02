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
  b) Сразу применяет WAL + busy_timeout + synchronous=NORMAL PRAGMA.
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
import sqlite3

log = logging.getLogger(__name__)

_PATCH_APPLIED = False

# Таймаут подключения к SQLite (сек). Pyrofork использует 1s — слишком мало
# при конкурентном доступе swarm-клиентов.
_SQLITE_CONNECT_TIMEOUT = 10


def _execute_pragmas(conn) -> None:
    """Применяет hardening-PRAGMA к уже открытому sqlite3-соединению.

    Порядок важен:
    1. busy_timeout ПЕРВЫМ — чтобы сами последующие PRAGMA не падали на lock.
    2. journal_mode=WAL — persists в заголовке файла.
    3. synchronous=NORMAL — безопасно для WAL, даёт ~2x throughput на writes.

    Примечание: ``auto_vacuum=INCREMENTAL`` намеренно НЕ применяется здесь.
    SQLite позволяет изменить auto_vacuum только до первой записи на новой базе,
    но к моменту вызова _execute_pragmas journal_mode=WAL уже записывает заголовок
    (4096 байт). Для существующих баз изменение auto_vacuum требует полного VACUUM —
    что является опасной операцией. Пространство освобождается органически через
    free-page list при обновлениях.
    """
    for pragma in (
        "PRAGMA busy_timeout=5000",
        "PRAGMA journal_mode=WAL",
        "PRAGMA synchronous=NORMAL",
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

    # Graceful retry-layer поверх update_usernames: под редкой конкуренцией
    # даже с WAL возможен OperationalError (например, schema upgrade на
    # старте). Логируем warning и глотаем — имена подтянутся в следующий раз.
    _orig_update_usernames = _ss.SQLiteStorage.update_usernames

    async def _safe_update_usernames(self, usernames):
        try:
            await _orig_update_usernames(self, usernames)
        except Exception as exc:  # noqa: BLE001 — sqlite3.OperationalError и др.
            msg = str(exc).lower()
            if "database is locked" in msg or "database table is locked" in msg:
                log.warning(
                    "pyrogram_sqlite_locked",
                    extra={"op": "update_usernames", "count": len(usernames or [])},
                )
                return None
            raise

    _ss.SQLiteStorage.update_usernames = _safe_update_usernames

    # Тот же retry-layer для update_peers: эта же storage-процедура, вызывается
    # чаще, и в логах перед Session 32 restart получали "database disk image is
    # malformed" 25 раз подряд. Без graceful retry один corrupted call валит
    # сессию. Глотаем locked + malformed (логируем); прочие — пробрасываем.
    _orig_update_peers = _ss.SQLiteStorage.update_peers

    async def _safe_update_peers(self, peers):
        try:
            await _orig_update_peers(self, peers)
        except Exception as exc:  # noqa: BLE001 — sqlite3.OperationalError/DatabaseError
            msg = str(exc).lower()
            if "database is locked" in msg or "database table is locked" in msg:
                log.warning(
                    "pyrogram_sqlite_locked",
                    extra={"op": "update_peers", "count": len(peers or [])},
                )
                return None
            if "database disk image is malformed" in msg:
                log.warning(
                    "pyrogram_sqlite_malformed_swallowed",
                    extra={"op": "update_peers", "count": len(peers or [])},
                )
                return None
            raise

    _ss.SQLiteStorage.update_peers = _safe_update_peers

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
