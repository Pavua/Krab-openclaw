# -*- coding: utf-8 -*-
"""
Тесты для src/core/hot_reload_config.py — HotReloadableConfig.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from src.core.hot_reload_config import HotReloadableConfig

# ---------------------------------------------------------------------------
# Fixtures


@pytest.fixture
def tmp_json(tmp_path: Path) -> Path:
    """Путь к временному JSON-файлу (не создаём заранее)."""
    return tmp_path / "config.json"


# ---------------------------------------------------------------------------
# Tests


class TestLoadMissingFile:
    """Загрузка при отсутствующем файле."""

    def test_load_missing_file_returns_empty(self, tmp_json: Path) -> None:
        cfg = HotReloadableConfig(path=tmp_json)
        assert cfg.get() == {}

    def test_load_missing_file_mtime_is_zero(self, tmp_json: Path) -> None:
        cfg = HotReloadableConfig(path=tmp_json)
        assert cfg._last_mtime == 0.0

    def test_load_missing_with_parser(self, tmp_json: Path) -> None:
        cfg = HotReloadableConfig(path=tmp_json, parser=lambda d: list(d.keys()))
        assert cfg.get() == []


class TestLoadExistingFile:
    """Загрузка существующего файла."""

    def test_load_existing_file(self, tmp_json: Path) -> None:
        tmp_json.write_text(json.dumps({"key": "value"}), encoding="utf-8")
        cfg = HotReloadableConfig(path=tmp_json)
        assert cfg.get() == {"key": "value"}

    def test_load_sets_mtime(self, tmp_json: Path) -> None:
        tmp_json.write_text("{}", encoding="utf-8")
        cfg = HotReloadableConfig(path=tmp_json)
        assert cfg._last_mtime > 0.0

    def test_load_nested_json(self, tmp_json: Path) -> None:
        data = {"a": {"b": [1, 2, 3]}, "c": True}
        tmp_json.write_text(json.dumps(data), encoding="utf-8")
        cfg = HotReloadableConfig(path=tmp_json)
        assert cfg.get() == data


class TestSaveWritesAndPreservesState:
    """save() пишет на диск и обновляет состояние."""

    def test_save_writes_file(self, tmp_json: Path) -> None:
        cfg = HotReloadableConfig(path=tmp_json)
        cfg.save({"x": 1})
        assert tmp_json.exists()
        assert json.loads(tmp_json.read_text()) == {"x": 1}

    def test_save_updates_state(self, tmp_json: Path) -> None:
        cfg = HotReloadableConfig(path=tmp_json)
        cfg.save({"x": 42})
        assert cfg.get() == {"x": 42}

    def test_save_updates_mtime(self, tmp_json: Path) -> None:
        cfg = HotReloadableConfig(path=tmp_json)
        old_mtime = cfg._last_mtime
        cfg.save({"y": 2})
        assert cfg._last_mtime > old_mtime

    def test_save_with_serializer(self, tmp_json: Path) -> None:
        cfg = HotReloadableConfig(path=tmp_json)
        data = [1, 2, 3]
        cfg.save(data, serializer=lambda lst: {"items": lst})
        assert json.loads(tmp_json.read_text()) == {"items": [1, 2, 3]}

    def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        nested = tmp_path / "a" / "b" / "config.json"
        cfg = HotReloadableConfig(path=nested)
        cfg.save({"ok": True})
        assert nested.exists()


class TestMaybeReloadPicksUpExternalEdit:
    """_maybe_reload() подхватывает внешние изменения файла."""

    def test_external_edit_reloaded_on_get(self, tmp_json: Path) -> None:
        tmp_json.write_text(json.dumps({"v": 1}), encoding="utf-8")
        cfg = HotReloadableConfig(path=tmp_json)
        assert cfg.get() == {"v": 1}

        # Имитируем внешнее редактирование с явным сдвигом mtime
        time.sleep(0.05)
        tmp_json.write_text(json.dumps({"v": 2}), encoding="utf-8")
        # Гарантируем, что mtime новее на > 0.1 сек
        new_mtime = cfg._last_mtime + 0.2
        import os
        os.utime(tmp_json, (new_mtime, new_mtime))

        assert cfg.get() == {"v": 2}

    def test_no_reload_without_mtime_change(self, tmp_json: Path) -> None:
        tmp_json.write_text(json.dumps({"v": 1}), encoding="utf-8")
        cfg = HotReloadableConfig(path=tmp_json)
        # Перезаписываем содержимое, но возвращаем старый mtime
        old_mtime = tmp_json.stat().st_mtime
        tmp_json.write_text(json.dumps({"v": 99}), encoding="utf-8")
        import os
        os.utime(tmp_json, (old_mtime, old_mtime))

        # Краб должен вернуть старые данные (mtime не изменился)
        assert cfg.get() == {"v": 1}

    def test_reload_returns_true_when_changed(self, tmp_json: Path) -> None:
        tmp_json.write_text(json.dumps({"v": 1}), encoding="utf-8")
        cfg = HotReloadableConfig(path=tmp_json)

        new_mtime = cfg._last_mtime + 0.2
        tmp_json.write_text(json.dumps({"v": 2}), encoding="utf-8")
        import os
        os.utime(tmp_json, (new_mtime, new_mtime))

        reloaded = cfg._maybe_reload()
        assert reloaded is True


class TestForceReloadDetectsChanges:
    """force_reload() возвращает True при изменениях."""

    def test_force_reload_detects_new_data(self, tmp_json: Path) -> None:
        tmp_json.write_text(json.dumps({"v": 1}), encoding="utf-8")
        cfg = HotReloadableConfig(path=tmp_json)
        # Меняем файл напрямую, сбрасываем mtime-кэш вручную
        tmp_json.write_text(json.dumps({"v": 2}), encoding="utf-8")
        cfg._last_mtime = 0.0  # форсируем reload
        changed = cfg.force_reload()
        assert changed is True
        assert cfg.get() == {"v": 2}

    def test_force_reload_no_change(self, tmp_json: Path) -> None:
        tmp_json.write_text(json.dumps({"v": 1}), encoding="utf-8")
        cfg = HotReloadableConfig(path=tmp_json)
        # force_reload без изменения содержимого
        changed = cfg.force_reload()
        assert changed is False


class TestCustomParserApplied:
    """parser применяется при каждой загрузке."""

    def test_parser_transforms_on_load(self, tmp_json: Path) -> None:
        tmp_json.write_text(json.dumps({"a": 1, "b": 2}), encoding="utf-8")
        cfg = HotReloadableConfig(path=tmp_json, parser=lambda d: sorted(d.keys()))
        assert cfg.get() == ["a", "b"]

    def test_parser_applied_on_save(self, tmp_json: Path) -> None:
        # parser переводит значения в строки
        cfg = HotReloadableConfig(
            path=tmp_json,
            parser=lambda d: {k: str(v) for k, v in d.items()},
        )
        cfg.save({"n": 42})
        assert cfg.get() == {"n": "42"}

    def test_parser_applied_on_hot_reload(self, tmp_json: Path) -> None:
        import os

        tmp_json.write_text(json.dumps({"count": 5}), encoding="utf-8")
        cfg = HotReloadableConfig(
            path=tmp_json,
            parser=lambda d: d.get("count", 0) * 2,
        )
        assert cfg.get() == 10

        new_mtime = cfg._last_mtime + 0.2
        tmp_json.write_text(json.dumps({"count": 7}), encoding="utf-8")
        os.utime(tmp_json, (new_mtime, new_mtime))

        assert cfg.get() == 14


class TestThreadSafeConcurrentAccess:
    """Конкурентный доступ не вызывает гонок."""

    def test_concurrent_reads_safe(self, tmp_json: Path) -> None:
        tmp_json.write_text(json.dumps({"counter": 0}), encoding="utf-8")
        cfg = HotReloadableConfig(path=tmp_json)

        errors: list[Exception] = []
        results: list[Any] = []

        def reader() -> None:
            try:
                for _ in range(50):
                    results.append(cfg.get())
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(results) == 500  # 10 threads * 50 reads

    def test_concurrent_saves_safe(self, tmp_json: Path) -> None:
        cfg = HotReloadableConfig(path=tmp_json)
        errors: list[Exception] = []

        def writer(i: int) -> None:
            try:
                cfg.save({"writer": i})
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # Файл должен содержать валидный JSON после конкурентных записей
        data = json.loads(tmp_json.read_text())
        assert "writer" in data


# ---------------------------------------------------------------------------
# Needed for type hint in test
from typing import Any  # noqa: E402
