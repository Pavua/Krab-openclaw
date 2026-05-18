# -*- coding: utf-8 -*-
"""S69 W1: tests for monkey-patched pyrogram Dispatcher.add_handler.

Проверяем:
1. install_pyrogram_patches() ставит патч при env=1.
2. install_pyrogram_patches() пропускает патч если env не выставлен.
3. Sync-path при отсутствии workers — групп мутируется directly.
4. Fallback на оригинал при running workers.
5. Группы остаются отсортированы после sync add.
"""

from __future__ import annotations

import asyncio
import os
from collections import OrderedDict
from typing import Any

import pytest

from src.bootstrap import pyrogram_patch


@pytest.fixture(autouse=True)
def _reset_patch_state():
    """Гарантируем чистое состояние патча между тестами."""
    pyrogram_patch._reset_add_handler_patch_for_tests()
    yield
    pyrogram_patch._reset_add_handler_patch_for_tests()


@pytest.fixture
def _restore_env(monkeypatch):
    """Изолируем KRAB_PYROGRAM_PATCH_ADD_HANDLER между тестами."""
    monkeypatch.delenv("KRAB_PYROGRAM_PATCH_ADD_HANDLER", raising=False)
    yield monkeypatch


class _DummyDispatcher:
    """Минимальный stub Dispatcher для unit-тестов."""

    def __init__(self, with_running_workers: bool = False):
        self.handler_worker_tasks: list[Any] = []
        self.error_handlers: list[Any] = []
        self.groups: OrderedDict = OrderedDict()
        if with_running_workers:
            # Имитируем работающие workers через "не done" task placeholder.
            class _NotDone:
                def done(self):
                    return False

            self.handler_worker_tasks = [_NotDone()]


def test_patch_install_with_env(_restore_env):
    """При KRAB_PYROGRAM_PATCH_ADD_HANDLER=1 патч ставится."""
    _restore_env.setenv("KRAB_PYROGRAM_PATCH_ADD_HANDLER", "1")
    applied = pyrogram_patch.install_pyrogram_patches()
    assert applied is True
    assert pyrogram_patch.is_add_handler_patch_applied() is True

    # Идемпотентность: повторный install — no-op, но True.
    assert pyrogram_patch.install_pyrogram_patches() is True


def test_patch_install_skipped_without_env(_restore_env):
    """Default OFF: install_pyrogram_patches возвращает False, патч не ставится."""
    # env explicitly unset выше через _restore_env
    applied = pyrogram_patch.install_pyrogram_patches()
    assert applied is False
    assert pyrogram_patch.is_add_handler_patch_applied() is False


def test_patched_add_handler_sync_when_not_running():
    """Sync-path: при пустых workers handler мутирует groups напрямую."""
    disp = _DummyDispatcher(with_running_workers=False)

    sentinel_handler = object()
    # Вызываем _patched_add_handler напрямую (без full install).
    pyrogram_patch._patched_add_handler(disp, sentinel_handler, group=0)

    assert 0 in disp.groups
    assert disp.groups[0] == [sentinel_handler]


def test_patched_add_handler_fallback_when_running(monkeypatch):
    """Fallback path: при running workers делегируем в оригинал."""
    called = {"hit": False}

    def _fake_original(self, handler, group):
        called["hit"] = True
        called["handler"] = handler
        called["group"] = group
        return "fallback-return"

    monkeypatch.setattr(pyrogram_patch, "_ORIGINAL_ADD_HANDLER", _fake_original)

    disp = _DummyDispatcher(with_running_workers=True)
    h = object()
    result = pyrogram_patch._patched_add_handler(disp, h, group=5)

    assert called["hit"] is True
    assert called["handler"] is h
    assert called["group"] == 5
    assert result == "fallback-return"
    # Sync-путь не сработал — groups остался пуст.
    assert 5 not in disp.groups


def test_groups_sorted_after_sync_add():
    """После нескольких sync add группы отсортированы по ключу."""
    disp = _DummyDispatcher(with_running_workers=False)

    h_a, h_b, h_c = object(), object(), object()
    pyrogram_patch._patched_add_handler(disp, h_a, group=10)
    pyrogram_patch._patched_add_handler(disp, h_b, group=0)
    pyrogram_patch._patched_add_handler(disp, h_c, group=5)

    assert list(disp.groups.keys()) == [0, 5, 10]
    assert disp.groups[0] == [h_b]
    assert disp.groups[5] == [h_c]
    assert disp.groups[10] == [h_a]


def test_patched_add_handler_routes_error_handler_to_error_list():
    """ErrorHandler идёт в error_handlers, не в groups (даже в sync-path)."""
    from pyrogram.handlers.error_handler import ErrorHandler

    disp = _DummyDispatcher(with_running_workers=False)

    async def _cb(client, update, error):
        return None

    eh = ErrorHandler(_cb)
    pyrogram_patch._patched_add_handler(disp, eh, group=0)

    assert eh in disp.error_handlers
    # group=0 не создаётся для ErrorHandler.
    assert 0 not in disp.groups


def test_install_then_real_dispatcher_uses_sync_path(_restore_env):
    """End-to-end: install ставит патч → реальный Dispatcher.add_handler sync.

    Создаём реальный Dispatcher с моком client (нужен только loop). Проверяем,
    что вызов add_handler без running workers сразу мутирует groups.
    """
    _restore_env.setenv("KRAB_PYROGRAM_PATCH_ADD_HANDLER", "1")
    assert pyrogram_patch.install_pyrogram_patches() is True

    from pyrogram.dispatcher import Dispatcher

    # Минимальный mock client: Dispatcher.__init__ дергает asyncio.get_event_loop.
    loop = asyncio.new_event_loop()
    try:

        class _FakeClient:
            pass

        # Dispatcher хочет client с атрибутом loop usage в __init__; используем
        # set_event_loop чтобы get_event_loop вернул наш loop.
        asyncio.set_event_loop(loop)
        disp = Dispatcher(_FakeClient())  # type: ignore[arg-type]
        # __init__ уже добавил conversation_handler в группу 0 — он не пустой.
        before_len = len(disp.groups[0])

        sentinel = object()
        # Workers ещё не стартанули → sync-path.
        disp.add_handler(sentinel, group=99)

        assert 99 in disp.groups
        assert disp.groups[99] == [sentinel]
        # Существующая группа 0 не повреждена.
        assert len(disp.groups[0]) == before_len
    finally:
        loop.close()
        asyncio.set_event_loop(None)
