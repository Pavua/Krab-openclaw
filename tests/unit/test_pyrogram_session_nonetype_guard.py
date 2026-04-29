# -*- coding: utf-8 -*-
"""
Тесты для защиты от NoneType.to_bytes race в Session.start
(Sentry PYTHON-FASTAPI-6G).

Покрытие:
1. AttributeError("'NoneType'... 'to_bytes'") ловится и Session.start ретраится.
2. Успешный путь не ломается (один вызов оригинального start, без задержек).
3. Лимит ретраев соблюдается — после _MAX_START_RETRIES падений
   исключение пробрасывается наружу.
4. Accessor cache: при value=None возвращается last-good.
5. Accessor cache: при Exception в оригинальном accessor — last-good.
6. Write-mode (с явным value) прозрачно делегируется.
7. **Реальный SQL не ломается** — pyrogram._get использует
   ``inspect.stack()[2].function`` чтобы построить ``SELECT <col> FROM sessions``;
   обёртки сохраняют имя метода (api_id/dc_id/...), и SQL формируется верно.
"""

from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.bootstrap import pyrogram_patch as pp


@pytest.fixture(autouse=True)
def _reset_guard():
    """Сбрасываем флаг между тестами и восстанавливаем оригинальные методы."""
    pp._reset_for_tests()
    from pyrogram.session import session as _sess_mod
    from pyrogram.storage import sqlite_storage as _ss

    orig_start = _sess_mod.Session.start
    orig_accessors = {name: getattr(_ss.SQLiteStorage, name) for name in pp._ACCESSOR_NAMES}
    yield
    _sess_mod.Session.start = orig_start
    for name, fn in orig_accessors.items():
        setattr(_ss.SQLiteStorage, name, fn)
    pp._reset_for_tests()


# ---------------------------------------------------------------------------
# Слой 2: Session.start retry loop
# ---------------------------------------------------------------------------


def test_session_start_retries_on_to_bytes_race():
    """Первые 2 попытки падают NoneType.to_bytes, 3-я успешна → возврат."""
    from pyrogram.session import session as _sess_mod

    calls = {"n": 0}

    async def flaky_start(self):
        calls["n"] += 1
        if calls["n"] < 3:
            raise AttributeError("'NoneType' object has no attribute 'to_bytes'")
        return "ok"

    _sess_mod.Session.start = flaky_start
    assert pp.apply_pyrogram_session_guard() is True

    async def fast_sleep(_d):
        return None

    with patch("asyncio.sleep", new=fast_sleep):
        fake_self = SimpleNamespace()
        result = asyncio.run(_sess_mod.Session.start(fake_self))

    assert result == "ok"
    assert calls["n"] == 3


def test_session_start_success_path_unchanged():
    """Если start не падает — патч его просто прокидывает, без задержек."""
    from pyrogram.session import session as _sess_mod

    calls = {"n": 0}

    async def good_start(self):
        calls["n"] += 1
        return "started"

    _sess_mod.Session.start = good_start
    assert pp.apply_pyrogram_session_guard() is True

    async def boom_sleep(_d):
        raise AssertionError("sleep called on success path")

    with patch("asyncio.sleep", new=boom_sleep):
        fake_self = SimpleNamespace()
        result = asyncio.run(_sess_mod.Session.start(fake_self))

    assert result == "started"
    assert calls["n"] == 1


def test_session_start_max_retries_respected():
    """После _MAX_START_RETRIES попыток исключение пробрасывается."""
    from pyrogram.session import session as _sess_mod

    calls = {"n": 0}

    async def always_race(self):
        calls["n"] += 1
        raise AttributeError("'NoneType' object has no attribute 'to_bytes'")

    _sess_mod.Session.start = always_race
    assert pp.apply_pyrogram_session_guard() is True

    async def fast_sleep(_d):
        return None

    with patch("asyncio.sleep", new=fast_sleep):
        fake_self = SimpleNamespace()
        with pytest.raises(AttributeError, match="to_bytes"):
            asyncio.run(_sess_mod.Session.start(fake_self))

    assert calls["n"] == pp._MAX_START_RETRIES


def test_session_start_unrelated_attribute_error_not_retried():
    """AttributeError без to_bytes/NoneType пробрасывается сразу."""
    from pyrogram.session import session as _sess_mod

    calls = {"n": 0}

    async def other_attr_error(self):
        calls["n"] += 1
        raise AttributeError("'Foo' object has no attribute 'bar'")

    _sess_mod.Session.start = other_attr_error
    assert pp.apply_pyrogram_session_guard() is True

    fake_self = SimpleNamespace()
    with pytest.raises(AttributeError, match="bar"):
        asyncio.run(_sess_mod.Session.start(fake_self))

    assert calls["n"] == 1


def test_session_guard_idempotent():
    """Повторный вызов apply_pyrogram_session_guard — no-op."""
    assert pp.apply_pyrogram_session_guard() is True
    assert pp.is_session_guard_applied() is True
    assert pp.apply_pyrogram_session_guard() is True


