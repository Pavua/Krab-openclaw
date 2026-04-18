# -*- coding: utf-8 -*-
"""
Тесты hot-reload для ChatFilterConfig.
Проверяют поведение mtime-polling и метода reload().
"""

from __future__ import annotations

import json
import time

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write(path, data: dict) -> None:
    path.write_text(json.dumps(data))


def _rule(mode: str, note: str = "") -> dict:
    return {"mode": mode, "updated_at": time.time(), "note": note}


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_mtime_reload_picks_up_external_edit(tmp_path):
    """get_mode должен вернуть новый mode после внешнего редактирования файла."""
    from src.core.chat_filter_config import ChatFilterConfig

    path = tmp_path / "filters.json"
    _write(path, {"chat1": _rule("active")})

    cfg = ChatFilterConfig(state_path=path)
    assert cfg.get_mode("chat1") == "active"

    # Внешняя правка — убеждаемся, что mtime изменится
    time.sleep(0.15)
    _write(path, {"chat1": _rule("muted", "external")})

    # Следующий read должен подхватить новый mtime
    assert cfg.get_mode("chat1") == "muted"


def test_no_reload_if_file_unchanged(tmp_path, monkeypatch):
    """_maybe_reload НЕ должен вызывать _load если mtime не изменился."""
    from src.core.chat_filter_config import ChatFilterConfig

    path = tmp_path / "filters.json"
    _write(path, {"chat1": _rule("active")})

    cfg = ChatFilterConfig(state_path=path)

    load_calls = []
    original_load = cfg._load

    def tracked_load():
        load_calls.append(1)
        original_load()

    monkeypatch.setattr(cfg, "_load", tracked_load)

    # Повторные вызовы без изменения файла — load не должен срабатывать
    cfg.get_mode("chat1")
    cfg.get_mode("chat2")
    assert load_calls == [], "reload triggered without mtime change"


def test_reload_returns_true_on_changes(tmp_path):
    """reload() должен вернуть True если правила изменились."""
    from src.core.chat_filter_config import ChatFilterConfig

    path = tmp_path / "filters.json"
    _write(path, {})
    cfg = ChatFilterConfig(state_path=path)

    _write(path, {"c1": _rule("muted")})
    assert cfg.reload() is True


def test_reload_returns_false_if_no_diff(tmp_path):
    """Второй вызов reload() без изменений — должен вернуть False."""
    from src.core.chat_filter_config import ChatFilterConfig

    path = tmp_path / "filters.json"
    _write(path, {"c1": _rule("muted")})
    cfg = ChatFilterConfig(state_path=path)

    # Второй reload с теми же данными
    assert cfg.reload() is False


def test_set_mode_aware_of_external_edits(tmp_path):
    """set_mode должен preserve external edits в других чатах."""
    from src.core.chat_filter_config import ChatFilterConfig

    path = tmp_path / "filters.json"
    _write(path, {"chat1": _rule("active")})

    cfg = ChatFilterConfig(state_path=path)
    assert cfg.get_mode("chat1") == "active"

    # Внешняя правка: добавляем chat2 в файл
    time.sleep(0.15)
    _write(path, {"chat1": _rule("active"), "chat2": _rule("muted")})

    # set_mode на chat1 должен подхватить внешний chat2 (через _maybe_reload)
    cfg.set_mode("chat1", "mention-only")

    # chat2 должен быть сохранён (не потерян)
    saved = json.loads(path.read_text())
    assert "chat2" in saved, "external edit for chat2 was lost"
    assert saved["chat2"]["mode"] == "muted"


def test_missing_file_graceful(tmp_path):
    """Если файл не существует — не должно быть исключений, дефолт возвращается."""
    from src.core.chat_filter_config import ChatFilterConfig

    missing = tmp_path / "nonexistent.json"
    cfg = ChatFilterConfig(state_path=missing)

    assert cfg.get_mode("x") == "mention-only"  # group default
    assert cfg.get_mode("x", is_group=False) == "active"  # DM default
    cfg.reload()  # не должен бросать исключение


def test_corrupted_json_does_not_raise(tmp_path):
    """Повреждённый JSON при hot-reload не должен крашить экземпляр."""
    from src.core.chat_filter_config import ChatFilterConfig

    path = tmp_path / "filters.json"
    _write(path, {"c1": _rule("active")})
    cfg = ChatFilterConfig(state_path=path)

    # Повредить файл
    time.sleep(0.15)
    path.write_text("{invalid json")

    # get_mode не должен бросать исключение
    result = cfg.get_mode("c1")
    # Значение может быть дефолтным (группа) или старым — главное не краш
    assert result in {"active", "mention-only", "muted"}


def test_save_does_not_trigger_spurious_reload(tmp_path, monkeypatch):
    """После _save mtime обновляется в памяти — следующий _maybe_reload не должен перезагружать."""
    from src.core.chat_filter_config import ChatFilterConfig

    path = tmp_path / "filters.json"
    _write(path, {})
    cfg = ChatFilterConfig(state_path=path)

    load_calls = []
    original_load = cfg._load

    def tracked_load():
        load_calls.append(1)
        original_load()

    # set_mode пишет файл и обновляет _last_mtime
    cfg.set_mode("chat1", "muted")
    monkeypatch.setattr(cfg, "_load", tracked_load)

    # Сразу после set_mode — не должно быть лишней перезагрузки
    cfg.get_mode("chat1")
    assert load_calls == [], "spurious reload after _save"


def test_reload_stats_reflect_new_data(tmp_path):
    """После reload() stats() должна отражать новые данные."""
    from src.core.chat_filter_config import ChatFilterConfig

    path = tmp_path / "filters.json"
    _write(path, {})
    cfg = ChatFilterConfig(state_path=path)
    assert cfg.stats()["total_rules"] == 0

    _write(
        path,
        {
            "c1": _rule("active"),
            "c2": _rule("muted"),
            "c3": _rule("muted"),
        },
    )
    cfg.reload()
    st = cfg.stats()
    assert st["total_rules"] == 3
    assert st["by_mode"].get("muted") == 2
    assert st["by_mode"].get("active") == 1
