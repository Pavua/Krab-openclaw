# -*- coding: utf-8 -*-
"""
Тесты для защиты от NoneType.to_bytes race в Session.start
(Sentry PYTHON-FASTAPI-6G).

Покрытие:
1. AttributeError("'NoneType'... 'to_bytes'") ловится и Session.start ретраится.
2. Успешный путь не ломается (один вызов оригинального start, без задержек).
3. Лимит ретраев соблюдается — после _MAX_START_RETRIES падений
   исключение пробрасывается наружу.
4. SQLiteStorage._get при value=None возвращает кэшированное значение.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.bootstrap import pyrogram_patch as pp


@pytest.fixture(autouse=True)
def _reset_guard():
    """Сбрасываем флаг между тестами и восстанавливаем оригинальные методы."""
    pp._reset_for_tests()
    # Сохраняем ссылки до патча
    from pyrogram.session import session as _sess_mod
    from pyrogram.storage import sqlite_storage as _ss

    orig_start = _sess_mod.Session.start
    orig_get = _ss.SQLiteStorage._get
    yield
    _sess_mod.Session.start = orig_start
    _ss.SQLiteStorage._get = orig_get
    pp._reset_for_tests()


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def test_session_start_retries_on_to_bytes_race():
    """Первые 2 попытки падают NoneType.to_bytes, 3-я успешна → возврат."""
    from pyrogram.session import session as _sess_mod

    calls = {"n": 0}

    async def flaky_start(self):
        calls["n"] += 1
        if calls["n"] < 3:
            raise AttributeError("'NoneType' object has no attribute 'to_bytes'")
        return "ok"

    # Подменяем оригинал ДО apply_pyrogram_session_guard
    _sess_mod.Session.start = flaky_start
    assert pp.apply_pyrogram_session_guard() is True

    # Ускоряем sleep чтобы тест шёл мгновенно
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

    async def boom_sleep(_d):  # asyncio.sleep не должен вызываться вообще
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

    assert calls["n"] == 1  # без ретраев


def test_storage_get_returns_cached_when_none():
    """SQLiteStorage._get: если оригинал вернул None — берём из cache."""
    from pyrogram.storage import sqlite_storage as _ss

    # Симулируем "stateful" backend: первый вызов даёт значение, второй — None
    fake_values = iter([42, None])

    def fake_orig_get(self):
        return next(fake_values)

    _ss.SQLiteStorage._get = fake_orig_get
    assert pp.apply_pyrogram_session_guard() is True

    storage = SimpleNamespace()
    # Первый вызов: получаем 42, кэшируется по имени accessor (который возьмётся
    # из inspect.stack() — для прямого вызова это будет имя локальной функции).
    # Поэтому делаем оба вызова через одинаковый wrapper, имитирующий accessor.

    def accessor(s):
        # Имитируем dc_id() / api_id() — фрейм этой функции даст stable name.
        return _ss.SQLiteStorage._get(s)

    v1 = accessor(storage)
    v2 = accessor(storage)
    assert v1 == 42
    # При None должен быть возвращён закешированный 42
    assert v2 == 42


def test_session_guard_idempotent():
    """Повторный вызов apply_pyrogram_session_guard — no-op."""
    assert pp.apply_pyrogram_session_guard() is True
    assert pp.is_session_guard_applied() is True
    # Второй вызов не должен ничего ломать
    assert pp.apply_pyrogram_session_guard() is True