# ---------------------------------------------------------------------------
# Слой 1: accessor cache (api_id/dc_id/auth_key/...)
# ---------------------------------------------------------------------------


def _make_real_storage():
    """Реальная SQLiteStorage с in-memory схемой pyrogram + одной session-row."""
    from pyrogram.storage import sqlite_storage as _ss

    conn = sqlite3.connect(":memory:")
    conn.executescript(_ss.SCHEMA)
    conn.executescript(_ss.UNAME_SCHEMA)
    conn.execute(
        "INSERT INTO sessions (dc_id, api_id, test_mode, auth_key, date, user_id, is_bot) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (2, 12345, 0, b"\x01\x02", 1_700_000_000, 99, 0),
    )
    conn.commit()
    storage = _ss.SQLiteStorage("test")
    storage.conn = conn
    return storage, conn


def test_accessor_real_sql_select_not_broken():
    """
    КРИТИЧНО: проверяем, что SQL `SELECT <col> FROM sessions` формируется
    корректно после патча. Предыдущая версия патча ломала
    inspect.stack()[2].function в pyrogram._get → "no such column: _accessor".
    """
    assert pp.apply_pyrogram_session_guard() is True
    storage, _ = _make_real_storage()

    async def go():
        # Каждый accessor читает свою колонку — если SQL сломан, поднимется
        # sqlite3.OperationalError("no such column: ...") и тест упадёт.
        assert await storage.api_id() == 12345
        assert await storage.dc_id() == 2
        assert await storage.test_mode() == 0
        assert await storage.auth_key() == b"\x01\x02"
        assert await storage.user_id() == 99
        assert await storage.is_bot() == 0
        assert await storage.date() == 1_700_000_000

    asyncio.run(go())


def test_accessor_returns_cached_when_db_returns_none():
    """Если БД вернула NULL — обёртка отдаёт cached last-good значение."""
    assert pp.apply_pyrogram_session_guard() is True
    storage, conn = _make_real_storage()

    async def go():
        first = await storage.api_id()
        assert first == 12345
        # NULL'ифицируем колонку — обёртка должна вернуть cached 12345
        conn.execute("UPDATE sessions SET api_id = NULL")
        conn.commit()
        cached = await storage.api_id()
        assert cached == 12345

    asyncio.run(go())


def test_accessor_returns_cached_on_exception():
    """Если оригинал поднял исключение (sqlite locked и т.п.) — отдаём cached."""
    assert pp.apply_pyrogram_session_guard() is True
    from pyrogram.storage import sqlite_storage as _ss

    storage, _ = _make_real_storage()

    async def go():
        # Прогрев кэша
        assert await storage.api_id() == 12345

        # Подменяем оригинал api_id (внутри обёртки) — но обёртка уже стоит
        # на классе, значит надо подменить _accessor у инстанса storage,
        # либо conn.execute. Проще всего: закрыть conn и поймать ProgrammingError.
        storage.conn.close()
        cached = await storage.api_id()
        assert cached == 12345

    asyncio.run(go())


def test_accessor_returns_none_when_no_cache():
    """None без cache — возвращаем None как есть (не raise)."""
    assert pp.apply_pyrogram_session_guard() is True
    storage, conn = _make_real_storage()
    conn.execute("UPDATE sessions SET api_id = NULL")
    conn.commit()

    async def go():
        # Кэш пуст, БД вернула None → возвращаем None без cache lookup
        result = await storage.api_id()
        assert result is None

    asyncio.run(go())


def test_accessor_write_mode_passes_through():
    """Вызов api_id(value) — write-mode, прозрачно делегируется оригиналу."""
    assert pp.apply_pyrogram_session_guard() is True
    storage, conn = _make_real_storage()

    async def go():
        await storage.api_id(99999)
        # Проверяем, что значение ушло в БД
        row = conn.execute("SELECT api_id FROM sessions").fetchone()
        assert row[0] == 99999
        # Read после write возвращает новое значение
        assert await storage.api_id() == 99999

    asyncio.run(go())


def test_accessor_cache_per_storage_instance():
    """Кэш изолирован между разными storage-инстансами."""
    assert pp.apply_pyrogram_session_guard() is True
    storage_a, conn_a = _make_real_storage()
    storage_b, conn_b = _make_real_storage()
    conn_b.execute("UPDATE sessions SET api_id = 77777")
    conn_b.commit()

    async def go():
        a = await storage_a.api_id()
        b = await storage_b.api_id()
        assert a == 12345
        assert b == 77777

    asyncio.run(go())


def test_accessor_function_name_preserved():
    """
    Имена обёрток должны совпадать с оригинальными — иначе
    inspect.stack()[2].function в pyrogram._get вернёт "safe_accessor"
    и SQL сломается.
    """
    assert pp.apply_pyrogram_session_guard() is True
    from pyrogram.storage import sqlite_storage as _ss

    for name in pp._ACCESSOR_NAMES:
        wrapped = getattr(_ss.SQLiteStorage, name)
        assert wrapped.__name__ == name, f"{name}: __name__={wrapped.__name__}"
