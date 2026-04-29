# -*- coding: utf-8 -*-
"""Тесты для src/core/auto_translate_chat.py (Idea 4)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.core.auto_translate_chat import ChatTranslateConfig


@pytest.fixture
def tmp_store(tmp_path: Path) -> Path:
    return tmp_path / "auto_translate_chats.json"


def _fixed_now() -> datetime:
    return datetime(2026, 4, 29, 12, 0, 0, tzinfo=timezone.utc)


def test_register_and_lookup_roundtrip(tmp_store: Path) -> None:
    cfg = ChatTranslateConfig(storage_path=tmp_store, now_fn=_fixed_now)
    cfg.register_chat(-1001234, "es", "ru", direction="inline")

    snapshot = cfg.get_config(-1001234)
    assert snapshot is not None
    assert snapshot["source_lang"] == "es"
    assert snapshot["target_lang"] == "ru"
    assert snapshot["direction"] == "inline"
    assert snapshot["registered_at"] == _fixed_now().isoformat()

    # API возвращает копию: мутация снаружи не должна ломать внутренний state.
    snapshot["source_lang"] = "MUTATED"
    assert cfg.get_config(-1001234)["source_lang"] == "es"

    # Невалидные direction / совпадающие коды → ValueError.
    with pytest.raises(ValueError):
        cfg.register_chat(42, "ru", "ru")
    with pytest.raises(ValueError):
        cfg.register_chat(42, "ru", "en", direction="bogus")  # type: ignore[arg-type]


def test_should_translate_matches_only_source_lang(tmp_store: Path) -> None:
    cfg = ChatTranslateConfig(storage_path=tmp_store, now_fn=_fixed_now)
    cfg.register_chat(-100, "es", "ru")

    # Совпадает с source → True.
    assert cfg.should_translate(-100, "es") is True
    # Регистронезависимо.
    assert cfg.should_translate(-100, "ES") is True
    # Не source (это уже целевой язык) → False.
    assert cfg.should_translate(-100, "ru") is False
    # Незарегистрированный чат → False.
    assert cfg.should_translate(-999, "es") is False
    # Неизвестный detected_lang → False (caller должен fallback решать сам).
    assert cfg.should_translate(-100, None) is False
    assert cfg.should_translate(-100, "") is False


def test_format_inline_translation_basic_and_fallback() -> None:
    # Стандартный кейс — `original [translated]`.
    assert (
        ChatTranslateConfig.format_inline_translation("Hola", "Привет")
        == "Hola [Привет]"
    )
    # Кириллический исходник тоже работает.
    assert (
        ChatTranslateConfig.format_inline_translation("Привет", "Hello")
        == "Привет [Hello]"
    )
    # Пустой / нулевой translated → возвращаем original без брэкетов.
    assert ChatTranslateConfig.format_inline_translation("Hola", "") == "Hola"
    assert ChatTranslateConfig.format_inline_translation("Hola", "   ") == "Hola"
    # Translated идентичен original → не дублируем.
    assert ChatTranslateConfig.format_inline_translation("Hola", "Hola") == "Hola"


def test_persist_and_reload_from_disk(tmp_store: Path) -> None:
    cfg1 = ChatTranslateConfig(storage_path=tmp_store, now_fn=_fixed_now)
    cfg1.register_chat(-1001234, "es", "ru", direction="both")
    cfg1.register_chat(-1009999, "en", "ru", direction="reply")

    # Файл реально записан и валидный JSON.
    raw = json.loads(tmp_store.read_text(encoding="utf-8"))
    assert "-1001234" in raw
    assert raw["-1001234"]["direction"] == "both"

    # Новый экземпляр читает с диска.
    cfg2 = ChatTranslateConfig(storage_path=tmp_store, now_fn=_fixed_now)
    assert cfg2.get_config(-1001234)["target_lang"] == "ru"
    assert cfg2.get_config(-1009999)["direction"] == "reply"

    listed = cfg2.list_chats()
    assert {item["chat_id"] for item in listed} == {"-1001234", "-1009999"}


def test_unregister_and_corrupt_file_handling(tmp_store: Path, tmp_path: Path) -> None:
    cfg = ChatTranslateConfig(storage_path=tmp_store, now_fn=_fixed_now)
    cfg.register_chat(-100, "es", "ru")
    assert cfg.unregister_chat(-100) is True
    assert cfg.unregister_chat(-100) is False  # повторный no-op
    assert cfg.get_config(-100) is None

    # Повреждённый файл не должен ронять загрузку.
    bad_path = tmp_path / "broken.json"
    bad_path.write_text("{not json", encoding="utf-8")
    cfg_bad = ChatTranslateConfig(storage_path=bad_path, now_fn=_fixed_now)
    assert cfg_bad.list_chats() == []

    # Не-словарь на корне → пропускаем без падения.
    arr_path = tmp_path / "array.json"
    arr_path.write_text("[1, 2, 3]", encoding="utf-8")
    cfg_arr = ChatTranslateConfig(storage_path=arr_path, now_fn=_fixed_now)
    assert cfg_arr.list_chats() == []

    # Запись с битым direction должна пропуститься, валидная — загрузиться.
    mixed_path = tmp_path / "mixed.json"
    mixed_path.write_text(
        json.dumps(
            {
                "-1": {"source_lang": "es", "target_lang": "ru", "direction": "inline"},
                "-2": {"source_lang": "es", "target_lang": "ru", "direction": "BOGUS"},
                "-3": "not a dict",
            }
        ),
        encoding="utf-8",
    )
    cfg_mixed = ChatTranslateConfig(storage_path=mixed_path, now_fn=_fixed_now)
    chats = {item["chat_id"] for item in cfg_mixed.list_chats()}
    assert chats == {"-1"}
